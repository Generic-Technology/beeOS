"""fulfillment_scopes.shopify.scope —— giftsjourneyrebirth 店铺经营协作边界

**这里是「这个边界里这四只盒子怎么连」的唯一事实源。**

## 形状：一条线 + 一个汇点

```
snapshot → traffic_diagnosis → acquisition_experiment → archive
    ↑                                                    │
    └──────────────── feedback（实验结论回到取数口）──────┘
```

- **链是线性的**：取数 → 诊断 → 实验 → 归档。
  为什么不并行：首轮要回答的是一个问题（流量断在哪），
  三个问题同时跑，结论互相污染。
- **archive 是唯一汇点**：它收上游产出（Learning 范式，
  `handoff` 边带通道），**没有出边**。
- **feedback 从实验盒发出，不从档案盒发出**：
  汇点的判定是「出度为 0 且入度大于 0 的唯一一只」
  （`core/wiring.py`）。档案盒一旦发出边，它就不再是汇点，
  整条链会画不出来（`center = None`）。

## 为什么 archive 收产出（Learning 范式），而不是 WMS 那套归档边

WMS 的系统档案盒**不收上游产出**（它去线上读），所以用 `archive` 边、无通道。
但这一轮的档案盒要沉淀的是「每轮实验的结论」——
**不收产出的话，它存的是线上现状，不是结论**，那不是这个边界要的东西。

所以：
- 边用 `handoff` + 通道（Learning 范式）；
- **不声明 `all_archived` 规则**——那条规则是给「不收产出的档案盒」准备的，
  套在会判错（Learning 边界的声明里就是这么写的）。

## 三次投递 → 一条经营记录

archive 盒跑三次（快照、诊断、实验各一次）。
**一个通道只能接一份产出**（`fulfillment_scopes/__init__.py` 的
`handoff_payload` 明确这么写），所以三次是分开跑的。

归到同一条记录靠 `subject_ref`：四只盒子的产出都带 `round_id`，
archive 盒的 `subject_ref_field` 指向它。
**不定这一条，三条通道就是三条互不相干的归档记录。**
"""

from __future__ import annotations

from core.models import (
    Edge,
    EdgeKind,
    FulfillmentScopeDeclaration,
    Rule,
    RuleKind,
)

SCOPE = FulfillmentScopeDeclaration(
    id="shopify_store",
    display_name="giftsjourneyrebirth 店铺经营",

    boxes=[
        "shopify_snapshot_box",
        "shopify_traffic_diagnosis_box",
        "shopify_acquisition_experiment_box",
        "shopify_archive_box",
    ],

    edges=[
        # ---- 交接边：产出真的流进下游的投料口，每条都带通道 ----
        Edge(from_box="shopify_snapshot_box", to_box="shopify_traffic_diagnosis_box",
             kind=EdgeKind.handoff, via="snapshot_ref"),
        Edge(from_box="shopify_traffic_diagnosis_box", to_box="shopify_acquisition_experiment_box",
             kind=EdgeKind.handoff, via="diagnosis_ref"),

        # 归档盒收三份产出：**三次投递**，一个通道一份
        Edge(from_box="shopify_snapshot_box", to_box="shopify_archive_box",
             kind=EdgeKind.handoff, via="snapshot_ref"),
        Edge(from_box="shopify_traffic_diagnosis_box", to_box="shopify_archive_box",
             kind=EdgeKind.handoff, via="diagnosis_ref"),
        Edge(from_box="shopify_acquisition_experiment_box", to_box="shopify_archive_box",
             kind=EdgeKind.handoff, via="experiment_ref"),

        # ---- 闭环边：实验结论回到取数口，开下一轮 ----
        #
        # **源必须是实验盒**：档案盒有出边就不再是唯一汇点，
        # 整条链会画不出来。
        Edge(from_box="shopify_acquisition_experiment_box", to_box="shopify_snapshot_box",
             kind=EdgeKind.feedback, no_channel=True),
    ],

    rules=[
        Rule(kind=RuleKind.all_handoff),
        Rule(kind=RuleKind.each_handoff_has_channel),
        # **没有 `all_archived`**：归档盒**收产出**（Learning 范式），
        # 那条规则是给「不收产出的档案盒」准备的，套在这里会判错。
    ],
)
