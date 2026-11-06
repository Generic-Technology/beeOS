"""domains.wms.domain —— WMS 域的声明

**这里是「WMS 域里这些盒子怎么连」的唯一事实源。**

## 边是**完整的数据依赖图**，不是「盒子上写的那几行」

旧协议里，每只盒子自己写着「我投给谁」——那只声明了 5 条交接，
但真实链路（`scripts/run_chain.py` 实跑）有 **8 条**：
另外 3 条（需求→测试、设计→测试、开发→运维）**一直藏在脚本里**。
照搬那 5 条，这 3 条会继续藏着，而这里号称是唯一事实源。

## 归档边没有通道，而且它**不是交接**

`wms_system_box` 是**系统档案盒**——它去线上读那套 WMS，写一份「真实在跑的是什么」
的档案。它**不收上游产出**（投料里 0 个 `ref:` 通道）。

所以那 5 条 archive 边表达的是「**这一环的产物进 WMS 档案**」——
**它没有运行时动作**（真归档由 `runtime/store.py` 的 `subject_ref` 机制负责），
它的作用是让域图有个中心。

> **规则读数要这样读**：`all_archived 5/5` 证明的是**五只盒子都写了归档归属声明**，
> **不证明**产物进了档案。声明层与事实层分开看（`docs/domain-declaration.md` §1.7）。
"""

from __future__ import annotations

from core.models import (
    FulfillmentScopeDeclaration,
    Edge,
    EdgeKind,
    Rule,
    RuleExemption,
    RuleKind,
)

SCOPE = FulfillmentScopeDeclaration(
    id="WMS",
    display_name="WMS 仓储",

    boxes=[
        "wms_requirement_box",
        "wms_design_box",
        "wms_development_box",
        "wms_testing_box",
        "wms_ops_box",
        "wms_analysis_box",
        "wms_system_box",
    ],

    edges=[
        # ---- 交接边：产出真的流进下游的投料口（8 条）----
        Edge(from_box="wms_requirement_box", to_box="wms_design_box",
             kind=EdgeKind.handoff, via="requirement_set_ref"),
        Edge(from_box="wms_design_box", to_box="wms_development_box",
             kind=EdgeKind.handoff, via="design_package_ref"),
        Edge(from_box="wms_development_box", to_box="wms_testing_box",
             kind=EdgeKind.handoff, via="artifact_ref"),
        # ↓ 这两条**原来只存在于 scripts/run_chain.py 里**，
        #   盒子上写的接线声明根本没提。
        Edge(from_box="wms_requirement_box", to_box="wms_testing_box",
             kind=EdgeKind.handoff, via="requirement_ref"),
        Edge(from_box="wms_design_box", to_box="wms_testing_box",
             kind=EdgeKind.handoff, via="expectation_ref"),
        Edge(from_box="wms_testing_box", to_box="wms_ops_box",
             kind=EdgeKind.handoff, via="verification_ref"),
        Edge(from_box="wms_development_box", to_box="wms_ops_box",
             kind=EdgeKind.handoff, via="artifact_ref"),
        Edge(from_box="wms_ops_box", to_box="wms_analysis_box",
             kind=EdgeKind.handoff, via="observation_ref"),

        # ---- 归档边：产物进系统档案（**没有通道**，档案盒不收上游产出）----
        Edge(from_box="wms_design_box", to_box="wms_system_box",
             kind=EdgeKind.archive),
        Edge(from_box="wms_development_box", to_box="wms_system_box",
             kind=EdgeKind.archive),
        Edge(from_box="wms_testing_box", to_box="wms_system_box",
             kind=EdgeKind.archive),
        Edge(from_box="wms_ops_box", to_box="wms_system_box",
             kind=EdgeKind.archive),
        Edge(from_box="wms_analysis_box", to_box="wms_system_box",
             kind=EdgeKind.archive),

        # ---- 闭环边：分析结论回到需求口，开新一轮 ----
        # 它**不是**数据交接（上游不产「新一轮需求」这份东西），
        # 所以显式声明没有通道。
        Edge(from_box="wms_analysis_box", to_box="wms_requirement_box",
             kind=EdgeKind.feedback, no_channel=True),
    ],

    rules=[
        Rule(
            kind=RuleKind.all_archived,
            exemptions=[
                RuleExemption(
                    box_id="wms_requirement_box",
                    # 本域的过程产物都沉淀进系统档案。
                    # 需求盒是**不得**归档的：它产出的是系统档案的**输入**
                    # （读基线：这份系统该是什么样），不是往档案里加东西——
                    # 声明它「投给」档案盒，等于说档案是需求盒的下游，
                    # 而真实关系正好相反。
                    reason="它产出的是系统档案的输入（读基线），不是往档案里加东西",
                    # **must，不是 may**：业务要求是「不得归档」。
                    # 写成 may 拦不住有人给它补一条归档边。
                    mode="must",
                ),
            ],
        ),
        Rule(kind=RuleKind.all_handoff),
        Rule(kind=RuleKind.each_handoff_has_channel),
    ],
)
