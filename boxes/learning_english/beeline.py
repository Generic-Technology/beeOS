"""boxes.learning_english.beeline - 英语盒 的作业路线

op 链（v1）：
  1. `english_intake`      取投料 → 这一跳要做什么
  2. `english_work`        真干活 → 真的观测到的东西
  3. `english_packager`    装配产出

**beeline 跟盒子是 1:1 且不可分割的**：它引用的 schema id 全部来自本盒的
`schemas.py`，每个 op 的 handler 都在本盒的 `runner.py` 里。
所以它住在盒子里面，而不是仓库顶层一个单独的目录——
顶层目录会让人以为存在一种「可复用的跨盒作业路线」，而实际上一条都没有。
"""

from __future__ import annotations

from core.beeline_models import (
    Bee,
    Beeline,
    Idempotency,
    NextRef,
    Operation,
)


# 同一任务重复执行必须产出同一份结果
IDEMPOTENT_BY_TASK = Idempotency(mode="required", key="task_id")


LEARNING_ENGLISH_BEELINE = Beeline(
    id="beeline_learning_english_v1",
    version=1,
    description=(
        "英语盒 履约作业路线：english_intake → english_work → english_packager"
    ),

    operations=[
        Operation(
            op_id="english_intake",
            type="english_intake",
            input_from="external",
            input="schema_learning_english_input",
            output="schema_learning_english_plan",
            next=[NextRef(op_id="english_work")],
            bee=Bee(type="english_intake_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="english_work",
            type="english_work",
            input_from="english_intake",
            input="schema_learning_english_plan",
            output="schema_learning_english_observation",
            next=[NextRef(op_id="english_packager")],
            bee=Bee(type="english_work_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="english_packager",
            type="english_packager",
            input_from="english_work",
            input="schema_learning_english_observation",
            output="schema_learning_english_output",
            next=[],
            bee=Bee(type="english_packager_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),
    ],
)


def get_beeline() -> Beeline:
    return LEARNING_ENGLISH_BEELINE
