"""
boxes.learning_growth.beeline - 成长记录盒 的作业路线（组件链）

op 链（v1）：
  1. `growth_intake`      取投料 → 这一跳要做什么
  2. `growth_work`        真干活 → 真的观测到的东西
  3. `growth_packager`    装配产出

**为什么是「组件链」而不是一份 `Beeline(...)`**：
盒子不手写 `Operation` / `NextRef` / `Bee` 那套样板——
它只声明每一跳的**形状**（吃什么、吐什么、数据从哪来、下一步去哪），
由 `core/scaffold.py` 的装配器把它组装成作业路线**并当场校验**。

**beeline 跟盒子是 1:1 且不可分割的**：它引用的 schema id 全部来自本盒的
`schemas.py`，每个 op 的 handler 都在本盒的 `runner.py` 里。
所以它住在盒子里面，而不是仓库顶层一个单独的目录——
顶层目录会让人以为存在一种「可复用的跨盒作业路线」，而实际上一条都没有。
"""

from __future__ import annotations

from core.beeline_models import Idempotency
from core.scaffold import Component


# 同一任务重复执行必须产出同一份结果
IDEMPOTENT_BY_TASK = Idempotency(mode="required", key="task_id")


COMPONENTS: list[Component] = [
    Component(
        op_id="growth_intake",
        type="growth_intake",
        input_from="external",
        input="schema_learning_growth_input",
        output="schema_learning_growth_plan",
        next=['growth_work'],
        bee_type="growth_intake_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),

    Component(
        op_id="growth_work",
        type="growth_work",
        input_from="growth_intake",
        input="schema_learning_growth_plan",
        output="schema_learning_growth_observation",
        next=['growth_packager'],
        bee_type="growth_work_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),

    Component(
        op_id="growth_packager",
        type="growth_packager",
        input_from="growth_work",
        input="schema_learning_growth_observation",
        output="schema_learning_growth_output",
        next=[],
        bee_type="growth_packager_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),

]
