"""boxes.shopify_archive.definition —— 经营档案盒的声明

**它是这个协作边界的唯一汇点**（出度 0，入度 3）。

**它存结果，不存过程**：快照盒、诊断盒、实验盒各自做自己那一段，
过程产物留在它们自己那里；本盒只沉淀「这一轮经营发生了什么」。

## 成熟度：live

这只盒子**不碰网络**：它只接住上游产出、复核一遍、归档。
所以它现在就能真跑、真测——而快照盒还卡在权限审批上。

**但它「活」不代表整条链活了**：上游三只还没在真店上跑过。
看板上的「已实现」说的是**这只盒子**，
不是「这轮经营做完了」。

## 判据只判「如实」，不判「结论对不对」

档案盒判的是：这段记录**有没有出处**、**该标的有没有标**、
**轮次号在不在**。
它判不了「诊断盒的假设对不对」——那是下一轮复测的事。
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
    {"trigger": "metric_status_honest == false", "action": "escalate_to_human"},
    {"trigger": "source_present == false", "action": "escalate_to_human"},
    {"trigger": "subject_present == false", "action": "escalate_to_human"},
]

DECLARATION = BoxDeclaration(
    id="shopify_archive_box",
    version=1,
 description=(
        "经营档案盒——把一轮经营的快照、诊断、实验沉淀成**一条**带出处的记录。\n\n"
        "**它存结果，不存过程。** 三只上游盒子各自做自己那一段，"
        "过程产物留在它们自己那里。\n\n"
        "**它自己复核，不信上游说「我过了」**：每条指标有没有状态、"
        "observed 有没有出处、unverifiable 有没有原因——这里重新算一遍。\n\n"
        "**三次投递归一条记录**：同一个 round_id 的三段，"
        "在归档里是同一轮经营，不是三件不相干的事。"
    ),

    # ---- 归属 ----
    display_name="经营档案盒",
    value_stream="Shopify Store",
    role="一轮经营记录的沉淀与复核",

    # ---- 投料入口（Intent）----
    task_type="archive_operating_round",
    task_schema="schema_shopify_archive_input",
    # **归档按轮次分组**：三个通道分三次投递，三次的 subject_ref 都是 round_id
    # → 归档里它们是同一条经营记录。
    subject_ref_field="round_id",
    subject_kind="operating_round",
    task_trigger=(
        "上游产出一份就归档一份\n"
        "OR 想看这一轮经营归档到哪了"
    ),

    # ---- 产出契约（Contract）----
    result_type="operating_round_archive",
    result_schema="schema_shopify_archive_output",
    result_description="一轮经营的一段记录（带出处、带未知项）",
    maturity="live",
    artifact_view="generic",
    acceptance=[
        # **复核结论**必须是绿的：这一段的标注是如实的
        AcceptanceRule(metric="metric_status_honest", op="eq", value=True),
        # 有出处
        AcceptanceRule(metric="source_present", op="eq", value=True),
        # 带着自己的号——三次投递靠它归成一条
        AcceptanceRule(metric="subject_present", op="eq", value=True),
        # 这一条记的是哪一段，必须是认得的三种之一
        AcceptanceRule(metric="record_kind", op="in",
                       value=["snapshot", "diagnosis", "experiment"]),
    ],
    exceptions=[
        ExceptionRule(condition="metric_status_honest == false", action="escalate"),
        ExceptionRule(condition="source_present == false", action="escalate"),
        ExceptionRule(condition="subject_present == false", action="escalate"),
    ],

    # ---- 作业路线（Procedure）----
    beeline_id="beeline_shopify_archive_v1",
    beeline_version=1,
    beeline_description=(
        "经营档案盒 履约作业路线：archive_intake → archive_verify → archive_packager"
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

    # **不声明指标**：归档量是**承诺**，不是事实。
    metrics=MetricsDef(),

    plugins=[
        PluginSpec(
            id="queen_escalation",
            extension_point="box.escalate",
            params={"escalation_rules": ESCALATION_RULES},
        ),
    ],
)
