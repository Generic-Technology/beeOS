"""fulfillment_scopes.shopify.shapes —— 本边界的三份产出形状（**唯一一份事实**）

快照盒产出什么、诊断盒收什么、实验盒收什么、档案盒收什么——
说的是**同三份形状**。这些形状在这里定义一次，各盒子各自在自己的
`schemas.py` 里**声明**自己收/产哪一份。

## 为什么不是「每个盒子各抄一份」

仓里 learning 那套是各盒各抄一份同构形状，再加一条守卫盯着它们别漂。
抄四份的代价是：改一处字段，忘了改另一处 → 交接在装配期判红，
或者更糟：**判绿但接错了对象**。

这里改成**边界维护一份**：

- 生产者用它当自己的产出形状；
- 消费者用它当自己声明的接料形状；
- 漂不了，因为只有一份。

**形状归消费者所有这条没被破坏**：消费者仍然在自己的
`ALL_SCHEMAS` 里声明「我收哪一份」，交接通道校验的仍然是
**消费者声明的那份**——只是这份不再手抄。

## 边界包是叶子

这里只 import `core.models`，不 import 任何盒子。
所以「盒子 import 边界」不会造成 import 顺序问题
（边界不会反过来依赖盒子）。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

from fulfillment_scopes.shopify.vocabulary import (
    ATTRIBUTION_METHODS,
    BOTTLENECK_STAGES,
    METRIC_STATUS,
)

#: 一条指标（带出处与状态）——三份产出里都用它
METRIC_ITEM = FieldDef(
    name="metric",
    type="object",
    required=True,
    properties=[
        FieldDef(name="name", type="string", required=True,
                 description="指标名（如 sessions / conversion_rate）"),
        FieldDef(name="value", type="number", required=False,
                 description="观测值；取不到时**留空**，不要写 0"),
        FieldDef(name="status", type="enum", required=True, values=METRIC_STATUS,
                 description="这条指标这次是什么状态"),
        FieldDef(name="source", type="string", required=False,
                 description="出处（observed 必填）"),
        FieldDef(name="retrieved_at", type="string", required=False,
                 description="采集时间（ISO）"),
        FieldDef(name="note", type="string", required=False,
                 description="为什么取不到 / 口径说明（unverifiable 必填）"),
    ],
)

#: 改前 / 改后字段的可对照形状（白名单里的五个字段）
WRITABLE_FIELD_PROPERTIES = [
    FieldDef(name="title", type="string", required=False),
    FieldDef(name="descriptionHtml", type="string", required=False),
    FieldDef(name="seo.title", type="string", required=False),
    FieldDef(name="seo.description", type="string", required=False),
    FieldDef(name="tags", type="array", required=False,
             items=FieldDef(name="tag", type="string", required=True)),
]

#: 结果指标列表
RESULT_METRICS = FieldDef(
    name="result_metrics",
    type="array",
    required=True,
    items=METRIC_ITEM,
)


# ============================================================
# 零、市场信号产出（链首那只盒子的产出形状）
# ============================================================

MARKET_SIGNAL_OUTPUT_SCHEMA = SchemaDef(
    id="schema_market_signal_output",
    description="这一轮站外的公开需求（带出处、无个人信息）",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="observed_at", type="string", required=True),
        FieldDef(name="signals", type="array", required=True, items=FieldDef(
            name="market_signal", type="object", required=True, properties=[
                FieldDef(name="platform", type="string", required=True),
                FieldDef(name="topic", type="string", required=True),
                FieldDef(name="occurrences", type="integer", required=False),
                FieldDef(name="source_url", type="string", required=False),
                FieldDef(name="captured_at", type="string", required=False),
                FieldDef(name="snippet", type="string", required=False),
            ],
        )),
        FieldDef(name="platform_status", type="array", required=True, items=FieldDef(
            name="platform_status", type="object", required=True, properties=[
                FieldDef(name="platform", type="string", required=True),
                FieldDef(name="status", type="string", required=True),
                FieldDef(name="detail", type="string", required=False),
                FieldDef(name="signal_count", type="integer", required=False),
            ],
        )),
        FieldDef(name="pii_removed_count", type="integer", required=True),
        FieldDef(name="pii_remaining_count", type="integer", required=True,
                 description="**产出里还剩几个个人信息片段——必须为 0**"),
        FieldDef(name="pii_clean", type="boolean", required=True,
                 description="有信号 **且** 没有个人信息——一条都没取到时它也是 false"),
        FieldDef(name="signal_status_complete", type="boolean", required=True),
        FieldDef(name="source_present", type="boolean", required=True),
    ],
)


# ============================================================
# 一、快照产出
# ============================================================

SNAPSHOT_OUTPUT_SCHEMA = SchemaDef(
    id="schema_shopify_snapshot_output",
    description="这一轮窗口的店铺流量事实（带出处、带状态）",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="observed_at", type="string", required=True,
                 description="这次观测的时间（ISO）"),
        FieldDef(name="window_start", type="string", required=True),
        FieldDef(name="window_end", type="string", required=True),
        FieldDef(name="store_domain", type="string", required=True),
        FieldDef(name="source", type="string", required=False,
                 description="这次的数据来自哪（schema + API 版本 + 查询语句）"),
        FieldDef(name="strategy", type="string", required=False,
                 description="这一轮打哪种仗（conversion_lift / ai_referral）"),
        FieldDef(name="api_version", type="string", required=True),

        FieldDef(name="total_sessions", type="integer", required=False,
                 description="全部真人 session（**含**被排除的非获客落地页）"),
        FieldDef(name="qualified_sessions", type="integer", required=False,
                 description="合格访问——**本次算一次**，不是相减出来的"),
        FieldDef(name="excluded_entry_sessions", type="integer", required=False,
                 description="因为落地页被排除掉的 session 数"),
        FieldDef(name="online_store_visitors", type="integer", required=False),
        FieldDef(name="pageviews", type="integer", required=False),

        FieldDef(name="sessions_with_cart_additions", type="integer", required=False),
        FieldDef(name="sessions_that_reached_checkout", type="integer", required=False),
        FieldDef(name="sessions_that_completed_checkout", type="integer", required=False),
        FieldDef(name="conversion_rate", type="number", required=False),

        FieldDef(name="qualified_session_rule", type="string", required=True,
                 description="合格访问的规则文本——写进产出，读者不用回头猜口径"),
        FieldDef(name="excluded_entry_paths", type="array", required=True,
                 items=FieldDef(name="prefix", type="string", required=True)),

        FieldDef(name="metrics", type="array", required=True, items=METRIC_ITEM),
        FieldDef(name="unverified", type="array", required=False,
                 items=FieldDef(name="item", type="string", required=True),
                 description="这次没取到的量 + 原因"),
        FieldDef(name="data_present", type="boolean", required=True,
                 description=(
                     "这次**取到量了吗**——「没取到」和「取到了但标注不诚实」"
                     "是两件不同的事：前者是环境问题，后者要人看"
                 )),
        FieldDef(name="metric_status_complete", type="boolean", required=True,
                 description="每条指标都带合法 status（observed 有 source、unverifiable 有 note）"),
        FieldDef(name="source_present", type="boolean", required=True,
                 description="产出里有出处——没有出处的数字不进档案"),
        FieldDef(name="session_count_consistent", type="boolean", required=False,
                 description="落地页分组合计 == 总量——不等说明有 session 的落地页不在返回里"),

        FieldDef(name="top_landing_pages", type="array", required=True, items=FieldDef(
            name="landing_page", type="object", required=True, properties=[
                FieldDef(name="landing_page_path", type="string", required=True),
                FieldDef(name="sessions", type="integer", required=True),
                FieldDef(name="sessions_that_completed_checkout", type="integer", required=False),
            ],
        ), description="流量前 N 的落地页——首轮实验的落地页候选从这里挑"),
        FieldDef(name="landing_funnel", type="array", required=True, items=FieldDef(
            name="landing_funnel_row", type="object", required=True, properties=[
                FieldDef(name="landing_page_path", type="string", required=True),
                FieldDef(name="sessions", type="integer", required=True),
                FieldDef(name="sessions_with_cart_additions", type="integer", required=True),
                FieldDef(name="sessions_that_reached_checkout", type="integer", required=True),
                FieldDef(name="sessions_that_completed_checkout", type="integer", required=True),
            ],
        ), description="**每个落地页的完整漏斗**（不止前 N 名）——"
                       "复测要按页比，改过的那一页未必在前 N 名里"),
        FieldDef(name="top_sources", type="array", required=True, items=FieldDef(
            name="source_row", type="object", required=True, properties=[
                FieldDef(name="referrer_source", type="string", required=True),
                FieldDef(name="agentic_referring_channel", type="string", required=True,
                         description="AI 引荐渠道（ChatGPT / Google AI Mode and Gemini / "
                                     "Microsoft Copilot / Shop）——**引荐来源是事实，"
                                     "「商品被 AI 引用」不是**"),
                FieldDef(name="sessions", type="integer", required=True),
                FieldDef(name="sessions_that_completed_checkout", type="integer", required=False),
            ],
        ), description="来源分布（含 AI 引荐渠道）"),
        FieldDef(name="traffic_pools", type="array", required=True, items=FieldDef(
            name="pool", type="object", required=True, properties=[
                FieldDef(name="referrer_source", type="string", required=False),
                FieldDef(name="agentic_referring_channel", type="string", required=False),
                FieldDef(name="landing_page_path", type="string", required=False),
                FieldDef(name="sessions", type="integer", required=True),
                FieldDef(name="sessions_that_completed_checkout", type="integer", required=True),
            ],
        ), description="来源 × AI 渠道 × 落地页 的交叉——**哪个流量池在漏水**"),
        FieldDef(name="daily_trend", type="array", required=True, items=FieldDef(
            name="day_row", type="object", required=True, properties=[
                FieldDef(name="day", type="string", required=False),
                FieldDef(name="sessions", type="integer", required=True),
                FieldDef(name="sessions_that_completed_checkout", type="integer", required=True),
            ],
        ), description="日级序列——**改完第三天就能看方向**，不用等满一个月"),
        FieldDef(name="market_signals", type="array", required=True, items=FieldDef(
            name="market_signal", type="object", required=True, properties=[
                FieldDef(name="platform", type="string", required=True),
                FieldDef(name="topic", type="string", required=True),
                FieldDef(name="occurrences", type="integer", required=False),
                FieldDef(name="source_url", type="string", required=False),
                FieldDef(name="captured_at", type="string", required=False),
            ],
        ), description="**站外的公开需求**（上一跳市场信号盒带来）——"
                       "这一轮的事实 = 店里的数据 + 外面在找什么"),
        FieldDef(name="site_search_terms", type="array", required=True, items=FieldDef(
            name="site_search", type="object", required=True, properties=[
                FieldDef(name="query", type="string", required=True,
                         description="买家在站内搜的词"),
                FieldDef(name="searches", type="integer", required=True,
                         description="搜了几次"),
            ],
        ), description=(
            "**买家在站内搜什么**——真实买家的需求信号，"
            "比任何爬来的线索都强"
        )),
        FieldDef(name="search_funnel", type="object", required=False, properties=[
            FieldDef(name="sessions_with_searches", type="integer", required=False,
                     description="有搜索的 session 数"),
            FieldDef(name="search_sessions_with_clicks", type="integer", required=False,
                     description="搜了并点了搜索结果的 session 数"),
            FieldDef(name="search_sessions_with_cart_additions", type="integer", required=False),
            FieldDef(name="search_sessions_that_completed_checkout", type="integer", required=False,
                     description="搜了并买了的 session 数"),
            FieldDef(name="search_click_rate", type="number", required=False),
            FieldDef(name="search_added_to_cart_rate", type="number", required=False),
            FieldDef(name="search_conversion_rate", type="number", required=False,
                     description="搜了之后成交的比例——**站内搜索漏斗的最后一环**"),
        ], description="站内搜索漏斗：搜了 → 点了结果 → 加购 → 成交"),
        FieldDef(name="top_search_terms", type="array", required=True, items=FieldDef(
            name="term_row", type="object", required=True, properties=[
                FieldDef(name="term", type="string", required=True),
                FieldDef(name="sessions", type="integer", required=True),
            ],
        ), description="自然搜索带来的搜索词——内容实验的抓手"),
    ],
)


# ============================================================
# 二、诊断产出
# ============================================================

DIAGNOSIS_OUTPUT_SCHEMA = SchemaDef(
    id="schema_shopify_diagnosis_output",
    description="这一轮的获客诊断（一个假设 + 证据 + 未知项）",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="observed_at", type="string", required=True),
        FieldDef(name="bottleneck_stage", type="enum", required=False,
                 values=BOTTLENECK_STAGES,
                 description="断点落在四段中的哪一段（或「分不出单一断点」）"),
        FieldDef(name="bottleneck_evidence", type="array", required=False,
                 items=FieldDef(name="ref", type="string", required=True),
                 description="证据引用——指到快照里的具体指标名"),
        FieldDef(name="hypothesis", type="string", required=False,
                 description="一个主问题假设——**一个**，不是三个"),
        FieldDef(name="target_customer", type="string", required=False,
                 description="目标客群（首轮：个性化节日礼物的购买者）"),
        FieldDef(name="target_scenario", type="string", required=False,
                 description="目标购买场景（首轮：个性化节日礼物）"),
        FieldDef(name="prioritized_channel", type="string", required=False,
                 description="优先渠道（首轮：自然搜索 / AI 引荐的内容实验）"),
        FieldDef(name="unknowns", type="array", required=False,
                 items=FieldDef(name="item", type="string", required=True),
                 description="这次判不出来的地方——**必须写出来**，不能装作全知"),
        FieldDef(name="leaking_pools", type="array", required=False, items=FieldDef(
            name="pool", type="object", required=True, properties=[
                FieldDef(name="referrer_source", type="string", required=False),
                FieldDef(name="agentic_referring_channel", type="string", required=False),
                FieldDef(name="landing_page_path", type="string", required=False),
                FieldDef(name="sessions", type="integer", required=True),
            ],
        ), description=(
            "**有流量但没成交的池子**（规则算的，不是模型说的）——"
            "「哪个池子在漏水」是算得出来的，就不要交给模型"
        )),
        FieldDef(name="strategy", type="string", required=False,
                 description="这一轮打哪种仗（conversion_lift / ai_referral / demand_match）——"
                             "目标页是按它挑的"),
        FieldDef(name="target_demand_query", type="string", required=False,
                 description="**买家搜的那个词**（demand_match 策略命中时才有）——"
                             "真实买家打出来的字，下游要照着它写"),
        FieldDef(name="target_keywords", type="array", required=False,
                 items=FieldDef(name="keyword", type="string", required=True),
                 description="这一轮要接住的买家用词——**从买家搜的词里来**，"
                             "不是我们自己想出来的词"),
        FieldDef(name="target_demand_source", type="string", required=False,
                 description="那个词来自哪儿——「站内搜索」（需求已经到门口了）"
                             "还是「站外」（门外的需求，比如 GSC 的查询词）"),
        FieldDef(name="snapshot_ref_present", type="boolean", required=True,
                 description="接住了上游快照——这是**前提**，不是结论"),
        FieldDef(name="hypothesis_present", type="boolean", required=True,
                 description="给出了一个主问题假设"),
        FieldDef(name="evidence_validated", type="boolean", required=True,
                 description=(
                     "结论里的每一条证据引用都指得到快照里的**真实指标名**——"
                     "编出来的指标名不算证据"
                 )),
        FieldDef(name="target_landing_page", type="string", required=False,
                 description="下一轮该动哪个落地页（handle）——"
                             "从快照的落地页清单里挑，不许自己编"),
    ],
)


# ============================================================
# 三、实验产出
# ============================================================

EXPERIMENT_OUTPUT_SCHEMA = SchemaDef(
    id="schema_shopify_experiment_output",
    description="这一轮实验的方案与（复测后）结果",
    fields=[
        FieldDef(name="round_id", type="string", required=True),
        FieldDef(name="observed_at", type="string", required=True),
        FieldDef(name="scenario", type="string", required=False,
                 description="一个购买场景"),
        FieldDef(name="landing_page", type="string", required=False,
                 description="一个落地页或集合页"),
        FieldDef(name="changes", type="array", required=False,
                 items=FieldDef(name="change", type="string", required=True),
                 description="改了哪些——「优化内容」不算变更项"),
        FieldDef(name="attribution_method", type="enum", required=False,
                 values=ATTRIBUTION_METHODS,
                 description="怎么归因——自然渠道没有 UTM"),
        FieldDef(name="cost_cap", type="number", required=False,
                 description="成本上限（自然渠道花的是时间和稿费，不是广告费）"),
        FieldDef(name="start_at", type="string", required=False),
        FieldDef(name="end_at", type="string", required=False),
        FieldDef(name="stopping_condition", type="string", required=False,
                 description="什么条件下停——没有停止条件的实验不会结束"),
        FieldDef(name="baseline_ref", type="string", required=False,
                 description="基线是哪一份快照"),
        FieldDef(name="result_metrics", type="array", required=False,
                 items=METRIC_ITEM,
                 description="复测后的指标（带状态与出处）"),
        FieldDef(name="unverified", type="array", required=False,
                 items=FieldDef(name="item", type="string", required=True)),
        FieldDef(name="applied", type="boolean", required=False,
                 description="**真的写上去了吗**——写闸关着就只出方案不落笔"),
        FieldDef(name="apply_requested", type="boolean", required=False,
                 description=(
                     "这一轮**要求落笔**了吗——"
                     "「没要求」是明确的不交付（no_deliver），"
                     "「要求了却没写成」才是要人看的事（escalate）"
                 )),
        FieldDef(name="draft", type="object", required=False,
                 description="模型起草的内容——**没落笔时它就是那份施工单**，"
                             "留在记录里免得白起草",
                 properties=list(WRITABLE_FIELD_PROPERTIES)),
        FieldDef(name="baseline_fields", type="object", required=False,
                 description="改之前的字段值（基线）——没有它就没法回滚、没法对照",
                 properties=list(WRITABLE_FIELD_PROPERTIES)),
        FieldDef(name="applied_fields", type="object", required=False,
                 description="写上去之后的字段值",
                 properties=list(WRITABLE_FIELD_PROPERTIES)),
        FieldDef(name="write_scope", type="string", required=False,
                 description="用的哪个写权限（write_products）——写操作要留痕"),
        FieldDef(name="diagnosis_ref_present", type="boolean", required=True,
                 description="接住了上游诊断——这是**前提**，不是结论"),
        FieldDef(name="cost_cap_present", type="boolean", required=True,
                 description="写清了成本上限——没有上限的实验不知道什么时候该停"),
        FieldDef(name="stopping_condition_present", type="boolean", required=True,
                 description="写清了停止条件——没有停止条件的实验不会结束"),
    ],
)

__all__ = [
    "MARKET_SIGNAL_OUTPUT_SCHEMA",
    "METRIC_ITEM",
    "WRITABLE_FIELD_PROPERTIES",
    "SNAPSHOT_OUTPUT_SCHEMA",
    "DIAGNOSIS_OUTPUT_SCHEMA",
    "EXPERIMENT_OUTPUT_SCHEMA",
]
