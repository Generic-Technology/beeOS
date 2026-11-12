"""
boxes.wms_design.definition - 业务设计履约盒的声明

Software Line 第一只盒子定义。

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + 8 条机械 acceptance + 5 条 exception）
- 履约政策 Policy（queen authorization + rules）
- 履约度量 Metrics（每个指标都有实测采集算法，见 measure.py）

设计哲学：
- acceptance 全部机械可验（8 条规则，0 条主观）
- exception 覆盖典型失败模式（需求超量 / 规则矛盾 / 无业务方 / 非 WMS 需求）
- queen 配置保守（continuous_improvement = observe_only）

**为什么现在是 `BoxDeclaration` 而不是 `BeeBoxDefinition`**：
盒子只声明**它是什么**，不负责把声明拼成运行时认识的那几样。
拼装（以及当场校验）由 `core/scaffold.py` 的装配器做——
声明自相矛盾的话，在装配那一步就报出来，而不是等到履约跑起来。

装出来的 `BeeBoxDefinition` 跟手写的那份**一模一样**
（`tests/test_scaffold.py` 守着这条），所以这次迁移不改行为，只改「谁写的」。
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    ExceptionRule,
    MetricDef,
    MetricsDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
)
from core.plugins import PluginSpec
from core.scaffold import BoxDeclaration


#: **升级规则的真源**（2026-11-12 从 `queen.rules` 搬到这里）
#:
#: 升级决策收编成盒级插件 `queen_escalation` 之后，
#: 规则住在**插件参数**里——`queen.rules` 那边不再有它。
ESCALATION_RULES = [
    {"trigger": "requirement_count > 1000", "action": "escalate_to_human"},
    # **原来这里还有一条 `rule_conflicts > 5`，删掉了**
    # （2026-11-01）：这个盒子**从来没算过规则冲突**——
    # `rule_conflicts` 恒是空数组，而且只存在于 evidence 里面，
    # 不在产出顶层。条件求值时字段根本取不到 →
    # 这条规则**一次都不会触发** = 一句空承诺。
    #
    # 「声明了却拿不到」比不声明更坏：读者以为规则在守着。
    # 等盒子真的算出冲突数（rule_consistency 现在还是 mock），
    # 再把这条加回来——那时候它才是真的。
]

DECLARATION = BoxDeclaration(
    id="wms_design_box",
    version=2,
    description=(
        "WMS 仓储设计履约盒——按结构化仓储需求产出可机械验证的 WMS 设计包："
        "WMS 领域实体（SKU / 货主 / 库位 / 库存 / 批次 / 波次 / 盘点）、"
        "WMS 业务约束（库位占用唯一 / 先进先出 / 安全库存 / 波次合并…）、"
        "WMS 运营指标，以及 7 项验证证据。\n\n"
        "**7 条判据里有 2 条是 WMS 专属的**（wms_binding / wms_rule_binding）："
        "前 5 条验「设计自洽」，这两条验「**这是不是一个 WMS 设计**」。"
        "少了它们，一个纯通用的「当库存 > 0 时…」照样五分之五全绿，"
        "而盒子挂着 WMS 的招牌。\n\n"
        "**已知盲区（如实声明）**：WMS 绑定是关键词机械匹配，认不出同义改写"
        "（「拣货」/「按订单取货」/「order picking」认作三个词）。"
        "认不出的需求会被 wms_binding 判红并点名，不会静默放过——"
        "宁可红，不要假装绑上了。"
    ),

    # ---- 归属：这只盒子在企业里站在哪 ----
    display_name="WMS Design Box",
    value_stream="Software Delivery",
    role="WMS Design Fulfillment",

    # ---- 投料入口（Intent）----
    task_type="handle_design_request",
    # **投料走 ref: 通道**：需求集是上游（需求盒）的产出，
    # 整个接过来，不在投料里内联一份。
    # 以前这里是 schema_business_requirement_set（内联），
    # 于是需求盒→设计盒这条通道**根本不存在**——
    # 看板拓扑上画着那条边，链子上却没有，每次都靠人手工拷。
    task_schema="schema_design_task_input",
    # 本次履约处理的业务对象标识（归档按它分组）
    #
    # 标识**藏在需求集对象里**——`ref:` 字段装的是整份上游产出，
    # 「这批设计对应哪批需求」的答案在它的 set_id 上。
    # 所以要指明从对象的哪个键取。
    subject_ref_field="requirement_set_ref",
    subject_ref_subfield="set_id",
    subject_kind="requirement",
    task_trigger=(
        "仓储主管提交结构化 WMS 需求集（上架 / 拣货 / 盘点 / 波次合并）\n"
        "OR 货主要求重新出 WMS 设计\n"
        "OR 已有 WMS 设计要扩展新需求\n"
        "OR 仓库换库位策略要重算设计"
    ),

    # ---- 产出契约（Contract）----
    result_type="business_design_produced",
    result_schema="schema_business_design_package",
    artifact_view="ddd_design",
    maturity="live",
    # **我的投料条数叫 requirement_count**（2026-11-01）：
    # runtime 的「投料是空的」归因以前写死了一个字段名——
    # 业务知识漏进内核。现在由盒子自己声明，runtime 按合同办事。
    empty_input_metric="requirement_count",
    acceptance=[
        AcceptanceRule(
            metric="requirement_coverage",
            op="gte",
            value=100,
        ),
        AcceptanceRule(
            metric="rule_consistency",
            op="eq",
            value=True,
        ),
        AcceptanceRule(
            metric="reference_integrity",
            op="eq",
            value=True,
        ),
        AcceptanceRule(
            metric="structural_compliance",
            op="eq",
            value=True,
        ),
        AcceptanceRule(
            metric="metrics_defined",
            op="eq",
            value=True,
            # 这批需求本来就没有指标时，产出是 n/a（不是 false），
            # runtime 会把它记进 not_applicable_rules——既不挡通过，
            # 也不谎称通过。纯主数据需求（「增加库区管理功能」）不该被指标判据拦下。
        ),
        # ---- WMS 域专属 ----
        #
        # 这两条验的不是「设计自洽」，是「**这是不是一个 WMS 设计**」。
        # 口径：分母是可设计需求，**要求 100% 落到 WMS 概念上**。
        #
        # 为什么卡 100 而不是「≥80% 算过」：这个盒子只服务 WMS 域，
        # 混进来的非 WMS 需求说明**投料错了**，不是「质量差一点」。
        # 判红的证据里会点名是哪几条（requirements_without_wms_binding），
        # 踢出去就行——比悄悄放过一条「用户登录要密码」强。
        AcceptanceRule(
            metric="wms_binding",
            op="eq",
            value=True,
        ),
        AcceptanceRule(
            metric="wms_rule_binding",
            op="eq",
            value=True,
        ),
        # ---- 预期载体 ----
        #
        # 「模型认出来的每个 WMS 概念，都要变成一条可判定的预期」。
        #
        # 这条不是重复 `wms_binding`：wms_binding 问的是
        # 「每条可设计需求都绑上了吗」，这条问的是
        # 「绑定有没有被带成预期」——中间隔了两跳，
        # 载体在中间被吃掉的话，wms_binding 照样绿，
        # 而下游（测试盒 / 分析盒）拿到的是**空的预期集**，
        # 于是「验证业务价值」静悄悄地退化成「什么都没验」。
        AcceptanceRule(
            metric="expectation_completeness",
            op="eq",
            value=True,
        ),
    ],
    exceptions=[
        # ---- 空需求集：明确不交付 ----
        #
        # 放在第一条，因为它比下面任何一条都**具体**
        # （例外匹配取第一条命中的）。
        #
        # 为什么要有这条（2026-11-01 实测）：
        # 投料里 0 条需求，盒子**照跑完了**，但判据全挂
        # （没有需求可绑、没覆盖率）。以前这里没有例外，
        # 于是走「没匹配到例外 = 没人管 = failed」——
        # 可这次履约**根本没跑挂**，它跑完了，只是**没东西可交**。
        # 写成 failed 是在说「跑砸了」，读者会去查代码，
        # 而真相是「合同说这种情况不交付」。
        #
        # 一句话：**「明确不交付」和「没人管」是两回事。**
        ExceptionRule(
            condition="requirement_count == 0",
            action="no_deliver",
        ),
        ExceptionRule(
            condition="requirement_count > 1000",
            action="escalate",
        ),
        ExceptionRule(
            condition="rule_consistency == false",
            action="rollback",
        ),
        ExceptionRule(
            condition="business_owner_assigned == false",
            action="no_deliver",
        ),
        # 投料里有非 WMS 需求：不是「设计失败」，是**投料错了**。
        # 送回去让业务方重新给需求，别拿通用需求硬凑一个 WMS 设计。
        ExceptionRule(
            condition="requirements_without_wms_binding > 0",
            # action 只有 escalate / rollback / no_deliver 三个值，
            # 没有「退回去改投料」。投料不对就是 escalate——送人。
            action="escalate",
        ),
    ],

    # ---- 作业路线（Procedure）----
    beeline_id="beeline_wms_design_v1",
    beeline_version=1,
    beeline_description=(
        "业务设计履约作业路线——按结构化业务需求产出可验证的业务设计包"
    ),

    # ---- 履约政策（Policy）----
    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            continuous_improvement="observe_only",  # 改善只观察
        ),
        rules=QueenRules(
            task_routing={
                "complexity_thresholds": {
                    "small": 50,    # < 50 需求直接设计
                    "medium": 200,  # 50-200 启用模板
                    "large": 1000,  # 200-1000 启用协作设计
                    # > 1000 触发 escalate
                },
            },
            exception_handling={
                # **无条件的策略声明**——runtime 不参与决策（它不知道
                # 「什么时候用」：没有条件）。它们描述的是**这只盒子自己的行为**
                # （追不到 trace 就自己推断、分类拿不准就归最像的那类）。
                #
                # **注意**：这里说「规则矛盾 → 送人看」，
                # 而下面的**例外**写的是「`rule_consistency == false` → 回滚」。
                # 同一件事两种说法——**这是盒子的业务决定，不是 runtime 该裁的**。
                # 现状：回滚补偿还没做，所以那条路暂时落「不动」，
                # 送人看的那条由升级默认口径兜住（升级 = 交给人）。
                "rule_conflict_resolution": "flag_for_human_review",
                "missing_trace_target": "auto_infer_from_description",
                "ambiguous_classification": "assign_to_most_likely_type",
            },
            continuous_improvement={
                "monitor_metrics": [
                    "first_pass_acceptance_rate",
                    "requirement_coverage_quality",
                    "design_reuse_rate",
                    "design_turnaround_time",
                ],
            },
        ),
    ),

    # ---- 履约度量（Metrics）----
    metrics=MetricsDef(
        quality=[
            MetricDef(
                name="first_pass_acceptance_rate",
                definition="首次设计即通过 acceptance 的比例",
                target=90.0,
                unit="pct",
            ),
            MetricDef(
                name="requirement_coverage_quality",
                definition="requirement_coverage == 100 的比例",
                target=95.0,
                unit="pct",
            ),
        ],
        latency=[
            MetricDef(
                name="design_turnaround_time",
                # 如实标注：这个盒子的 beeline 目前走 runtime/mock_runner，
                # 测到的是 mock 执行耗时，不是真实设计耗时。
                # 不这么写，看板上会显示一个 0.0s 的「设计时长」，读起来像设计极快。
                definition="从需求提交到设计包产出时长（当前设计腿走 mock runner，"
                           "此值是 mock 执行耗时，不等于真实设计耗时）",
                target=3600.0,  # 1 hour
                unit="s",
                direction="lower_is_better",
            ),
            MetricDef(
                name="p95_design_turnaround_time",
                definition="95 分位设计时长（同上，mock 执行耗时）",
                target=7200.0,  # 2 hour
                unit="s",
                direction="lower_is_better",
            ),
        ],
        # cost 暂时空着：没有成本记账就不声明成本指标，
        # 等于在看板上挂一个永远读不出数的槽。记账做起来再加回来。
    ),
    # ---- 升级决策插件（盒级）----
    #
    # **升级规则搬到这里了**（2026-11-12）：升级决策收编成盒级插件，
    # 规则的**唯一真源**是插件参数——
    # `queen.rules.exception_handling` 那边不再有它（那边的键已退休）。
    plugins=[
        PluginSpec(
            id="queen_escalation",
            extension_point="box.escalate",
            params={"escalation_rules": ESCALATION_RULES},
        ),
    ],
)
