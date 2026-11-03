"""
boxes.receipt - 小票识别盒的履约流水线

三步，每步一个工位：
1. read_receipt     看图抄字（唯一真调 LLM 的一步）
2. verify_receipt   算术与格式自洽（纯机械）
3. package_receipt  合并成产出包（纯机械）

跟其他 beeline 的区别只有一处，但很关键：**LLM 只在第 1 步**。
校验和打包都不再碰模型，所以「模型为了好看把数字凑平」这条路是堵死的。

**为什么是「组件链」而不是一份 `Beeline(...)`**：
盒子不手写 `Operation` / `NextRef` / `Bee` 那套样板——
它只声明每一跳的**形状**（吃什么、吐什么、数据从哪来、下一步去哪），
由 `core/scaffold.py` 的装配器把它组装成作业路线**并当场校验**。

beeline 跟盒子是 **1:1 且不可分割** 的：它引用的 schema id 全部来自本盒的
`schemas.py`，每个 op 的 handler 都在本盒的 `runner.py` 里。所以它住在盒子
里面，而不是仓库顶层一个单独的目录——顶层目录会让人以为存在一种
「可复用的跨盒作业路线」，而实际上一条都没有。
"""

from __future__ import annotations

from core.scaffold import Component


COMPONENTS: list[Component] = [
    Component(
        op_id="read_receipt",
        type="read_receipt",
        input_from="external",
        input="schema_receipt_photo",
        output="schema_receipt_parsed",
        next=["verify_receipt"],
        bee_type="receipt_reader",
    ),
    Component(
        op_id="verify_receipt",
        type="verify_receipt",
        input_from="read_receipt",
        input="schema_receipt_parsed",
        output="schema_receipt_parsed",
        next=["package_receipt"],
        bee_type="arithmetic_checker",
    ),
    Component(
        op_id="package_receipt",
        type="package_receipt",
        input_from="verify_receipt",
        input="schema_receipt_parsed",
        output="schema_receipt_parsed",
        next=[],
        bee_type="receipt_packager",
    ),
]
