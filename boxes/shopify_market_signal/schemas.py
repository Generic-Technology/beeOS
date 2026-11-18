"""boxes.shopify_market_signal.schemas —— 市场信号盒的业务 schemas

**这只盒子只出一件事：外面的人正在公开地想什么 / 找什么。**

它不出「谁想要」——那需要个人信息，而那正是我们要避开的东西。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

from fulfillment_scopes.shopify.market_signal import PLATFORMS
from fulfillment_scopes.shopify.shapes import MARKET_SIGNAL_OUTPUT_SCHEMA

#: 一条需求信号
SIGNAL_ITEM = FieldDef(
    name="signal",
    type="object",
    required=True,
    properties=[
        FieldDef(name="platform", type="string", required=True,
                 description="哪个平台（gsc / reddit / etsy / pinterest）"),
        FieldDef(name="topic", type="string", required=True,
                 description="需求主题——**一句话，不含任何个人信息**"),
        FieldDef(name="occurrences", type="integer", required=False,
                 description="**需求大小**——有多少人在门外搜过（搜索词 = 展示数）"),
        FieldDef(name="clicks", type="integer", required=False,
                 description="点击数——**我们接住了多少**（不是需求大小）"),
        FieldDef(name="position", type="number", required=False,
                 description="平均排名——离第 1 页多远"),
        FieldDef(name="ctr_percent", type="number", required=False,
                 description="点击率（百分比，2.48 = 2.48%）"),
        FieldDef(name="source_url", type="string", required=False,
                 description="**出处**——没有出处的主题不算数"),
        FieldDef(name="captured_at", type="string", required=True,
                 description="抓取时间（ISO）"),
        FieldDef(name="snippet", type="string", required=False,
                 description="原文片段（已洗过个人信息）"),
    ],
)

#: **一条页面信号**——站外「我们的哪一页有展示、点了没、排第几」
#:
#: 它跟需求词是**两件事**：词是「买家想要什么」，
#: 页面信号是「那个东西在我们这儿摆成什么样」。
PAGE_SIGNAL_ITEM = FieldDef(
    name="page_signal",
    type="object",
    required=True,
    properties=[
        FieldDef(name="platform", type="string", required=True),
        FieldDef(name="path", type="string", required=True,
                 description="页面路径（/products/xxx）——要和 landing_page_path 对得上"),
        FieldDef(name="url", type="string", required=False,
                 description="完整 URL（**出处**）"),
        FieldDef(name="query", type="string", required=False,
                 description="**是哪个词把它摆在那儿的**——（API 那条路才有）"),
        FieldDef(name="impressions", type="integer", required=False,
                 description="展示数——有多少人在搜索结果里见过它"),
        FieldDef(name="clicks", type="integer", required=False,
                 description="点击数——**0 就是「摆在那儿没人点」**"),
        FieldDef(name="position", type="number", required=False,
                 description="平均排名——**离第 1 页多远**"),
        FieldDef(name="ctr_percent", type="number", required=False,
                 description="点击率（百分比，2.48 = 2.48%）"),
        FieldDef(name="captured_at", type="string", required=False),
    ],
)

#: 一个平台这次取没取到
PLATFORM_STATUS_ITEM = FieldDef(
    name="platform_status",
    type="object",
    required=True,
    properties=[
        FieldDef(name="platform", type="string", required=True),
        FieldDef(name="status", type="string", required=True,
                 description="observed / unverifiable"),
        FieldDef(name="detail", type="string", required=False,
                 description="取不到的原因——**要写清**"),
        FieldDef(name="signal_count", type="integer", required=False),
    ],
)

MARKET_SIGNAL_INPUT_SCHEMA = SchemaDef(
    id="schema_market_signal_input",
    description="投料：这一轮要找什么需求",
    fields=[
        FieldDef(name="round_id", type="string", required=True,
                 description="经营轮次号——跟其他盒子同一条轮次"),
        FieldDef(name="keywords", type="array", required=True,
                 items=FieldDef(name="keyword", type="string", required=True),
                 description="要找的关键词（品类 / 场景 / 送礼对象）"),
        FieldDef(name="platforms", type="array", required=False,
                 items=FieldDef(name="platform", type="enum", required=True,
                                values=PLATFORMS),
                 description="去哪些平台找（默认全部）"),
        FieldDef(name="window_days", type="integer", required=False,
                 description="回看多少天（默认 28）"),
        FieldDef(name="gsc_csv_path", type="string", required=False,
                 description="GSC 导出的 CSV 路径——**没授权时也能跑的那条路**"),
    ],
)

MARKET_SIGNAL_PLAN_SCHEMA = SchemaDef(
    id="schema_market_signal_plan",
    description="计划：这一跳去哪些平台找什么",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="keywords", type="array", required=True,
                 items=FieldDef(name="keyword", type="string", required=True)),
        FieldDef(name="platforms", type="array", required=True,
                 items=FieldDef(name="platform", type="string", required=True)),
        FieldDef(name="window_days", type="integer", required=True),
        FieldDef(name="gsc_csv_path", type="string", required=False),
    ],
)

MARKET_SIGNAL_RAW_SCHEMA = SchemaDef(
    id="schema_market_signal_raw",
    description="观测：各平台取回了什么、哪些带个人信息被丢了",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="signals", type="array", required=True, items=SIGNAL_ITEM),
        FieldDef(name="page_signals", type="array", required=False,
                 items=PAGE_SIGNAL_ITEM,
                 description="**站外需求落到我们页面上的样子**（GSC 才有）"),
        FieldDef(name="platform_status", type="array", required=True,
                 items=PLATFORM_STATUS_ITEM),
        FieldDef(name="pii_removed", type="array", required=True,
                 items=FieldDef(name="dropped", type="object", required=True,
                                properties=[
                                    FieldDef(name="platform", type="string", required=False),
                                    FieldDef(name="topic", type="string", required=False),
                                    FieldDef(name="pii_hits", type="array", required=False,
                                             items=FieldDef(name="hit", type="string",
                                                            required=True)),
                                ]),
                 description="**带个人信息被整条丢掉的**——丢掉是故意的"),
    ],
)

#: **产出形状住在边界包里**（`fulfillment_scopes/shopify/shapes.py`）——
#: 快照盒要声明它（交接通道按「下游声明的形状」校验），
#: 而两个盒子都从边界包引用 → **没有「盒子 import 另一个盒子」**。
ALL_SCHEMAS = [
    MARKET_SIGNAL_OUTPUT_SCHEMA,
    MARKET_SIGNAL_INPUT_SCHEMA,
    MARKET_SIGNAL_PLAN_SCHEMA,
    MARKET_SIGNAL_RAW_SCHEMA,
    MARKET_SIGNAL_OUTPUT_SCHEMA,
]

__all__ = [
    "ALL_SCHEMAS",
    "MARKET_SIGNAL_INPUT_SCHEMA",
    "MARKET_SIGNAL_PLAN_SCHEMA",
    "MARKET_SIGNAL_RAW_SCHEMA",
    "MARKET_SIGNAL_OUTPUT_SCHEMA",
]
