"""boxes.learning_growth.definition - 成长记录盒的 Definition 数据

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
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricsDef,
    PlacementDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    TaskDef,
)
from boxes.learning_growth.schemas import ALL_SCHEMAS, SOURCE_KINDS

LEARNING_GROWTH_BOX = BeeBoxDefinition(
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

    schemas=ALL_SCHEMAS,

    placement=PlacementDef(
        display_name="成长记录盒",
        value_stream="Learning",
        role="学习成长档案的履约",
        domain="Learning",
        # **汇点没有下游**：它不被投给谁，它是所有人的落点。
        feeds_into=[],
    ),

    task=[
        TaskDef(
            type="run_growth",
            task_schema="schema_learning_growth_input",
            beeline_id="beeline_learning_growth_v1",
            beeline_version=1,
            # **不声明 subject_ref_field**：这一条记录**就是**档案的一条，
            # 它不属于某个需求单元——它描述的是车宥安的学习轨迹本身。
            trigger=(
                "一次学科练习记完之后\n"
                "OR 想看成长档案时补录一条"
            ),
        ),
    ],

    result=ResultDef(
        type="learning_growth_result",
        result_schema="schema_learning_growth_output",
        maturity="live",
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
        view="generic",
    ),

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

    # **不声明指标**：掌握度该定到多少，那是家里和老师定的。
    # 在这个数定下来之前声明一个，等于在看板上放一个**编出来的标准**。
    metrics=MetricsDef(),
)


def get_definition() -> BeeBoxDefinition:
    return LEARNING_GROWTH_BOX
