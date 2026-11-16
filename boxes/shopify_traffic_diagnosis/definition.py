"""boxes.shopify_traffic_diagnosis.definition —— 获客诊断盒的声明

**它只回答一个问题：流量断在哪一段。**

**成熟度：skeleton**。判据和形状都声明好了，
**腿还没实现**——断点判定要么走规则（四段逐段比对），
要么走模型（读快照说人话），本轮还没拍板哪条路。
`maturity="skeleton"` 让看板显式标「未实现」，
不把它算进验收率——「未实现」和「实现了但不达标」必须分得开。

## 判据只判「如实」

它判的是：结论有没有证据引用、未知项有没有列出来、假设有没有写。
**它判不了「假设对不对」**——那要等下一轮复测，
而首轮样本（168 访问 / 3 订单）本来就判不了对错。

## 「一个假设」是硬要求

首轮数据量小，**同时给三个假设等于什么都没给**。
所以判据卡住：只能选一个断点、一个场景、一个渠道。
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

from fulfillment_scopes.shopify.vocabulary import BOTTLENECK_STAGES

ESCALATION_RULES = [
    {"trigger": "hypothesis_present == false", "action": "escalate_to_human"},
    {"trigger": "snapshot_ref_present == false", "action": "escalate_to_human"},
]

DECLARATION = BoxDeclaration(
    id="shopify_traffic_diagnosis_box",
    version=1,
    description=(
        "获客诊断盒——判断这一轮流量**断在哪一段**，给出一个主问题假设、一个目标客群、"
        "一个优先渠道。\n\n"
        "**断点分四段**：没有曝光 / 有曝光没点击 / 有点击没 session / 有 session 没商品浏览。\n\n"
        "**结论必须带证据引用**——「流量少」不是证据，"
        "「qualified_sessions = 148」才是。\n\n"
        "**未知项必须写出来**：分不出来的段就说是分不出来，"
        "不能装作全知。\n\n"
        "**只给一个假设**：首轮数据量小，同时给三个等于什么都没给。"
    ),

    # ---- 归属 ----
    display_name="获客诊断盒",
    value_stream="Shopify Store",
    role="流量断点定位与主问题假设",

    # ---- 投料入口（Intent）----
    task_type="diagnose_traffic",
    task_schema="schema_shopify_diagnosis_input",
    subject_ref_field="snapshot_ref",
    subject_ref_subfield="round_id",
    subject_kind="operating_round",
    task_trigger="一轮快照取完之后\nOR 想复核上一轮的诊断",

    # ---- 产出契约（Contract）----
    result_type="traffic_diagnosis",
    result_schema="schema_shopify_diagnosis_output",
    result_description="这一轮的获客诊断（一个假设 + 证据 + 未知项）",
    # **live**：2026-11-16 真跑过一次——真快照 + 真模型 + 真校验，全绿。
    maturity="live",
    artifact_view="generic",
    acceptance=[
        # 断点必须落在认得的四段（外加「分不出单一断点」）
        AcceptanceRule(metric="bottleneck_stage", op="in", value=list(BOTTLENECK_STAGES)),
        # 有假设
        AcceptanceRule(metric="hypothesis_present", op="eq", value=True),
        # 结论带证据引用，且每条引用都指得到快照里的**真实指标名**
        AcceptanceRule(metric="evidence_validated", op="eq", value=True),
    ],
    exceptions=[
        ExceptionRule(condition="hypothesis_present == false", action="escalate"),
        ExceptionRule(condition="evidence_validated == false", action="escalate"),
        ExceptionRule(condition="snapshot_ref_present == false", action="escalate"),
    ],

    # ---- 作业路线（Procedure）----
    beeline_id="beeline_shopify_traffic_diagnosis_v1",
    beeline_version=1,
    beeline_description=(
        "获客诊断盒 履约作业路线：diagnosis_intake → diagnosis_work → diagnosis_packager"
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

    # **不声明指标**：诊断的指标（假设命中率）是**承诺**，要等复测才有。
    metrics=MetricsDef(),

    plugins=[
        PluginSpec(
            id="queen_escalation",
            extension_point="box.escalate",
            params={"escalation_rules": ESCALATION_RULES},
        ),
    ],
)
