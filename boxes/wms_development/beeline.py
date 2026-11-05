"""boxes.wms_development - WMS Development Box 的作业路线

WMS 开发盒——**按设计包改代码**：读方案 → 定位源码 → 改 → 构建 → 制品。

op 链：
  1. `dev_intake`      —— 设计包 + 目标提交范围 → 开发计划（哪些预期 + 哪些源码位置）
                         （schema_dev_task_input → schema_dev_plan）
  2. `dev_code`        —— 在隔离工作区里按方案改代码、提交
                         （schema_dev_plan → schema_code_change）
  3. `dev_build`       —— 编译 + 仓库自带测试套件 + 构建两遍比摘要
                         （schema_code_change → schema_build_check）
  4. `build_packager`  —— 打包成制品 + 给出评审覆盖 / 可追溯判定
                         （schema_build_check → schema_build_artifact）

**为什么是四跳而不是一跳**：每一跳的答案来自**不同的外部神谕**——
模型（改动）、编译器（编译）、测试套件（单测）、git（评审覆盖与可追溯）。
把它们压成一跳，就没法说清「红在哪一步」。

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
        op_id="dev_intake",
        type="dev_intake",
        input_from="external",
        input="schema_dev_task_input",
        output="schema_dev_plan",
        next=['dev_code'],
        bee_type="dev_intake_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),

        # **不许重放**（forbidden）：这一跳会真的提交到 WMS 仓、并建一个交付分支。
        # 重放一次 = 两个提交、两个分支 —— 而「重复执行产生同结果」对它根本不成立。
        # **还会留下东西**（leaves_artifact）：提交和分支就在那儿。
    Component(
        op_id="dev_code",
        type="dev_code",
        input_from="dev_intake",
        input="schema_dev_plan",
        output="schema_code_change",
        next=['dev_build'],
        bee_type="dev_code_handler",
        idempotency=FORBIDDEN_REPLAY,
        leaves_artifact=True,
    ),

        # **会留下东西**（leaves_artifact）：构建失败时隔离工作区**不会被拆**
        #（拆工作区在下一跳的 finally 里），仓里会多一个 worktree。
    Component(
        op_id="dev_build",
        type="dev_build",
        input_from="dev_code",
        input="schema_code_change",
        output="schema_build_check",
        next=['build_packager'],
        bee_type="dev_build_handler",
        idempotency=IDEMPOTENT_BY_TASK,
        leaves_artifact=True,
    ),

        # **会留下东西**（leaves_artifact）：jar 和前端产物被复制进制品库
        #（制品库在仓外，不会被拆工作区带走）。
    Component(
        op_id="build_packager",
        type="build_packager",
        input_from="dev_build",
        input="schema_build_check",
        output="schema_build_artifact",
        next=[],
        bee_type="build_packager_handler",
        idempotency=IDEMPOTENT_BY_TASK,
        leaves_artifact=True,
    ),
]
