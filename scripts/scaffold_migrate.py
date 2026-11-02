"""
scripts.scaffold_migrate - 从旧写法导出成新声明（迁移辅助）

**这是迁移期的脚手架，迁完最后一只盒子就删。**

它做两件事，各是一个方向的转换：

- `declaration_from_definition()`：旧的 `BeeBoxDefinition` → 新的 `BoxDeclaration`
- `components_from_beeline()`：旧的 `Beeline` → 新的 `Component` 列表

## 为什么它住在 scripts 而不是 core

因为它是**兼容层**，不是协议。

`core/` 是长期住的地方；这个模块的存在前提是「还有盒子用旧写法」。
迁完最后一只盒子，旧写法消失，这个模块也该消失——
「不留兼容层」这条决策对它同样适用。

## 它同时是协议的证明

迁移脚本本身跑不通，就说明新协议表达不了某只盒子。
所以除了「迁移」，它还有第二个用途：
**把全部已注册盒子导出一遍再装回去**，验协议能表达现有的一切，
且装回去的东西跟原来**一模一样**（见 `tests/test_scaffold.py`）。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import BoxDeclaration, Component


def declaration_from_definition(
    definition: BeeBoxDefinition,
    *,
    beeline: Beeline | None = None,
) -> BoxDeclaration:
    """旧的 BeeBoxDefinition → 新的 BoxDeclaration

    Args:
        definition: 旧声明
        beeline: 这只盒子对应的作业路线。用来挑出「哪个 task 绑着这条路线」
            ——一只盒子有多条路线时，装配器要按路线来配 task。

    Raises:
        ValueError: 找不到绑着这条路线的 task。
            **不猜**：猜一个 task 出来，装出来的盒子接的投料契约就是错的。
    """
    task = _task_for(definition, beeline)
    result = definition.result
    placement = definition.placement

    return BoxDeclaration(
        id=definition.id,
        version=definition.version,
        description=definition.description,
        display_name=placement.display_name,
        value_stream=placement.value_stream,
        role=placement.role,
        domain=placement.domain,
        feeds_into=list(placement.feeds_into),
        feedback_into=list(placement.feedback_into),
        task_type=task.type,
        task_schema=task.task_schema,
        task_trigger=task.trigger,
        accepts_free_text=task.accepts_free_text,
        subject_ref_field=task.subject_ref_field,
        subject_ref_from_result=task.subject_ref_from_result,
        subject_ref_subfield=task.subject_ref_subfield,
        subject_kind=task.subject_kind,
        result_type=result.type,
        result_schema=result.result_schema,
        acceptance=list(result.acceptance),
        exceptions=list(result.exceptions),
        maturity=result.maturity,
        artifact_view=result.view,
        empty_input_metric=result.empty_input_metric,
        beeline_id=task.beeline_id,
        beeline_version=task.beeline_version,
        beeline_description=beeline.description if beeline is not None else "",
        queen=definition.queen,
        metrics=definition.metrics,
    )


def components_from_beeline(beeline: Beeline) -> list[Component]:
    """旧的 Beeline → 新的 Component 列表

    **实现不在这份转换里**——handler 由盒子的 handler 表提供。
    """
    return [
        Component(
            op_id=op.op_id,
            type=op.type,
            input_from=op.input_from,
            input=op.input,
            output=op.output,
            next=[nxt.op_id for nxt in op.next],
            bee_type=op.bee.type if op.bee is not None else "",
            external_system=op.external_system,
            idempotency=op.idempotency,
            leaves_artifact=op.leaves_artifact,
        )
        for op in beeline.operations
    ]


def _task_for(
    definition: BeeBoxDefinition,
    beeline: Beeline | None,
):
    """挑出绑着这条作业路线的 task"""
    if beeline is None:
        if len(definition.task) != 1:
            raise ValueError(
                f"{definition.id} 有 {len(definition.task)} 个 task，"
                f"得说清楚用哪一个"
            )
        return definition.task[0]

    for task in definition.task:
        if task.beeline_id == beeline.id:
            return task

    raise ValueError(
        f"{definition.id} 里没有 task 绑着 beeline {beeline.id!r}——"
        f"它声明的 task 是：{[t.type for t in definition.task]}"
    )
