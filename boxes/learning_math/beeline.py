"""
boxes.learning_math.beeline - 数学盒 的作业路线（组件链）

op 链（v2）：
  1. `math_read_photo` 看照片 → 把图上印着的事实抄出来（没投照片就跳过）
  2. `math_intake`      取投料 → 这一跳要做什么（手工填的优先，照片补空）
  3. `math_work`        真干活 → 真的观测到的东西
  4. `math_packager`    装配产出

**为什么加了这一跳**：v1 只有手工投料，家长得把分数一个个数进表单。
2026-11-01 起投料口能拍作业照片——但**看图是模型那一腿的事**，
它得是独立的一跳：只有这样，看图失败、照片读不出分数、
人工补字段这三件事在履约轨迹上才分得开（op_trace 里看得见停在哪一跳）。

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
        op_id="math_read_photo",
        type="math_read_photo",
        input_from="external",
        input="schema_learning_math_input",
        output="schema_learning_math_photo_read",
        next=["math_intake"],
        bee_type="math_read_photo_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),

    Component(
        op_id="math_intake",
        type="math_intake",
        input_from="math_read_photo",
        input="schema_learning_math_photo_read",
        output="schema_learning_math_plan",
        next=["math_work"],
        bee_type="math_intake_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),

    Component(
        op_id="math_work",
        type="math_work",
        input_from="math_intake",
        input="schema_learning_math_plan",
        output="schema_learning_math_observation",
        next=["math_packager"],
        bee_type="math_work_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),

    Component(
        op_id="math_packager",
        type="math_packager",
        input_from="math_work",
        input="schema_learning_math_observation",
        output="schema_learning_math_output",
        next=[],
        bee_type="math_packager_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
]
