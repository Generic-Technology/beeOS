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

import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from core.beeline_models import Beeline, NextRef, Operation
from runtime.llm import LLMError, LLMParseError
from core.plugins import PluginContext
from runtime.models import OperationRecord, TaskRun, TaskRunStatus
from runtime.plugins import (
    EXTENSION_OP_AFTER,
    EXTENSION_OP_AROUND,
    EXTENSION_OP_BEFORE,
    PluginFailure,
    PluginRejected,
    abort_on_plugin_failure,
    call_around_plugins,
    call_plugins,
    merge_patch,
    verdict_problems,
)
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


def _any_leaves_artifact(task_run: TaskRun, beeline: Beeline) -> bool:
    """已经跑完的跳里，有没有哪一跳声明了「留下了东西」

    **判的是声明，不是事实**（与 `_end_unfinished` 同一口径）。
    """
    return any(
        _leaves_artifact(beeline, r.op_id) for r in _completed_ops(task_run)
    )


def _budget_exhausted(deadline: Optional[float]) -> bool:
    """这一跳的时间预算用完了吗

    **用单调钟**（`time.monotonic`）：墙钟会被 NTP 校正跳一下，
    那种跳会让「还剩 3 秒」突然变成「还剩 -2 小时」。
    """
    if deadline is None:
        return False
    return time.monotonic() >= deadline


def _note_budget_exceeded(
    task_run: TaskRun,
    op: Operation,
    call_started: float,
    deadline: Optional[float],
) -> None:
    """预算被突破、但这一跳**成功了** → 接受产出，但要**看得见**

    **为什么不判失败**：这一跳可能已经产生了副作用（提交、部署、落库）。
    说它「没成」而实际上它成了 → 下游重跑它 → **副作用做两遍**。
    「跑得比说的久」是**要看得见的事实**，不是「没成」的证据。
    """
    if deadline is None:
        return
    elapsed = time.monotonic() - call_started
    if elapsed <= (deadline - call_started):
        return
    task_run.event_log.append(
        event="op_budget_exceeded",
        op_id=op.op_id,
        detail={
            "budget_seconds": op.timeout_seconds,
            "elapsed_seconds": round(elapsed, 3),
        },
    )


class _OpCallFailed(Exception):
    """这一跳没成——**内部信号**，不是给插件看的

    **为什么需要它**：around 链把执行包起来之后，
    「这一跳失败」不能再用 `return` 表达了——
    `return` 会从 `execute()` 直接出去，把外层的包装绕过去。
    所以失败要**抛出来**，由最外层统一落状态。

    **插件看到的是它**：它的 `str()` 里带着原来的错误消息，
    而 `__cause__` 是原始异常（插件要分类错误时看那个）。
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


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

    def __init__(
        self,
        runner: HandlerRunner,
        plugin_context: Optional[PluginContext] = None,
    ) -> None:
        if runner is None:
            raise ValueError(
                "BeelineExecutor requires a runner from the box "
                "(boxes/*/runner.py RUNNER); the kernel no longer "
                "falls back to a shared handler table"
            )
        self.runner = runner
        # **插件上下文（装配时冻结的快照）**——
        # 没有它就等于没有插件：op 上的插件声明不会被调用。
        # 「装配期验过的那份 = 运行时跑的那份」✓
        self.plugin_context = plugin_context

    def _run_op_hooks(
        self,
        task_run: TaskRun,
        op: Operation,
        point_id: str,
        view: dict[str, Any],
        *,
        granularity: str,
    ) -> list[Any]:
        """执行挂在某个 op 上的钩子插件

        **粒度对不上就不调**——钩子自己声明了它是 `per_call` 还是 `per_attempt` ✓
        （重试几次都只跑一遍 vs 每试一次都跑 ✓）。

        **verdict 类钩子有问题 = 拒绝** → 抛 `PluginRejected`，
        由调用方决定这一跳还跑不跑（准入拒绝 → 整跳中止 ✓）。
        """
        if self.plugin_context is None:
            return []
        point = self.plugin_context.extension_points.get(point_id)
        if point is None or point.granularity != granularity:
            return []
        bindings = [b for b in op.plugins if b.extension_point == point_id]
        if not bindings:
            return []
        results = call_plugins(
            self.plugin_context,
            bindings,
            extension_point=point_id,
            source=view,
        )
        if point.return_contract == "verdict":
            problems = verdict_problems(results)
            if problems:
                raise PluginRejected("；".join(problems))
        return results

    def _run_around(
        self,
        op: Operation,
        source: dict[str, Any],
        execute: Callable[[], Any],
    ) -> Any:
        """把这一跳的执行包在 around 插件链里

        **没有 around 插件时**，直接执行——
        「没挂插件」不该让这一跳多走一层（那会让 op_trace 变样）。

        **包住的是整次调用**（含它内部的所有尝试）：
        包裹层看到的是「这一跳总共跑了多久」，不是「一次尝试多久」。
        """
        if self.plugin_context is None:
            return execute()
        bindings = [
            b for b in op.plugins if b.extension_point == EXTENSION_OP_AROUND
        ]
        if not bindings:
            return execute()
        return call_around_plugins(
            self.plugin_context,
            bindings,
            extension_point=EXTENSION_OP_AROUND,
            source=source,
            execute=execute,
        )

    def _apply_patches(
        self,
        op: Operation,
        output: dict[str, Any],
        results: list[Any],
    ) -> dict[str, Any]:
        """把「执行后」钩子返回的补丁合并进产出

        **只许往 list 尾部追加**（`runtime/plugins.py::merge_patch` 守着 ✓），
        追加到哪些字段由**插件类型自己声明**（`append_targets` ✓）。
        """
        if self.plugin_context is None or not results:
            return output
        bindings = [b for b in op.plugins if b.extension_point == EXTENSION_OP_AFTER]
        merged = output
        for binding, patch in zip(bindings, results):
            ptype = self.plugin_context.types.get(binding.id)
            allowed = ptype.append_targets if ptype is not None else ()
            merged = merge_patch(merged, patch, allowed_targets=allowed)
        return merged

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
                # ================= 一次「组件调用」（call）=================
                #
                # **调用** = 这一跳的一次执行 + 它的所有重试；
                # **尝试** = 真正调了一次 handler。
                #
                # 「执行前 / 执行后」这类钩子默认 **per_call**（在调用这一层，
                # 重试几次都只跑一遍）；声明 `per_attempt` 的则每试一次都跑。
                #
                # **为什么要有这一层**：重试发生在**组件内部**，
                # 「准入校验」不该每试一次都查（查三遍 = 三倍的错），
                # 而「每次尝试都记一笔」又必须每试一次都记。
                # ---- 外层：around 链（包住这一整次调用）----
                #
                # **around 拿得到 `call()`**：`call()` 落到 `execute_call`，
                # 它执行这一跳（含它内部的所有尝试）。
                call_started = time.monotonic()
                deadline = (
                    call_started + current_op.timeout_seconds
                    if current_op.timeout_seconds is not None
                    else None
                )

                def execute_call() -> dict[str, Any]:
                    """这一跳的真正执行（around 链包住的就是它）"""
                    nonlocal step_count

                    # ---- 执行前（准入）：不通过就别跑 ----
                    try:
                        self._run_op_hooks(
                            task_run,
                            current_op,
                            EXTENSION_OP_BEFORE,
                            {"input": dict(op_input)},
                            granularity="per_call",
                        )
                    except Exception as e:
                        # **准入没过 → 履约中止**（不进重试：
                        # 准入拒绝不是「传输失败」，重试它只会再拒一次）
                        raise _OpCallFailed(
                            f"op '{current_op.op_id}' 准入没过：{e}"
                        ) from e

                    while True:
                        # **预算耗尽 → 不再开新尝试**（§4.6.9）
                        if _budget_exhausted(deadline):
                            raise _OpCallFailed(
                                f"op '{current_op.op_id}' 的时间预算 "
                                f"{current_op.timeout_seconds}s 已耗尽，不再开新尝试"
                            )
                        step_count += 1
                        if step_count > max_steps:
                            raise _OpCallFailed("step limit exceeded")

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
                            self._run_op_hooks(
                                task_run,
                                current_op,
                                EXTENSION_OP_BEFORE,
                                {"input": dict(op_input)},
                                granularity="per_attempt",
                            )
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
                                # **预算耗尽 → 不再开新尝试**
                                # （预算的意义就在这儿：它止的是「再花一遍时间」）
                                if _budget_exhausted(deadline):
                                    raise _OpCallFailed(
                                        f"op '{current_op.op_id}' 传输失败"
                                        f"（{error_kind}），且时间预算 "
                                        f"{current_op.timeout_seconds}s 已耗尽，"
                                        f"不再重试：{e}"
                                    ) from e
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
                            raise _OpCallFailed(
                                f"op '{current_op.op_id}' failed: {e}"
                            ) from e
                        break  # 这一跳成功了 → 退出「尝试」循环

                    # op 成功
                    record.status = "completed"
                    record.finished_at = datetime.now(timezone.utc)
                    record.output = op_output
                    task_run.event_log.append(
                        event="op_finish",
                        op_id=current_op.op_id,
                        detail={"status": "completed"},
                    )

                    # **调用层的「执行后」钩子**（收尾：可以往产出里追加）
                    call_patches = self._run_op_hooks(
                        task_run,
                        current_op,
                        EXTENSION_OP_AFTER,
                        # **产出原样给插件**（不转 dict）——同上
                        {"input": dict(op_input), "output": op_output},
                        granularity="per_call",
                    )
                    op_output = self._apply_patches(current_op, op_output, call_patches)
                    record.output = op_output

                    # **超预算但成功**：接受产出，但要**看得见**
                    _note_budget_exceeded(
                        task_run, current_op, call_started, deadline
                    )
                    return op_output

                try:
                    op_output = self._run_around(
                        current_op,
                        {"input": dict(op_input)},
                        execute_call,
                    )
                except PluginFailure as failure:
                    # **插件坏了**（契约违反：改了产出 / 没调 call / 调了两次 /
                    # 把失败变成成功）→ 按 §4.6.7 中止履约（不降级）。
                    abort_on_plugin_failure(
                        task_run,
                        failure,
                        leaves_artifact=_any_leaves_artifact(task_run, beeline),
                    )
                    return task_run
                except _OpCallFailed as e:
                    _end_unfinished(task_run, beeline, reason=e.reason)
                    return task_run
                except Exception as e:
                    # **包裹层自己抛了异常** → 这一跳不行。
                    # （不是「插件坏了」：包裹层抛异常是在说「这一跳不行」，
                    #  那是**对执行的判断**，不是机制故障。）
                    _end_unfinished(
                        task_run, beeline,
                        reason=f"op '{current_op.op_id}' 的执行外层插件失败：{e}",
                    )
                    return task_run

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