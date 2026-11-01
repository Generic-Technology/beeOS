"""boxes.wms_ops - WMS Ops Box 的作业路线

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、以及把线上真实状态回写进系统档案。

op 链：
  1. `ops_intake` — 接收运维任务（变更 / 告警 / 回滚）（schema_ops_task_input → schema_ops_handling）
  2. `ops_handle` — 执行处置 + 回写线上真实状态（schema_ops_handling → schema_ops_verdict）
  3. `ops_packager` — 打包成运维记录（schema_ops_verdict → schema_ops_record）

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



WMS_OPS_BEELINE = Beeline(
    id="beeline_wms_ops_v1",
    version=1,
    description=(
        "WMS Ops Box 履约作业路线：ops_intake → ops_handle → ops_packager"
    ),

    operations=[
        Operation(
            op_id="ops_intake",
            type="ops_intake",
            input_from="external",
            input="schema_ops_task_input",
            output="schema_ops_handling",
            next=[NextRef(op_id="ops_handle")],
            bee=Bee(type="ops_intake_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="ops_handle",
            type="ops_handle",
            input_from="ops_intake",
            input="schema_ops_handling",
            output="schema_ops_verdict",
            next=[NextRef(op_id="ops_packager")],
            bee=Bee(type="ops_handle_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="ops_packager",
            type="ops_packager",
            input_from="ops_handle",
            input="schema_ops_verdict",
            output="schema_ops_record",
            next=[],
            bee=Bee(type="ops_packager_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        )
    ],
)


def get_beeline() -> Beeline:
    return WMS_OPS_BEELINE
