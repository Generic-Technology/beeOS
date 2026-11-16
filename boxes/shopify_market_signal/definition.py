"""boxes.shopify_market_signal.definition —— 市场信号盒的声明

**它回答的是「外面的人在公开地找什么」——不是「谁想要」。**

只读盒：不发消息、不建受众、不改页面。

## 成熟度：skeleton

腿实现了（四个平台客户端 + PII 剔除 + 交接），
但**只有 GSC 的 CSV 那条路用夹具验过**；
其余平台要等凭证到位才跑得起来。
第一次真跑过之后标 `live`。

## 判据只判三件机械的事

1. **产出里没有个人信息**（`pii_remaining_count == 0`）——安全底线
2. **每个平台都交代了取没取到**（取不到也要说清为什么）
3. **每条信号都有出处**（没出处的主题不算数）

**它不判「有多少条信号」**——需求信号本来就是长尾，
要求凑够条数，只会逼着盒子去凑数。
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
from core.plugins import PluginSpec
from core.scaffold import BoxDeclaration

ESCALATION_RULES = [
    {"trigger": "pii_remaining_count > 0", "action": "escalate_to_human"},
    {"trigger": "pii_clean == false", "action": "escalate_to_human"},
    {"trigger": "signal_status_complete == false", "action": "escalate_to_human"},
    {"trigger": "source_present == false", "action": "escalate_to_human"},
]

DECLARATION = BoxDeclaration(
    id="shopify_market_signal_box",
    version=1,
    description=(
        "市场信号盒——去外部平台看**公开的需求**：谁在公开地找什么、抱怨什么、想要什么。\n\n"
        "**它抓的是「需求主题」，不是「人」。** 产出里不会有邮箱、手机号、姓名——\n"
        "带个人信息的候选**整条丢掉**（带联系方式的帖子不是需求信号，\n"
        "是有人在私下交易）。\n\n"
        "**只读盒**：不发消息、不建受众、不改任何东西。\n\n"
        "**一个平台挂了不拖垮整轮**：那个平台标 unverifiable + 原因，其余照常。\n\n"
        "四个平台：GSC（我们自己店的搜索词，第一方官方数据）、\n"
        "Reddit / Etsy / Pinterest（各自的官方 API，不爬）。"
    ),

    # ---- 归属 ----
    display_name="市场信号盒",
    value_stream="Shopify Store",
    role="站外公开需求信号的采集与去敏",

    # ---- 投料入口（Intent）----
    task_type="capture_market_signals",
    task_schema="schema_market_signal_input",
    subject_ref_field="round_id",
    subject_kind="operating_round",
    task_trigger=(
        "一轮经营开始前，去看看外面在找什么\n"
        "OR 想知道站外的需求信号有没有变化"
    ),

    # ---- 产出契约（Contract）----
    result_type="market_signals",
    result_schema="schema_market_signal_output",
    result_description="这一轮站外的需求主题清单（带出处、无个人信息）",
    maturity="skeleton",
    artifact_view="generic",
    acceptance=[
        # **安全底线**：产出里不能有一个邮箱/手机号
        # （用 `pii_clean` 而不是 `pii_remaining_count == 0`：
        #  一条都没取到时，「没有个人信息」是空集上的真话，
        #  但那一轮其实白跑了——所以它也该判红）
        AcceptanceRule(metric="pii_clean", op="eq", value=True),
        # 每个平台都交代了取没取到
        AcceptanceRule(metric="signal_status_complete", op="eq", value=True),
        # 每条信号都有出处
        AcceptanceRule(metric="source_present", op="eq", value=True),
    ],
    exceptions=[
        ExceptionRule(condition="pii_remaining_count > 0", action="escalate"),
        ExceptionRule(condition="pii_clean == false", action="escalate"),
        ExceptionRule(condition="signal_status_complete == false", action="escalate"),
        ExceptionRule(condition="source_present == false", action="escalate"),
    ],

    # ---- 作业路线（Procedure）----
    beeline_id="beeline_shopify_market_signal_v1",
    beeline_version=1,
    beeline_description=(
        "市场信号盒 履约作业路线："
        "market_signal_intake → market_signal_work → market_signal_packager"
    ),

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

    # **不声明指标**：需求信号有多少条不是承诺，是事实。
    metrics=MetricsDef(),

    plugins=[
        PluginSpec(
            id="queen_escalation",
            extension_point="box.escalate",
            params={"escalation_rules": ESCALATION_RULES},
        ),
    ],
)
