"""
runtime.acceptance - Acceptance 阶段评估器

按 [beebox-design.md §3.4.6] 实现：
- task run COMPLETED → AWAITING_ACCEPTANCE
- 按 definition.result.acceptance 逐条判定 result 数据
- 全通过 → ACCEPTED
- 任一失败 → 按 contract.exceptions 决定：no_deliver / rollback / escalate
- rollback → COMPENSATING_ACCEPTANCE（执行反向 op 后回 AWAITING_ACCEPTANCE）
- escalate → 触发 queen 接管

本 PoC：
- 实现 ACCEPTED / REJECTED / COMPENSATING_ACCEPTANCE 三种判定
- escalate 走 queen hook（runtime/queen.py）
- rollback 暂不实现反向 op（标记 COMPENSATING_ACCEPTANCE，回 AWAITING_ACCEPTANCE）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.models import AcceptanceRule, ExceptionRule, ResultDef, is_not_applicable
from runtime.models import (
    DECISION_HUMAN_REVIEW,
    AcceptanceEvaluation,
    AcceptanceStatus,
    RejectionClass,
    TaskRun,
    TaskRunStatus,
)


# ============================================================
# 单条 acceptance rule 评估
# ============================================================


def _evaluate_rule(rule: AcceptanceRule, result: dict[str, Any]) -> bool | None:
    """评估单条 acceptance rule

    Args:
        rule: AcceptanceRule（metric + op + value）
        result: task run 业务结果 dict

    Returns:
        True = pass, False = fail, None = **不适用**

    None 不是「缺值」也不是「失败」，是「这条判据对这次产出用不上」。
    典型：票上没印单价，却要验「数量 × 单价 == 金额」——无从检验，
    不该说它过，也不该说它错。混进 pass 是撒谎，混进 fail 同样是撒谎。
    """
    actual = result.get(rule.metric)
    if is_not_applicable(actual):
        return None

    if rule.op == "eq":
        return actual == rule.value
    if rule.op == "gte":
        return actual is not None and actual >= rule.value
    if rule.op == "lte":
        return actual is not None and actual <= rule.value
    if rule.op == "in":
        return actual in rule.value
    if rule.op == "match":
        # 简单 substring 匹配
        return actual is not None and rule.value in str(actual)

    raise ValueError(f"unknown op: {rule.op}")


@dataclass
class ConditionOutcome:
    """一条条件的求值结果——**成立 / 不成立 / 用不上 / 为什么**

    **为什么要多一个「用不上」**（2026-11-01 实测撞出来的）：
    条件引用的字段在产出里根本没有、或者拿数组跟数字比——
    这两种情况**永远不成立**。以前一律返回 False，
    于是「规则指向一个盒子根本没算的量」= **静默失效**：
    读者以为规则在守着，其实它一次都没触发过。

    这种「声明了却拿不到」必须**看得见**，所以求值结果要带原因。
    """
    matched: bool
    usable: bool = True
    reason: str = ""
    field: str = ""


def evaluate_condition(
    condition: str, result: dict[str, Any],
) -> ConditionOutcome:
    """求值一条条件，**并说清是「不成立」还是「用不上」**

    与 `_evaluate_condition` 的区别只有一个：**带 detail**。
    求值口径完全一样（同一个函数，`_evaluate_condition` 是它的薄包装）——
    两套求值口径 = 迟早漂，所以不另写一份。
    """
    parts = condition_parts(condition)
    if not parts:
        return ConditionOutcome(False, usable=False, reason="条件是空的")
    if len(parts) > 1:
        # AND：每一段都要成立。
        # **每一段都要求值**（不短路）——不然后面某段指向一个不存在的量时，
        # 这次求值看不出「有一条规则用不上」。
        outcomes = [evaluate_condition(part, result) for part in parts]
        unusable = [o for o in outcomes if not o.usable]
        if unusable:
            return ConditionOutcome(
                False, usable=False, reason=unusable[0].reason, field=unusable[0].field,
            )
        return ConditionOutcome(all(o.matched for o in outcomes), usable=True)

    cond = parts[0]
    matches = list(re.finditer(r"(>=|<=|==|!=|>|<)", cond))
    if not matches:
        # 裸标识符：一个 op 都匹配不上 → 永不触发。
        # 这里返回 False 是刻意的（见 _evaluate_rule 的注释），
        # 所以「有没有 op」必须由守卫提前拦住。
        return ConditionOutcome(
            False, usable=False, reason=f"条件里没有比较符：{cond!r}",
        )
    if len(matches) > 1:
        # 一段里塞了两个比较符（多半是漏了 AND）。
        # 硬跑下去会在比较时抛 TypeError——那会把整个 acceptance 评估
        # 掀翻，看板上这只盒子直接 500，比规则不触发还糟。
        # 守卫会提前拦住这种写法，这里只是不让它炸。
        return ConditionOutcome(
            False, usable=False,
            reason=f"一段里塞了两个比较符（多半是漏了 AND）：{cond!r}",
        )
    match = matches[0]

    op = match.group(1)
    field = cond[: match.start()].strip()
    value = cond[match.end():].strip()

    op_fns = {
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
    }
    # 尝试转换 value
    try:
        if value in ("true", "false"):
            value = value == "true"
        else:
            value = float(value) if "." in value else int(value)
    except (ValueError, TypeError):
        value = value.strip('"').strip("'")

    if field not in result:
        return ConditionOutcome(
            False, usable=False,
            reason=f"产出里没有「{field}」这个量", field=field,
        )
    actual = result.get(field)
    if actual is None:
        return ConditionOutcome(
            False, usable=False,
            reason=f"「{field}」这次是空值（算不出来）", field=field,
        )
    try:
        return ConditionOutcome(bool(op_fns[op](actual, value)), usable=True, field=field)
    except TypeError:
        # 类型对不上（比如拿数组跟数字比、拿字符串跟数字比）。
        # 同样是「这条规则这次用不上」，不是「整个评估崩掉」。
        return ConditionOutcome(
            False, usable=False,
            reason=(
                f"「{field}」的类型（{type(actual).__name__}）"
                f"跟 {value!r} 对不上"
            ),
            field=field,
        )


def _evaluate_condition(condition: str, result: dict[str, Any]) -> bool:
    """条件成不成立（只有成立 / 不成立）

    想要「用不上」的原因，用 `evaluate_condition`。
    """
    return evaluate_condition(condition, result).matched


def _find_exception_rule(
    exceptions: list[ExceptionRule],
    result: dict[str, Any],
) -> ExceptionRule | None:
    """按 result 数据找触发的 exception 规则（第一条匹配）"""
    for rule in exceptions:
        # PoC 简化：condition 是 "field op value" 形式（如 "refund_amount > 500"）
        # 更复杂的用真实 expression engine
        if _evaluate_condition(rule.condition, result):
            return rule
    return None


def condition_parts(condition: str) -> list[str]:
    """把一个 condition 按 AND 切成若干段

    AND 大小写都认：写成小写 `and` 是很自然的手滑，而它的后果是
    整条规则静默失效——**不报错、不提示**，比抛异常更难发现。

    切分要求 AND 两侧有空白，避免把字段名里的字母误当连接词
    （`brand` 里也有 `and`）。
    """
    return [
        part.strip()
        for part in re.split(r"\s+AND\s+", condition.strip(), flags=re.IGNORECASE)
        if part.strip()
    ]


def condition_fields(condition: str) -> list[str]:
    """这个条件实际会去读哪些字段

    **必须和 `_evaluate_condition` 用同一套解析。**

    这不是洁癖，是一个已经犯过的错：守卫原来用
    `re.split(r"(>=|<=|==|!=|>|<)", cond, maxsplit=1)` 取**位置最靠前**的
    比较符，被验的代码按 `(">=", "<=", "==", "!=", ">", "<")` 的**优先级顺序**
    找第一个出现的。条件里有两个比较符时两边选到不同的字段——
    守卫验的是它自己那套解析，于是放行了一条实际永远不触发的规则。

    守卫用被验者自己的解析，才能保证「验的就是那个」。
    """
    fields: list[str] = []
    for part in condition_parts(condition):
        match = re.search(r"(>=|<=|==|!=|>|<)", part)
        if match is None:
            continue  # 裸标识符：不读任何字段
        fields.append(part[: match.start()].strip())
    return fields


def _classify_failure(
    failed: list[dict[str, Any]],
    result: dict[str, Any],
    empty_input_metric: Optional[str] = None,
) -> RejectionClass:
    """给 REJECTED 归因——区分"投料没东西" / "模型没覆盖" / "规则打架" / 其余结构问题

    判据（按优先级，先看是否根本没东西可建）：
    1. 所有失败判据的 actual 都是 None——评测器一个值都没取到，
       说明上游没产出这些量（典型：0 条需求 → 没有任何设计元素 → 没有覆盖率）
    2. **盒子声明的投料条数为 0**——明确没投料
       （字段名由**盒子自己声明**（`ResultDef.empty_input_metric`），
       runtime 不写死任何一个盒子的字段名——业务知识不进内核）
    3. rule_consistency 判定为 false——规则自相矛盾（优先级高于覆盖率，
       因为规则矛盾时先修规则，补覆盖率没有意义）
    4. 有覆盖率判据失败且取到了具体数值——没覆盖全（判据名含 coverage 即可，
       不同盒子叫法不同：requirement_coverage / source_coverage）
    5. 其余（引用 / 结构 / 指标）
    """
    # 1 + 2：什么都没取到 / 明确没投料
    if empty_input_metric and result.get(empty_input_metric) == 0:
        return RejectionClass.EMPTY_INPUT
    if failed and all(f.get("actual") is None for f in failed):
        return RejectionClass.EMPTY_INPUT

    metrics_failed = {f.get("metric") for f in failed}

    # 3：规则矛盾优先归因
    if "rule_consistency" in metrics_failed and result.get("rule_consistency") is False:
        return RejectionClass.CONFLICT

    # 4：覆盖率有值但不够。
    #    按名字找 coverage 判据，不写死某一个：设计盒叫 requirement_coverage，
    #    捕获盒叫 source_coverage。写死一个名字 = 别的盒子全部落进 STRUCTURAL，
    #    归因错了比不归因更坏——人会被引去改错的东西。
    for metric in sorted(metrics_failed):
        if metric and "coverage" in metric and result.get(metric) is not None:
            return RejectionClass.INSUFFICIENT_COVERAGE

    # 5：其余结构性判据
    return RejectionClass.STRUCTURAL


# ============================================================
# Acceptance 评估器
# ============================================================


def evaluate_acceptance(
    task_run: TaskRun,
    contract: ResultDef,
    queen_decision_fn=None,
) -> AcceptanceEvaluation:
    """评估 task run 的 acceptance

    Args:
        task_run: 已 COMPLETED 的 task run（必须 status=COMPLETED）
        contract: beeBox definition.result（含 acceptance + exceptions）
        queen_decision_fn: queen 决策函数（escalate 时调用，可选）

    Returns:
        AcceptanceEvaluation（含 status + 通过/失败规则 + 触发的 exception action）

    状态转移：
    - 无 fail（n/a 不算 fail）→ ACCEPTED
    - 有 fail + exception=rollback → COMPENSATING_ACCEPTANCE（PoC 不执行反向 op，直接回 AWAITING）
    - 有 fail + exception=escalate → REJECTED（queen 决策）
    - 有 fail + exception=no_deliver → REJECTED
    - 有 fail 无 exception → REJECTED（默认）

    n/a（判据对本次产出用不上）既不挡 ACCEPTED，也不进 passed_rules——
    它进 not_applicable_rules，由看板单独列出来。说成「通过」是撒谎。
    """
    if task_run.status.value != "completed":
        raise ValueError(
            f"acceptance requires task_run.status=completed, got {task_run.status.value}"
        )

    result = task_run.result or {}
    passed: list[str] = []
    failed: list[dict[str, Any]] = []
    not_applicable: list[str] = []

    for rule in contract.acceptance:
        error: str | None = None
        try:
            ok = _evaluate_rule(rule, result)
        except Exception as e:
            ok = False
            error = str(e)

        # 三态：None = 用不上，不进 failed 也不进 passed
        if ok is None:
            not_applicable.append(rule.metric)
            continue
        if ok:
            passed.append(rule.metric)
        else:
            entry = {
                "metric": rule.metric,
                "op": rule.op,
                "expected": rule.value,
                "actual": result.get(rule.metric),
            }
            if error is not None:
                entry["error"] = error
            failed.append(entry)

    # 全 pass（n/a 不算失败）
    if not failed:
        return AcceptanceEvaluation(
            status=AcceptanceStatus.ACCEPTED,
            passed_rules=passed,
            failed_rules=[],
            not_applicable_rules=not_applicable,
        )

    # 有 fail：找 exception 规则
    exception = _find_exception_rule(contract.exceptions, result)

    rejection_class = _classify_failure(
        failed, result, contract.empty_input_metric,
    )

    # 没匹配到 exception：默认 REJECTED
    if exception is None:
        return AcceptanceEvaluation(
            status=AcceptanceStatus.REJECTED,
            passed_rules=passed,
            failed_rules=failed,
            not_applicable_rules=not_applicable,
            rejection_class=rejection_class,
            exception_action=None,
        )

    if exception.action == "no_deliver":
        return AcceptanceEvaluation(
            status=AcceptanceStatus.REJECTED,
            passed_rules=passed,
            failed_rules=failed,
            not_applicable_rules=not_applicable,
            rejection_class=rejection_class,
            exception_action="no_deliver",
        )

    if exception.action == "rollback":
        # PoC 简化：标记 COMPENSATING_ACCEPTANCE（实际生产要执行反向 op）
        return AcceptanceEvaluation(
            status=AcceptanceStatus.COMPENSATING_ACCEPTANCE,
            passed_rules=passed,
            failed_rules=failed,
            not_applicable_rules=not_applicable,
            exception_action="rollback",
        )

    if exception.action == "escalate":
        # **默认交给人**（2026-11-01 改）：
        # 例外写 `action="escalate"`，而每一条 escalate 例外在盒子里的
        # 注释都是「升级人工」「送人」——**升级的意思就是交给人看**。
        #
        # 以前这里是 `queen_decision = "pending"`，然后不管判成什么
        # 验收一律 REJECTED → **决策没有牙齿**，而且没人接的拒绝
        # 比多问一句更糟。现在：没有 queen 决策函数也照样交给人。
        queen_decision = DECISION_HUMAN_REVIEW
        if queen_decision_fn is not None:
            queen_decision = queen_decision_fn(task_run, failed)
        return AcceptanceEvaluation(
            status=AcceptanceStatus.REJECTED,  # 验收层：没过
            passed_rules=passed,
            failed_rules=failed,
            not_applicable_rules=not_applicable,
            rejection_class=rejection_class,
            exception_action="escalate",
            queen_decision=queen_decision,
        )

    raise ValueError(f"unknown exception action: {exception.action}")

# ============================================================
# 验收结论 → 履约层状态
# ============================================================


def apply_acceptance_to_run(
    task_run: TaskRun,
    evaluation: AcceptanceEvaluation,
) -> None:
    """把验收结论落到**履约层**状态上

    **为什么要有这一步**（2026-11-01 实测出来的）：
    线上那条被判红的开发盒，记录里是
    `status=completed` + `acceptance_status=rejected`——
    一次**没交付**的履约，履约层却写着「跑完了」。

    两层各说各话，读者只能自己猜「跑完了到底是成了还是没成」。
    判据在验收层、交付在履约层，那**履约层的终态就该说「交付了没有」**。

    口径（一句话一条，理由在括号里）：

    | 验收结果 | 履约层 | 为什么 |
    |---|---|---|
    | 通过 | `completed` | 交付了 |
    | 不通过 + 例外 `no_deliver` | `rejected` | 合同说「不交付」，这是**明确的不交付**，不是「跑挂了」 |
    | 不通过 + 例外 `escalate`，queen 判 `human_review` | `waiting_external` | 要**等人给个说法**（见下） |
    | 不通过 + 例外 `escalate`，其它决策 | `rejected` | queen 接管了，但这次不交付 |
    | 不通过 + 例外 `rollback` | 不动（保持 `completed`） | 补偿还没做（反向 op 的设计还没定）；等那边定下来再改成 `compensating` |
    | 不通过 + 没匹配到例外 | `failed` | 没成，且没人接 |

    **「等外部」为什么是真的在等**：
    queen 的 escalate 决策今天只被**记成一个字符串**
    （`human_review` / `auto_resolve` / `observe_only`），
    然后不管判成什么，验收一律返回 REJECTED——
    **决策没有牙齿**：queen 说「要人看」，系统还是直接拒了。

    现在 `human_review` 会让这次履约**停在 `waiting_external`**，
    并把「等什么、为什么等、谁能答」记进 `pending_external`——
    不然读者只看到「在等」，不知道在等谁。
    """
    if evaluation.status == AcceptanceStatus.ACCEPTED:
        # 交付了 → 履约层保持 completed
        return

    if evaluation.status == AcceptanceStatus.COMPENSATING_ACCEPTANCE:
        # 补偿还没实现：履约层先不动（见函数说明）
        return

    if evaluation.status == AcceptanceStatus.REJECTED:
        if (
            evaluation.exception_action == "escalate"
            and evaluation.queen_decision == DECISION_HUMAN_REVIEW
        ):
            task_run.pending_external = {
                "kind": "acceptance_decision",
                "question": (
                    "验收没过，且合同要求升级给人判定——"
                    "这次交付要不要放行？"
                ),
                "failed_rules": evaluation.failed_rules,
                "rejection_class": (
                    evaluation.rejection_class.value
                    if evaluation.rejection_class else ""
                ),
                "decidable_by": "human",
                "queen_decision": evaluation.queen_decision,
            }
            task_run.transition_to(
                TaskRunStatus.WAITING_EXTERNAL,
                reason=(
                    "验收不通过，合同要求升级；queen 判 human_review"
                    "——这次履约在等人给判定"
                ),
            )
            return

        action = evaluation.exception_action
        if action is None:
            # **没人接**：判据没过，又没有合同例外说「不交付」
            # → 就是没成（failed），不是「明确拒绝」。
            # 这两者的区别是「有人决定了不交付」和「没人管」。
            task_run.transition_to(
                TaskRunStatus.FAILED,
                reason="验收不通过，且没有匹配的合同例外——这次没成",
            )
            return
        task_run.transition_to(
            TaskRunStatus.REJECTED,
            reason=f"验收不通过（{action}）——决定不交付",
        )
        return

    # 理论上到不了这里（REJECTED / ACCEPTED / COMPENSATING 三种都覆盖了）
    task_run.transition_to(
        TaskRunStatus.FAILED,
        reason=f"验收结论不认识：{evaluation.status.value}",
    )
