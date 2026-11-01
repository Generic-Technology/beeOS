"""
runtime.executor - 进程内 beeline executor

按 [beebox-design.md §3.4.3 + §3.4.4] 实现：
- 起点 operation（input_from=external）
- 顺序 / 并发 / 分支推进
- when 条件评估
- 终态判断

PoC 2 范围：
- 顺序 + 分支（按 when 评估）
- 并发（fan-out）支持但不实现 fan-in（统一在终态或下一 op）
- 汇聚（fan-in）支持单输入情况
- COMPENSATING 状态下一轮（等反向 op 的设计定下来）

执行流程：
1. create_task_run → status=triggered
2. executor.execute(task_run, beeline, input) → 转移 status=running
3. 找 start op（input_from=external）
4. 循环：
   a. 执行 current_op（mock runner）
   b. 记录 op output
   c. 选 next op（按 when）
   d. 没 next → 退出循环
5. 转移 status=completed
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from core.beeline_models import Beeline, NextRef, Operation
from runtime.llm import LLMError, LLMParseError
from runtime.models import OperationRecord, TaskRun, TaskRunStatus
from runtime.runner import HandlerRunner


#: 同一个 op 最多试几次（**含**第一次）
#:
#: **为什么是 3**：LLM 客户端自己已经做了 3 次传输级重试
#: （`runtime/llm.py` 的指数退避），能走到这里的已经是
#: 「传输层救不回来」的了。再给 2 次**整跳**的机会，是给
#: 「上游刚好在重启」这种长窗口故障留的余量。
#:
#: **上限必须有**：没有上限的重试 = 一条永远跑不完的履约，
#: 而且看板上看它一直停在「重试中」，人也不知道它到底还活着没有。
MAX_OP_ATTEMPTS = 3


def _completed_ops(task_run: TaskRun) -> list[OperationRecord]:
    """已经成功完成的 op"""
    return [r for r in task_run.operations.values() if r.status == "completed"]


def _end_unfinished(
    task_run: TaskRun, beeline: Beeline, reason: str,
) -> None:
    """链没走完 → **失败** 还是 **部分完成**？

    **「有东西留在现场」和「什么都没发生」必须分开**（PARTIALLY_COMPLETED）：
    开发盒的第 2 跳提交了代码、第 3 跳构建没过 → 这次履约**留下了东西**
    （一个 commit、一个分支）。只记 failed 的话，读者看到的是「跑挂了」，
    不会想到「仓里还躺着一个提交」——而那正是补偿动作要处理的现场。

    **判据不是「跑完了几跳」，是「哪几跳动了真实世界」**：
    需求盒的第 1 跳（把句子切碎）也是成功完成的，可它什么都没留下；
    开发盒的第 2 跳（提交代码）也是成功完成的，可它留下了一个提交。
    runtime 分不出这两者——**只有盒子知道**（`Operation.leaves_artifact`）。

    所以这里问的是：**有没有哪一跳声明了「我留下了东西」，而且它成功了**。
    """
    left = [
        r for r in _completed_ops(task_run)
        if _leaves_artifact(beeline, r.op_id)
    ]
    if left:
        task_run.transition_to(
            TaskRunStatus.PARTIALLY_COMPLETED,
            reason=(
                f"{reason}；但 {'、'.join(r.op_id for r in left)} "
                "已经留下了东西（现场有残留）"
            ),
        )
    else:
        task_run.transition_to(TaskRunStatus.FAILED, reason=reason)


def _leaves_artifact(beeline: Beeline, op_id: str) -> bool:
    """这一跳声明了「成功后会留下东西」吗"""
    op = beeline.get_operation(op_id)
    return bool(op is not None and op.leaves_artifact)


def _classify_error(exc: Exception) -> str:
    """这次失败是哪一类（llm_parse / llm_call / op_error）"""
    if isinstance(exc, LLMParseError):
        return "llm_parse"
    if isinstance(exc, LLMError):
        return "llm_call"
    return "op_error"


def _retryable(op: Operation, error_kind: str, attempts: int) -> bool:
    """这次失败该不该**重试整跳**

    **只有传输类失败才自动重试**（`llm_call`：网络 / 超时 / 5xx）：
    - `llm_parse`（模型不听话）：同一个 prompt 再问一次大概率还是那样
      → 不重试。盒子自己的循环会带着**反馈**重新问，那才有用。
    - `op_error`（盒子逻辑错）：重试修不好逻辑 bug → 不重试。

    **op 自己说了算**：声明了 `idempotency.mode=forbidden` 的 op 不许重试——
    它有副作用，重放会重复做事（开发盒的 `dev_code` 会再提交一个 commit，
    运维盒的部署会再放一次行）。这条声明以前是**摆设**
    （8 条 beeline 一个常量套到底），现在它**决定要不要重试**——
    所以它必须逐 op 说真话。

    **不做跨履约的去重**：`required` 的「重复执行产生同结果」是**盒子自己的承诺**，
    runtime 只管「能不能重试、重试几次、每次都落盘」。
    跨履约的幂等台账（按 key 查历史产出）还没做——
    做了再说，别先把声明写成 runtime 已经保证的样子。
    """
    if error_kind != "llm_call":
        return False
    if op.idempotency is not None and op.idempotency.mode == "forbidden":
        return False
    return attempts < MAX_OP_ATTEMPTS


# 解析 when 条件为可执行函数（极简版）
def _evaluate_when(when: Optional[str], op_output: dict[str, Any]) -> bool:
    """评估 when 条件

    支持：
    - None → True（无条件）
    - "resolution_type == fulfilled" → 等值
    - "resolution_type in [fulfilled, partial_fulfilled]" → in
    - "eta_acceptable == true" → 布尔
    """
    if when is None:
        return True

    cond = when.strip()

    # in [...] 语法
    if " in [" in cond:
        field = cond.split(" in ")[0].strip()
        values_str = cond.split(" in [")[1].rstrip("]")
        values = [v.strip().strip('"').strip("'") for v in values_str.split(",")]
        return op_output.get(field) in values

    # 单等值
    if "==" in cond:
        field, value = cond.split("==", 1)
        field = field.strip()
        value = value.strip()
        # 处理 true/false
        if value == "true":
            value = True
        elif value == "false":
            value = False
        elif value.isdigit():
            value = int(value)
        return op_output.get(field) == value

    raise ValueError(f"unsupported when clause: {when!r}")


def _pick_next(op: Operation, op_output: dict[str, Any]) -> Optional[NextRef]:
    """从 op.next 中选一个 next

    规则：
    - 多个 next 带 when：取第一个 when 匹配的（互斥）
    - 多个 next：取第一个 when=None 的（默认）
    - 单个 next：返回它
    """
    if not op.next:
        return None

    # 第一遍：找带 when 匹配的
    for nxt in op.next:
        if nxt.when is not None:
            if _evaluate_when(nxt.when, op_output):
                return nxt

    # 第二遍：找无条件默认
    for nxt in op.next:
        if nxt.when is None:
            return nxt

    # 没匹配上：返回 None（断路）
    return None


class BeelineExecutor:
    """进程内 beeline executor

    用法：
        # runner 由盒子自己提供（boxes/*/runner.py 里的 RUNNER）
        executor = BeelineExecutor(runner=some_box.RUNNER)
        task_run = executor.execute(task_run, beeline, input_data)

    runner 是**必填**且必须由调用方（编排层）按 box_id 取。
    内核不兜底一张「什么都认识」的表：以前它兜底过，那张表里塞了
    三只盒子的业务实现，于是内核比任何一只盒子都懂业务，
    加盒子必须改内核。兜底掉的代价是盒子带不走自己的实现。
    """

    def __init__(self, runner: HandlerRunner) -> None:
        if runner is None:
            raise ValueError(
                "BeelineExecutor requires a runner from the box "
                "(boxes/*/runner.py RUNNER); the kernel no longer "
                "falls back to a shared handler table"
            )
        self.runner = runner

    def execute(
        self,
        task_run: TaskRun,
        beeline: Beeline,
        input_data: dict[str, Any],
    ) -> TaskRun:
        """执行 beeline（同步，进程内）

        Args:
            task_run: 已创建的 task run（status=triggered）
            beeline: 要执行的 beeline
            input_data: task 输入（按 task_schema 形状）

        Returns:
            更新后的 task run（status=running → completed/failed）

        Raises:
            ValueError: beeline 完整性错误
            NotImplementedError: op type 未注册
        """
        # triggered -> running
        task_run.transition_to(TaskRunStatus.RUNNING, reason="executor picked up")
        task_run.identity.started_at = datetime.now(timezone.utc)
        task_run.current_op = None

        # 找起点 op
        starts = beeline.get_start_operations()
        if not starts:
            task_run.transition_to(TaskRunStatus.FAILED, reason="no start operation")
            return task_run
        if len(starts) > 1:
            task_run.transition_to(TaskRunStatus.FAILED, reason=f"multiple start ops: {[o.op_id for o in starts]}")
            return task_run

        current_op: Operation = starts[0]
        # task input 作为起点 input
        op_input: dict[str, Any] = dict(input_data)

        # 沿 beeline 推进
        # 安全网（防意外的环）——**要给重试留出步数**：
        # 每一跳最多试 MAX_OP_ATTEMPTS 次，所以上限按「跳数 × 尝试次数」算。
        # 少了这个 ×，一个 4 跳的链在第 3 跳重试时就会撞上限
        # → 「重试」被「步数上限」判死，机制自己把机制掐了。
        max_steps = len(beeline.operations) * (MAX_OP_ATTEMPTS + 1)
        step_count = 0

        try:
            while current_op is not None:
                step_count += 1
                if step_count > max_steps:
                    _end_unfinished(task_run, beeline, reason="step limit exceeded")
                    return task_run

                task_run.current_op = current_op.op_id
                task_run.event_log.append(event="op_start", op_id=current_op.op_id)

                # 记录 op 启动（**重试时沿用上一条记录**，只加 attempts）
                previous = task_run.operations.get(current_op.op_id)
                record = OperationRecord(
                    op_id=current_op.op_id,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    input=op_input,
                    attempts=(previous.attempts + 1) if previous is not None else 1,
                )
                task_run.operations[current_op.op_id] = record

                try:
                    op_output = self.runner.run(current_op.type, op_input)
                except Exception as e:
                    record.status = "failed"
                    record.finished_at = datetime.now(timezone.utc)
                    record.error = str(e)
                    # 中止归因：LLM 是基础设施设施，runtime 认得它；盒子据此统计
                    # 自己的调用失败率。判据不是业务知识，所以这层归类不算越界。
                    # **三态归因，别把两件事混成一个**：
                    # - llm_parse：模型答了但格式不对（提示词/约束的问题）
                    # - llm_call：网络/凭证/服务端挂了（基础设施的问题）
                    # - op_error：跟 LLM 无关，盒子的逻辑错了
                    # 混成一个的后果：盒子的 llm_call_failure_rate 指标
                    # 既反映不了网络稳定性，也反映不了模型服从度——
                    # 一个数同时坏在两个方向上。
                    error_kind = _classify_error(e)
                    record.error_kind = error_kind
                    task_run.event_log.append(
                        event="op_finish",
                        op_id=current_op.op_id,
                        detail={"status": "failed", "error": str(e), "error_kind": error_kind},
                    )

                    # ---- 重试（RETRYING）----
                    #
                    # **重试的是同一条腿、同一个模型、同一份 prompt**——
                    # 不是降级（不换模型、不换数据、不跳过这一跳）。
                    #
                    # 走 RETRYING 再回 RUNNING，是为了让「这次在重试」
                    # 在履约层**看得见**：光在 op 里闷头重试，
                    # 看板上看它一直停在 running，人不知道它是在动还是死了。
                    if _retryable(current_op, error_kind, record.attempts):
                        task_run.transition_to(
                            TaskRunStatus.RETRYING,
                            reason=(
                                f"op '{current_op.op_id}' 第 {record.attempts} 次"
                                f"传输失败（{error_kind}），准备重试"
                            ),
                        )
                        task_run.event_log.append(
                            event="op_retry",
                            op_id=current_op.op_id,
                            detail={
                                "attempt": record.attempts + 1,
                                "error_kind": error_kind,
                                "error": str(e),
                                "max_attempts": MAX_OP_ATTEMPTS,
                            },
                        )
                        task_run.transition_to(
                            TaskRunStatus.RUNNING,
                            reason=f"retry op '{current_op.op_id}'",
                        )
                        # **重跑同一个 op，input 不变**
                        continue

                    # ---- 走不下去：失败 / 部分完成 ----
                    _end_unfinished(
                        task_run, beeline,
                        reason=f"op '{current_op.op_id}' failed: {e}",
                    )
                    return task_run

                # op 成功
                record.status = "completed"
                record.finished_at = datetime.now(timezone.utc)
                record.output = op_output
                task_run.event_log.append(
                    event="op_finish",
                    op_id=current_op.op_id,
                    detail={"status": "completed"},
                )

                # 选 next
                next_ref = _pick_next(current_op, op_output)
                if next_ref is None:
                    # 终态
                    task_run.result = op_output  # 最终结果作为 task run result
                    task_run.current_op = None
                    break

                next_op = beeline.get_operation(next_ref.op_id)
                if next_op is None:
                    _end_unfinished(
                        task_run, beeline,
                        reason=f"next op '{next_ref.op_id}' not found",
                    )
                    return task_run

                # 下个 op 的 input = 当前 op 的 output（按 input_from 链）
                op_input = dict(op_output)
                current_op = next_op

            # running -> completed
            if task_run.status == TaskRunStatus.RUNNING:
                task_run.transition_to(TaskRunStatus.COMPLETED, reason="beeline finished")


        except Exception as e:
            # ============================================================
            # **兜底：任何异常路径都必须落成一条 failed 记录**
            # ============================================================
            #
            # **为什么需要这段**（2026-10-30 调研实测）：
            # 上面的 try 只包着 `runner.run(op)`。但整条链上还有几处会抛：
            # - `_pick_next` 对不支持的 when 子句 raise ValueError
            # - `op_input = dict(op_output)` —— op 输出不是 dict 就 TypeError
            # - `transition_to` 非法转移
            # 这些异常冒到 kanban/trigger.py，**直接跳过 store.append** →
            # **这次履约在看板/store 里根本不存在**。
            #
            # 「失败但查无此事」是最贵的一种故障：连「它挂了」都看不到，
            # 事后无从判断是哪一环、为什么。
            #
            # **这不是降级**：状态照旧是 failed，结论照旧是红。
            # 变的只是「它现在能被查到了」。
            reason = f"履约异常中断（{type(e).__name__}）：{e}"
            rec = None
            if current_op is not None:
                rec = task_run.operations.get(current_op.op_id)
            if rec is not None and rec.status == "running":
                # 挂在还在跑的那个 op 头上 —— 归因要落到具体环节
                rec.status = "failed"
                rec.finished_at = datetime.now(timezone.utc)
                rec.error = reason
                rec.error_kind = "op_error"
                task_run.event_log.append(
                    event="op_finish",
                    op_id=rec.op_id,
                    detail={"status": "failed", "error": reason,
                            "error_kind": "op_error"},
                )
            try:
                _end_unfinished(task_run, beeline, reason=reason)
            except Exception:
                # 连状态转移都非法（例：已经 terminal 了）→
                # 强行落状态 + 事件，绝不让记录蒸发
                task_run.status = TaskRunStatus.FAILED
                task_run.event_log.append(
                    event="status_change", detail={"reason": reason},
                )

        return task_run