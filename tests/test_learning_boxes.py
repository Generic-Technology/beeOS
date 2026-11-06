"""tests.test_learning_boxes - 车宥安的学习盒子：契约不许漂，坏投料必须红

这组守卫守三件事：

1. **形状不许漂**——三只学科盒的产出形状，必须还对得上成长记录盒
   声明的那份（**消费者说了算**）。两边各声明一次 → 两份事实 →
   必须有一条守卫比对，否则漂了没有任何东西会报错。
2. **坏投料必须红**——得分超过满分、出处类型不认识、缺出处，
   这些都该**当场拒**并说清为什么，而不是收一条「看起来正常」的记录。
3. **档案里没有裸数字**——每条记录都带着出处、来源类型和事件号。
   缺了就是「以后没法查这笔到底是看到的还是听说的」。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from kanban.trigger import (
    BOX_REGISTRY,
    fulfillment_scope_wiring,
    trigger_task,
)
from runtime.store import TaskRunStore

SUBJECT_BOXES = [
    "learning_math_box",
    "learning_chinese_box",
    "learning_english_box",
]
GROWTH_BOX = "learning_growth_box"

# 成长记录盒**接受**的那份形状（消费者自己声明的）
CONSUMED_SCHEMA = "schema_learning_subject_result"


def _definition(box_id: str):
    return BOX_REGISTRY[box_id][0]()


def _schema(box_id: str, schema_id: str):
    return _definition(box_id).get_schema(schema_id)


def _fields(schema) -> dict:
    return {f.name: f for f in schema.fields}


# ------------------------------------------------------------------
# 1. 形状不许漂
# ------------------------------------------------------------------
class TestTheShapesStayCompatible:
    """三只学科盒的产出 ≡ 成长记录盒收的那份

    **为什么必须有这条守卫**：形状归消费者所有，所以成长记录盒声明一份
    「我收什么」，每只学科盒也声明一份「我吐什么」——
    **两份事实**，中间只隔着一条守卫。不隔着，它就漂；
    漂了之后的表现是「交接通道在某一次悄悄拒了」，
    而那要等到某天真的投料才发现。
    """

    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_the_subject_output_carries_every_field_the_archive_accepts(self, box_id):
        consumed = _fields(_schema(GROWTH_BOX, CONSUMED_SCHEMA))
        produced = _fields(_schema(box_id, _definition(box_id).result.result_schema))

        missing = sorted(set(consumed) - set(produced))
        assert not missing, (
            f"{box_id} 的产出缺了档案要收的字段：{missing}——"
            "这条记录到了档案那边接不住"
        )

    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_the_shared_fields_have_the_same_type_on_both_sides(self, box_id):
        consumed = _fields(_schema(GROWTH_BOX, CONSUMED_SCHEMA))
        produced = _fields(_schema(box_id, _definition(box_id).result.result_schema))

        mismatched = [
            f"{name}: 档案收 {consumed[name].type} / "
            f"{box_id} 吐 {produced[name].type}"
            for name in sorted(set(consumed) & set(produced))
            if consumed[name].type != produced[name].type
        ]
        assert not mismatched, (
            "同一份形状，两边的类型不一样：\n  " + "\n  ".join(mismatched)
        )

    def test_the_archive_only_accepts_the_two_known_source_kinds(self):
        """出处类型**只有两个**合法取值

        `observed`（真的看到了）/ `reported`（家里口述的）。
        第三种（空 / 自造词）不进档案——不认得的来源类型，
        半年后没人说得清这条记录是依据什么。
        """
        field = _fields(_schema(GROWTH_BOX, CONSUMED_SCHEMA))["source_kind"]
        assert field.values == ["observed", "reported"], (
            f"出处类型的合法取值变成了 {field.values}——"
            "档案只认「看到的」和「听说的」两种"
        )

    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_the_subject_declares_the_same_source_kinds(self, box_id):
        field = _fields(
            _schema(box_id, f"schema_{box_id[:-4]}_input"),
        )["source_kind"]
        assert field.values == ["observed", "reported"], (
            f"{box_id} 的投料口允许的出处类型是 {field.values}，"
            "跟档案那一份对不上"
        )


# ------------------------------------------------------------------
# 2. 学习协作边界是放射形状
# ------------------------------------------------------------------
class TestTheLearningDomainIsAStar:
    def test_the_archive_is_the_only_sink(self):
        """成长记录盒是这个协作边界**唯一**的汇点"""
        m = fulfillment_scope_wiring("Learning")
        assert m.center == GROWTH_BOX, f"汇点算成了 {m.center}"
        assert m.shape == "star", (
            f"学习协作边界的形状是 {m.shape}——三科之间没有交接，它该是放射"
        )
        assert m.handoffs == (), "放射形状里不该有交接边"

    def test_every_subject_hands_off_to_the_archive(self):
        """三科都把产出交给成长记录盒——**从协作边界声明读**

        接线住在 `fulfillment_scopes/<边界>/`，不在盒子自己身上了。
        """
        from fulfillment_scopes import box_wiring

        m = fulfillment_scope_wiring("Learning")
        for box_id in SUBJECT_BOXES:
            handoffs = box_wiring(box_id)["handoffs"]
            assert list(handoffs) == [GROWTH_BOX], (
                f"{box_id} 的下游是 {list(handoffs)}，应该是 {GROWTH_BOX}"
            )
            assert box_id in m.feeds_center


# ------------------------------------------------------------------
# 3. 坏投料必须红
# ------------------------------------------------------------------
class TestBadInputGoesRed:
    """坏投料**当场拒**并说清为什么

    不许「收一条看起来正常的记录」——档案里混进一条错数字，
    比没有档案更坏：它会让整份档案的可信度塌掉。
    """

    @pytest.fixture()
    def store(self, tmp_path: pathlib.Path) -> TaskRunStore:
        return TaskRunStore(tmp_path / "runs.jsonl")

    def _payload(self, **overrides) -> dict:
        base = {
            "activity": "数学作业第 12 页",
            "observed_at": "2026-11-01T16:30",
            "evidence": "作业本 P12",
            "source_kind": "observed",
            "score": 92,
            "total": 100,
            "minutes": 25,
            "errors": [],
            "notes": "",
        }
        base.update(overrides)
        return base

    def test_a_score_above_the_total_is_refused(self, store):
        run = trigger_task(
            "learning_math_box", "run_math",
            self._payload(score=120), store,
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "failed", "得分超过满分居然过了"
        assert "超过" in str(record.get("error_reason")), (
            f"报错要说清哪里不对：{record.get('error_reason')}"
        )

    def test_an_unknown_source_kind_is_refused(self, store):
        """不认得的出处类型 → **在投料口就被拒**

        取值表在 schema 上，所以拒得更早（还没进履约）。
        腿里那道检查是第二道：万一投料绕过契约进来，它仍然会拒。
        """
        from kanban.trigger import TriggerError

        with pytest.raises(TriggerError) as exc:
            trigger_task(
                "learning_math_box", "run_math",
                self._payload(source_kind="guessed"), store,
            )
        assert "source_kind" in str(exc.value), (
            f"报错要指名是出处类型不对：{exc.value}"
        )

    def test_a_missing_evidence_is_refused(self, store):
        """没有出处的记录不进档案"""
        run = trigger_task(
            "learning_math_box", "run_math",
            self._payload(evidence=""), store,
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "failed", "没有出处的记录居然进了档案"
        assert "evidence" in str(record.get("error_reason"))

    def test_the_archive_refuses_a_record_without_a_number(self, store):
        """档案收到一条缺字段的记录 → 拒，不补空

        补一条空记录会让人以为那天学过。
        """
        from runtime.handoff import HandoffError, assemble_handoff

        definition = _definition(GROWTH_BOX)
        task = definition.task[0]
        task_schema = definition.get_schema(task.task_schema)

        broken = {
            "subject": "math",
            "activity": "数学作业第 12 页",
            "observed_at": "2026-11-01T16:30",
            "mastery": 0.92,
            "evidence": "作业本 P12",
            "source_kind": "observed",
            "detail": "得分 92/100",
            # **没有 record_id** —— 这条记录认不出自己是哪次学习
        }
        with pytest.raises(HandoffError) as exc:
            assemble_handoff(
                {},
                {"subject_result": "run-1"},
                task_schema=task_schema,
                schemas=definition.schemas,
                get_run=lambda run_id: {
                    "status": "completed",
                    "artifact": broken,
                },
            )
        assert "record_id" in str(exc.value), (
            f"报错要指名缺的是哪个字段：{exc.value}"
        )


# ------------------------------------------------------------------
# 4. 档案里没有裸数字
# ------------------------------------------------------------------
class TestTheArchiveKeepsItsProvenance:
    @pytest.fixture()
    def store(self, tmp_path: pathlib.Path) -> TaskRunStore:
        return TaskRunStore(tmp_path / "runs.jsonl")

    def test_an_archived_entry_carries_where_it_came_from(self, store):
        run = trigger_task(
            "learning_math_box", "run_math",
            {
                "activity": "数学作业第 12 页",
                "observed_at": "2026-11-01T16:30",
                "evidence": "作业本 P12",
                "source_kind": "observed",
                "score": 92,
                "total": 100,
                "minutes": 25,
                "errors": [],
                "notes": "",
            },
            store,
        )
        archived = trigger_task(
            GROWTH_BOX, "run_growth", {},
            store,
            handoff={"subject_result": run["task_run_id"]},
        )
        assert archived["acceptance_status"] == "accepted"

        artifact = store.get_run(archived["task_run_id"])["artifact"]
        assert artifact["evidence"] == "作业本 P12", "归档条目丢了出处"
        assert artifact["source_kind"] == "observed", "归档条目丢了来源类型"
        assert artifact["record_id"], "归档条目丢了事件号"
        assert artifact["observed_at"] == "2026-11-01T16:30", (
            "时间线按学习发生的时间排——补录不该改掉它"
        )

    def test_the_entry_keeps_the_upstream_number(self, store):
        """归档条目的号 = 上游给的号（**同一个事实一个号**）"""
        run = trigger_task(
            "learning_math_box", "run_math",
            {
                "activity": "数学作业第 12 页",
                "observed_at": "2026-11-01T16:30",
                "evidence": "作业本 P12",
                "source_kind": "observed",
                "score": 92,
                "total": 100,
                "minutes": 25,
                "errors": [],
                "notes": "",
            },
            store,
        )
        upstream = store.get_run(run["task_run_id"])["artifact"]
        archived = trigger_task(
            GROWTH_BOX, "run_growth", {},
            store,
            handoff={"subject_result": run["task_run_id"]},
        )
        artifact = store.get_run(archived["task_run_id"])["artifact"]
        assert artifact["record_id"] == upstream["record_id"], (
            "归档另起了一个号 —— 同一件事会变成两件事"
        )
