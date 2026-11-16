"""boxes.shopify_archive.schemas —— 经营档案盒的业务 schemas

**它是这个协作边界的唯一汇点**：三份产出（快照 / 诊断 / 实验）
都投给它，它沉淀成一**轮经营记录**。

## 形状归消费者所有

档案盒**自己声明**它收哪三种形状。
交接通道按**档案盒这份声明**校验上游产出。

**那三份形状来自边界**（`fulfillment_scopes/shopify/shapes.py`）——
边界里只有一份事实：生产者和消费者用的是同一份，
所以「抄错字段」这种漂移不可能发生。

## 三次投递 → 一条记录

档案盒跑三次（快照、诊断、实验各一次）。
**一个通道只能接一份产出**——所以三次是分开跑的，
归到同一条记录靠 `round_id`（`subject_ref_field`）。

## 为什么档案盒要自己复核

上游说「我过了」不算数。这里**重新算一遍**
「每条指标有没有状态、observed 有没有出处、unverifiable 有没有原因」——
这正是「盒子说自己过了，别人不回头核对」那族病的解法。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

from fulfillment_scopes.shopify.shapes import (
    DIAGNOSIS_OUTPUT_SCHEMA,
    EXPERIMENT_OUTPUT_SCHEMA,
    SNAPSHOT_OUTPUT_SCHEMA,
)

ARCHIVE_INPUT_SCHEMA = SchemaDef(
    id="schema_shopify_archive_input",
    description=(
        "投料：一份产出 + 轮次号。\n\n"
        "**三个通道一次只能填一个**——一个通道只能接一份产出。"
    ),
    fields=[
        FieldDef(name="round_id", type="string", required=True,
                 description="经营轮次号——三次投递靠它归成一条记录"),
        FieldDef(name="snapshot_ref", type="ref:schema_shopify_snapshot_output",
                 required=False, description="这一份是**快照**产出"),
        FieldDef(name="diagnosis_ref", type="ref:schema_shopify_diagnosis_output",
                 required=False, description="这一份是**诊断**产出"),
        FieldDef(name="experiment_ref", type="ref:schema_shopify_experiment_output",
                 required=False, description="这一份是**实验**产出"),
    ],
)

ARCHIVE_PART_SCHEMA = SchemaDef(
    id="schema_shopify_archive_part",
    description="这一跳接住的那一段（轮次号 + 这一段是什么 + 它带着的东西）",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="record_kind", type="enum", required=True,
                 values=["snapshot", "diagnosis", "experiment"]),
        # 三份产出**分字段**带（不是塞进一个万能 object）：
        # 形状由 `ref:` 指到本盒声明的那三份形状，校验走的是同一套机制。
        FieldDef(name="snapshot_payload", type="ref:schema_shopify_snapshot_output",
                 required=False, description="这一段是快照时带着的产出"),
        FieldDef(name="diagnosis_payload", type="ref:schema_shopify_diagnosis_output",
                 required=False, description="这一段是诊断时带着的产出"),
        FieldDef(name="experiment_payload", type="ref:schema_shopify_experiment_output",
                 required=False, description="这一段是实验时带着的产出"),
    ],
)

ARCHIVE_VERDICT_SCHEMA = SchemaDef(
    id="schema_shopify_archive_verdict",
    description="复核结论：这一段标得实不实",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="record_kind", type="enum", required=True,
                 values=["snapshot", "diagnosis", "experiment"]),
        FieldDef(name="honest", type="boolean", required=True,
                 description="复核结论——**不是上游说的，是这里重算的**"),
        FieldDef(name="source_present", type="boolean", required=True),
        FieldDef(name="unknowns", type="array", required=True,
                 items=FieldDef(name="item", type="string", required=True)),
        FieldDef(name="summary", type="string", required=True),
    ],
)

ARCHIVE_OUTPUT_SCHEMA = SchemaDef(
    id="schema_shopify_archive_output",
    description="产出：这一轮经营的一条档案记录",
    fields=[
        FieldDef(name="round_id", type="string", required=True,
                 description="这一轮的号——三次投递共享它，归档里是同一条记录"),
        FieldDef(name="observed_at", type="string", required=True),
        FieldDef(name="record_kind", type="enum", required=True,
                 values=["snapshot", "diagnosis", "experiment"],
                 description="这一条记的是哪一段"),
        FieldDef(name="archived_parts", type="array", required=True,
                 items=FieldDef(name="part", type="string", required=True),
                 description=(
                     "这一条覆盖的段——每次投递记一段，"
                     "三段共享同一个 round_id，归档里合成同一条经营记录"
                 )),
        FieldDef(name="summary", type="string", required=True,
                 description="人话一句：这一段记的是什么"),
        FieldDef(name="metric_status_honest", type="boolean", required=True,
                 description=(
                     "**档案盒自己复核的结论**——不是上游说「我过了」，"
                     "是这里重新算了一遍"
                 )),
        FieldDef(name="unknowns", type="array", required=True,
                 items=FieldDef(name="item", type="string", required=True),
                 description="这一段里说不清的地方（上游声明的 + 复核时发现的）"),
        FieldDef(name="source_present", type="boolean", required=True,
                 description="这一段带出处"),
        FieldDef(name="subject_present", type="boolean", required=True,
                 description="这一轮带着自己的号"),
    ],
)

ALL_SCHEMAS = [
    SNAPSHOT_OUTPUT_SCHEMA,
    DIAGNOSIS_OUTPUT_SCHEMA,
    EXPERIMENT_OUTPUT_SCHEMA,
    ARCHIVE_INPUT_SCHEMA,
    ARCHIVE_PART_SCHEMA,
    ARCHIVE_VERDICT_SCHEMA,
    ARCHIVE_OUTPUT_SCHEMA,
]

__all__ = [
    "ALL_SCHEMAS",
    "SNAPSHOT_OUTPUT_SCHEMA",
    "DIAGNOSIS_OUTPUT_SCHEMA",
    "EXPERIMENT_OUTPUT_SCHEMA",
    "ARCHIVE_INPUT_SCHEMA",
    "ARCHIVE_PART_SCHEMA",
    "ARCHIVE_VERDICT_SCHEMA",
    "ARCHIVE_OUTPUT_SCHEMA",
]
