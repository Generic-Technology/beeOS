"""boxes.shopify_traffic_diagnosis.beeline —— 获客诊断盒的作业路线

op 链：
  1. `diagnosis_intake`    接住这一轮的快照
  2. `diagnosis_work`      逐段核对四段漏斗（**腿还没实现**）
  3. `diagnosis_packager`  装成一个假设 + 证据 + 未知项

**第 2 跳是空的，而且必须显眼**：它现在直接报错说「还没实现」，
不是悄悄返回空——一只永远返回空结果的盒子，读者会以为它在守着。
"""

from __future__ import annotations

from core.beeline_models import Idempotency
from core.scaffold import Component

#: 诊断是**判断**：同一份快照读两次可能给出不同说法（尤其是走模型时）。
#: 声明幂等等于骗 runtime 说「重放安全」。
IDEMPOTENT_BY_TASK = Idempotency(mode="optional")

COMPONENTS: list[Component] = [
    Component(
        op_id="diagnosis_intake",
        type="diagnosis_intake",
        input_from="external",
        input="schema_shopify_diagnosis_input",
        output="schema_shopify_diagnosis_plan",
        next=["diagnosis_work"],
        bee_type="diagnosis_intake_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
    Component(
        op_id="diagnosis_work",
        type="diagnosis_work",
        input_from="diagnosis_intake",
        input="schema_shopify_diagnosis_plan",
        output="schema_shopify_diagnosis_observation",
        next=["diagnosis_packager"],
        bee_type="diagnosis_work_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
    Component(
        op_id="diagnosis_packager",
        type="diagnosis_packager",
        input_from="diagnosis_work",
        input="schema_shopify_diagnosis_observation",
        output="schema_shopify_diagnosis_output",
        next=[],
        bee_type="diagnosis_packager_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
]
