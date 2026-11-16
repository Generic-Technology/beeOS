"""boxes.shopify_traffic_diagnosis.schemas —— 获客诊断盒的业务 schemas

**它只回答一个问题：流量断在哪一段。**

断点分四段（外加「分不出单一断点」）：

| 断点 | 现象 | 下一轮该做什么 |
|---|---|---|
| `no_impression` | 没有曝光 | 分发 / SEO / 内容 / 广告没启动 |
| `no_click` | 有曝光没点击 | 素材 / 受众 / 卖点不匹配 |
| `no_session` | 有点击没 session | 链接 / 追踪 / 落地页有问题 |
| `no_product_view` | 有 session 没商品浏览 | 落地页与商品承接有问题 |

## 结论必须带证据引用

`bottleneck_evidence` 里每一条都要指得到快照里的**某个指标名**——
「流量少」不是证据，「qualified_sessions = 148」才是。

**判据只判「如实」，不判「假设对不对」**：假设对不对要等下一轮复测。

## 两份形状都来自边界

- `schema_shopify_snapshot_output`：**本盒收的那份**（消费者声明）；
- `schema_shopify_diagnosis_output`：**本盒产出的那份**。

两份都定义在 `fulfillment_scopes/shopify/shapes.py`——
边界里只有一份事实，抄不出第二份会漂的副本。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

from fulfillment_scopes.shopify.shapes import (
    DIAGNOSIS_OUTPUT_SCHEMA,
    SNAPSHOT_OUTPUT_SCHEMA,
)
from fulfillment_scopes.shopify.vocabulary import BOTTLENECK_STAGES

DIAGNOSIS_INPUT_SCHEMA = SchemaDef(
    id="schema_shopify_diagnosis_input",
    description="投料：这一轮的快照",
    fields=[
        FieldDef(name="snapshot_ref", type="ref:schema_shopify_snapshot_output",
                 required=True, description="上游快照产出（交接通道按本盒声明的形状校验过）"),
        FieldDef(name="exclude_landing_pages", type="array", required=False,
                 items=FieldDef(name="handle", type="string", required=True),
                 description="**最近改过的页**（handle）——它们要留观察期，"
                             "这一轮优先改别的（不是硬排除：全排除了就什么都不做了）"),
    ],
)

DIAGNOSIS_PLAN_SCHEMA = SchemaDef(
    id="schema_shopify_diagnosis_plan",
    description="计划：这一跳要查哪几段",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="stages_to_check", type="array", required=True,
                 items=FieldDef(name="stage", type="string", required=True),
                 description="要逐段核对的四段"),
        FieldDef(name="snapshot_ref", type="ref:schema_shopify_snapshot_output", required=True),
        FieldDef(name="exclude_landing_pages", type="array", required=False,
                 items=FieldDef(name="handle", type="string", required=True),
                 description="最近改过的页——**每一跳都要声明，不然会被投影掉**"),
    ],
)

DIAGNOSIS_OBSERVATION_SCHEMA = SchemaDef(
    id="schema_shopify_diagnosis_observation",
    description="观测：模型判了什么（**以及校验把它毙了没有**）",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="snapshot_ref", type="ref:schema_shopify_snapshot_output",
                 required=True),
        FieldDef(name="conclusion", type="object", required=False,
                 description="模型给出的结论——**校验不过时这里缺席**",
                 properties=[
                     FieldDef(name="bottleneck_stage", type="string", required=True),
                     FieldDef(name="bottleneck_evidence", type="array", required=True,
                              items=FieldDef(name="ref", type="string", required=True)),
                     FieldDef(name="hypothesis", type="string", required=True),
                     FieldDef(name="target_customer", type="string", required=True),
                     FieldDef(name="target_scenario", type="string", required=True),
                     FieldDef(name="prioritized_channel", type="string", required=True),
                     FieldDef(name="target_landing_page", type="string", required=False),
                     FieldDef(name="unknowns", type="array", required=True,
                              items=FieldDef(name="item", type="string", required=True)),
                 ]),
        FieldDef(name="reject_reason", type="string", required=False,
                 description="结论为什么被毙了（模型不合格 / 模型调不通）"),
    ],
)

ALL_SCHEMAS = [
    DIAGNOSIS_INPUT_SCHEMA,
    DIAGNOSIS_PLAN_SCHEMA,
    DIAGNOSIS_OBSERVATION_SCHEMA,
    SNAPSHOT_OUTPUT_SCHEMA,
    DIAGNOSIS_OUTPUT_SCHEMA,
]

__all__ = [
    "ALL_SCHEMAS",
    "DIAGNOSIS_INPUT_SCHEMA",
    "DIAGNOSIS_PLAN_SCHEMA",
    "DIAGNOSIS_OBSERVATION_SCHEMA",
    "DIAGNOSIS_OUTPUT_SCHEMA",
    "SNAPSHOT_OUTPUT_SCHEMA",
]
