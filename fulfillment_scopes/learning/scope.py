"""domains.learning.domain —— 学习域的声明

**学习域是「放射」形状**：三只学科盒各自把学习结果投给成长记录盒，
它们**之间没有交接**（数学的产出不是语文的输入）。

## 所以 `all_archived` 规则在这里**不适用**

Learning 的汇点是 `learning_growth_box`——它**收上游产出**
（投料里有 `subject_result` 通道），跟 WMS 的档案盒正好相反：

| 域 | 汇点 | 收不收上游产出 | 所以边是 |
|---|---|---|---|
| WMS | `wms_system_box` | **不收**（去线上读）| `archive` 边，无通道 |
| Learning | `learning_growth_box` | **收** | `handoff` 边，有通道 |

**「汇点」只是拓扑位置（域内没有下游的那只），不是角色。**
决定边是什么性质的是 `kind`，不是「指向不指向汇点」
（`docs/domain-declaration.md` §5.5）。

**三条边共用同一个通道字段** `subject_result`——三科产出三个不同 schema，
但字段完全相同，符合**同一份「学科结果」形状**。
"""

from __future__ import annotations

from core.models import (
    FulfillmentScopeDeclaration,
    Edge,
    EdgeKind,
    Rule,
    RuleKind,
)

SCOPE = FulfillmentScopeDeclaration(
    id="Learning",
    display_name="学习成长",

    boxes=[
        "learning_math_box",
        "learning_chinese_box",
        "learning_english_box",
        "learning_growth_box",
    ],

    edges=[
        # 三科 → 成长记录：都是**真交接**（成长记录真的接产出）
        Edge(from_box="learning_math_box", to_box="learning_growth_box",
             kind=EdgeKind.handoff, via="subject_result"),
        Edge(from_box="learning_chinese_box", to_box="learning_growth_box",
             kind=EdgeKind.handoff, via="subject_result"),
        Edge(from_box="learning_english_box", to_box="learning_growth_box",
             kind=EdgeKind.handoff, via="subject_result"),
    ],

    rules=[
        # **没有 `all_archived`**：成长记录盒**收产出**，
        # 它不是「产物进档案的档案盒」——套用 WMS 那条规则会判错。
        Rule(kind=RuleKind.all_handoff),
        Rule(kind=RuleKind.each_handoff_has_channel),
    ],
)
