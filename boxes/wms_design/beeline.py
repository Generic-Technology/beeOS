"""
boxes.wms_design.beeline - 业务设计履约作业路线（组件链）

op 链（7 跳）：
  1. `parse_requirements`         （校验结构 + 提取有效 id）
  2. `classify_requirements`      （按 type 分组：object / rule / process / metric / goal / event）
  3. `generate_design_elements`    （按分类生成 DDD 战术元素：entity / specification / domain_service / domain_event / domain_metric）
  4. `verify_coverage`            （检查 requirement_coverage）
  5. `check_consistency`          （rule_consistency + reference_integrity + structural_compliance）
  6. `generate_evidence`          （汇总 5 项 evidence）
  7. `package_design`             （合并成 BusinessDesignPackage）
  8.  （终态）

编排特征：
- 顺序链（无分支，每步必须完成才能进入下一步）
- 幂等性：所有 op = optional（详见下面 IDEMPOTENT_BY_SET 的注释）

**为什么现在是「组件链」而不是一份 `Beeline(...)`**：
盒子不再手写 `Operation` / `NextRef` / `Bee` 那套样板——
它只声明每一跳的**形状**（吃什么、吐什么、数据从哪来、下一步去哪），
由 `core/scaffold.py` 的装配器把它组装成作业路线**并当场校验**。

装出来的东西跟手写的那份**一模一样**（`tests/test_scaffold.py` 守着这条），
所以这次迁移不改行为，只改「谁写的」。

beeline 跟盒子是 **1:1 且不可分割的**：它引用的 schema id 全部来自本盒的
`schemas.py`，每个 op 的 handler 都在本盒的 `runner.py` 里。所以它住在盒子
里面，而不是仓库顶层一个单独的目录——顶层目录会让人以为存在一种
「可复用的跨盒作业路线」，而实际上一条都没有。

**注意 `check_consistency` 的数据来源**：
它的 `input_from` 是 `generate_design_elements`，而执行时喂给它的是
`verify_coverage` 的产出（两者形状相同，所以看不出差别）。
**`input_from`（声明的数据来源）和 `next`（执行顺序）是两件事**——
装配器分别校验这两条边，不合并。
"""

from __future__ import annotations

from core.beeline_models import Idempotency
from core.scaffold import Component


#: **幂等声明——逐 op 说的是真话**（2026-11-01 改）
#:
#: 以前这里是一个常量 `required, key=...` 套在**每一个** op 上。
#: 可「重复执行产生同结果」对多数 op 并不成立：
#: LLM 有采样（同一份 prompt 再问一次是另一个产出），
#: 有副作用的 op 更不成立（重放会再提交一次、再部署一次）。
#:
#: **为什么现在要紧**：runtime 开始拿这个声明**决定要不要重试**
#: （`idempotency.mode=forbidden` 的 op 不许重试）。
#: 声明不准 = 重试会重放有副作用的 op（开发盒会提交出第二个 commit）。
#:
#: **为什么是 optional 而不是 required**：
#: `required` 的语义是「runtime 用 key 去重」——
#: 跨履约的幂等台账（按 key 查历史产出）**还没做**，
#: 现在声明 required 就是「声明了却没人执行」。
#: 台账做出来之后，再把真正幂等的 op 升到 required。
IDEMPOTENT_BY_SET = Idempotency(mode="optional")
#: **不许重放**的 op 单独一个：它有副作用，重放会重复做事。
#: runtime 拿这个声明决定「传输类失败要不要整跳重试」。
#:
#: 本盒目前**还没用上它**（7 跳里没有动真实世界的那一步）。
#: 先留着：等真有副作用的 op 加进来，它就是现成的。
FORBIDDEN_REPLAY = Idempotency(mode="forbidden")


COMPONENTS: list[Component] = [
    Component(
        op_id="parse_requirements",
        type="requirement_parser",
        input_from="external",
        # **投料是 ref: 形状**（{requirement_set_ref: {...}}）：
        # 需求集从上游盒子的产出接过来，不在投料里内联。
        # 以前这里是 schema_business_requirement_set（内联），
        # 于是需求盒→设计盒这条通道**根本不存在**——
        # 看板拓扑上画着那条边，链子上却没有，每次都靠人手工拷。
        input="schema_design_task_input",
        output="schema_parsed_requirements",
        next=["classify_requirements"],
        bee_type="requirement_parser_impl",
        idempotency=IDEMPOTENT_BY_SET,
    ),

    Component(
        op_id="classify_requirements",
        type="requirement_classifier",
        input_from="parse_requirements",
        input="schema_parsed_requirements",
        output="schema_classified_requirements",
        next=["generate_design_elements"],
        bee_type="requirement_classifier_impl",
        idempotency=IDEMPOTENT_BY_SET,
    ),

    Component(
        op_id="generate_design_elements",
        type="design_generator",
        input_from="classify_requirements",
        input="schema_classified_requirements",
        output="schema_design_elements",
        next=["verify_coverage"],
        bee_type="design_generator_impl",
        idempotency=IDEMPOTENT_BY_SET,
    ),

    Component(
        op_id="verify_coverage",
        type="coverage_verifier",
        input_from="generate_design_elements",
        input="schema_design_elements",
        output="schema_design_elements",
        next=["check_consistency"],
        bee_type="coverage_verifier_impl",
        idempotency=IDEMPOTENT_BY_SET,
    ),

    Component(
        op_id="check_consistency",
        type="consistency_checker",
        input_from="generate_design_elements",
        input="schema_design_elements",
        output="schema_design_elements",
        next=["generate_evidence"],
        bee_type="consistency_checker_impl",
        idempotency=IDEMPOTENT_BY_SET,
    ),

    Component(
        op_id="generate_evidence",
        type="evidence_generator",
        input_from="check_consistency",
        input="schema_design_elements",
        output="schema_design_evidence",
        next=["package_design"],
        bee_type="evidence_generator_impl",
        idempotency=IDEMPOTENT_BY_SET,
    ),

    Component(
        op_id="package_design",
        type="design_packager",
        input_from="generate_evidence",
        input="schema_design_evidence",
        output="schema_business_design_package",
        next=[],
        bee_type="design_packager_impl",
        idempotency=IDEMPOTENT_BY_SET,
    ),
]
