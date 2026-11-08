"""boxes.learning_growth.schemas - 成长记录盒的业务 schemas

**这一盒是学习域的汇点**：数学 / 语文 / 英语三只学科盒的产出
最终都落进这里。

## 它吃什么

`subject_result` 是一条 `ref:` 字段 —— 交接通道按**本盒自己声明的**
那份形状校验上游产出，然后接进来。
**形状归消费者所有**：这份 `schema_learning_subject_result`
是本盒说了算的「我收什么」。

三只学科盒各自也声明了一份**同构**的产出形状（它们的 id 不一样）。
两份必须对得上——对不上就是「学科盒吐出来的，本盒接不住」，
所以有守卫盯着：`tests/test_learning_boxes.py`。

## 它吐出什么

**一条**记录。成长档案是**时间线**：每次履约追加一条，
档案 = 这只盒子的历次履约产出，按学习发生的时间排。

**为什么一次履约只追加一条**：一条履约 = 一条学习记录。
把三科的记录并成一条，档案里就分不清「数学这次」和「语文那次」
各自是什么——那不是档案，是流水账。
"""


from __future__ import annotations

from core.models import FieldDef, SchemaDef


# **出处类型的合法取值只有两个**：observed（真的看到了）/ reported（家里口述的）。
# 第三种（空 / 自造词）不进档案。
SOURCE_KINDS = ["observed", "reported"]


LEARNING_GROWTH_SUBJECT_RESULT_SCHEMA = SchemaDef(
    id="schema_learning_subject_result",
    description="一条学习记录（本盒**接受**的那份形状——形状归消费者所有）",
    fields=[
        FieldDef(
            name="record_id",
            type="string",
            required=True,
            description="这次学习记录的唯一号（从内容推导，同一件事重记也是同一个号）",
        ),
        FieldDef(
            name="subject",
            type="string",
            required=True,
            description="哪一科：math / chinese / english",
        ),
        FieldDef(
            name="activity",
            type="string",
            required=True,
            description="这次做了什么",
        ),
        FieldDef(
            name="observed_at",
            type="string",
            required=True,
            description="学习发生的时间（ISO）",
        ),
        FieldDef(
            name="mastery",
            type="number",
            required=True,
            description="掌握度 0..1",
        ),
        FieldDef(
            name="evidence",
            type="string",
            required=True,
            description="出处：这次是基于什么记的（作业本 P12 / 单元测卷 / 口述）",
        ),
        FieldDef(
            name="source_kind", type="enum", required=True,
            values=['observed', 'reported'],
            description="observed = 这次真的看到了；reported = 家里口述的",
        ),
        FieldDef(
            name="detail",
            type="string",
            required=True,
            description="这一科的细节（人话一句）",
        ),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="备注",
        ),
    ],
)

LEARNING_GROWTH_INPUT_SCHEMA = SchemaDef(
    id="schema_learning_growth_input",
    description="投料：一条学习记录（由交接通道接进来）",
    fields=[
        FieldDef(
            name="subject_result",
            type="ref:schema_learning_subject_result",
            required=True,
            description="一条学习记录——交接通道按本盒声明的形状校验后接进来",
        ),
    ],
)

LEARNING_GROWTH_PLAN_SCHEMA = SchemaDef(
    id="schema_learning_growth_plan",
    description="计划：这一跳要做什么",
    fields=[
        FieldDef(
            name="plan",
            type="string",
            required=False,
            description="这一跳打算做什么（人话一句，够定位就行）",
        ),
        FieldDef(
            name="subject_result",
            type="ref:schema_learning_subject_result",
            required=True,
            description="要归档的那一条",
        ),
    ],
)

LEARNING_GROWTH_OBSERVATION_SCHEMA = SchemaDef(
    id="schema_learning_growth_observation",
    description="观测：这一跳真的看到了什么",
    fields=[
        FieldDef(
            name="observed",
            type="string",
            required=False,
            description="这一跳**真的**观测到的东西（没观测到就留空，别填推测）",
        ),
        FieldDef(
            name="detail",
            type="string",
            required=False,
            description="这一条记录里的人话细节",
        ),
        FieldDef(
            name="subject_result",
            type="ref:schema_learning_subject_result",
            required=True,
            description="要归档的那一条",
        ),
    ],
)

LEARNING_GROWTH_OUTPUT_SCHEMA = SchemaDef(
    id="schema_learning_growth_output",
    description="产出：追加到成长档案的一条记录",
    fields=[
        FieldDef(
            name="record_id",
            type="string",
            required=True,
            description="这次学习记录的唯一号（沿用学科盒给的号——**同一个事实一个号**）",
        ),
        FieldDef(
            name="subject",
            type="string",
            required=True,
            description="哪一科：math / chinese / english",
        ),
        FieldDef(
            name="activity",
            type="string",
            required=True,
            description="这次做了什么",
        ),
        FieldDef(
            name="observed_at",
            type="string",
            required=True,
            description="学习发生的时间（ISO）——**时间线按它排，不按归档时间**",
        ),
        FieldDef(
            name="mastery",
            type="number",
            required=True,
            description="掌握度 0..1（从学科盒那边接过来的原值）",
        ),
        FieldDef(
            name="evidence",
            type="string",
            required=True,
            description="出处：这次是基于什么记的",
        ),
        FieldDef(
            name="source_kind",
            type="string",
            required=True,
            description="observed = 这次真的看到了；reported = 家里口述的",
        ),
        FieldDef(
            name="detail",
            type="string",
            required=True,
            description="这一科的细节（人话一句）",
        ),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="备注",
        ),
        FieldDef(
            name="source_record_present",
            type="boolean",
            required=True,
            description="接过来的那条**带着自己的号**——出处链没断",
        ),
    ],
)

ALL_SCHEMAS = [
    LEARNING_GROWTH_SUBJECT_RESULT_SCHEMA,
    LEARNING_GROWTH_INPUT_SCHEMA,
    LEARNING_GROWTH_PLAN_SCHEMA,
    LEARNING_GROWTH_OBSERVATION_SCHEMA,
    LEARNING_GROWTH_OUTPUT_SCHEMA,
]
