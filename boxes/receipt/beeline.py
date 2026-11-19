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
from runtime.watchdog import watchdog_spec


#: **这一跳的时间预算**（秒，含它内部的所有尝试）
#:
#: **为什么要有**：LLM 客户端自己管**一次调用**（单次 120s，传输层重试 3 次），
#: 但**整跳**（读图 + 修正，可能两轮）加上执行器的整跳重试，最坏能到几十分钟。
#: 预算是「不再开新的整跳重试」的闸——不是打断已经在跑的那次尝试
#: （同进程杀不掉线程，docs/component-plugin-model.md §4.6.9）。
#:
#: **300s 的来由**：一次 LLM 调用的最坏时间 = 120s × 4 次传输尝试 = 480s；
#: 预算取在它下面 → 「慢成这样就别整跳重试了」，
#: 而**快的失败照旧重试**（传输错误几秒就返回，预算根本用不到）。
READ_RECEIPT_BUDGET_SECONDS = 300

#: **看门狗**：整跳超过 180s 就当这张票没读出来
#:
#: **为什么要有**：一张小票读 3 分钟还没读出来，
#: 多半是照片不行或模型卡住了——**人可以去重拍**。
#: 与其把一个 3 分钟前的答案当新鲜的交下去，不如当场说「没读出来」。
#:
#: **只对没有副作用的跳有效**（看门狗自己看得见宿主声明，见 runtime/watchdog.py）。
READ_RECEIPT_WATCHDOG_SECONDS = 180


COMPONENTS: list[Component] = [
    Component(
        op_id="read_receipt",
        type="read_receipt",
        input_from="external",
        input="schema_receipt_photo",
        output="schema_receipt_parsed",
        next=["verify_receipt"],
        bee_type="receipt_reader",
        timeout_seconds=READ_RECEIPT_BUDGET_SECONDS,
        plugins=[watchdog_spec(READ_RECEIPT_WATCHDOG_SECONDS)],
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
