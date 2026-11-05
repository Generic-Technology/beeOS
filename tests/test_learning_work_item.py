"""
tests.test_learning_work_item - 学习域的履约得归得到「一件任务」

**这个 bug 是怎么被发现的**（2026-11-05）：
看板上选了 Learning 域，数学盒明明「RUNS 2」，左边的任务列表却是空的。

查下去发现：那两次运行��� `subject_ref = None`——**没有工作项号**。
而任务列表的定义就是「按工作项归拢」：
没有 subject_ref 的记录是「某只盒子跑过什么」，不是「哪件任务」。

**根因不是机制没实现，是盒子没声明**：
数学盒的产出里明明有 `record_id`（required=True，
「从内容推导，同一件事重记还是同一个号」），
可它**没声明 `subject_ref_from_result`**——
于是运行时取不到，这条记录在归档里归不到任何工作单元。

WMS 的需求盒早就是对的（`subject_ref_from_result="set_id"` +
`subject_kind="requirement"`）。学习域是**漏了**。

这组守卫盯三件事：
1. 学科盒跑完，记录**真的带上了它自己造出来的那个号**；
2. 学科盒 + 成长记录盒 = **一件任务**（同一个号，不是一件事实记两遍）；
3. 声明一个产出里根本没有的键 → **装配期就报错**（不等到运行时废掉一次履约）。
"""

from __future__ import annotations

import pytest

from kanban.trigger import BOX_REGISTRY, trigger_task
from runtime.store import TaskRunStore


SUBJECT_BOXES = [
    "learning_math_box",
    "learning_chinese_box",
    "learning_english_box",
]
GROWTH_BOX = "learning_growth_box"

#: 学科盒的投料（**不带照片**：手填的事实就够，看图那一跳跳不跳）
#:
#: 每只盒子自己的得分/满分字段名不一样（数学 score/total、
#: 语文 reading_score/reading_total、英语 dictation_score/dictation_total）——
#: 那是各盒 schema 自己的形状，不抄一份到测试里当常量。
BASE_PAYLOAD = {
    "activity": "练习第 12 页",
    "observed_at": "2026-11-01T16:30",
    "evidence": "作业本 P12",
    "source_kind": "observed",
    "errors": [],
    "notes": "",
}


def _payload_for(box_id: str) -> dict:
    """按这只盒子自己的投料契约拼一份能跑的投料"""
    schema = BOX_REGISTRY[box_id][0]().get_schema(
        BOX_REGISTRY[box_id][0]().task[0].task_schema
    )
    payload = dict(BASE_PAYLOAD)
    for field in schema.fields:
        if field.name in payload:
            continue
        if field.name == "score" or field.name.endswith("_score"):
            payload[field.name] = 92
        elif field.name == "total" or field.name.endswith("_total"):
            payload[field.name] = 100
        elif field.name == "minutes":
            payload[field.name] = 25
    return payload


@pytest.fixture
def store(tmp_path):
    return TaskRunStore(path=tmp_path / "runs.jsonl")


def _run_subject_box(box_id: str, store: TaskRunStore) -> dict:
    task_type = BOX_REGISTRY[box_id][0]().task[0].type
    run = trigger_task(box_id, task_type, _payload_for(box_id), store)
    record = store.get_run(run["task_run_id"])
    assert record["status"] == "completed", record.get("error_reason")
    return record


class TestTheRunClaimsItsOwnNumber:
    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_the_record_carries_the_id_the_box_produced(self, box_id, store):
        """产出里的 record_id 就是这次履约的工作项号

        没有它：这条记录在归档里归不到任何工作单元——
        看板上就只剩「这只盒子跑过 N 次」，读者看不到「哪件任务」。
        """
        record = _run_subject_box(box_id, store)
        produced = record["artifact"]["record_id"]
        assert produced, f"{box_id} 的产出里没有 record_id"
        assert record["subject_ref"] == produced, (
            f"{box_id} 这次履约没有认领自己造出来的号："
            f"subject_ref={record['subject_ref']!r}，产出里的号是 {produced!r}"
        )

    def test_the_subject_box_and_the_archive_are_one_task(self, store):
        """学科盒 + 成长记录盒 = 一件任务（**同一个号**）

        成长记录盒沿用上游的号（同一个事实一个号），
        两边认领同一个 record_id → 归档里是一件牵过 2 只盒子的任务，
        不是两件不相干的事。
        """
        from kanban.web import _collect_tasks

        record = _run_subject_box("learning_math_box", store)
        archived = trigger_task(
            GROWTH_BOX, "run_growth", {}, store,
            handoff={"subject_result": record["task_run_id"]},
        )
        assert archived["acceptance_status"] == "accepted"

        tasks = _collect_tasks(store._cache)
        assert len(tasks) == 1, f"应当是一件任务，实际 {len(tasks)} 件：{tasks}"
        assert {b["box_id"] for b in tasks[0]["boxes"]} == {
            "learning_math_box", GROWTH_BOX,
        }, "这件任务没把两只盒子都带上"


class TestAMisdeclaredIdentifierIsRefusedEarly:
    def test_the_assembler_refuses_a_key_the_result_cannot_produce(self):
        """声明一个产出里根本没有的键 → **装配期就报错**

        不拦的话：盒子装得出来，跑一次才废一次——
        运行时取不到那个键，这次履约被当场拒绝（契约被破了）。
        """
        import pytest as _pytest

        from core.models import AcceptanceRule, ExceptionRule, FieldDef, SchemaDef
        from core.scaffold import (
            AssemblyError,
            BoxDeclaration,
            Component,
            assemble,
        )

        declaration = BoxDeclaration(
            id="demo_box",
            version=1,
            description="合成盒：只用来验「认领一个拿不到的号」",
            task_type="do_it",
            beeline_id="beeline_demo",
            task_schema="schema_demo_in",
            result_type="demo_result",
            result_schema="schema_demo_out",
            # **产出里根本没有这个键**
            subject_ref_from_result="no_such_key",
            subject_kind="demo",
            acceptance=[AcceptanceRule(metric="count", op="gte", value=1)],
            exceptions=[ExceptionRule(condition="count > 0", action="escalate")],
        )
        schemas = [
            SchemaDef(
                id="schema_demo_in",
                fields=[FieldDef(name="topic", type="string")],
            ),
            SchemaDef(
                id="schema_demo_out",
                fields=[FieldDef(name="count", type="integer")],
            ),
        ]
        component = Component(
            op_id="op1", type="op1",
            input_from="external", input="schema_demo_in", output="schema_demo_out",
            handler=lambda _d: {"count": 1},
        )
        with _pytest.raises(AssemblyError) as exc:
            assemble(declaration, [component], schemas=schemas)
        message = str(exc.value)
        assert "no_such_key" in message
        assert "产出 schema" in message, (
            f"报错要说清是产出里没这个键：{message}"
        )
