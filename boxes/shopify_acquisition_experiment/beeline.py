"""boxes.shopify_acquisition_experiment.beeline —— 获客实验盒的作业路线

op 链：
  1. `experiment_intake`    接住这一轮的诊断
  2. `experiment_work`      设计这一轮实验（**腿还没实现**）
  3. `experiment_packager`  装成方案 + 复测结果

**第 2 跳是空的，而且必须显眼**：它现在直接报错说「还没实现」。
一只永远返回空结果的盒子，读者会以为它在守着。
"""

from __future__ import annotations

from core.beeline_models import Idempotency
from core.scaffold import Component

#: 实验方案是**设计**：同一份诊断读两次可能给出不同方案。
IDEMPOTENT_BY_TASK = Idempotency(mode="optional")

#: **不许重放**：第 2 跳会真的改店里的页面。
#: 声明 idempotent 等于骗 runtime 说「重放安全」——
#: 重放一次就是同一份内容写两遍，第二次写的是「已经是新版的那个页面」。
FORBIDDEN_REPLAY = Idempotency(mode="forbidden")

COMPONENTS: list[Component] = [
    Component(
        op_id="experiment_intake",
        type="experiment_intake",
        input_from="external",
        input="schema_shopify_experiment_input",
        output="schema_shopify_experiment_plan",
        next=["experiment_work"],
        bee_type="experiment_intake_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
    Component(
        op_id="experiment_work",
        type="experiment_work",
        input_from="experiment_intake",
        input="schema_shopify_experiment_plan",
        output="schema_shopify_experiment_observation",
        next=["experiment_packager"],
        bee_type="experiment_work_handler",
        idempotency=FORBIDDEN_REPLAY,
    ),
    Component(
        op_id="experiment_packager",
        type="experiment_packager",
        input_from="experiment_work",
        input="schema_shopify_experiment_observation",
        output="schema_shopify_experiment_output",
        next=[],
        bee_type="experiment_packager_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
]
