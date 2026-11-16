"""boxes.shopify_acquisition_experiment.schemas —— 获客实验盒的业务 schemas

**一轮只改一个变量、一个渠道。**

首轮实验（自然渠道内容实验）的形状：

| 字段 | 说明 |
|---|---|
| `scenario` | 一个购买场景（首轮：个性化节日礼物） |
| `landing_page` | 一个落地页或集合页 |
| `changes` | 改了哪些东西（标题 / 描述 / FAQ / 个性化选项 / 价格与配送承诺）|
| `attribution_method` | **归因方式**——`utm` / `landing_page_time_window` / `none` |
| `cost_cap` | **成本上限**（不是预算上限——自然渠道花的是时间和稿费）|
| `start_at` / `end_at` | 起止时间 |
| `stopping_condition` | 什么条件下停 |
| `baseline_ref` | 基线是哪一份快照 |

## 为什么归因方式是**一等字段**

自然搜索没有 UTM。把 UTM 当唯一归因方式，
等于让 SEO / AI 引荐这一类渠道**永远归因不到**——
而这家店 100% 的销售来自自然搜索。

## 首轮不判 ROAS

168 次访问 / 3 个订单的样本，转化率波动比信号大得多。
ROAS 是**辅助指标**，不是首轮的判据。

## 两份形状都来自边界

`schema_shopify_diagnosis_output`（本盒收的那份）与
`schema_shopify_experiment_output`（本盒产出的那份），
都定义在 `fulfillment_scopes/shopify/shapes.py`。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

from fulfillment_scopes.shopify.shapes import (
    DIAGNOSIS_OUTPUT_SCHEMA,
    EXPERIMENT_OUTPUT_SCHEMA,
    WRITABLE_FIELD_PROPERTIES,
)

EXPERIMENT_INPUT_SCHEMA = SchemaDef(
    id="schema_shopify_experiment_input",
    description="投料：这一轮的诊断",
    fields=[
        FieldDef(name="diagnosis_ref", type="ref:schema_shopify_diagnosis_output",
                 required=True, description="上游诊断产出（交接通道按本盒声明的形状校验过）"),
        FieldDef(name="apply", type="boolean", required=False,
                 description=(
                     "这一次**真的要落笔**吗——默认否。"
                     "没有它就只出方案：交接链路、看板示例投料、"
                     "任何自动发起的履约都不会改店里的页面"
                 )),
    ],
)

EXPERIMENT_PLAN_SCHEMA = SchemaDef(
    id="schema_shopify_experiment_plan",
    description="计划：这一跳要设计什么实验",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="target_handle", type="string", required=False,
                 description="这一轮要改哪个商品页（handle）——**要带下去**，"
                             "不然产出里落地页是空的"),
        FieldDef(name="diagnosis_ref", type="ref:schema_shopify_diagnosis_output",
                 required=True),
    ],
)

EXPERIMENT_OBSERVATION_SCHEMA = SchemaDef(
    id="schema_shopify_experiment_observation",
    description="观测：起草了什么、**写没写上去**、改前是什么样",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="diagnosis_ref", type="ref:schema_shopify_diagnosis_output",
                 required=True),
        FieldDef(name="applied", type="boolean", required=False,
                 description="真的写上去了吗——写闸关着就是 False"),
        FieldDef(name="baseline_fields", type="object", required=False,
                 description="改之前的字段值（没有它就没法回滚、没法对照）",
                 properties=list(WRITABLE_FIELD_PROPERTIES)),
        FieldDef(name="applied_fields", type="object", required=False,
                 description="写上去之后的字段值",
                 properties=list(WRITABLE_FIELD_PROPERTIES)),
        FieldDef(name="draft", type="object", required=False,
                 description="模型起草的内容（写闸关着时也有——它就是那份施工单）",
                 properties=list(WRITABLE_FIELD_PROPERTIES)),
        FieldDef(name="user_errors", type="array", required=False,
                 items=FieldDef(name="err", type="string", required=True),
                 description="Shopify 拒了什么——如实带回去，不吞"),
        FieldDef(name="reject_reason", type="string", required=False,
                 description="为什么没落笔（写闸关着 / 草稿不合格 / 写入失败）"),
        FieldDef(name="write_scope", type="string", required=False,
                 description="用的哪个写权限——写操作要留痕"),
    ],
)

ALL_SCHEMAS = [
    EXPERIMENT_INPUT_SCHEMA,
    EXPERIMENT_PLAN_SCHEMA,
    EXPERIMENT_OBSERVATION_SCHEMA,
    DIAGNOSIS_OUTPUT_SCHEMA,
    EXPERIMENT_OUTPUT_SCHEMA,
]

__all__ = [
    "ALL_SCHEMAS",
    "EXPERIMENT_INPUT_SCHEMA",
    "EXPERIMENT_PLAN_SCHEMA",
    "EXPERIMENT_OBSERVATION_SCHEMA",
    "EXPERIMENT_OUTPUT_SCHEMA",
    "DIAGNOSIS_OUTPUT_SCHEMA",
]
