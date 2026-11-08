"""
boxes.learning_english.definition - 英语盒的声明

把一次英语练习记成一条带出处的学习记录

**成熟度：live**。腿是真的：投料 → 算听写得分率 → 装出带出处的一条学习记录。

**这一盒只干一件事**：把「这次练习的事实」变成一条**能被追溯**的学习记录。
它不判断孩子学得好不好——那是家里和老师的事；
它只保证记下来的那条**有出处、算数、装得进档案**。

## 判据为什么是这三条

- `mastery >= 0` / `mastery <= 1` —— 掌握度是个**比率**，越界就是投料错了
- `ratio_exact` —— 掌握度**确实是本盒口径的得分率**：
  装配跳拿原始分与满分复算一遍，跟工作跳算出来的那份对不上就判红。
  这一条防的是「掌握度被写成了一个跟分数无关的数」（比如写死 0.8）。

## 例外为什么是这两条

- `mastery > 1` —— 得分超过满分：投料错了，升级人工
- `ratio_exact == false` —— 两跳算出来的数对不上：升级人工

**为什么是 `BoxDeclaration` 而不是 `BeeBoxDefinition`**：
盒子只声明**它是什么**，不负责把声明拼成运行时认识的那几样。
拼装（以及当场校验）由 `core/scaffold.py` 的装配器做——
声明自相矛盾的话，在装配那一步就报出来，而不是等到履约跑起来。
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    ExceptionRule,
    MetricsDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
)
from core.scaffold import BoxDeclaration

from boxes.learning_english.schemas import ALL_SCHEMAS


DECLARATION = BoxDeclaration(
    id="learning_english_box",
    version=1,
    description=(
        "把一次英语练习记成一条带出处的学习记录\n\n"
        "**成熟度：live**。腿是真的：投料 → 算听写得分率 → "
        "装出带出处的一条学习记录。"
    ),

    # ---- 归属：这只盒子在企业里站在哪 ----
    display_name="英语盒",
    value_stream="Learning",
    role="英语学习记录的履约",

    # ---- 投料入口（Intent）----
    task_type="run_english",
    task_schema="schema_learning_english_input",
    # **认领自己造出来的那个号**：产出里的 record_id 就是「这次学习记录」
    # 的唯一号（从内容推导，同一件事重记还是同一个号）。
    #
    # 不认领的话，这条履约在归档里归不到任何工作单元——
    # 任务列表按工作项归拢，于是这只盒子只剩「跑过 N 次」，
    # 读者看不到「哪件任务」。**明明产出里有，却没声明，所以拿不到。**
    subject_ref_from_result="record_id",
    subject_kind="learning_record",
    task_trigger="记一次英语练习：听写、单词、课文朗读",

    # ---- 产出契约（Contract）----
    result_type="learning_english_result",
    result_schema="schema_learning_english_output",
    maturity="live",
    artifact_view="generic",
    acceptance=[
        # **判据读的是产出里的字段名**——字段不在产出 schema 里，
        # 这条判据就永远读不到值。
        AcceptanceRule(metric="mastery", op="gte", value=0),
        AcceptanceRule(metric="mastery", op="lte", value=1),
        AcceptanceRule(metric="ratio_exact", op="eq", value=True),
    ],
    exceptions=[
        ExceptionRule(condition="mastery > 1", action="escalate"),
        ExceptionRule(condition="ratio_exact == false", action="escalate"),
    ],

    # ---- 作业路线（Procedure）----
    beeline_id="beeline_learning_english_v2",
    beeline_version=2,
    beeline_description="英语盒 履约作业路线：english_intake → english_work → english_packager",

    # ---- 履约政策（Policy）----
    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            continuous_improvement="observe_only",
        ),
        rules=QueenRules(
            task_routing={},
            exception_handling={},
            continuous_improvement={},
        ),
    ),

    # **不声明指标**：指标是**承诺**（target），不是事实。
    # 掌握度该定到多少，那是家里和老师定的——
    # 在这个数定下来之前声明一个，等于在看板上放一个**编出来的标准**。
    metrics=MetricsDef(),
)
