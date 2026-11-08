"""boxes.learning_math.schemas - 数学盒的业务 schemas

**schema 归消费者所有**：本盒自己声明自己吃什么、吐什么。

## 投料：一张作业照片就行

**输入就是拍照**：投一张作业/试卷照片，本盒会**看图**读出错题清单，
再按错题给出**针对性训练方案**。照片之外的事实可以手填，
手填的**优先**——照片只补空。

`source_kind` 只有两个取值，**别混**：

- `observed` —— 这次真的看到了（批改过的作业、试卷上的分数）
- `reported` —— 家里口述的（"今天练了半小时"）

混在一起的话，半年后翻档案就分不清哪些是**看到的**、哪些是**听说的**。

## 产出：一张「错题训练卡」

产出有两块：

1. **错题训练卡**（这次的主产出）：`wrong_questions`（哪几题错了、错在哪）
   + `training_plan`（针对这些错怎么练）。
2. **学习记录**（交给成长记录盒的那一份）：`record_id` / `subject` /
   `activity` / `observed_at` / `mastery` / `evidence` / `source_kind` /
   `detail` / `notes`。

产出字段是**成长记录盒**要收的那份形状的**超集**
（那边才是消费者，那边说了算）——两份必须对得上，
守卫盯着：`tests/test_learning_boxes.py`。

## 得分率读不出来时是 `n/a`，不是猜一个

`mastery` 的口径**只有一个**：这次练习的**得分率**（得分 ÷ 满分）。
照片里没印得分/满分时，这一格是 **`n/a`**（这次这个量用不上），
**不是** 0，也不是猜一个 100——猜一个满分，错的是以后每一份档案里的比率。

`n/a` 这个第三态是平台级的（`core.models.NOT_APPLICABLE`），
判据遇到它判「不适用」，既不判过也不判红。

## 判据 metric 名就是本文件的字段名

这不是风格约定：`acceptance` 指向 schema 里没有的字段，
等于判了一条**判不出来的判据**。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef


# ============================================================
# 错因分类：固定的表，不多不少
# ============================================================
#
# 为什么是固定表：错因是**要被归类的**，不是自由文本。
# 自由文本的话，「计算错了」「算错了」「粗心」会变成三种，
# 于是「这几道题是一类错」没法机械地判断，
# 训练方案也就没法按类分组——最后就退化成一段泛泛的建议。
#
# 「其它」必须留着：宁可归到「其它」等人来看，
# 也不要为了凑满分类把说不清的错因硬塞进某一类。
ERROR_TYPES = [
    "计算失误",       # 思路对，算错了
    "概念不清",       # 概念/公式没掌握
    "审题不清",       # 题意看错、条件看漏
    "步骤不全",       # 会做但没写完
    "单位换算错",     # 单位没换
    "抄写笔误",       # 会做，抄错了
    "未作答",         # 空着没做
    "其它",           # 说不清的归这儿
]

# 正确答案的来路——**分得清是抄的还是算的**。
# 图上印着答案就写「照片」；图上没印、模型自己算出来的写「模型推算」。
# 混起来的话，家长分不清「这是书上的正确答案」还是「这是机器算的」。
ANSWER_SOURCES = ["照片", "模型推算"]

# 照片里错题读到哪一步了。
#
# **三态不是多余的**：[]（这次没错题）和 null（看不出有没有错题）
# 是两件完全不同的事——前者是「都对」，后者是「没看清」。
# 把它俩混成一个空数组，等于跟家长说「都对」，
# 而实际上只是没看清。
WRONG_STATUSES = ["found", "none", "unclear"]


# ============================================================
# 复用字段：同一份形状只写一遍
# ============================================================
#
# 错题这一份形状在四个 schema 里出现（读图、计划、分析、产出）。
# **手抄四遍 = 四份事实**：改一处漏三处，产出就开始漂。
# 所以这里定义一次，四处引用。
WRONG_QUESTION_FIELD = FieldDef(
    name="wrong_question",
    type="object",
    required=False,
    description="一道错题",
    properties=[
        FieldDef(
            name="no",
            type="string",
            required=False,
            description="题号（第几题）",
        ),
        FieldDef(
            name="topic",
            type="string",
            required=False,
            description="知识点/题型（模型判断的）",
        ),
        FieldDef(
            name="question",
            type="string",
            required=False,
            description="题目原文",
        ),
        FieldDef(
            name="student_answer",
            type="string",
            required=False,
            description="孩子写的答案",
        ),
        FieldDef(
            name="correct_answer",
            type="string",
            required=False,
            description="正确答案",
        ),
        FieldDef(
            name="answer_source",
            type="enum",
            required=False,
            values=ANSWER_SOURCES,
            description="正确答案哪来的：照片上印的，还是模型算的",
        ),
        FieldDef(
            name="error_type",
            type="enum",
            required=False,
            values=ERROR_TYPES,
            description="错因（从固定表里选）",
        ),
        FieldDef(
            name="error_note",
            type="string",
            required=False,
            description="错在哪（人话一句）",
        ),
    ],
)

TRAINING_ITEM_FIELD = FieldDef(
    name="training",
    type="object",
    required=False,
    description="一组针对性训练",
    properties=[
        FieldDef(
            name="focus",
            type="string",
            required=False,
            description="针对什么（人话，如「两位数加法的进位」）",
        ),
        FieldDef(
            name="error_type",
            type="enum",
            required=False,
            values=ERROR_TYPES,
            description="这一组针对的错因",
        ),
        FieldDef(
            name="drills",
            type="array",
            required=False,
            items=FieldDef(name="drill", type="string", required=False),
            description="建议练的题/题型（一条一个）",
        ),
        FieldDef(
            name="how",
            type="string",
            required=False,
            description="怎么练（步骤/口诀）",
        ),
        FieldDef(
            name="minutes",
            type="number",
            required=False,
            description="大概练多少分钟",
        ),
    ],
)


def _wrong_questions_field(description: str) -> FieldDef:
    """错题清单字段（各处描述不同，形状一样）"""
    return FieldDef(
        name="wrong_questions",
        type="array",
        required=False,
        items=WRONG_QUESTION_FIELD,
        description=description,
    )


def _training_plan_field() -> FieldDef:
    """训练方案字段（形状固定）"""
    return FieldDef(
        name="training_plan",
        type="array",
        required=False,
        items=TRAINING_ITEM_FIELD,
        description="针对性训练方案（按错因分组，一组一条）",
    )


def _error_types_field() -> FieldDef:
    """错因清单字段"""
    return FieldDef(
        name="error_types",
        type="array",
        required=False,
        items=FieldDef(name="error_type", type="enum", required=False, values=ERROR_TYPES),
        description="这次出现的错因（去重，从错题清单里算出来的）",
    )


# ============================================================
# 投料：一次数学练习
# ============================================================

LEARNING_MATH_INPUT_SCHEMA = SchemaDef(
    id="schema_learning_math_input",
    description="投料：一张作业照片（其余可留空，照片读不出的再补）",
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
                "作业照片（手机拍的作业/试卷）。**投一张就够**：本盒会看图读出"
                "得分、满分、日期和错题；照片读不出的，在下面手工补"
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
            name="score",
            type="number",
            required=False,
            description="这次练习的得分",
        ),
        FieldDef(
            name="total",
            type="number",
            required=False,
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
            description="这次错在哪（人话摘要，一条一个）——照片里没读出错题时填这里",
        ),
    ],
)


# ============================================================
# 计划：这一跳要做什么
# ============================================================

LEARNING_MATH_PLAN_SCHEMA = SchemaDef(
    id="schema_learning_math_plan",
    description="计划：这一跳要做什么（事实都核对好了）",
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
            required=False,
            description=(
                "学习发生的时间（ISO）——照片上没写日期时这一格可以空着，"
                "空着不是 0，是「这次没读出来」"
            ),
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
            required=False,
            description="这次练习的得分（照片上没印就空着）",
        ),
        FieldDef(
            name="total",
            type="number",
            required=False,
            description="这次练习的满分（照片上没印就空着）",
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
        _wrong_questions_field("照片里读出来的错题（抄写的结果，还没判错因）"),
        FieldDef(
            name="wrong_questions_status",
            type="enum",
            required=False,
            values=WRONG_STATUSES,
            description=(
                "照片里的错题读到哪一步了：found = 读到了；none = 看得全、都没错；"
                "unclear = 看不清有没有错题（**跟「都对」是两回事**）"
            ),
        ),
        FieldDef(
            name="missing_fields",
            type="array",
            required=False,
            items=FieldDef(name="field", type="string", required=False),
            description="这次没拿到的字段（照片里没读出来、也没手填的）",
        ),
    ],
)


# ============================================================
# 分析：错因 + 针对性训练方案
# ============================================================

LEARNING_MATH_ANALYSIS_SCHEMA = SchemaDef(
    id="schema_learning_math_analysis",
    description="分析：错因判完了，训练方案也出来了",
    fields=[
        FieldDef(
            name="photo_digest",
            type="string",
            required=False,
            description="这次的照片指纹（从图片字节推导；没有照片就是空串）",
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
            required=False,
            description="学习发生的时间（ISO）",
        ),
        FieldDef(
            name="evidence",
            type="string",
            required=True,
            description="出处：这次是基于什么记的",
        ),
        FieldDef(
            name="source_kind",
            type="enum",
            required=True,
            values=['observed', 'reported'],
            description="observed = 这次真的看到了；reported = 家里口述的",
        ),
        FieldDef(
            name="score",
            type="number",
            required=False,
            description="这次练习的得分",
        ),
        FieldDef(
            name="total",
            type="number",
            required=False,
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
        _wrong_questions_field("这次错在哪（抄写 + 判错因之后）"),
        FieldDef(
            name="wrong_questions_status",
            type="enum",
            required=False,
            values=WRONG_STATUSES,
            description="照片里的错题读到哪一步了",
        ),
        FieldDef(
            name="missing_fields",
            type="array",
            required=False,
            items=FieldDef(name="field", type="string", required=False),
            description="这次没拿到的字段（照片里没读出来、也没手填的）",
        ),
        _error_types_field(),
        _training_plan_field(),
        FieldDef(
            name="training_summary",
            type="string",
            required=False,
            description="人话一句：这次错在哪、下一步怎么练",
        ),
    ],
)


# ============================================================
# 观测：这一跳真的看到了什么
# ============================================================

LEARNING_MATH_OBSERVATION_SCHEMA = SchemaDef(
    id="schema_learning_math_observation",
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
            type="number_or_na",
            required=False,
            description=(
                "算出来的掌握度（得分率）——照片里没读出得分/满分时是 "
                "`n/a`（这次这个量用不上），**不是 0，也不是猜一个数**"
            ),
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
            required=False,
            description="学习发生的时间（ISO）",
        ),
        FieldDef(
            name="evidence",
            type="string",
            required=True,
            description="出处：这次是基于什么记的",
        ),
        FieldDef(
            name="source_kind",
            type="enum",
            required=True,
            values=['observed', 'reported'],
            description="observed = 这次真的看到了；reported = 家里口述的",
        ),
        FieldDef(
            name="score",
            type="number",
            required=False,
            description="这次练习的得分",
        ),
        FieldDef(
            name="total",
            type="number",
            required=False,
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
        _wrong_questions_field("这次错在哪"),
        _error_types_field(),
        _training_plan_field(),
        FieldDef(
            name="training_summary",
            type="string",
            required=False,
            description="人话一句：这次错在哪、下一步怎么练",
        ),
        FieldDef(
            name="missing_fields",
            type="array",
            required=False,
            items=FieldDef(name="field", type="string", required=False),
            description="这次没拿到的字段（照片里没读出来、也没手填的）",
        ),
    ],
)


# ============================================================
# 产出：一张错题训练卡 + 一条学习记录
# ============================================================

LEARNING_MATH_OUTPUT_SCHEMA = SchemaDef(
    id="schema_learning_math_output",
    description="产出：这次错在哪、怎么针对性练，外加一条带出处的学习记录",
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
            description=(
                "学习发生的时间（ISO）——照片上没写日期时是**空串**，"
                "空串的意思是「这次没读出来」，不是「1970 年」"
            ),
        ),
        FieldDef(
            name="mastery",
            type="number_or_na",
            required=True,
            description=(
                "掌握度 0..1（这次练习的**得分率**）；照片里没读出得分/满分时是 "
                "`n/a`——**口径只有这一个**，换个口径这份档案就没法横向比"
            ),
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
            type="boolean_or_na",
            required=True,
            description=(
                "掌握度确实是本盒口径的得分率（装配跳复算过）；"
                "没有得分率时是 `n/a`（这次没什么可复算的）"
            ),
        ),
        # ---- 错题训练卡（这次的主产出）----
        _wrong_questions_field("这次错在哪（逐题：题号、题目、孩子的答案、正确答案、错因）"),
        FieldDef(
            name="wrong_count",
            type="number",
            required=True,
            description="错了几题",
        ),
        FieldDef(
            name="verified_right_count",
            type="number",
            required=True,
            description=(
                "「看着像错题、算下来是对的」剔掉了几道——"
                "**错题是算出来的，不是看出来的**，剔掉的要说明白"
            ),
        ),
        _error_types_field(),
        _training_plan_field(),
        FieldDef(
            name="training_summary",
            type="string",
            required=True,
            description="人话一句：这次错在哪、下一步怎么练",
        ),
        FieldDef(
            name="training_covers_errors",
            type="boolean",
            required=True,
            description=(
                "训练方案把这次出现的每一个错因都覆盖到了——"
                "漏了一类就是「练了但没练到点上」"
            ),
        ),
        FieldDef(
            name="missing_fields",
            type="array",
            required=False,
            items=FieldDef(name="field", type="string", required=False),
            description=(
                "这次没能从照片里读出来、也没手填的字段（score / total / "
                "observed_at / wrong_questions …）——**列出来，别让人以为没有**"
            ),
        ),
    ],
)


# ============================================================
# 照片那一跳：读出来的事实
# ============================================================

LEARNING_MATH_PHOTO_FACTS_SCHEMA = SchemaDef(
    id="schema_learning_math_photo_facts",
    description="从作业照片里读出来的事实（读不出来的留空——不猜）",
    fields=[
        FieldDef(
            name="score",
            type="number",
            required=False,
            description="照片上印的得分",
        ),
        FieldDef(
            name="total",
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
            name="wrong_questions_status",
            type="enum",
            required=False,
            values=WRONG_STATUSES,
            description="错题读到哪一步了：found / none / unclear",
        ),
        _wrong_questions_field("照片上看得出的错题（抄写的结果）"),
        FieldDef(
            name="notes",
            type="string",
            required=False,
            description="照片上其他看得出的信息",
        ),
    ],
)


LEARNING_MATH_PHOTO_READ_SCHEMA = SchemaDef(
    id="schema_learning_math_photo_read",
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
            type="ref:schema_learning_math_photo_facts",
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
            name="score",
            type="number",
            required=False,
            description="这次练习的得分",
        ),
        FieldDef(
            name="total",
            type="number",
            required=False,
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


ALL_SCHEMAS = [
    LEARNING_MATH_PHOTO_FACTS_SCHEMA,
    LEARNING_MATH_PHOTO_READ_SCHEMA,
    LEARNING_MATH_INPUT_SCHEMA,
    LEARNING_MATH_PLAN_SCHEMA,
    LEARNING_MATH_ANALYSIS_SCHEMA,
    LEARNING_MATH_OBSERVATION_SCHEMA,
    LEARNING_MATH_OUTPUT_SCHEMA,
]
