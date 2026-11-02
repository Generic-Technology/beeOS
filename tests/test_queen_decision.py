"""
tests.test_queen_decision - Queen 决策层的守卫

**这一组守卫盯的是「自治边界从声明变成能力」的那一步**（2026-11-01）：

Queen 层以前是个摆设：
- `runtime/queen.py` 里**写死了 `refund_amount >`**（一个业务词躺在内核里），
  于是六只盒子声明的升级规则**一条都没被求值过**；
- **没有任何地方传过 `queen_decision_fn`** → escalate 永远落 `rejected`
  → **`waiting_external` 一次都没被走到过**（88 次真实履约，0 次）；
- 三套词表各说各话：盒子说 `escalate_to_human`、runtime 说
  `queen.escalate_to_human`、验收层认 `human_review`。

现在：规则由**盒子自己声明**，runtime 只做通用求值；
决策词表只有三个；**升级默认就是交给人**；决策落进 `policy_decision` 可追溯。

这份测试盯住四件事：
1. **runtime 里不许有业务词**（分层红线）；
2. **盒子的规则真能触发**（不是摆设）；
3. **用不上的规则不许静默**（声明了却拿不到 = 看得见）；
4. **决策可追溯**（落进 task run 和记录）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from runtime.acceptance import evaluate_condition
from runtime.models import TaskRunStatus, create_task_run
from runtime.queen import (
    ALL_DECISIONS,
    DECISION_AUTO_RESOLVE,
    DECISION_HUMAN_REVIEW,
    QueenDecision,
    decide_escalation,
)
from runtime.store import TaskRunStore


REPO = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO / "runtime"


# ============================================================
# 1. runtime 层不许有业务词
# ============================================================

#: 业务词表（WMS / 收据 / 学习域的专有词）
#:
#: **为什么按「标识符分词」而不是整串 find**：
#: - `_pick_next` 里有个 pick、`timezone` 里有个 zone —— 整串 find 会误伤；
#: - `sku_count` 这种**嵌在标识符里的**业务词，整串 find 反而会漏。
#: 按 `_` 切成词再逐个比对，两头都对。
DOMAIN_WORDS = {
    "refund", "fefo", "sku", "inventory", "warehouse", "receipt", "outbound",
    "inbound", "wave", "expiring", "requirement", "batch", "lot", "learning",
    "chinese", "english", "growth", "zone", "location", "mastery", "picking",
    "stockout", "expire", "expired", "sku_id", "warehouse_id", "location_id",
    "zone_id", "requirement_set", "pick_accuracy", "expiring_soon",
    "space_utilization", "inventory_turnover",
}


def _code_tokens(path: Path) -> tuple[list[str], list[str]]:
    """文件里的**标识符**和**字符串常量**（**不含 docstring**）

    **为什么用 ast 而不是 find**：docstring 里可以自由地讨论业务
    （解释「为什么这条规则指向一个拿不到的量」就该提业务名），
    代码里则一个字都不该有。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            text = ast.get_docstring(node, clean=False)
            if text is not None:
                docstrings.add(text)

    identifiers: list[str] = []
    strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.append(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.append(node.attr)
        elif isinstance(node, ast.arg):
            identifiers.append(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                strings.append(node.value)
    return identifiers, strings


@pytest.mark.parametrize(
    "path", sorted(RUNTIME_DIR.glob("*.py")), ids=lambda p: p.name,
)
def test_the_runtime_layer_knows_no_domain_word(path: Path):
    """**runtime 里不许出现业务词**——业务知识归盒子

    内核（runtime/core）只该认识「字段、条件、状态」，
    不该认识「库区、拣货、收据、作业」——
    一旦认识了，它就成了替盒子做业务决定的地方。
    """
    identifiers, strings = _code_tokens(path)
    bad: list[str] = []
    for ident in identifiers:
        for token in ident.lower().split("_"):
            if token in DOMAIN_WORDS:
                bad.append(f"标识符 {ident!r}")
    for text in strings:
        low = text.lower()
        for word in DOMAIN_WORDS:
            if word in low:
                bad.append(f"字符串 {text[:60]!r}")
    assert not bad, (
        f"{path.name} 里出现了业务词（runtime 不该认识业务）：{bad[:8]}"
    )


def test_escalate_without_a_queen_function_still_goes_to_a_human():
    """**没有 queen 决策函数时，升级也得交给人**

    「升级」的意思就是交给人看。**没有决策函数 ≠ 没人管**——
    以前这里写死 `"pending"`，然后验收一律 REJECTED：
    一次**没人看的拒绝**，而读者以为「升级了」。

    这条路是**兜底**：调用方没接决策函数时，升级也不能悄悄变成拒绝。
    """
    from core.models import AcceptanceRule, ExceptionRule, ResultDef
    from runtime.acceptance import apply_acceptance_to_run, evaluate_acceptance

    contract = ResultDef(
        type="x_resolved",
        result_schema="schema_x",
        acceptance=[AcceptanceRule(metric="ok", op="eq", value=True)],
        exceptions=[ExceptionRule(condition="ok == false", action="escalate")],
    )
    task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
    task_run.transition_to(TaskRunStatus.RUNNING, reason="x")
    task_run.transition_to(TaskRunStatus.COMPLETED, reason="x")
    task_run.result = {"ok": False}

    # **故意不传 queen_decision_fn**
    evaluation = evaluate_acceptance(task_run, contract)
    assert evaluation.queen_decision == DECISION_HUMAN_REVIEW
    apply_acceptance_to_run(task_run, evaluation)
    assert task_run.status == TaskRunStatus.WAITING_EXTERNAL


def test_the_decision_words_come_from_one_place():
    """**两层的决策词必须同源**——以前是三套词各说各话

    盒子说 `escalate_to_human`、runtime 说 `queen.escalate_to_human`、
    验收层认 `human_review` → 三套词对不上，
    决策就传不到履约层 → **`waiting_external` 一次都没被走到过**。

    现在：词表的**唯一真源**在 `runtime/models.py`，两层都从那儿取。
    这条守卫盯的就是「别又各自写一个字符串」。
    """
    from runtime import acceptance, queen
    from runtime.models import ALL_DECISIONS as FROM_MODELS

    assert queen.ALL_DECISIONS == FROM_MODELS
    assert acceptance.DECISION_HUMAN_REVIEW == DECISION_HUMAN_REVIEW
    # **这三个词是跨系统契约**（看板 JS、历史记录、resume 接口都用它们），
    # 所以字面量本身要被钉住——改名就等于把存量记录和界面一起改坏。
    assert FROM_MODELS == ("human_review", "auto_resolve", "observe_only")
    # **验收层不许在代码里写死这个词**——写死就等于又多了一份真源
    _ids, strings = _code_tokens(RUNTIME_DIR / "acceptance.py")
    assert "human_review" not in strings, (
        f"验收层里写死了决策词：{[s for s in strings if 'human_review' in s]}"
    )


# ============================================================
# 2. 盒子的规则真能触发
# ============================================================


def _run_with_result(result: dict[str, Any]):
    task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
    task_run.result = result
    return task_run


def test_a_matching_rule_drives_the_decision():
    """条件成立 → 按盒子的 action 映射到 runtime 的决策词"""
    task_run = _run_with_result({"build_failed": True})
    decision = decide_escalation(task_run, [], {
        "exception_handling": {
            "escalation_rules": [
                {"trigger": "build_failed == true", "action": "escalate_to_human"},
            ],
        },
    })
    assert decision.decision == DECISION_HUMAN_REVIEW
    assert decision.triggered_rule == "build_failed == true"
    assert decision.reason, "决策必须带理由"


def test_a_non_matching_rule_leaves_it_to_the_human():
    """没命中任何规则 → **仍然交给人看**

    「升级」的意思就是交给人看。Queen 只能按盒子明说的降级，
    不许自己决定「这事不用人看」——没人看的拒绝比多问一句更糟。
    """
    task_run = _run_with_result({"build_failed": False})
    decision = decide_escalation(task_run, [], {
        "exception_handling": {
            "escalation_rules": [
                {"trigger": "build_failed == true", "action": "escalate_to_human"},
            ],
        },
    })
    assert decision.decision == DECISION_HUMAN_REVIEW
    assert decision.triggered_rule == ""


def test_the_box_may_say_it_resolves_itself():
    """**盒子明说「这个我自动解决」→ 才不升级**"""
    task_run = _run_with_result({"amount": 1})
    decision = decide_escalation(task_run, [], {
        "exception_handling": {
            "escalation_rules": [
                {"trigger": "amount < 10", "action": "auto_resolve"},
            ],
        },
    })
    assert decision.decision == DECISION_AUTO_RESOLVE


def test_an_unknown_action_is_not_guessed():
    """不认识的 action → **按「要人看」处理，并把不认识这件事说出来**

    猜成 auto_resolve = 一次没人看的拒绝；猜成 human_review = 多问一句。
    """
    task_run = _run_with_result({"amount": 99})
    decision = decide_escalation(task_run, [], {
        "exception_handling": {
            "escalation_rules": [
                {"trigger": "amount > 1", "action": "ask_the_boss"},
            ],
        },
    })
    assert decision.decision == DECISION_HUMAN_REVIEW
    assert "ask_the_boss" in decision.reason, decision.reason


def test_the_decision_is_always_one_of_the_three_words():
    """决策词表是**封闭**的——履约层只认这三个"""
    task_run = _run_with_result({"amount": 1})
    for rules in (
        {"exception_handling": {"escalation_rules": []}},
        {"exception_handling": {"escalation_rules": [
            {"trigger": "amount > 0", "action": "escalate_to_human"}]}},
        {"exception_handling": {"escalation_rules": [
            {"trigger": "amount > 999", "action": "escalate_to_human"}]}},
    ):
        decision = decide_escalation(task_run, [], rules)
        assert decision.decision in ALL_DECISIONS, decision.decision


# ============================================================
# 3. 用不上的规则不许静默
# ============================================================


def test_a_rule_pointing_at_a_missing_field_is_reported():
    """**字段取不到 → 这条规则用不上 → 必须说出来**

    「声明了却拿不到」= 一句空承诺。读者以为规则在守着，
    其实它一次都没触发过——**不许静默**。
    """
    task_run = _run_with_result({"amount": 1})
    decision = decide_escalation(task_run, [], {
        "exception_handling": {
            "escalation_rules": [
                {"trigger": "no_such_metric > 3", "action": "escalate_to_human"},
            ],
        },
    })
    assert decision.unusable_rules, "用不上的规则被吞了"
    assert "no_such_metric" in decision.unusable_rules[0]
    # 用不上 ≠ 规则生效：这条没命中，决策仍是「交给人」
    assert decision.triggered_rule == ""


def test_a_rule_comparing_the_wrong_type_is_reported():
    """拿数组跟数字比 → 也算「用不上」，不是「不成立」"""
    task_run = _run_with_result({"items": ["a", "b"]})
    decision = decide_escalation(task_run, [], {
        "exception_handling": {
            "escalation_rules": [
                {"trigger": "items > 3", "action": "escalate_to_human"},
            ],
        },
    })
    assert decision.unusable_rules, "类型对不上被吞了"


def test_the_condition_evaluator_says_why_it_could_not_evaluate():
    """条件求值器本身要说清「不成立」和「用不上」的区别"""
    result = {"amount": 1}
    assert evaluate_condition("amount > 0", result).matched is True
    assert evaluate_condition("amount > 5", result).matched is False
    missing = evaluate_condition("nope > 5", result)
    assert missing.matched is False
    assert missing.usable is False
    assert "nope" in missing.reason


# ============================================================
# 4. 端到端：触发 → 决策 → 停在等判定 → 决策可追溯
# ============================================================


def test_the_trigger_really_asks_the_queen(tmp_path: Path):
    """**触发入口真的把 Queen 接上了**——全系统第一次

    这条守卫盯的是「接上了」这件事本身：
    以前 `trigger_task` 一次都没传 `queen_decision_fn`，
    于是 escalate 永远落 rejected，**等判定这条状态永远走不到**。

    用系统盒做例子：探不通 → 验收红 → escalate → 升级 = 交给人
    → 履约停在 `waiting_external`，且决策可追溯。
    """
    from kanban.trigger import trigger_task

    store = TaskRunStore(path=tmp_path / "runs.jsonl")
    result = trigger_task(
        "wms_system_box", "sync_system_profile",
        # 指向一个**根本不通**的地址 → 健康端点探不到
        {"target_base_url": "http://127.0.0.1:1", "timeout_seconds": 1},
        store,
    )
    assert result["status"] == "waiting_external", result["status"]
    assert result["acceptance_status"] == "rejected"
    # 等的是谁、等什么
    assert result["pending_external"]["kind"] == "acceptance_decision"
    # **决策可追溯**（设计文档 §3.4.7）
    assert result["policy_decision"]["decision"] == "human_review"
    # 记录里也要有——历史记录查得到这次为什么在等
    record = store.list_recent()[-1]
    assert record["policy_decision"]["decision"] == "human_review"
    assert record["status"] == "waiting_external"


def test_the_run_status_can_still_move_on_from_waiting(tmp_path: Path):
    """停在等判定 → 人给了说法 → 这次履约接回去

    这一条保证「等判定」不是死路：等完能回来。
    """
    from kanban.trigger import trigger_task
    from runtime.resume import resume_waiting_run

    store = TaskRunStore(path=tmp_path / "runs.jsonl")
    result = trigger_task(
        "wms_system_box", "sync_system_profile",
        {"target_base_url": "http://127.0.0.1:1", "timeout_seconds": 1},
        store,
    )
    assert result["status"] == "waiting_external"
    record = resume_waiting_run(
        store, result["task_run_id"], "reject", "探不通，不交付",
    )
    assert record["status"] == "rejected"
    assert record["human_decision"]["decision"] == "reject"


def test_a_decision_object_can_be_written_and_read_back():
    """决策对象本身的形状——落盘就长这样"""
    decision = QueenDecision(
        decision=DECISION_HUMAN_REVIEW,
        reason="规则命中",
        triggered_rule="a > 1",
        unusable_rules=["b > 1（产出里没有「b」这个量）"],
    )
    data = decision.to_dict()
    assert data["decision"] == "human_review"
    assert data["triggered_rule"] == "a > 1"
    assert data["unusable_rules"]


def test_the_terminal_statuses_are_still_terminal():
    """等判定**不是终态**——它还能被人接回去（终态就接不回去了）"""
    from runtime.models import TERMINAL_STATUSES

    assert TaskRunStatus.WAITING_EXTERNAL not in TERMINAL_STATUSES
    assert TaskRunStatus.REJECTED in TERMINAL_STATUSES


# ============================================================
# 5. 跨盒守卫：升级规则的字段必须在盒子的产出里
# ============================================================


def test_every_escalation_rule_reads_a_field_the_box_actually_produces():
    """**盒子声明的升级规则不许指向一个它自己都没算的量**

    「声明了却拿不到」是同一族病：规则在，量不在，
    条件永远不成立 → 规则一次都没触发过 → 读者以为它在守着。

    以前设计盒就有一条 `rule_conflicts > 5`：
    那个量恒是空数组、且只在 evidence 里面——**永远取不到**。
    """
    from kanban.trigger import BOX_REGISTRY

    offenders: list[str] = []
    for box_id in sorted(BOX_REGISTRY):
        try:
            definition = BOX_REGISTRY[box_id][0]()
        except Exception:  # noqa: BLE001 — 造不好的盒子不算它的账
            continue
        rules = (
            (definition.queen.rules.model_dump().get("exception_handling") or {})
            .get("escalation_rules") or []
        )
        if not rules:
            continue
        schema = definition.get_schema(definition.result.result_schema)
        fields = {f.name for f in schema.fields} if schema else set()
        for rule in rules:
            trigger = str(rule.get("trigger") or "")
            for field in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", trigger):
                # 只看比较符**左边**的那个量（右边的多半是字面量）
                if field not in fields and trigger.startswith(field):
                    offenders.append(
                        f"{box_id} 的升级规则「{trigger}」指向了产出里没有的量 {field!r}"
                    )
    assert not offenders, "\n".join(offenders)
