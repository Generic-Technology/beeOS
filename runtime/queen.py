"""
runtime.queen - Queen 决策（bounded autonomy）

**Queen 的权力来自盒子自己的声明，runtime 只做通用求值**（2026-11-01 实测撞出来的）：

以前这里写死了 `refund_amount >` —— 一个**退款领协作边界的词躺在 runtime 里**。
后果不只是"脏"：

- 六只盒子声明的升级规则（`build_failed == true`、`rollback_ready == false`、
  `open_defect_count > 0`……）**一条都没被求值过**——
  规则在盒子里，runtime 不看；
- escalate 永远落 `rejected` → **`waiting_external` 从来没被走到过**
  （88 次真实履约，0 次；见 runtime/README.md 的状态表）。

现在：**规则由盒子声明**，runtime 做三件事，一件业务都不碰：

1. 用**条件求值器**（和验收例外同一套，不另写一份）求值；
2. 把盒子的 action 映射到 runtime 自己的**决策词表**；
3. 把决策**记进 task run**（`policy_decision`），可追溯、可复盘
   （设计文档 §3.4.7 要求「Queen 每次决策都记录」）。

**「升级」默认就是交给人**（2026-11-01 定）：
盒子的例外写 `action="escalate"`，而**每一条** escalate 例外在盒子里的
注释都是「升级人工」「送人」——**升级的意思就是交给人看**。
所以没命中任何 Queen 规则时，决策是 `human_review`（停在等判定），
不是 `auto_resolve`（直接拒掉）。

理由：**没人看的拒绝比多问一句更糟**。以前的口径是「没规则 = 不升级 =
直接拒」，于是 escalate 永远落 `rejected`，
**`waiting_external` 一次都没被走到过**（88 次真实履约，0 次）。
Queen 只能**按盒子明说的**降级（`auto_resolve` / `observe_only`），
不许自己决定「这事不用人看」。

**runtime 里不许出现任何业务词**：runtime 不知道什么是 zone / build / refund，
它只知道「盒子的这条条件成不成立」。守卫（`tests/test_queen_decision.py`）
会扫源码盯住这条线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.plugins import (
    PluginType,
    register_implementation,
    register_plugin_type,
)
from runtime.acceptance import evaluate_condition
from runtime.models import (
    ALL_DECISIONS,
    DECISION_AUTO_RESOLVE,
    DECISION_HUMAN_REVIEW,
    DECISION_OBSERVE_ONLY,
)
from runtime.plugins import EXTENSION_ESCALATE


# **决策词表在 runtime/models.py**（唯一的真源，两层共用）：
# 以前这里自己定义了一份、验收层又写死了一个字符串 → 三套词各说各话
# → 决策传不到履约层 → 等判定永远走不到。

#: 盒子声明的 action → runtime 的决策词表
#:
#: **不认识的 action 不猜**：按「要人看」处理，并在理由里写清
#: 「不认识的 action X」。理由是**「没人管」比「多问一句」更糟**——
#: 猜成 auto_resolve = 一次没人看的拒绝；猜成 human_review = 多问一句。
BOX_ACTION_TO_DECISION = {
    "escalate_to_human": DECISION_HUMAN_REVIEW,
    "flag_for_human_review": DECISION_HUMAN_REVIEW,
    "auto_resolve": DECISION_AUTO_RESOLVE,
    "observe_only": DECISION_OBSERVE_ONLY,
}


@dataclass
class QueenDecision:
    """一次 Queen 决策——**带理由、带命中的规则、带用不上的规则**

    **为什么要把「用不上的规则」也带上**（2026-11-01 实测撞出来的）：
    一条规则指向一个产出里根本没有的量 → 它永远不会触发 →
    读者以为它在守着，其实它一次都没触发过。
    这种「声明了却拿不到」必须**看得见**，不然就等于没声明。

    规则**只在盒子的产出上求值**——那是盒子自己的字段，
    runtime 不去别处找（找不到就当用不上，如实说）。
    """

    decision: str
    reason: str = ""
    triggered_rule: str = ""
    unusable_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "triggered_rule": self.triggered_rule,
            "unusable_rules": list(self.unusable_rules),
        }


def decide_escalation(
    result: dict[str, Any],
    failed_rules: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> QueenDecision:
    """按**盒子自己声明的**升级规则做一次 escalate 决策

    Args:
        result: 这次履约的产出（条件在它上面求值）
        failed_rules: 验收失败的判据（写进理由，让读者知道为什么升级）
        rules: 盒子声明的升级规则（每条 `trigger` + `action`）

    Returns:
        QueenDecision（`decision` ∈ 三个决策词之一）

    **为什么不再收 `task_run` 和 `queen_rules`**：
    这两样现在分别由**插件的受限视图**（`result`）和**插件自己的参数**
    （`rules`）提供——升级决策已经收编成盒级插件，
    规则的唯一真源是插件参数，不再住在 `queen.rules` 里。
    """
    if not rules:
        return QueenDecision(
            decision=DECISION_HUMAN_REVIEW,
            reason="这只盒子没有声明升级规则 → 按『升级 = 交给人』处理",
        )

    unusable: list[str] = []
    for rule in rules:
        trigger = str(rule.get("trigger") or "").strip()
        action = str(rule.get("action") or "").strip()
        outcome = evaluate_condition(trigger, result)
        if not outcome.usable:
            # **这条规则这次用不上**——记下来，别让它悄悄消失。
            unusable.append(f"{trigger}（{outcome.reason}）")
            continue
        if not outcome.matched:
            continue

        decision = BOX_ACTION_TO_DECISION.get(action)
        if decision is None:
            return QueenDecision(
                decision=DECISION_HUMAN_REVIEW,
                reason=(
                    f"规则「{trigger}」命中了，但 action {action!r} 不认识——"
                    "按『要人看』处理（没人管比多问一句更糟）"
                ),
                triggered_rule=trigger,
                unusable_rules=unusable,
            )
        return QueenDecision(
            decision=decision,
            reason=(
                f"规则「{trigger}」命中（{action}）"
                + (f"；{len(failed_rules)} 条判据没过" if failed_rules else "")
            ),
            triggered_rule=trigger,
            unusable_rules=unusable,
        )

    return QueenDecision(
        decision=DECISION_HUMAN_REVIEW,
        reason=(
            "没命中任何升级规则 → 按『升级 = 交给人』处理"
            "（Queen 只能按盒子明说的降级，不许自己决定不升级）"
            + (f"；{len(failed_rules)} 条判据没过" if failed_rules else "")
        ),
        unusable_rules=unusable,
    )


# ============================================================
# 升级决策插件（盒级）
# ============================================================
#
# **升级决策已经收编成盒级插件**（2026-11-12）。
#
# **为什么收编**：它本来就是「挂在盒子上、盒子声明规则、runtime 通用求值、
# 决策落进 task run 可追溯」的横切能力——**形态已经是插件了**，只是没人叫它插件。
# 而且它证明「盒级插件 + 条件触发扩展点 + 公共层实现 + 记录证据」这条路走得通。
#
# **收编后规则住哪**：升级规则的唯一真源是**插件参数**
# （`BoxDeclaration.plugins` 里那个 `queen_escalation` 的 `params`），
# 不再住在 `queen.rules.exception_handling` 里——
# 那边留着的是**别的**东西（各盒自己的无条件策略声明，runtime 不消费）。

#: 插件类型 id
QUEEN_ESCALATION_PLUGIN = "queen_escalation"

#: 实现键（实现在哪张表里）
QUEEN_ESCALATION_IMPL = "queen.escalation"


class EscalationRule(BaseModel):
    """一条升级规则

    **不许静默吞字段**：写错当场报错。
    """

    model_config = ConfigDict(extra="forbid")

    trigger: str = Field(description="触发条件（在这次履约的产出上求值）")
    action: str = Field(description="命中后的动作（映射到 runtime 的决策词表）")


class QueenEscalationParams(BaseModel):
    """升级决策插件的参数：这只盒子声明的升级规则"""

    model_config = ConfigDict(extra="forbid")

    escalation_rules: list[EscalationRule] = Field(
        default_factory=list, description="这只盒子自己的升级规则"
    )


def default_escalation_decision() -> dict[str, Any]:
    """**没挂升级决策插件时的默认口径**：升级 = 交给人

    理由跟「这只盒子声明了插件但没写规则」时**完全一样**——
    Queen 只能按盒子明说的降级，不许自己决定不升级。
    （不装插件的盒子拿到的决策，与装了插件但规则为空的盒子**逐字相同**。）
    """
    return QueenDecision(
        decision=DECISION_HUMAN_REVIEW,
        reason="这只盒子没有声明升级规则 → 按『升级 = 交给人』处理",
    ).to_dict()


def queen_escalation_plugin(view: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """**升级决策插件的实现**（盒级插件 `queen_escalation`）

    **它吃两样东西**：

    - `view` —— runtime 给的**受限视图**：`result`（这次履约的产出）
      + `failed_rules`（没过的判据）。视图是**深拷贝**，插件改不到宿主的数据。
    - `params` —— 插件**自己的参数**：这只盒子声明的升级规则。

    **返回完整决策对象**（不是光一个词）——
    理由、命中的规则、用不上的规则都在里面。
    少一项读者就看不到发生了什么：尤其「用不上的规则」——
    一条规则指向产出里根本没有的量 → 它永远不会触发 →
    读者以为它在守着，其实它一次都没触发过。
    """
    decision = decide_escalation(
        dict(view.get("result") or {}),
        list(view.get("failed_rules") or []),
        list(params.get("escalation_rules") or []),
    )
    return decision.to_dict()


def register_queen_escalation_plugin() -> None:
    """登记这个插件类型与它的实现

    **登记主体是 runtime（公共提供方）**——
    升级决策的**机制**是通用的（runtime 做通用求值，不认识任何业务词），
    通用型插件由公共提供方登记一次，各盒子只声明绑定。
    """
    register_plugin_type(
        PluginType(
            id=QUEEN_ESCALATION_PLUGIN,
            allowed_hosts=("box",),
            extension_points=(EXTENSION_ESCALATE,),
            implementation_key=QUEEN_ESCALATION_IMPL,
            params_model=QueenEscalationParams,
            summary="盒级升级决策：验收没过且例外要求升级时，由这只盒子自己声明的规则决定",
        )
    )
    register_implementation(QUEEN_ESCALATION_IMPL, queen_escalation_plugin)


register_queen_escalation_plugin()
