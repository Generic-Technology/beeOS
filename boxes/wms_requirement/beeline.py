"""
boxes.wms_requirement - 需求捕获履约作业路线

Software Line 第二只 beeline。

业务流：
  input: 自然语言业务表述（BusinessNarrative）
    ↓
  split_source_sentences   （规则：按句末标点 + 顿号枚举切句，每句带 sentence_id）
    ↓
  extract_requirements     （LLM：抽流程图——节点 / 边 / 守卫；不可用则履约中止）
    ↓
  build_requirement_set    （机械：组装 set_id、编号、绑定 trace_to）
    ↓
  verify_graph_fidelity    （机械：4 项 acceptance——契约 / 覆盖 / 溯源 / 幻觉）
    ↓
  package_requirement_set  （机械：合并成 RequirementSetPackage）
    ↓
  （终态）

**为什么只有 1 个 op 走 LLM**：
"哪句是业务事实、它属于流程图里的哪个位置"需要语义理解；
其余全部是集合运算 / 字符串匹配 / 编号——用模型做只会更慢更不稳。
这条边界是本 beeline 最核心的设计决定。

编排特征：
- 顺序链（无分支）
- 幂等性：所有 op = required（key=set_id，同一份原文重复抽取产出同结果）

beeline 跟盒子是 **1:1 且不可分割** 的：它引用的 schema id 全部来自本盒的
`schemas.py`，每个 op 的 handler 都在本盒的 `runner.py` 里。所以它住在盒子
里面，而不是仓库顶层一个单独的目录——顶层目录会让人以为存在一种
「可复用的跨盒作业路线」，而实际上一条都没有。
"""

from __future__ import annotations

from core.beeline_models import (
    Bee,
    Beeline,
    Idempotency,
    NextRef,
    Operation,
)


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
FORBIDDEN_REPLAY = Idempotency(mode="forbidden")



WMS_REQUIREMENT_BEELINE = Beeline(
    id="beeline_wms_requirement_v1",
    version=1,
    description=(
        "需求捕获履约作业路线——把自然语言业务表述抽成可机械验证的"
        "结构化业务需求集（1 个 LLM op + 4 个机械 op）"
    ),

    operations=[
        Operation(
            op_id="split_source_sentences",
            type="source_sentence_splitter",
            input_from="external",
            input="schema_business_narrative",
            output="schema_extracted_facts",
            next=[NextRef(op_id="extract_requirements")],
            bee=Bee(type="source_sentence_splitter_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="extract_requirements",
            type="requirement_extractor",
            input_from="split_source_sentences",
            input="schema_extracted_facts",
            output="schema_extracted_facts",
            next=[NextRef(op_id="build_requirement_set")],
            bee=Bee(type="requirement_extractor_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="build_requirement_set",
            type="requirement_set_builder",
            input_from="extract_requirements",
            input="schema_extracted_facts",
            output="schema_extracted_facts",
            next=[NextRef(op_id="verify_graph_fidelity")],
            bee=Bee(type="requirement_set_builder_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="verify_graph_fidelity",
            type="graph_fidelity_verifier",
            input_from="build_requirement_set",
            input="schema_extracted_facts",
            output="schema_extracted_facts",
            next=[NextRef(op_id="package_requirement_set")],
            bee=Bee(type="graph_fidelity_verifier_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="package_requirement_set",
            type="requirement_set_packager",
            input_from="verify_graph_fidelity",
            input="schema_extracted_facts",
            output="schema_requirement_set_package",
            next=[],
            bee=Bee(type="requirement_set_packager_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),
    ],
)


def get_beeline() -> Beeline:
    return WMS_REQUIREMENT_BEELINE
