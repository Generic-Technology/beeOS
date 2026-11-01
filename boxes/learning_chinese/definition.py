"""boxes.learning_chinese.definition - 语文盒的 Definition 数据

把一次语文练习记成一条带出处的学习事件

**成熟度：live**。腿是真的：投料 → 算阅读得分率 → 装出带出处的一条学习事件。

**这一盒只干一件事**：把「这次练习的事实」变成一条**能被追溯**的学习事件。
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
from boxes.learning_chinese.schemas import ALL_SCHEMAS

LEARNING_CHINESE_BOX = BeeBoxDefinition(
    id="learning_chinese_box",
    version=1,
    description=(
        "把一次语文练习记成一条带出处的学习事件\n\n"
        "**成熟度：live**。腿是真的：投料 → 算阅读得分率 → "
        "装出带出处的一条学习事件。"
    ),

    schemas=ALL_SCHEMAS,

    placement=PlacementDef(
        display_name="语文盒",
        value_stream="Learning",
        role="语文学习事件的履约",
        domain="Learning",
        # **放射形状**：本域没有交接链——语文的产出不是别科的输入，
        # 它们各自直投成长记录盒。接线在生成时就填好了。
        feeds_into=['learning_growth_box'],
    ),

    task=[
        TaskDef(
            type="run_chinese",
            task_schema="schema_learning_chinese_input",
            beeline_id="beeline_learning_chinese_v2",
            beeline_version=2,
            trigger="记一次语文练习：阅读理解、作文、背诵",
        ),
    ],

    result=ResultDef(
        type="learning_chinese_result",
        result_schema="schema_learning_chinese_output",
        maturity="live",
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

    # **不声明指标**：指标是**承诺**（target），不是事实。
    # 掌握度该定到多少，那是家里和老师定的——
    # 在这个数定下来之前声明一个，等于在看板上放一个**编出来的标准**。
    metrics=MetricsDef(),
)


def get_definition() -> BeeBoxDefinition:
    return LEARNING_CHINESE_BOX
