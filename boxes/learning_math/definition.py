"""
boxes.learning_math.definition - 数学盒的声明

拍一张作业照 → **错在哪 + 针对这些错怎么练**

**成熟度：live**。腿是真的：投料 → 看图抄题 → 判错因 → 出训练方案 → 装产出。

**这一盒现在干两件事**：

1. **错题训练卡**（这次的主产出）：哪几题错了、错在哪（错因）、
   针对每一类错该怎么练。
2. **一条带出处的学习记录**（交给成长记录盒）：这次练习的事实，
   掌握度是这次练习的**得分率**。

它不判断孩子聪不聪明，也不排名——它只保证：
**拍一张就能得到一份能照着练的东西**，而且**读不出来的地方不编**。

## 判据为什么是这四条

- `mastery >= 0` / `mastery <= 1` —— 掌握度是个**比率**，越界就是投料错了。
  照片里没读出得分/满分时它是 `n/a`（这次这个量用不上），
  判据对 `n/a` 判「不适用」，既不判过也不判红。
- `ratio_exact` —— 掌握度**确实是本盒口径的得分率**：
  装配跳拿原始分与满分复算一遍，跟工作跳算出来的那份对不上就判红。
  这一条防的是「掌握度被写成了一个跟分数无关的数」（比如写死 0.8）。
- `training_covers_errors` —— 训练方案把这次出现的**每一类错因**都覆盖到了。
  漏了一类就是「练了但没练到点上」，这张卡就不成立。

## 例外为什么是这三条

- `mastery > 1` —— 得分超过满分：投料错了，升级人工
- `ratio_exact == false` —— 两跳算出来的数对不上：升级人工
- `training_covers_errors == false` —— 错因有，方案没覆盖到：升级人工

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


DECLARATION = BoxDeclaration(
    id="learning_math_box",
    version=2,
    description=(
        "拍一张作业照 → 错在哪 + 针对这些错怎么练\n\n"
        "**成熟度：live**。腿是真的：投料 → 看图抄题 → 判错因 → "
        "出针对性训练方案 → 装产出。\n\n"
        "**读不出来的地方不编**：照片里没有得分/满分/日期时，"
        "那些格子留空、掌握度记 `n/a`，并把缺了哪几个写明。"
    ),

    # ---- 归属：这只盒子在企业里站在哪 ----
    display_name="数学盒",
    value_stream="Learning",
    role="数学错题分析与针对性训练的履约",

    # ---- 投料入口（Intent）----
    task_type="run_math",
    task_schema="schema_learning_math_input",
    # **认领自己造出来的那个号**：产出里的 record_id 就是「这次学习记录」
    # 的唯一号（从内容推导，同一件事重记还是同一个号）。
    #
    # 不认领的话，这条履约在归档里归不到任何工作单元——
    # 任务列表按工作项归拢，于是这只盒子只剩「跑过 N 次」，
    # 读者看不到「哪件任务」。**明明产出里有，却没声明，所以拿不到。**
    subject_ref_from_result="record_id",
    subject_kind="learning_record",
    task_trigger="拍一张作业照：要错题清单和针对性训练方案",

    # ---- 产出契约（Contract）----
    result_type="learning_math_result",
    result_schema="schema_learning_math_output",
    maturity="live",
    artifact_view="math_review",
    acceptance=[
        # **判据读的是产出里的字段名**——字段不在产出 schema 里，
        # 这条判据就永远读不到值。
        AcceptanceRule(metric="mastery", op="gte", value=0),
        AcceptanceRule(metric="mastery", op="lte", value=1),
        AcceptanceRule(metric="ratio_exact", op="eq", value=True),
        # 训练方案要盖住这次出现的每一类错因
        AcceptanceRule(metric="training_covers_errors", op="eq", value=True),
    ],
    exceptions=[
        ExceptionRule(condition="mastery > 1", action="escalate"),
        ExceptionRule(condition="ratio_exact == false", action="escalate"),
        ExceptionRule(condition="training_covers_errors == false", action="escalate"),
    ],

    # ---- 作业路线（Procedure）----
    beeline_id="beeline_learning_math_v3",
    beeline_version=3,
    beeline_description=(
        "数学盒 履约作业路线："
        "math_read_photo → math_intake → math_analyze → math_work → math_packager"
    ),

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
