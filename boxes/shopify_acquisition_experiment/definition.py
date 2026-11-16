"""boxes.shopify_acquisition_experiment.definition —— 获客实验盒的声明

**一轮只改一个变量、一个渠道。**

**成熟度：skeleton**。形状与判据声明好了，**腿还没实现**——
而且有一件事必须先拍板：**这只盒子是「产出方案」还是「直接投放」**。

- **产出方案**（人执行）：判据 = 方案完整可执行
  （场景 / 落地页 / 变更项 / 成本上限 / 起止 / 停止条件 / 归因方式齐全）。
- **直接投放**（盒子调广告 API）：判据 = 是否已投出、花了多少钱、有没有超预算。

首轮走**前者**：只出方案，执行留人工或外部工具，
盒子不碰任何写接口。付费投放留到基线和归因探测都做完之后。

## 首轮不判 ROAS

168 次访问 / 3 个订单的样本，转化率波动比信号大。
ROAS 是辅助指标，不是首轮判据。

## 「不执行」是契约的一部分

这只盒子**没有任何写操作**。改价、改库存、发优惠、投广告——
一律不在它的职责里。要那些能力，先改这份声明，再改代码。
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

from fulfillment_scopes.shopify.vocabulary import ATTRIBUTION_METHODS

#: **只有「要求落笔却没写成」才升级给人**。
#:
#: 「没要求落笔」（干跑）不是要人做决定——那是这一轮本来就不交付，
#: 走 `no_deliver`：**明确的不交付，不是跑挂了**。
ESCALATION_RULES = [
    {"trigger": "diagnosis_ref_present == false", "action": "escalate_to_human"},
    {"trigger": "cost_cap_present == false", "action": "escalate_to_human"},
    {
        "trigger": "apply_requested == true and applied == false",
        "action": "escalate_to_human",
    },
]

DECLARATION = BoxDeclaration(
    id="shopify_acquisition_experiment_box",
    version=1,
    description=(
        "获客实验盒——设计并记录一轮获客实验：场景、落地页、变更项、"
        "成本上限、起止时间、停止条件、归因方式。\n\n"
        "**一轮只改一个变量、一个渠道。** 同时改三样，结果不可归因。\n\n"
        "**首轮是自然渠道内容实验**：这家店 100% 的销售来自自然搜索，"
        "自然渠道已被验证，付费渠道还没有任何基线。\n\n"
        "**它不执行任何写操作**：只出方案，改价/改库存/发优惠/投广告"
        "都不在它的职责里。\n\n"
        "**首轮不判 ROAS**：样本太小，ROAS 是辅助指标。"
    ),

    # ---- 归属 ----
    display_name="获客实验盒",
    value_stream="Shopify Store",
    role="一轮获客实验的设计、记录与复测",

    # ---- 投料入口（Intent）----
    task_type="run_acquisition_experiment",
    task_schema="schema_shopify_experiment_input",
    subject_ref_field="diagnosis_ref",
    subject_ref_subfield="round_id",
    subject_kind="operating_round",
    task_trigger="诊断出了断点之后\nOR 一轮实验到期要复测",

    # ---- 产出契约（Contract）----
    result_type="acquisition_experiment",
    result_schema="schema_shopify_experiment_output",
    result_description="这一轮实验的方案与（复测后）结果",
    maturity="skeleton",
    artifact_view="generic",
    acceptance=[
        # 归因方式必须是认得的三种之一
        AcceptanceRule(metric="attribution_method", op="in",
                       value=list(ATTRIBUTION_METHODS)),
        # 写清了停止条件
        AcceptanceRule(metric="stopping_condition_present", op="eq", value=True),
        # 有成本上限——没有上限的实验不知道什么时候该停
        AcceptanceRule(metric="cost_cap_present", op="eq", value=True),
        # **真的落笔了才算交付**：出方案但没写上去，不算这一轮做了实验
        AcceptanceRule(metric="applied", op="eq", value=True),
    ],
    exceptions=[
        # **干跑**：没要求落笔 → 明确的不交付（不是失败，也不是等人放行）
        ExceptionRule(condition="apply_requested == false", action="no_deliver"),
        # **真出事**：要求落笔却没写成 → 要人看
        ExceptionRule(
            condition="apply_requested == true and applied == false",
            action="escalate",
        ),
        ExceptionRule(condition="diagnosis_ref_present == false", action="escalate"),
        ExceptionRule(condition="cost_cap_present == false", action="escalate"),
        ExceptionRule(condition="stopping_condition_present == false", action="escalate"),
    ],

    # ---- 作业路线（Procedure）----
    beeline_id="beeline_shopify_acquisition_experiment_v1",
    beeline_version=1,
    beeline_description=(
        "获客实验盒 履约作业路线：experiment_intake → experiment_work → experiment_packager"
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

    metrics=MetricsDef(),

    plugins=[
        PluginSpec(
            id="queen_escalation",
            extension_point="box.escalate",
            params={"escalation_rules": ESCALATION_RULES},
        ),
    ],
)
