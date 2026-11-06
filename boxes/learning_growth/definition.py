"""
boxes.learning_growth.definition - 成长记录盒的声明

把每一次学习记成一条带出处的成长记录。

**它存结果，不存过程**：数学 / 语文 / 英语三只学科盒各自做自己那一段的
过程（看这次练习、算得分率），过程产物留在它们自己那里；
本盒只沉淀**一条条记录**——这就是车宥安的成长档案。

**档案里没有一个裸数字。** 每条记录都带着：
学的是哪一科、这次做了什么、掌握度是多少、
**这次是基于什么记的**（`evidence`）、
**是看到的还是听说的**（`source_kind`）。

**为什么分 `observed` 和 `reported`**：
「批改过的作业上 92 分」和「今天练了半小时」是两种事实，
混在一起的话，半年后翻这份档案就分不清哪些是**看见的**、哪些是**听说的**。
分不清的档案 = 看着完整，其实不能当依据。

**它是 Learning 域的唯一汇点**：三只学科盒的产出最终都跟这份档案对上。

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

from boxes.learning_growth.schemas import ALL_SCHEMAS, SOURCE_KINDS


DECLARATION = BoxDeclaration(
    id="learning_growth_box",
    version=1,
    description=(
        "把每一次学习记成一条带出处的成长记录\n\n"
        "**它存结果，不存过程。** 三只学科盒各自做自己那一段的过程，"
        "过程产物留在它们自己那里；本盒只沉淀一条条记录。\n\n"
        "**档案里没有一个裸数字。** 每条记录都带着学的是哪一科、"
        "这次做了什么、掌握度是多少、**这次是基于什么记的**、"
        "**是看到的还是听说的**。"
    ),

    # ---- 归属：这只盒子在企业里站在哪 ----
    display_name="成长记录盒",
    value_stream="Learning",
    role="学习成长档案的履约",

    # ---- 投料入口（Intent）----
    task_type="run_growth",
    task_schema="schema_learning_growth_input",
    # **沿用上游的号**：档案里的号和学科盒的号是同一个，
    # 所以两边认领同一个 record_id → 归档里它们是**一件任务**
    # （一件任务牵过 2 只盒子），不是两件不相干的事。
    subject_ref_from_result="record_id",
    subject_kind="learning_record",
    task_trigger=(
        "一次学科练习记完之后\n"
        "OR 想看成长档案时补录一条"
    ),

    # ---- 产出契约（Contract）----
    result_type="learning_growth_result",
    result_schema="schema_learning_growth_output",
    maturity="live",
    artifact_view="generic",
    acceptance=[
        # 掌握度是**比率**，越界就是上游给错了——档案不该收。
        AcceptanceRule(metric="mastery", op="gte", value=0),
        AcceptanceRule(metric="mastery", op="lte", value=1),
        # 出处类型必须是认得的那两种：第三种（空 / 自造词）不进档案。
        AcceptanceRule(
            metric="source_kind", op="in", value=list(SOURCE_KINDS),
        ),
        # 出处链没断：这条记录还认得自己是从哪次学习来的。
        AcceptanceRule(metric="source_record_present", op="eq", value=True),
    ],
    exceptions=[
        ExceptionRule(
            condition="source_record_present == false", action="escalate",
        ),
        ExceptionRule(condition="mastery > 1", action="escalate"),
    ],

    # ---- 作业路线（Procedure）----
    beeline_id="beeline_learning_growth_v1",
    beeline_version=1,
    beeline_description="成长记录盒 履约作业路线：growth_intake → growth_work → growth_packager",

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
