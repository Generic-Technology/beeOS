"""boxes.learning_math.schemas - 数学盒的业务 schemas

**schema 归消费者所有**：本盒自己声明自己吃什么、吐什么。

## 投料：一次数学练习

第一版是**手工投料**：家里把这次练习的事实（做了什么、得分多少、出处在哪）
填进来，本盒把它整理成一条**带出处的学习事件**。

`source_kind` 只有两个取值，**别混**：

- `observed` —— 这次真的看到了（批改过的作业、试卷上的分数）
- `reported` —— 家里口述的（"今天练了半小时"）

混在一起的话，半年后翻档案就分不清哪些是**看到的**、哪些是**听说的**。

## 产出：一条学习事件

产出字段是**成长记录盒**要收的那份形状的**同构副本**
（那边才是消费者，那边说了算）——两份必须对得上，
守卫盯着：`tests/test_learning_boxes.py`。

## 判据 metric 名就是本文件的字段名

这不是风格约定：`acceptance` 指向 schema 里没有的字段，
等于判了一条**判不出来的判据**。
"""


from __future__ import annotations

from core.models import FieldDef, SchemaDef


LEARNING_MATH_INPUT_SCHEMA = SchemaDef(
    id="schema_learning_math_input",
    description="投料：一次数学练习的事实",
    fields=[
        FieldDef(
            name="activity",
            type="string",
            required=True,
            description="这次做了什么：作业 / 单元测 / 订正 / 复习",
        ),
        FieldDef(
            name="observed_at",
            type="string",
            required=True,
            description="学习发生的时间（ISO，比如 2026-11-01T16:30）",
        ),
        FieldDef(
            name="evidence",
            type="string",
            required=True,
            description="出处：这次是基于什么记的（作业本 P12 / 单元测卷 / 口述）",
        ),
        FieldDef(
            name="source_kind",
            type="enum",
            required=True,
            values=['observed', 'reported'],
            description="observed = 这次真的看到了；reported = 家里口述的（两种别混）",
        ),
        FieldDef(
            name="score",
            type="number",
            required=True,
            description="这次练习的得分",
        ),
        FieldDef(
            name="total",
            type="number",
            required=True,
            description="这次练习的满分",
        ),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="备注",
        ),
        FieldDef(
            name="minutes",
            type="number",
            required=False,
            description="花了多少分钟",
        ),
        FieldDef(
            name="errors",
            type="array",
            required=False,
            items=FieldDef(name="error", type="string", required=False),
            description="这次错在哪（人话摘要，一条一个）",
        ),
    ],
)

LEARNING_MATH_PLAN_SCHEMA = SchemaDef(
    id="schema_learning_math_plan",
    description="计划：这一跳要做什么",
    fields=[
        FieldDef(
            name="plan",
            type="string",
            required=False,
            description="这一跳打算做什么（人话一句，够定位就行）",
        ),
        FieldDef(
            name="activity",
            type="string",
            required=True,
            description="这次做了什么：作业 / 单元测 / 订正 / 复习",
        ),
        FieldDef(
            name="observed_at",
            type="string",
            required=True,
            description="学习发生的时间（ISO，比如 2026-11-01T16:30）",
        ),
        FieldDef(
            name="evidence",
            type="string",
            required=True,
            description="出处：这次是基于什么记的（作业本 P12 / 单元测卷 / 口述）",
        ),
        FieldDef(
            name="source_kind",
            type="enum",
            required=True,
            values=['observed', 'reported'],
            description="observed = 这次真的看到了；reported = 家里口述的（两种别混）",
        ),
        FieldDef(
            name="score",
            type="number",
            required=True,
            description="这次练习的得分",
        ),
        FieldDef(
            name="total",
            type="number",
            required=True,
            description="这次练习的满分",
        ),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="备注",
        ),
        FieldDef(
            name="minutes",
            type="number",
            required=False,
            description="花了多少分钟",
        ),
        FieldDef(
            name="errors",
            type="array",
            required=False,
            items=FieldDef(name="error", type="string", required=False),
            description="这次错在哪（人话摘要，一条一个）",
        ),
    ],
)

LEARNING_MATH_OBSERVATION_SCHEMA = SchemaDef(
    id="schema_learning_math_observation",
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
            description="这一科的细节（人话一句）",
        ),
        FieldDef(
            name="mastery",
            type="number",
            required=False,
            description="算出来的掌握度（得分率）",
        ),
        FieldDef(
            name="activity",
            type="string",
            required=True,
            description="这次做了什么：作业 / 单元测 / 订正 / 复习",
        ),
        FieldDef(
            name="observed_at",
            type="string",
            required=True,
            description="学习发生的时间（ISO，比如 2026-11-01T16:30）",
        ),
        FieldDef(
            name="evidence",
            type="string",
            required=True,
            description="出处：这次是基于什么记的（作业本 P12 / 单元测卷 / 口述）",
        ),
        FieldDef(
            name="source_kind",
            type="enum",
            required=True,
            values=['observed', 'reported'],
            description="observed = 这次真的看到了；reported = 家里口述的（两种别混）",
        ),
        FieldDef(
            name="score",
            type="number",
            required=True,
            description="这次练习的得分",
        ),
        FieldDef(
            name="total",
            type="number",
            required=True,
            description="这次练习的满分",
        ),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="备注",
        ),
        FieldDef(
            name="minutes",
            type="number",
            required=False,
            description="花了多少分钟",
        ),
        FieldDef(
            name="errors",
            type="array",
            required=False,
            items=FieldDef(name="error", type="string", required=False),
            description="这次错在哪（人话摘要，一条一个）",
        ),
    ],
)

LEARNING_MATH_OUTPUT_SCHEMA = SchemaDef(
    id="schema_learning_math_output",
    description="产出：一条带出处的数学学习事件",
    fields=[
        FieldDef(
            name="record_id",
            type="string",
            required=True,
            description="这次学习事件的唯一号（从内容推导，同一件事重记也是同一个号）",
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
        FieldDef(
            name="ratio_exact",
            type="boolean",
            required=True,
            description="掌握度确实是本盒口径的得分率（装配跳复算过）",
        ),
    ],
)

ALL_SCHEMAS = [
    LEARNING_MATH_INPUT_SCHEMA,
    LEARNING_MATH_PLAN_SCHEMA,
    LEARNING_MATH_OBSERVATION_SCHEMA,
    LEARNING_MATH_OUTPUT_SCHEMA,
]
