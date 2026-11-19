"""tests.test_op_around_and_timeout - 执行外层（around）与超时

**M7 的目标**：让「包裹这一跳的执行」与「这一跳最多花多久」两件事落地。

- **`op.around`**：插件拿到一个 `call()`，**包住**这一跳的执行。
- **三条锁**（docs/component-plugin-model.md §4.6.8）：
  ① `call()` 至多调一次；② 返回值必须等于 `call()` 的返回值；
  ③ `call()` 必须被调用过恰好一次。
- **超时**（§4.6.9）：这一跳的**总预算**——
  它是「不再开新尝试」的闸，**不是**「打断」的闸
  （同进程杀不掉已经在跑的线程）。

**每条新校验都配一条喂反例的守卫**（老纪律）：
守卫要证明「真的会红」，而不只是「正常时是绿的」。
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from core.beeline_models import Bee, Beeline, NextRef, Operation
from core.plugins import (
    PluginBinding,
    PluginContext,
    PluginSpec,
    PluginType,
    register_implementation,
    register_plugin_type,
)
from runtime.executor import BeelineExecutor
from runtime.llm import LLMError
from runtime.models import TaskRunStatus, create_task_run
from runtime.plugins import EXTENSION_OP_AROUND
from runtime.runner import HandlerRunner


# ============================================================
# 工具
# ============================================================


def _beeline(*, plugins=None, timeout_seconds=None) -> Beeline:
    """一只最小的盒子（插件与超时都挂在**第一跳**上）"""
    return Beeline(
        id="b_around", version=1, description="around 用",
        operations=[
            Operation(
                op_id="op1", type="w", input_from="external",
                input="s_in", output="s_out",
                next=[NextRef(op_id="op2")], bee=Bee(type="t"),
                plugins=list(plugins or []),
                timeout_seconds=timeout_seconds,
            ),
            Operation(
                op_id="op2", type="w", input_from="op1",
                input="s_out", output="s_out",
                next=[], bee=Bee(type="t"),
            ),
        ],
    )


def _task_run():
    return create_task_run("t", "i", "c", "r", "b_around", 1, "rt", "in")


def _around_context(*impls, probe=None) -> tuple[PluginContext, list[PluginBinding]]:
    """登记若干个 around 插件类型，返回（冻结的上下文，绑定列表）

    **绑定顺序 = 声明顺序**（先声明的在外层）。
    每个元素是（插件类型 id, 实现）。

    `probe` 会在**登记还在表里的时候**跑（装配期校验要看到那张表）。
    """
    from core import plugins as plugins_mod

    saved = (
        dict(plugins_mod.PLUGIN_TYPES),
        dict(plugins_mod.EXTENSION_POINTS),
        dict(plugins_mod.IMPLEMENTATIONS),
    )
    try:
        bindings: list[PluginBinding] = []
        for pid, impl in impls:
            key = f"{pid}.impl"
            register_plugin_type(
                PluginType(
                    id=pid,
                    allowed_hosts=("op",),
                    extension_points=(EXTENSION_OP_AROUND,),
                    implementation_key=key,
                )
            )
            register_implementation(key, impl)
            bindings.append(
                PluginBinding(
                    id=pid,
                    extension_point=EXTENSION_OP_AROUND,
                    params={},
                    implementation_key=key,
                )
            )
        ctx = plugins_mod.freeze_plugin_context()
        if probe is not None:
            probe()
        return ctx, bindings
    finally:
        plugins_mod.PLUGIN_TYPES.clear()
        plugins_mod.PLUGIN_TYPES.update(saved[0])
        plugins_mod.EXTENSION_POINTS.clear()
        plugins_mod.EXTENSION_POINTS.update(saved[1])
        plugins_mod.IMPLEMENTATIONS.clear()
        plugins_mod.IMPLEMENTATIONS.update(saved[2])


def _runner(handler) -> HandlerRunner:
    return HandlerRunner({"w": handler}, owner="t")


def _events(run, name: str) -> list:
    return [e for e in run.event_log.entries if e.event == name]


def _reason(run) -> str:
    """状态 reason（在 status_change 事件里）"""
    for e in reversed(run.event_log.entries):
        if e.event == "status_change":
            return (e.detail or {}).get("reason") or ""
    return ""


# ============================================================
# 一、around 真的包住了执行
# ============================================================


class TestAroundReallyWraps:
    def test_the_hook_wraps_the_execution(self):
        """**around 真的在执行的前后各跑一次**"""
        order: list[str] = []

        def impl(view, params):
            order.append("around:in")
            out = view["call"]()
            order.append("around:out")
            return out

        ctx, bindings = _around_context(("around_plugin", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: (order.append("handler"), {"ok": True})[1]),
            plugin_context=ctx,
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        # 插件只挂在**第一跳**上 → 第二跳没有 around
        assert order == ["around:in", "handler", "around:out", "handler"], order
        assert run.status == TaskRunStatus.COMPLETED
        assert run.result == {"ok": True}

    def test_the_output_is_identical_with_and_without_around(self):
        """**挂 around 不改变产出**（零影响）"""

        def impl(view, params):
            return view["call"]()

        ctx, bindings = _around_context(("around_plugin", impl))
        handler = lambda p: {"ok": True}  # noqa: E731

        plain = _task_run()
        BeelineExecutor(_runner(handler)).execute(plain, _beeline(), {"x": 1})

        wrapped = _task_run()
        BeelineExecutor(
            _runner(handler), plugin_context=ctx
        ).execute(wrapped, _beeline(plugins=bindings), {"x": 1})

        assert wrapped.result == plain.result
        assert wrapped.status == plain.status

    def test_the_first_declared_wraps_the_second(self):
        """**嵌套顺序 = 声明顺序**（先声明的在外层，像中间件）"""
        order: list[str] = []

        def outer(view, params):
            order.append("outer:in")
            out = view["call"]()
            order.append("outer:out")
            return out

        def inner(view, params):
            order.append("inner:in")
            out = view["call"]()
            order.append("inner:out")
            return out

        ctx, bindings = _around_context(("outer_p", outer), ("inner_p", inner))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: (order.append("handler"), {"ok": True})[1]),
            plugin_context=ctx,
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        assert order == [
            "outer:in", "inner:in", "handler", "inner:out", "outer:out",
            "handler",  # 第二跳（op2）没有 around
        ], order

    def test_the_view_is_a_copy(self):
        """**插件改自己的视图副本，改不到宿主**"""
        seen: list[int] = []

        def impl(view, params):
            seen.append(view["input"]["x"])
            view["input"]["x"] = 999
            return view["call"]()

        ctx, bindings = _around_context(("around_plugin", impl))
        payload = {"x": 1}
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx
        ).execute(run, _beeline(plugins=bindings), payload)

        assert payload == {"x": 1}, "插件改到了宿主的输入"
        assert seen == [1], "插件拿到的不是副本"


# ============================================================
# 二、三条锁（每条都喂反例）
# ============================================================


class TestTheThreeLocks:
    def test_it_may_not_change_the_output(self):
        """**锁②：around 不许改产出内容**"""
        ran: list[int] = []

        def impl(view, params):
            view["call"]()
            ran.append(1)
            return {"ok": "被换了"}

        ctx, bindings = _around_context(("around_plugin", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        assert ran, "插件根本没被调用"
        assert run.status == TaskRunStatus.FAILED
        assert run.result is None, "被改过的产出被当成了结果交出去"
        # **失败必须落盘**
        failures = _events(run, "plugin_failure")
        assert failures, "插件契约违反没有落盘"
        assert "产出" in str(failures[0].detail)

    def test_it_may_not_mutate_the_output_in_place(self):
        """**锁②（就地改）：`call()` 的返回值本体被改掉，也算改了产出**

        **为什么这条要单独守**：插件拿到的就是那个对象本身。
        它就地改完再返回，两边是**同一个对象** → 直接比会「一样」✗。
        所以比的是**快照**。
        """
        def impl(view, params):
            result = view["call"]()
            result["ok"] = False
            return result

        ctx, bindings = _around_context(("mutator", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        assert run.status == TaskRunStatus.FAILED
        assert run.result is None

    def test_it_may_not_substitute_a_nested_type(self):
        """**锁②（换类型）：`True` 换成 `1` 也是改了产出**

        `True == 1` 在 Python 里成立 → 只比值会放过这种替换。
        """
        from copy import deepcopy

        def impl(view, params):
            result = deepcopy(view["call"]())
            result["ok"] = 1
            return result

        ctx, bindings = _around_context(("type_mutator", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        assert run.status == TaskRunStatus.FAILED
        assert run.result is None

    def test_it_must_call(self):
        """**锁③：不许跳过执行**"""
        ran: list[int] = []

        def impl(view, params):
            ran.append(1)
            return {"ok": True}

        ctx, bindings = _around_context(("around_plugin", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: (ran.append(99), {"ok": True})[1]),
            plugin_context=ctx,
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        assert 99 not in ran, "没调 call()，handler 却还是跑了"
        assert run.status == TaskRunStatus.FAILED
        assert _events(run, "plugin_failure"), "契约违反没有落盘"

    def test_it_may_not_call_twice(self):
        """**锁①：`call()` 至多调一次**（插件不许私设重试）"""
        ran: list[int] = []

        def impl(view, params):
            view["call"]()
            view["call"]()  # 第二次 → 当场抛错
            return {"ok": True}

        ctx, bindings = _around_context(("around_plugin", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: (ran.append(1), {"ok": True})[1]),
            plugin_context=ctx,
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        assert len(ran) == 1, f"handler 被跑了 {len(ran)} 次（第二次本该当场拦下）"
        assert run.status == TaskRunStatus.FAILED
        assert _events(run, "plugin_failure")

    def test_it_may_not_turn_a_failure_into_a_success(self):
        """**不许把失败变成成功**（吞掉 call() 的失败）"""

        def impl(view, params):
            try:
                view["call"]()
            except Exception:
                pass
            return {"ok": True}

        ctx, bindings = _around_context(("around_plugin", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: (_ for _ in ()).throw(RuntimeError("这一跳挂了"))),
            plugin_context=ctx,
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        assert run.status == TaskRunStatus.FAILED
        assert run.result is None
        assert _events(run, "plugin_failure")


# ============================================================
# 三、包裹层自己的异常 = 这一跳失败（不是「插件坏了」）
# ============================================================


class TestAroundRaises:
    def test_an_exception_fails_the_component(self):
        """**包裹层抛异常 → 这一跳不行**（履约不继续）"""

        def impl(view, params):
            raise RuntimeError("包裹层炸了")

        ctx, bindings = _around_context(("around_plugin", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        assert run.status == TaskRunStatus.FAILED
        assert "包裹层炸了" in _reason(run)
        assert not _events(run, "plugin_failure"), (
            "包裹层抛异常不是「插件坏了」——那是这一跳失败"
        )

    def test_the_inner_failure_still_reaches_the_outer_layer(self):
        """**内层失败会穿过包裹层往上抛**（包裹层看得见失败）"""
        seen: list[str] = []

        def impl(view, params):
            try:
                return view["call"]()
            except Exception as e:
                seen.append(str(e))
                raise

        ctx, bindings = _around_context(("around_plugin", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: (_ for _ in ()).throw(RuntimeError("这一跳挂了"))),
            plugin_context=ctx,
        ).execute(run, _beeline(plugins=bindings), {"x": 1})

        assert seen, "包裹层没看见内层的失败"
        assert "这一跳挂了" in seen[0]
        assert run.status == TaskRunStatus.FAILED


# ============================================================
# 四、超时：预算是闸，不是断头台
# ============================================================


class TestTimeout:
    def test_the_budget_stops_a_new_attempt(self):
        """**预算耗尽 → 不再开新尝试**"""
        attempts = {"n": 0}

        def flaky(payload):
            attempts["n"] += 1
            time.sleep(0.05)  # 把预算耗光
            raise LLMError("传输挂了")

        run = _task_run()
        BeelineExecutor(_runner(flaky)).execute(
            run, _beeline(timeout_seconds=0.01), {"x": 1}
        )

        assert attempts["n"] == 1, (
            f"预算耗尽后还开了 {attempts['n']} 次尝试——预算的意义就在这儿"
        )
        assert run.status == TaskRunStatus.FAILED
        assert "预算" in _reason(run)

    def test_a_generous_budget_still_retries(self):
        """**预算没耗尽 → 照旧重试**（既有行为不变）"""
        attempts = {"n": 0}

        def flaky(payload):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise LLMError("传输挂了")
            return {"ok": True}

        run = _task_run()
        BeelineExecutor(_runner(flaky)).execute(
            run, _beeline(timeout_seconds=60), {"x": 1}
        )

        assert run.operations["op1"].attempts == 3, (
            f"预算够的时候该照旧重试，实际试了 "
            f"{run.operations['op1'].attempts} 次"
        )
        assert run.status == TaskRunStatus.COMPLETED
        assert run.result == {"ok": True}

    def test_a_late_success_is_accepted_but_recorded(self):
        """**超预算但成功 → 接受产出，但要看得见**"""
        run = _task_run()
        BeelineExecutor(_runner(lambda p: (time.sleep(0.05), {"ok": True})[1])).execute(
            run, _beeline(timeout_seconds=0.01), {"x": 1}
        )

        assert run.status == TaskRunStatus.COMPLETED
        assert run.result == {"ok": True}, "超预算不等于没成"
        over = _events(run, "op_budget_exceeded")
        assert over, "超预算没有留下痕迹——那件事就看不见了"
        assert over[0].detail["budget_seconds"] == 0.01

    def test_the_time_spent_after_the_call_counts_too(self, monkeypatch):
        """**包裹层自己花的时间也算这一跳花的**（它在 `call()` 返回后还要做事）

        预算判在**整次调用之后**——只判在尝试循环里的话，
        包裹层尾巴上那段时间就漏出去了。
        """
        clock = [0.0]
        monkeypatch.setattr(
            "runtime.executor.time.monotonic", lambda: clock[0]
        )

        def impl(view, params):
            result = view["call"]()
            clock[0] = 2.0  # 包裹层在 call() 之后又花了 2 秒
            return result

        ctx, bindings = _around_context(("slow_after", impl))
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx
        ).execute(run, _beeline(plugins=bindings, timeout_seconds=1.0), {"x": 1})

        assert run.status == TaskRunStatus.COMPLETED
        assert _events(run, "op_budget_exceeded"), [
            (e.event, e.detail) for e in run.event_log.entries
        ]

    def test_no_budget_means_no_budget_event(self):
        """**没设预算 → 不该冒出预算事件**"""
        run = _task_run()
        BeelineExecutor(_runner(lambda p: {"ok": True})).execute(
            run, _beeline(), {"x": 1}
        )
        assert not _events(run, "op_budget_exceeded")

    def test_a_nonpositive_budget_is_refused(self):
        """**预算必须大于 0**（0 = 永远不跑，是个静默的坑）"""
        with pytest.raises(ValidationError):
            Operation(
                op_id="op1", type="w", input_from="external",
                input="s_in", output="s_out", bee=Bee(type="t"),
                timeout_seconds=0,
            )
        with pytest.raises(ValidationError):
            Operation(
                op_id="op1", type="w", input_from="external",
                input="s_in", output="s_out", bee=Bee(type="t"),
                timeout_seconds=-1,
            )


# ============================================================
# 五、装配期
# ============================================================


class TestAssembly:
    def test_the_budget_reaches_the_operation(self):
        """**声明层的预算要传到作业路线上**（否则它只是个字段）"""
        from core.scaffold import BoxDeclaration, Component, assemble
        from core.models import (
            AcceptanceRule,
            ExceptionRule,
            FieldDef,
            SchemaDef,
        )

        schemas = [
            SchemaDef(
                id="s_in",
                fields=[FieldDef(name="text", type="string", required=True)],
            ),
            SchemaDef(
                id="s_out",
                fields=[FieldDef(name="ok", type="boolean")],
            ),
        ]
        declaration = BoxDeclaration(
            id="demo_box", version=1, description="演示盒",
            display_name="演示盒", value_stream="Demo",
            task_type="run_demo", task_schema="s_in",
            result_type="demo_result", result_schema="s_out",
            acceptance=[AcceptanceRule(metric="ok", op="eq", value=True)],
            exceptions=[ExceptionRule(condition="ok == true", action="escalate")],
            beeline_id="beeline_demo_v1", beeline_version=1,
        )
        components = [
            Component(
                op_id="demo_work", type="demo_work", input_from="external",
                input="s_in", output="s_out", next=[],
                handler=lambda payload: {"ok": True},
                timeout_seconds=12.5,
            )
        ]
        box = assemble(declaration, components, schemas=schemas)
        op = box.beeline.get_operation("demo_work")
        assert op is not None
        assert op.timeout_seconds == 12.5

    def test_an_around_hook_cannot_be_mounted_on_a_box(self):
        """**around 只在 op 上触发**（挂到盒级 = 永远不会被调用）"""
        from core.plugins import check_plugin_spec

        problems: list[str] = []

        def probe() -> None:
            problems.extend(
                check_plugin_spec(
                    PluginSpec(id="around_plugin", extension_point=EXTENSION_OP_AROUND),
                    host_kind="box",
                )
            )

        _around_context(
            ("around_plugin", lambda view, params: view["call"]()),
            probe=probe,
        )
        assert problems, "把 around 挂到盒级宿主上没人拦"
        assert any("永远不会" in p for p in problems), problems

    def test_the_around_extension_point_is_registered_for_ops(self):
        """**around 这个钩子确实留了，且只在 op 上触发**"""
        from core.plugins import EXTENSION_POINTS

        point = EXTENSION_POINTS.get(EXTENSION_OP_AROUND)
        assert point is not None, "runtime 没留 around 这个钩子"
        assert point.host_kinds == ("op",)
        assert point.granularity == "per_call"
        assert point.view_keys == ("input", "call", "op", "observe")
