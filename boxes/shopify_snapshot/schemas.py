"""boxes.shopify_snapshot.schemas —— 店铺快照盒的业务 schemas

**它只读**。它不判断、不建议、不动商品——它只回答一件事：

> 这一轮窗口里，**流量到底断在哪一段**（数据侧的事实）。

## 产出里没有一个裸数字

每条指标都带三样：

| 字段 | 为什么 |
|---|---|
| `status` | `observed` / `declared_from_baseline` / `unverifiable`（三态） |
| `source` | 取自哪张 schema、哪个 API 版本（`observed` 必填） |
| `note` | 为什么取不到（`unverifiable` 必填） |

**取不到写 `unverifiable`，不写 0。** 0 是观测值，
「拿不到」不是——两者混在一起，下游会把「没数据」读成「没有流量」。

## 「合格访问」只算一次

`qualified_sessions` 是**在产出里一次算出来的**，
不是「查两次相减」。规则文本同时写进 `qualified_session_rule`——
读者不用回头猜口径。

排除只按**首个落地页**：一个 session 后来访问了购物车或结账页，
恰恰说明它是高意向流量，按路径把整个 session 删掉会扔掉最值钱的会话。

## 产出形状由边界统一维护

`schema_shopify_snapshot_output` 定义在
`fulfillment_scopes/shopify/shapes.py`——**边界里只有一份**，
本盒（生产者）和下游三只盒子（消费者）用的是同一份。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

from fulfillment_scopes.shopify.shapes import SNAPSHOT_OUTPUT_SCHEMA
from fulfillment_scopes.shopify.vocabulary import (
    EXCLUDED_ENTRY_PATH_PREFIXES,
    METRIC_STATUS,
    QUALIFIED_SESSION_RULE,
)

SNAPSHOT_INPUT_SCHEMA = SchemaDef(
    id="schema_shopify_snapshot_input",
    description="投料：这一轮取哪一段时间、哪一家店",
    fields=[
        FieldDef(name="round_id", type="string", required=True,
                 description="经营轮次号——四只盒子靠它认出「同一轮」"),
        FieldDef(name="store_domain", type="string", required=True,
                 description="店铺域名（如 giftsjourneyrebirth.myshopify.com）"),
        FieldDef(name="window_start", type="string", required=True,
                 description="统计窗口起（ISO，**整日**，不含当天未走完的部分）"),
        FieldDef(name="window_end", type="string", required=True,
                 description="统计窗口止（ISO，整日）"),
    ],
)

SNAPSHOT_PLAN_SCHEMA = SchemaDef(
    id="schema_shopify_snapshot_plan",
    description="计划：这一跳要取什么、按什么口径",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="store_domain", type="string", required=True),
        FieldDef(name="window_start", type="string", required=True),
        FieldDef(name="window_end", type="string", required=True),
        FieldDef(name="qualified_session_rule", type="string", required=True,
                 description="合格访问的规则文本——写进产出，读者不用回头猜口径"),
        FieldDef(name="excluded_entry_paths", type="array", required=True,
                 items=FieldDef(name="prefix", type="string", required=True),
                 description="落地页排除前缀清单"),
    ],
)

SNAPSHOT_RAW_SCHEMA = SchemaDef(
    id="schema_shopify_snapshot_raw",
    description="观测：这一跳真的取回了什么",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="source", type="string", required=True,
                 description="出处：ShopifyQL sessions schema + API 版本 + 查询语句"),
        FieldDef(name="api_version", type="string", required=True),
        FieldDef(name="retrieved_at", type="string", required=True),
        FieldDef(name="totals", type="object", required=False, properties=[
            FieldDef(name="sessions", type="integer", required=True),
            FieldDef(name="online_store_visitors", type="integer", required=True,
                     description="独立访客数——**不能按落地页相加**（一个人会落多个页）"),
            FieldDef(name="pageviews", type="integer", required=True),
            FieldDef(name="sessions_with_cart_additions", type="integer", required=True),
            FieldDef(name="sessions_that_reached_checkout", type="integer", required=True),
            FieldDef(name="sessions_that_completed_checkout", type="integer", required=True),
            FieldDef(name="conversion_rate", type="number", required=True),
            FieldDef(name="products_viewed", type="integer", required=False,
                     description="看了几个**不同商品**（不是 session 数）"),
            FieldDef(name="collections_viewed", type="integer", required=False),
            FieldDef(name="searches_submitted", type="integer", required=False,
                     description="站内搜索次数——顾客想找什么但没找到，是很强的信号"),
        ]),
        FieldDef(name="landing_rows", type="array", required=True, items=FieldDef(
            name="landing_row", type="object", required=True, properties=[
                FieldDef(name="landing_page_path", type="string", required=True),
                FieldDef(name="sessions", type="integer", required=True),
                FieldDef(name="sessions_with_cart_additions", type="integer", required=True),
                FieldDef(name="sessions_that_reached_checkout", type="integer", required=True),
                FieldDef(name="sessions_that_completed_checkout", type="integer", required=True),
            ],
        )),
        FieldDef(name="view_rows", type="array", required=True, items=FieldDef(
            name="view_row", type="object", required=True, properties=[
                FieldDef(name="session_viewed_product", type="boolean", required=False,
                         description="这个 session 有没有看商品页"),
                FieldDef(name="sessions", type="integer", required=True),
            ],
        ), description="按 session_viewed_product 分组的结果——"
                        "**第四段断点（no_product_view）的依据**"),
        FieldDef(name="warnings", type="array", required=True,
                 items=FieldDef(name="warning", type="string", required=True),
                 description="Shopify 的非致命警告（如 parseWarnings）——要留痕，不当错误"),
        FieldDef(name="pages_fetched", type="integer", required=True,
                 description="分页取了几页（撞顶就要翻页，漏页 = 少算流量）"),
        FieldDef(name="fetch_ok", type="boolean", required=True,
                 description="总量与落地页取到了吗——没取到就没有任何量"),
    ],
)

ALL_SCHEMAS = [
    SNAPSHOT_INPUT_SCHEMA,
    SNAPSHOT_PLAN_SCHEMA,
    SNAPSHOT_RAW_SCHEMA,
    SNAPSHOT_OUTPUT_SCHEMA,
]

__all__ = [
    "ALL_SCHEMAS",
    "SNAPSHOT_INPUT_SCHEMA",
    "SNAPSHOT_PLAN_SCHEMA",
    "SNAPSHOT_RAW_SCHEMA",
    "SNAPSHOT_OUTPUT_SCHEMA",
    "EXCLUDED_ENTRY_PATH_PREFIXES",
    "METRIC_STATUS",
    "QUALIFIED_SESSION_RULE",
]
