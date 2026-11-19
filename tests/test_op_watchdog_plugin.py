"""tests.test_op_watchdog_plugin - 看门狗（around 的第一个业务用户）

**看门狗是什么**：挂在 `op.around` 上，包住这一跳的整次执行，
超过上限就**判这一跳没成**。

**为什么它要看得见宿主声明**（`view["op"]`）：
「超时了就算失败」这件事，只在**没有副作用**时才成立。
有副作用时（提交代码、部署、落库），说它「没成」而实际上它成了
→ 下游重跑它 → **副作用做两遍**。

**所以本文件守两条**：
① 没有副作用的跳：超了就是没成；
② 有副作用的跳：**不许**判它没成（晚到的成功必须接受）。
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from core.beeline_models import Bee, Beeline, NextRef, Operation
from core.plugins import PluginBinding, PluginContext, PluginType
from runtime.executor import BeelineExecutor
from runtime.models import TaskRunStatus, create_task_run
from runtime.plugins import EXTENSION_OP_AROUND
from runtime.runner import HandlerRunner
from runtime.watchdog import (
    WATCHDOG_TYPE_ID,
    WatchdogParams,
    watchdog_spec,
)


# ============================================================
# 工具
# ============================================================


def _beeline(*, plugins=None, leaves_artifact=False, idempotency=None) -> Beeline:
    """一只最小的盒子（插件与声明都挂在**第一跳**上）"""
    return Beeline(
        id="b_watchdog", version=1, description="看门狗用",
        operations=[
            Operation(
                op_id="op1", type="w", input_from="external",
                input="s_in", output="s_out",
                next=[NextRef(op_id="op2")], bee=Bee(type="t"),
                plugins=list(plugins or []),
                leaves_artifact=leaves_artifact,
                idempotency=idempotency,
            ),
            Operation(
                op_id="op2", type="w", input_from="op1",
                input="s_out", output="s_out",
                next=[], bee=Bee(type="t"),
            ),
        ],
    )


def _task_run():
    return create_task_run("t", "i", "c", "r", "b_watchdog", 1, "rt", "in")


def _context() -> PluginContext:
    """装配快照（看门狗的类型在 runtime/watchdog.py 登记时就在表里了）"""
    from core import plugins as plugins_mod
    import runtime.watchdog  # noqa: F401  （确保登记发生）

    return plugins_mod.freeze_plugin_context()


def _binding(seconds: float) -> PluginBinding:
    return PluginBinding(
        id=WATCHDOG_TYPE_ID,
        extension_point=EXTENSION_OP_AROUND,
        params={"seconds": seconds},
        implementation_key="op_watchdog",
    )


def _events(run, name: str) -> list:
    return [e for e in run.event_log.entries if e.event == name]


def _reason(run) -> str:
    for e in reversed(run.event_log.entries):
        if e.event == "status_change":
            return (e.detail or {}).get("reason") or ""
    return ""


# ============================================================
# 一、没有副作用的跳：超了就是没成
# ============================================================


class TestWatchdogFailsASlowOp:
    def test_a_slow_op_fails(self):
        """**整跳超时 → 这一跳没成**"""
        run = _task_run()
        BeelineExecutor(
            HandlerRunner({"w": lambda p: (time.sleep(0.05), {"ok": True})[1]}, owner="t"),
            plugin_context=_context(),
        ).execute(run, _beeline(plugins=[_binding(0.01)]), {"x": 1})

        assert run.status == TaskRunStatus.FAILED
        assert "看门狗" in _reason(run)

    def test_a_fast_op_is_untouched(self):
        """**没超上限 → 产出原样**"""
        run = _task_run()
        BeelineExecutor(
            HandlerRunner({"w": lambda p: {"ok": True}}, owner="t"),
            plugin_context=_context(),
        ).execute(run, _beeline(plugins=[_binding(60)]), {"x": 1})

        assert run.status == TaskRunStatus.COMPLETED
        assert run.result == {"ok": True}

    def test_the_watchdog_does_not_change_the_output(self):
        """**看门狗不许改产出**（它只是量时间 + 判失败）"""
        run = _task_run()
        BeelineExecutor(
            HandlerRunner({"w": lambda p: {"ok": True}}, owner="t"),
            plugin_context=_context(),
        ).execute(run, _beeline(plugins=[_binding(60)]), {"x": 1})

        assert run.result == {"ok": True}
        assert run.operations["op1"].output == {"ok": True}


# ============================================================
# 二、有副作用的跳：不许判它没成
# ============================================================


class TestWatchdogNeverFailsASideEffectingOp:
    def test_an_op_that_leaves_an_artifact_is_not_failed(self):
        """**声明了会留下东西的跳：超了也不判失败**

        说它「没成」而实际上它成了 → 下游重跑 → **副作用做两遍**。
        """
        run = _task_run()
        BeelineExecutor(
            HandlerRunner({"w": lambda p: (time.sleep(0.05), {"ok": True})[1]}, owner="t"),
            plugin_context=_context(),
        ).execute(
            run,
            _beeline(plugins=[_binding(0.01)], leaves_artifact=True),
            {"x": 1},
        )

        assert run.status == TaskRunStatus.COMPLETED
        assert run.result == {"ok": True}

    def test_an_op_that_may_not_be_replayed_is_not_failed(self):
        """**声明了「不许重放」的跳：超了也不判失败**"""
        from core.beeline_models import Idempotency

        run = _task_run()
        BeelineExecutor(
            HandlerRunner({"w": lambda p: (time.sleep(0.05), {"ok": True})[1]}, owner="t"),
            plugin_context=_context(),
        ).execute(
            run,
            _beeline(
                plugins=[_binding(0.01)],
                idempotency=Idempotency(mode="forbidden"),
            ),
            {"x": 1},
        )

        assert run.status == TaskRunStatus.COMPLETED
        assert run.result == {"ok": True}


# ============================================================
# 三、装配期
# ============================================================


class TestWatchdogDeclaration:
    def test_the_params_must_be_a_positive_number(self):
        """**上限必须是正数**（0 = 立刻就算超时，是个坑）"""
        with pytest.raises(ValidationError):
            WatchdogParams(seconds=0)
        with pytest.raises(ValidationError):
            WatchdogParams(seconds=-1)

    def test_unknown_params_are_refused(self):
        """**不许静默吞字段**"""
        with pytest.raises(ValidationError):
            WatchdogParams(seconds=10, second=10)

    def test_the_type_is_registered(self):
        """**看门狗是一种已登记的插件类型**（装配期查得到）"""
        from core.plugins import PLUGIN_TYPES

        ptype = PLUGIN_TYPES.get(WATCHDOG_TYPE_ID)
        assert ptype is not None, "看门狗没登记——挂上去也不会被调用"
        assert ptype.allowed_hosts == ("op",)
        assert EXTENSION_OP_AROUND in ptype.extension_points
        assert ptype.implementation_key == "op_watchdog"

    def test_the_receipt_box_mounts_it(self):
        """**票据盒的 LLM 腿挂了看门狗 + 预算**（第一个业务用户）"""
        from boxes.receipt.beeline import COMPONENTS

        read = next(c for c in COMPONENTS if c.op_id == "read_receipt")
        assert read.timeout_seconds, "这一跳没声明时间预算"
        ids = [p.id for p in read.plugins]
        assert WATCHDOG_TYPE_ID in ids, "票据盒的 LLM 腿没挂看门狗"
        spec = next(p for p in read.plugins if p.id == WATCHDOG_TYPE_ID)
        assert spec.extension_point == EXTENSION_OP_AROUND
        assert spec.params["seconds"] > 0
