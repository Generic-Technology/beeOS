"""boxes.wms_testing - WMS Testing Box 的作业路线

WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。

op 链：
  1. `test_intake` — 接收测试任务（制品 + 需求 + 场景）（schema_test_task_input → schema_test_execution）
  2. `test_execute` — 跑场景 + 统计覆盖率与缺陷（schema_test_execution → schema_test_verdict）
  3. `test_packager` — 打包成测试报告（schema_test_verdict → schema_test_report）

**这三个 op 现在还没有实现**（runner 里是桩）。beeline 是声明：它先把
要走的路线和每一步的数据形状钉住，腿才跟着路走。

beeline 跟盒子是 **1:1 且不可分割** 的：它引用的 schema id 全部来自本盒的
`schemas.py`，每个 op 的 handler 都在本盒的 `runner.py` 里。所以它住在盒子
里面，而不是仓库顶层一个单独的目录——顶层目录会让人以为存在一种
「可复用的跨盒作业路线」，而实际上一条都没有。
**为什么是「组件链」而不是一份 `Beeline(...)`**：
盒子不手写 `Operation` / `NextRef` / `Bee` 那套样板——
它只声明每一跳的**形状**（吃什么、吐什么、数据从哪来、下一步去哪），
由 `core/scaffold.py` 的装配器把它组装成作业路线**并当场校验**。
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
IDEMPOTENT_BY_TASK = Idempotency(mode="optional")
#: **不许重放**的 op 单独一个：它有副作用，重放会重复做事。
#: runtime 拿这个声明决定「传输类失败要不要整跳重试」。
FORBIDDEN_REPLAY = Idempotency(mode="forbidden")


COMPONENTS: list[Component] = [
    Component(
        op_id="test_intake",
        type="test_intake",
        input_from="external",
        input="schema_test_task_input",
        output="schema_test_execution",
        next=['test_execute'],
        bee_type="test_intake_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),

        # **不许重放**（forbidden）：这一跳会往验证实例里**造数据**
        #（库区、库位…）再清掉。重放一次 = 再造一套，
        # 而上一套要是没清干净（正是失败的时候），两套都留在那儿。
        # **会留下东西**（leaves_artifact）：造出来的数据就是残留。
    Component(
        op_id="test_execute",
        type="test_execute",
        input_from="test_intake",
        input="schema_test_execution",
        output="schema_test_verdict",
        next=['test_packager'],
        bee_type="test_execute_handler",
        idempotency=FORBIDDEN_REPLAY,
        leaves_artifact=True,
    ),

    Component(
        op_id="test_packager",
        type="test_packager",
        input_from="test_execute",
        input="schema_test_verdict",
        output="schema_test_report",
        next=[],
        bee_type="test_packager_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
]
