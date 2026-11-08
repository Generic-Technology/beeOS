"""boxes.learning_english.schemas - 英语盒的业务 schemas

**schema 归消费者所有**：本盒自己声明自己吃什么、吐什么。

## 投料：一次英语练习

**投料可以是照片，也可以是手填**：投一张作业照片，本盒会**看图**读出
得分、满分、日期和错题（读不出来的留空，不猜）；照片之外的事实手填，
手填的**优先**——照片只补空。

手工投料也照旧：把这次练习的事实（做了什么、得分多少、出处在哪）
填进来，本盒把它整理成一条**带出处的学习记录**。

`source_kind` 只有两个取值，**别混**：

- `observed` —— 这次真的看到了（批改过的作业、试卷上的分数）
- `reported` —— 家里口述的（"今天练了半小时"）

混在一起的话，半年后翻档案就分不清哪些是**看到的**、哪些是**听说的**。

## 产出：一条学习记录

产出字段是**成长记录盒**要收的那份形状的**同构副本**
（那边才是消费者，那边说了算）——两份必须对得上，
守卫盯着：`tests/test_learning_boxes.py`。

## 判据 metric 名就是本文件的字段名

这不是风格约定：`acceptance` 指向 schema 里没有的字段，
等于判了一条**判不出来的判据**。
"""


from __future__ import annotations

from core.models import FieldDef, SchemaDef


LEARNING_ENGLISH_INPUT_SCHEMA = SchemaDef(
    id="schema_learning_english_input",
    description="投料：一次英语练习的事实",
    fields=[
        FieldDef(
            name="photo",
            type="string",
            media="image",
            media_accept=[
                "image/heic", "image/heif",
                "image/jpeg", "image/png", "image/webp", "image/gif",
            ],
            required=False,
            description=(
                "作业照片（手机拍的作业/试卷）。填了它，本盒会看图读出得分、"
                "满分、日期和错题；照片读不出的，在下面手工补"
            ),
        ),
        FieldDef(
            name="activity",
            type="string",
            required=False,
            description="这次做了什么：作业 / 单元测 / 订正 / 复习",
        ),
        FieldDef(
            name="observed_at",
            type="string",
            required=False,
            description="学习发生的时间（ISO，比如 2026-11-01T16:30）",
        ),
        FieldDef(
            name="evidence",
            type="string",
            required=False,
            description="出处：这次是基于什么记的（作业本 P12 / 单元测卷 / 口述）",
        ),
        FieldDef(
            name="source_kind",
            type="enum",
            required=False,
            values=['observed', 'reported'],
            description="observed = 这次真的看到了；reported = 家里口述的（两种别混）",
        ),
        FieldDef(
            name="dictation_score",
            type="number",
            required=False,
            description="听写的得分",
        ),
        FieldDef(
            name="dictation_total",
            type="number",
            required=False,
            description="听写的满分",
        ),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="备注",
        ),
        FieldDef(
            name="new_words",
            type="number",
            required=False,
            description="新学的单词数",
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

LEARNING_ENGLISH_PLAN_SCHEMA = SchemaDef(
    id="schema_learning_english_plan",
    description="计划：这一跳要做什么",
    fields=[
        FieldDef(
            name="photo_digest",
            type="string",
            required=False,
            description="这次的照片指纹（从图片字节推导；没有照片就是空串）",
        ),
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
            name="dictation_score",
            type="number",
            required=True,
            description="听写的得分",
        ),
        FieldDef(
            name="dictation_total",
            type="number",
            required=True,
            description="听写的满分",
        ),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="备注",
        ),
        FieldDef(
            name="new_words",
            type="number",
            required=False,
            description="新学的单词数",
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

LEARNING_ENGLISH_OBSERVATION_SCHEMA = SchemaDef(
    id="schema_learning_english_observation",
    description="观测：这一跳真的看到了什么",
    fields=[
        FieldDef(
            name="photo_digest",
            type="string",
            required=False,
            description="这次的照片指纹（从图片字节推导；没有照片就是空串）",
        ),
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
            name="dictation_score",
            type="number",
            required=True,
            description="听写的得分",
        ),
        FieldDef(
            name="dictation_total",
            type="number",
            required=True,
            description="听写的满分",
        ),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="备注",
        ),
        FieldDef(
            name="new_words",
            type="number",
            required=False,
            description="新学的单词数",
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

LEARNING_ENGLISH_OUTPUT_SCHEMA = SchemaDef(
    id="schema_learning_english_output",
    description="产出：一条带出处的英语学习记录",
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
        FieldDef(
            name="ratio_exact",
            type="boolean",
            required=True,
            description="掌握度确实是本盒口径的得分率（装配跳复算过）",
        ),
    ],
)

LEARNING_ENGLISH_PHOTO_FACTS_SCHEMA = SchemaDef(
    id="schema_learning_english_photo_facts",
    description="从作业照片里读出来的事实（读不出来的留空——不猜）",
    fields=[
        FieldDef(
            name="dictation_score",
            type="number",
            required=False,
            description="照片上印的得分",
        ),

        FieldDef(
            name="dictation_total",
            type="number",
            required=False,
            description="照片上印的满分",
        ),

        FieldDef(
            name="observed_at",
            type="string",
            required=False,
            description="照片上写的日期（ISO，看不到就空）",
        ),

        FieldDef(
            name="activity",
            type="string",
            required=False,
            description="照片上写的这次是什么练习（看不到就空）",
        ),

        FieldDef(
            name="errors",
            type="array",
            required=False,
            items=FieldDef(name="error", type="string"),
            description="照片上看得出的错题（一条一个）",
        ),

        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="照片上其他看得出的信息",
        ),
    ],
)

LEARNING_ENGLISH_PHOTO_READ_SCHEMA = SchemaDef(
    id="schema_learning_english_photo_read",
    description="看图那一跳的产出：读出来的事实 + 原样带下去的投料",
    fields=[
        FieldDef(
            name="photo_digest",
            type="string",
            required=False,
            description="照片指纹（从图片字节推导；同一张照片永远是同一个号）",
        ),
        FieldDef(
            name="photo_facts",
            type="ref:schema_learning_english_photo_facts",
            required=False,
            description="从照片里读出来的事实",
        ),
        FieldDef(
            name="activity",
            type="string",
            required=False,
            description="这次做了什么：作业 / 单元测 / 订正 / 复习",
        ),
        FieldDef(
            name="observed_at",
            type="string",
            required=False,
            description="学习发生的时间（ISO，比如 2026-11-01T16:30）",
        ),
        FieldDef(
            name="evidence",
            type="string",
            required=False,
            description="出处：这次是基于什么记的（作业本 P12 / 单元测卷 / 口述）",
        ),
        FieldDef(
            name="source_kind",
            type="enum",
            required=False,
            values=['observed', 'reported'],
            description="observed = 这次真的看到了；reported = 家里口述的（两种别混）",
        ),
        FieldDef(
            name="dictation_score",
            type="number",
            required=False,
            description="听写的得分",
        ),
        FieldDef(
            name="dictation_total",
            type="number",
            required=False,
            description="听写的满分",
        ),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="备注",
        ),
        FieldDef(
            name="new_words",
            type="number",
            required=False,
            description="新学的单词数",
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


ALL_SCHEMAS = [
    LEARNING_ENGLISH_PHOTO_FACTS_SCHEMA,
    LEARNING_ENGLISH_PHOTO_READ_SCHEMA,
    LEARNING_ENGLISH_INPUT_SCHEMA,
    LEARNING_ENGLISH_PLAN_SCHEMA,
    LEARNING_ENGLISH_OBSERVATION_SCHEMA,
    LEARNING_ENGLISH_OUTPUT_SCHEMA,
]
