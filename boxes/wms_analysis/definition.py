"""boxes.wms_analysis.definition - WMS Analysis Box Definition 数据

WMS 线上效果分析盒——上线之后拿真实运行数据回头看：**当初那条需求，
线上到底满足了没有**。落差就是下一轮需求的种子。

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息 + 展示位（显示名 / 价值流 / 角色）
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + 4 条机械 acceptance + 2 条 exception）
- 履约政策 Policy（queen）
- 履约度量 Metrics

**成熟度：live**。4 条判据现在真的在验：预期 vs 实测逐项比、
落差归因到「哪条需求、哪个概念、证据在哪」、并播下下一轮需求的种子。

**不声明任何指标**：没有基线就不声明「线上该是多少」，
凭空写一个数字只会让看板上出现一个没人解释过的指标。

## 接线上为什么是「反馈」而不是「交接」

它产出「线上满不满足需求」这个结论，这个结论**不是**交给需求盒去验的
（需求盒不会去验），它是拿去**开新一轮需求**的——所以是反馈。

（这两条边都声明在**域声明**里：`domains/wms/domain.py`。
接线不在盒子自己身上——一只盒子不许同时留两套。）

而 ops → analysis 才是交接：ops 把线上真实状态投过来，分析接着比。
所以链路是 ops ─交接→ analysis ─反馈→ requirement，
而 requirement → design → development → testing → ops 仍是单向流水线。
环就这样闭上了：闭环的边不进拓扑排序，主干永远无环。

**为什么是 `BoxDeclaration` 而不是 `BeeBoxDefinition`**：
盒子只声明**它是什么**，不负责把声明拼成运行时认识的那几样。
拼装（以及当场校验）由 `core/scaffold.py` 的装配器做——
声明自相矛盾的话，在装配那一步就报出来，而不是等到履约跑起来。
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
from core.scaffold import BoxDeclaration


DECLARATION = BoxDeclaration(
    id='wms_analysis_box',
    version=1,
    description=(
        "WMS 线上效果分析盒——**上线之后回头验需求**：拿真实运行数据，"
        "跟当初那条需求声明的预期值逐项比。\n\n"
        "它是 WMS 域的闭环口：前五只盒子走的是「需求 → 设计 → 开发 → 测试 → 运维」，"
        "走完一遍还是没人回答「线上到底满没满足需求」。这只盒子回答它，"
        "而落差直接变成下一轮需求的种子——所以它反馈给需求盒，而不是交给需求盒去验。"
    ),

    # ---- 归属：这只盒子在企业里站在哪 ----
    display_name="WMS Analysis Box",
    value_stream="Software Delivery",
    role="WMS Post-Release Effect Analysis Fulfillment",

    # ---- 投料入口（Intent）----
    task_type="handle_effect_analysis_request",
    task_schema="schema_analysis_input",
    # 本次履约处理的业务对象标识（归档按它分组）
    # 分析盒的 subject 是一条**需求**：它问的就是「当初那条需求
    # 线上满没满足」，所以 id 就是需求号本身。
    subject_ref_field="requirement_ref",
    subject_kind="requirement",
    task_trigger=(
        "需求上线满一个观察窗口\n"
        "OR 某条需求的关键指标连续偏离预期\n"
        "OR 要复盘：当初说的「提升分拣时效」到底有没有提升\n"
        "OR 运维发现线上行为跟设计意图对不上"
    ),

    # ---- 产出契约（Contract）----
    result_type="wms_effect_analyzed",
    result_schema="schema_analysis_record",
    # **live**：腿是真的了——它读测试盒跑出来的实测观测，
    # 跟设计盒声明的预期逐项比，落差归因 + 播种下一轮需求。
    #
    # 以前是 skeleton：产出是桩，刻意不产出判据要的 metric，
    # 让看板显式标「未实现」。腿真了徽章就得摘掉——
    # **「未实现」和「实现了但不达标」必须分得开**。
    maturity="live",
    acceptance=[
        AcceptanceRule(
            metric="convergence_rate", op="eq", value=True,
        ),
        AcceptanceRule(
            metric="requirement_traceability", op="eq", value=True,
        ),
        AcceptanceRule(
            metric="gap_attribution", op="eq", value=True,
        ),
        AcceptanceRule(
            metric="next_requirement_seeded", op="eq", value=True,
        ),
    ],
    exceptions=[
        ExceptionRule(
            condition="compared_metric_count == 0", action="no_deliver",
        ),
        ExceptionRule(
            # 「有差距」且「还没把下一轮需求播种下去」→ 升级人工。
            # 字段名是 next_requirement_seeded（带 ed），类型是 boolean，
            # 所以比的是 == false 而不是 == ''。
            # 原写法 "gap_count > 0 and next_requirement_seed == ''" 三处都错：
            # 小写 and（evaluator 只认大写 AND）、字段名少个 ed、拿布尔比空串。
            # 它一直静默返 False——这条规则从来没响过。
            condition="gap_count > 0 AND next_requirement_seeded == false", action="escalate",
        ),
    ],
    artifact_view="generic",

    # ---- 作业路线（Procedure）----
    beeline_id='beeline_wms_analysis_v1',
    beeline_version=1,
    beeline_description=(
        "WMS Analysis Box 履约作业路线："
        "analysis_intake → analysis_compare → analysis_packager"
    ),

    # ---- 履约政策（Policy）----
    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            # **observe_only 不是省事**：这只盒子的腿还没实现，
            # 让它自己改进只会把「没做」优化成「做得更像」。
            continuous_improvement="observe_only",
        ),
        rules=QueenRules(
            task_routing={},
            exception_handling={
                "escalation_rules": [
                    {"trigger": "compared_metric_count == 0", "action": "escalate_to_human"},
                    {"trigger": "gap_count > 0 and next_requirement_seed == ''",
                     "action": "escalate_to_human"},
                ],
            },
        ),
    ),

    # ---- 履约度量（Metrics）----
    metrics=MetricsDef(),
)
