"""
tests.test_taskrun_states - TaskRun 9 个状态里那 4 个「转正」的守卫

**这四个状态以前是 placeholder**（`runtime/models.py` 里写着
「本 PoC 不实现转移」）：RETRYING / PARTIALLY_COMPLETED /
WAITING_EXTERNAL / REJECTED。

它们不是「多几个枚举值」——**每个状态都要有一个真的触发器**，
否则就是「声明了却没人执行」那一族病。这份测试盯住的就是
「每个状态**真的会被走到**，且走到时说的话是实话」。

| 状态 | 触发器 |
|---|---|
| RETRYING | op 传输类失败（llm_call）+ op 声明可重试 + 次数没耗尽 |
| PARTIALLY_COMPLETED | 链没走完，且**有跳声明留下了东西**（`leaves_artifact`）且它成功了 |
| WAITING_EXTERNAL | 验收不通过 + 合同例外 escalate + queen 判 human_review |
| REJECTED | 验收不通过 + 合同例外 no_deliver（**明确的不交付**） |

另外两条：

- 履约层的终态要跟验收结果对齐（以前「没交付」的履约写着 completed）
- 人给判定之后，这次履约能**接回去**（resume），且放行不抹掉没过的判据
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.beeline_models import Bee, Beeline, Idempotency, NextRef, Operation
from runtime.acceptance import apply_acceptance_to_run
from runtime.executor import MAX_OP_ATTEMPTS, BeelineExecutor
from runtime.llm import LLMError
from runtime.models import (
    AcceptanceEvaluation,
    AcceptanceStatus,
    RejectionClass,
    TaskRunStatus,
    create_task_run,
)
from runtime.resume import ResumeError, resume_waiting_run
from runtime.store import TaskRunStore


# ============================================================
# 工具：造一条最小 beeline + 一个会按剧本失败的 runner
# ============================================================


def _beeline(*, idempotency: Idempotency | None = None,
             leaves_artifact: bool = False) -> Beeline:
    return Beeline(
        id="b_states", version=1, description="states chain",
        operations=[
            Operation(
                op_id="op1", type="noop", input_from="external",
                input="s_in", output="s_out",
                next=[NextRef(op_id="op2")], bee=Bee(type="t"),
                leaves_artifact=leaves_artifact,
            ),
            Operation(
                # **声明挂在会失败的那一跳上**——runtime 问的是
                # 「这一跳能不能重放」，不是「这一条链能不能重放」
                op_id="op2", type="boom", input_from="op1",
                input="s_out", output="s_out2",
                bee=Bee(type="t"),
                idempotency=idempotency,
            ),
        ],
    )


class _ScriptedRunner:
    """按 op_id 剧本走：op1 成功，op2 抛指定的错"""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls: list[str] = []

    def run(self, op_type: str, op_input: dict[str, Any]) -> Any:
        self.calls.append(op_type)
        if op_type == "boom":
            raise self.exc
        return {"ok": True}


def _run(exc: Exception, **beeline_kw) -> tuple[Any, _ScriptedRunner]:
    runner = _ScriptedRunner(exc)
    task_run = create_task_run("o", "i", "c", "r", "b_states", 1, "rt", "in")
    result = BeelineExecutor(runner).execute(
        task_run, _beeline(**beeline_kw), {},
    )
    return result, runner


# ============================================================
# 1) RETRYING：传输类失败会整跳重试
# ============================================================


class TestRetrying:
    def test_a_transport_failure_retries_the_op(self):
        """传输类失败 → 这一跳重试（不是把整条链判死）

        真跑链撞过：开发盒跑了 30 轮，模型正答着时网络抖了一下
        → 整轮当场归零，前面写的代码全成孤儿。
        """
        result, runner = _run(LLMError("SSL: UNEXPECTED_EOF_WHILE_READING"))
        assert "boom" in runner.calls, "没有重试"
        assert runner.calls.count("boom") > 1, f"只跑了一次：{runner.calls}"

    def test_the_retry_is_visible_in_the_run(self):
        """「在重试」必须在履约层看得见

        光在 op 里闷头重试，看板上看它一直停在 running，
        人不知道它是在动还是死了。
        """
        result, _runner = _run(LLMError("boom"))
        transitions = [
            (e.detail or {}).get("to")
            for e in result.event_log.entries if e.event == "status_change"
        ]
        assert "retrying" in transitions, f"没有经过 retrying：{transitions}"
        assert transitions.count("running") >= 2, (
            f"重试之后没回到 running：{transitions}"
        )

    def test_every_attempt_is_recorded(self):
        """每次尝试都要留下记录——「退了几次」是事实，不是推测"""
        result, _runner = _run(LLMError("boom"))
        record = result.operations["op2"]
        assert record.attempts > 1, f"尝试次数没记：{record.attempts}"
        retries = [e for e in result.event_log.entries if e.event == "op_retry"]
        assert len(retries) >= 1, "重试没进事件流"

    def test_the_retry_count_is_bounded(self):
        """**上限必须有**：没有上限的重试 = 一条永远跑不完的履约"""
        result, runner = _run(LLMError("boom"))
        assert runner.calls.count("boom") <= MAX_OP_ATTEMPTS, (
            f"重试没上限：{runner.calls.count('boom')}"
        )
        assert result.status in (TaskRunStatus.FAILED, TaskRunStatus.PARTIALLY_COMPLETED)

    def test_an_op_that_declares_forbidden_is_not_retried(self):
        """**有副作用的 op 不许重试**——重放会重复做事

        开发盒的 dev_code 会提交到 WMS 仓：重放一次 = 两个 commit。
        所以它声明 forbidden，runtime 就不许重试它。
        """
        result, runner = _run(
            LLMError("boom"),
            idempotency=Idempotency(mode="forbidden"),
        )
        assert runner.calls.count("boom") == 1, (
            f"声明了不许重放却重试了：{runner.calls}"
        )

    def test_a_box_logic_error_is_not_retried(self):
        """盒子逻辑错（op_error）重试也修不好——不浪费轮次"""
        result, runner = _run(ValueError("盒子自己算错了"))
        assert runner.calls.count("boom") == 1, f"逻辑错也重试了：{runner.calls}"

    def test_a_parse_error_is_not_retried(self):
        """模型答了但格式不对 → 同一个 prompt 再问一次大概率还是那样

        盒子自己的循环会带着**反馈**重新问，那才有用。
        """
        from runtime.llm import LLMParseError

        result, runner = _run(LLMParseError("不是 JSON"))
        assert runner.calls.count("boom") == 1, f"解析错也重试了：{runner.calls}"


# ============================================================
# 2) PARTIALLY_COMPLETED：有东西留在现场 ≠ 什么都没发生
# ============================================================


class TestPartiallyCompleted:
    def test_a_leftover_artifact_is_reported_as_partial(self):
        """链没走完，但**有东西留在现场** → 部分完成

        开发盒：第 2 跳提交了代码、第 3 跳构建没过 →
        仓里躺着一个提交和一个分支。读者必须能知道这件事。
        """
        result, _runner = _run(
            ValueError("build failed"),
            leaves_artifact=True,
        )
        assert result.status == TaskRunStatus.PARTIALLY_COMPLETED, (
            f"有残留却只记了失败：{result.status}"
        )

    def test_nothing_left_behind_is_a_plain_failure(self):
        """没留下任何东西 → 就是失败

        需求盒的第 1 跳（把句子切碎）也是「成功完成」，
        可它什么都没留下（碎句只在内存里）——
        runtime 分不出这两者，**只有盒子知道**（leaves_artifact）。
        """
        result, _runner = _run(ValueError("boom"))
        assert result.status == TaskRunStatus.FAILED, (
            f"什么都没留下却记成部分完成：{result.status}"
        )

    def test_the_reason_names_what_was_left(self):
        """理由要指名是哪一跳留下了东西——读者才知道去哪儿找"""
        result, _runner = _run(
            ValueError("boom"), leaves_artifact=True,
        )
        reasons = [
            str((e.detail or {}).get("reason") or "")
            for e in result.event_log.entries if e.event == "status_change"
        ]
        assert any("op1" in r for r in reasons), (
            f"理由没指名留下的那一跳：{reasons}"
        )


# ============================================================
# 3) REJECTED：明确的不交付，不是「跑挂了」
# ============================================================


def _evaluation(
    *, action: str | None = "no_deliver",
    queen_decision: str | None = None,
) -> AcceptanceEvaluation:
    return AcceptanceEvaluation(
        status=AcceptanceStatus.REJECTED,
        passed_rules=[],
        failed_rules=[{"metric": "review_coverage", "actual": False}],
        not_applicable_rules=[],
        rejection_class=RejectionClass.STRUCTURAL,
        exception_action=action,
        queen_decision=queen_decision,
    )


class TestRejected:
    def test_no_deliver_means_the_run_says_rejected(self):
        """合同说「不交付」→ 履约层说「没交付」

        以前履约层写的是 completed —— 一次没交付的履约写着「跑完了」。
        """
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.transition_to(TaskRunStatus.RUNNING, reason="x")
        task_run.transition_to(TaskRunStatus.COMPLETED, reason="x")
        apply_acceptance_to_run(task_run, _evaluation(action="no_deliver"))
        assert task_run.status == TaskRunStatus.REJECTED

    def test_a_failure_without_an_exception_rule_is_failed(self):
        """验收不通过、又没人接 → 就是失败"""
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.transition_to(TaskRunStatus.RUNNING, reason="x")
        task_run.transition_to(TaskRunStatus.COMPLETED, reason="x")
        apply_acceptance_to_run(task_run, _evaluation(action=None))
        assert task_run.status == TaskRunStatus.FAILED


# ============================================================
# 4) WAITING_EXTERNAL：等外部，且能接回去
# ============================================================


class TestWaitingExternal:
    def _parked_run(self) -> Any:
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.transition_to(TaskRunStatus.RUNNING, reason="x")
        task_run.transition_to(TaskRunStatus.COMPLETED, reason="x")
        apply_acceptance_to_run(
            task_run, _evaluation(action="escalate", queen_decision="human_review"),
        )
        return task_run

    def test_human_review_parks_the_run(self):
        """queen 说「要人看」→ 这次履约**停在等判定**

        以前这个决策只被记成一个字符串，然后不管判成什么一律 REJECTED——
        **决策没有牙齿**。
        """
        task_run = self._parked_run()
        assert task_run.status == TaskRunStatus.WAITING_EXTERNAL
        assert task_run.pending_external, "没记「在等什么」"
        assert task_run.pending_external["kind"] == "acceptance_decision"
        assert task_run.pending_external["failed_rules"], (
            "没记「为什么需要人判」——判的人不知道该看什么"
        )

    def test_other_queen_decisions_do_not_park(self):
        """auto_resolve / observe_only 不等人 → 直接拒"""
        for decision in ("auto_resolve", "observe_only"):
            task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
            task_run.transition_to(TaskRunStatus.RUNNING, reason="x")
            task_run.transition_to(TaskRunStatus.COMPLETED, reason="x")
            apply_acceptance_to_run(
                task_run, _evaluation(action="escalate", queen_decision=decision),
            )
            assert task_run.status == TaskRunStatus.REJECTED, decision

    def test_a_person_can_answer_and_the_run_continues(self, tmp_path: Path):
        """等人判定 → 人给了说法 → 这次履约接回去（不是永远停着）"""
        store = TaskRunStore(path=tmp_path / "runs.jsonl")
        task_run = self._parked_run()
        store.append(
            task_run, box_id="wms_development_box",
            acceptance_status="rejected",
            acceptance_detail=[{
                "metric": "review_coverage", "outcome": "failed", "passed": False,
            }],
        )
        record = resume_waiting_run(store, task_run.identity.task_run_id, "approve", "人看过")
        assert record["status"] == "completed"
        assert record["acceptance_status"] == "accepted"

    def test_an_approval_does_not_erase_the_failed_rule(self, tmp_path: Path):
        """**人工放行 ≠ 判据过了**

        放行了哪条没过、谁放的、为什么——都要留痕。
        「人放行了」和「判据过了」混成一件就是造假信号。
        """
        store = TaskRunStore(path=tmp_path / "runs.jsonl")
        task_run = self._parked_run()
        store.append(
            task_run, box_id="wms_development_box",
            acceptance_status="rejected",
            acceptance_detail=[{
                "metric": "review_coverage", "outcome": "failed", "passed": False,
            }],
        )
        record = resume_waiting_run(store, task_run.identity.task_run_id, "approve", "人看过")
        decision = record["human_decision"]
        assert decision["decision"] == "approve"
        assert "review_coverage" in decision["overridden_rules"]
        assert decision["note"] == "人看过"
        # 没过的那条判据**还在**明细里
        assert any(
            row.get("metric") == "review_coverage"
            and row.get("outcome") == "failed"
            for row in record["acceptance_detail"]
        )

    def test_rejecting_keeps_the_run_rejected(self, tmp_path: Path):
        store = TaskRunStore(path=tmp_path / "runs.jsonl")
        task_run = self._parked_run()
        store.append(task_run, box_id="b", acceptance_status="rejected")
        record = resume_waiting_run(store, task_run.identity.task_run_id, "reject", "不行")
        assert record["status"] == "rejected"
        assert record["acceptance_status"] == "rejected"

    def test_only_a_waiting_run_can_be_answered(self, tmp_path: Path):
        """别的状态不许接回去——否则就是随便改历史"""
        store = TaskRunStore(path=tmp_path / "runs.jsonl")
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.transition_to(TaskRunStatus.RUNNING, reason="x")
        task_run.transition_to(TaskRunStatus.COMPLETED, reason="x")
        store.append(task_run, box_id="b", acceptance_status="accepted")
        with pytest.raises(ResumeError):
            resume_waiting_run(store, task_run.identity.task_run_id, "approve", "")

    def test_an_unknown_decision_is_refused(self, tmp_path: Path):
        store = TaskRunStore(path=tmp_path / "runs.jsonl")
        task_run = self._parked_run()
        store.append(task_run, box_id="b", acceptance_status="rejected")
        with pytest.raises(ResumeError):
            resume_waiting_run(store, task_run.identity.task_run_id, "随便", "")

    def test_the_latest_record_wins(self, tmp_path: Path):
        """同一次履约追加了新记录 → 取**最新**那条（当前状态）"""
        store = TaskRunStore(path=tmp_path / "runs.jsonl")
        task_run = self._parked_run()
        store.append(task_run, box_id="b", acceptance_status="rejected")
        run_id = task_run.identity.task_run_id
        assert store.get_run(run_id)["status"] == "waiting_external"
        resume_waiting_run(store, run_id, "approve", "行")
        assert store.get_run(run_id)["status"] == "completed"


# ============================================================
# 5) 履约层的终态 = 交付了没有
# ============================================================


class TestTheRunStatusAnswersDelivery:
    def test_a_rejected_delivery_is_not_recorded_as_completed(self):
        """一次没交付的履约，履约层不能写「跑完了」

        线上真跑出来过：status=completed + acceptance_status=rejected。
        读者看到「completed」根本不知道这次成没成。
        """
        from kanban.trigger import trigger_task

        store = TaskRunStore(path=Path("/tmp/beeos_states_probe.jsonl"))
        # 用一只判据必过的盒子做对照
        result = trigger_task(
            "wms_system_box", "sync_system_profile", {}, store,
        )
        assert result["status"] in ("completed", "rejected", "failed")
        if result["acceptance_status"] == "accepted":
            assert result["status"] == "completed"
        else:
            assert result["status"] != "completed", (
                "验收没过，履约层却写着 completed"
            )

    def test_the_record_says_what_the_acceptance_says(self, tmp_path: Path):
        """记录里两个状态都要在——读者不用猜"""
        from kanban.trigger import trigger_task

        store = TaskRunStore(path=tmp_path / "runs.jsonl")
        trigger_task("wms_system_box", "sync_system_profile", {}, store)
        record = store.list_recent()[-1]
        assert record["status"] in (
            "completed", "rejected", "failed", "partially_completed",
        )
        assert record["acceptance_status"] in (
            "accepted", "rejected", "compensating_acceptance",
        )

    def test_the_judgement_detail_is_stored_not_just_returned(self, tmp_path: Path):
        """**判据明细要落盘**——不能只活在 HTTP 响应里

        以前明细只在 trigger 那一瞬间的响应体里，
        于是历史记录里查不到「哪条判据没过、实测值是多少」，
        看板的验收栏对旧记录是空的（又一处「算了却看不见」）。

        人要判「这次要不要放行」的时候，更需要它。
        """
        from kanban.trigger import trigger_task

        store = TaskRunStore(path=tmp_path / "runs.jsonl")
        trigger_task("wms_system_box", "sync_system_profile", {}, store)
        record = store.list_recent()[-1]
        detail = record["acceptance_detail"]
        assert detail, "判据明细没落盘——历史记录里查不到为什么判红"
        for row in detail:
            assert row.get("metric"), f"明细行没有判据名：{row}"
            assert row.get("outcome") in ("passed", "failed", "not_applicable")
