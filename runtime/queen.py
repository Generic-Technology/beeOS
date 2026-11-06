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

from runtime.acceptance import evaluate_condition
from runtime.models import (
    ALL_DECISIONS,
    DECISION_AUTO_RESOLVE,
    DECISION_HUMAN_REVIEW,
    DECISION_OBSERVE_ONLY,
    TaskRun,
)


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
    task_run: TaskRun,
    failed_rules: list[dict[str, Any]],
    queen_rules: Optional[dict[str, Any]] = None,
) -> QueenDecision:
    """按**盒子自己声明的**升级规则做一次 escalate 决策

    Args:
        task_run: 已 COMPLETED 的这次履约（条件在它的 result 上求值）
        failed_rules: 验收失败的判据（写进理由，让读者知道为什么升级）
        queen_rules: 盒子声明的 `queen.rules`（`model_dump()` 后的 dict）

    Returns:
        QueenDecision（`decision` ∈ 三个决策词之一）
    """
    result = task_run.result or {}
    handling = (queen_rules or {}).get("exception_handling") or {}
    rules = handling.get("escalation_rules") or []

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


def default_queen_escalation_handler(
    task_run: TaskRun,
    failed_rules: list[dict[str, Any]],
    queen_rules: Optional[dict[str, Any]] = None,
) -> str:
    """兼容入口：只要**决策字符串**的老调用方

    新代码用 `decide_escalation`——它还带理由和用不上的规则。
    """
    return decide_escalation(task_run, failed_rules, queen_rules).decision
