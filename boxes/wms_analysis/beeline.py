"""boxes.wms_analysis - WMS Analysis Box 的作业路线

WMS 线上效果分析盒——上线之后拿真实运行数据回头看：当初那条需求，
线上到底满足了没有。落差变成下一轮需求的种子。

op 链：
  1. `analysis_intake` — 取投料（需求预期 + ops 回写的线上实测）（schema_analysis_input → schema_analysis_comparison）
  2. `analysis_compare` — 逐项比对，标出落差（schema_analysis_comparison → schema_analysis_finding）
  3. `analysis_packager` — 打包成分析记录 + 下一轮需求种子（schema_analysis_finding → schema_analysis_record）

**这三个 op 现在还没有实现**（runner 里是桩）。beeline 是声明：它先把
要走的路线和每一步的数据形状钉住，腿才跟着路走。

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


# 同一任务重复执行必须产出同一份结果
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



WMS_ANALYSIS_BEELINE = Beeline(
    id="beeline_wms_analysis_v1",
    version=1,
    description=(
        "WMS Analysis Box 履约作业路线："
        "analysis_intake → analysis_compare → analysis_packager"
    ),

    operations=[
        Operation(
            op_id="analysis_intake",
            type="analysis_intake",
            input_from="external",
            input="schema_analysis_input",
            output="schema_analysis_comparison",
            next=[NextRef(op_id="analysis_compare")],
            bee=Bee(type="analysis_intake_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="analysis_compare",
            type="analysis_compare",
            input_from="analysis_intake",
            input="schema_analysis_comparison",
            output="schema_analysis_finding",
            next=[NextRef(op_id="analysis_packager")],
            bee=Bee(type="analysis_compare_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="analysis_packager",
            type="analysis_packager",
            input_from="analysis_compare",
            input="schema_analysis_finding",
            output="schema_analysis_record",
            next=[],
            bee=Bee(type="analysis_packager_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        )
    ],
)


def get_beeline() -> Beeline:
    return WMS_ANALYSIS_BEELINE
