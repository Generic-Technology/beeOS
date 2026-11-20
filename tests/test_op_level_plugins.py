"""tests.test_op_level_plugins - op 级插件的执行语义

**M6 的目标**：让「组件挂插件」不只是能装配，而是**真的会执行**。

这一批落地的：

- **执行器把「一次调用」与「多次尝试」拆开**——
  调用 = 这一跳的一次执行 + 它的所有重试；尝试 = 真正调了一次 handler。
- **两个 op 级钩子**：`op.before`（准入）/ `op.after`（收尾，可追加）
- **粒度**：`per_call`（默认，一次调用里只跑一遍）/ `per_attempt`（每次尝试都跑）
- **补丁**：只许往 list 尾部追加，追加到哪些字段由插件类型自己声明

**为什么「调用 / 尝试」要拆开**：重试发生在**组件内部**——
「准入校验」不该每试一次都查（查三遍 = 三倍的错），
而「每次尝试都记一笔」又必须每试一次都记。
"""

from __future__ import annotations

import pytest

from core.beeline_models import Bee, Beeline, NextRef, Operation
from core.models import FieldDef, SchemaDef
from core.plugins import (
    ExtensionPoint,
    PluginBinding,
    PluginContext,
    PluginSpec,
    PluginType,
    register_extension_point,
    register_implementation,
    register_plugin_type,
)
from runtime.executor import BeelineExecutor
from runtime.llm import LLMError
from runtime.models import TaskRunStatus, create_task_run
from runtime.plugins import (
    EXTENSION_OP_AFTER,
    EXTENSION_OP_BEFORE,
    PluginRejected,
    merge_patch,
)
from runtime.runner import HandlerRunner


# ============================================================
# 工具：一只最小的盒子 + 一个「先失败后成功」的 runner
# ============================================================


def _beeline(*, plugins=None) -> Beeline:
    """一只最小的盒子

    `plugins` 挂在**第一跳**上（`plugins=None` 就不挂 ✓）。
    """
    return Beeline(
        id="b_op", version=1, description="op 级插件用",
        operations=[
            Operation(
                op_id="op1", type="w", input_from="external",
                input="s_in", output="s_out",
                next=[NextRef(op_id="op2")], bee=Bee(type="t"),
                plugins=list(plugins or []),
            ),
            Operation(
                op_id="op2", type="w", input_from="op1",
                input="s_out", output="s_out",
                next=[], bee=Bee(type="t"),
            ),
        ],
    )


def _binding(point_id: str) -> PluginBinding:
    """**编译后的绑定**（运行时认的是这个，不是 `PluginSpec`）"""
    return PluginBinding(
        id="op_plugin",
        extension_point=point_id,
        params={},
        implementation_key="op.impl",
    )


def _task_run() -> "object":
    return create_task_run("t", "i", "c", "r", "b_op", 1, "rt", "in")


def _runner(handler) -> HandlerRunner:
    return HandlerRunner({"w": handler}, owner="t")


#: **业务 schema**（补丁合并后要当场过形状闸，闸要 schema）
#:
#: **为什么执行器要 schema**：补丁是唯一能改产出内容的东西，
#: 它合并完必须重新过形状闸——而闸要 schema。
#: 判不了形状就不能把未校验的产出往下游送（fail closed）。
_SCHEMAS = [
    SchemaDef(
        id="s_in",
        fields=[FieldDef(name="x", type="number")],
    ),
    SchemaDef(
        id="s_out",
        fields=[
            FieldDef(name="ok", type="boolean"),
            FieldDef(
                name="notes", type="array",
                items=FieldDef(name="note", type="string"),
            ),
        ],
    ),
]


def _context(*, impls: dict, point_overrides: dict | None = None) -> PluginContext:
    """登记一套 op 级钩子 + 实现，返回冻结的上下文"""
    from core import plugins as plugins_mod

    saved = (
        dict(plugins_mod.PLUGIN_TYPES),
        dict(plugins_mod.EXTENSION_POINTS),
        dict(plugins_mod.IMPLEMENTATIONS),
    )
    try:
        # **直接改注册表**，不是再登记一次——
        # 「同一个钩子登记两次、内容不一样」本来就该报错 ✓
        for point_id, kwargs in (point_overrides or {}).items():
            plugins_mod.EXTENSION_POINTS[point_id] = ExtensionPoint(
                id=point_id, host_kinds=("op",), **kwargs
            )
        register_plugin_type(
            PluginType(
                id="op_plugin",
                allowed_hosts=("op",),
                extension_points=("op.before", "op.after"),
                implementation_key="op.impl",
                append_targets=("notes",),
            )
        )
        register_implementation("op.impl", impls["op.impl"])
        return plugins_mod.freeze_plugin_context()
    finally:
        plugins_mod.PLUGIN_TYPES.clear()
        plugins_mod.PLUGIN_TYPES.update(saved[0])
        plugins_mod.EXTENSION_POINTS.clear()
        plugins_mod.EXTENSION_POINTS.update(saved[1])
        plugins_mod.IMPLEMENTATIONS.clear()
        plugins_mod.IMPLEMENTATIONS.update(saved[2])


# ============================================================
# 一、钩子真的会被调用
# ============================================================


class TestTheHookIsReallyCalled:
    def test_a_before_hook_runs_before_the_component(self):
        """**执行前钩子真的在组件之前跑**"""
        order: list[str] = []

        def impl(view, params):
            order.append("before")
            return None

        ctx = _context(impls={"op.impl": impl})
        runner = HandlerRunner(
            {
                "w": lambda payload: (order.append("handler"), {"ok": True})[1],
            },
            owner="t",
        )
        task_run = _task_run()
        BeelineExecutor(runner, plugin_context=ctx).execute(
            task_run,
            _beeline(plugins=[_binding(EXTENSION_OP_BEFORE)]),
            {"x": 1},
        )
        # 插件只挂在**第一跳**上 → 第二跳没有钩子
        assert order == ["before", "handler", "handler"], order

    def test_a_before_hook_can_reject_the_component(self):
        """**准入拒绝 → 这一跳不执行**（handler 一次都没被调）"""
        calls: list[int] = []

        def impl(view, params):
            calls.append(1)
            return {"problems": ["输入不合法"]}

        ctx = _context(impls={"op.impl": impl})
        runner = HandlerRunner(
            {"w": lambda payload: (calls.append(99), {"ok": True})[1]},
            owner="t",
        )
        task_run = _task_run()
        BeelineExecutor(runner, plugin_context=ctx).execute(
            task_run,
            _beeline(plugins=[_binding(EXTENSION_OP_BEFORE)]),
            {"x": 1},
        )
        assert 99 not in calls, "准入拒绝了，handler 却还是跑了"
        assert task_run.status == TaskRunStatus.FAILED


# ============================================================
# 二、粒度：一次调用 vs 多次尝试
# ============================================================


class TestGranularity:
    """**一次调用 vs 多次尝试**

    重试只发生在**传输类失败**（`llm_call`）上——
    盒子逻辑错（`op_error`）不重试 ✓（那是「重试也修不好」的错）。
    """

    def test_a_per_call_hook_runs_once_even_when_the_component_retries(self):
        """**per_call：重试几次都只跑一遍**（准入校验不是查三遍）"""
        attempts = {"n": 0}
        before_calls = {"n": 0}

        def flaky(payload):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise LLMError("传输挂了")
            return {"ok": True}

        def impl(view, params):
            before_calls["n"] += 1
            return None

        ctx = _context(
            impls={"op.impl": impl},
            point_overrides={
                EXTENSION_OP_BEFORE: {
                    "view_keys": ("input",),
                    "return_contract": "verdict",
                    "granularity": "per_call",
                }
            },
        )
        runner = HandlerRunner({"w": flaky}, owner="t")
        task_run = _task_run()
        BeelineExecutor(runner, plugin_context=ctx).execute(
            task_run,
            _beeline(plugins=[_binding(EXTENSION_OP_BEFORE)]),
            {"x": 1},
        )
        # op1 试了 3 次（传输失败会重试）+ op2 试了 1 次
        assert attempts["n"] == 4, attempts["n"]
        # **一次调用 = 一跳 + 它的所有重试** → per_call 的钩子只该跑 1 次
        # （插件只挂在第一跳上，第二跳没有钩子 ✓）
        assert before_calls["n"] == 1, (
            f"per_call 的钩子跑了几次：{before_calls['n']}（应该只跑 1 次）"
        )

    def test_a_per_attempt_hook_runs_on_every_attempt(self):
        """**per_attempt：每试一次都跑一遍**"""
        attempts = {"n": 0}
        before_calls = {"n": 0}

        def flaky(payload):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise LLMError("传输挂了")
            return {"ok": True}

        def impl(view, params):
            before_calls["n"] += 1
            return None

        ctx = _context(
            impls={"op.impl": impl},
            point_overrides={
                EXTENSION_OP_BEFORE: {
                    "view_keys": ("input",),
                    "return_contract": "verdict",
                    "granularity": "per_attempt",
                }
            },
        )
        runner = HandlerRunner({"w": flaky}, owner="t")
        task_run = _task_run()
        BeelineExecutor(runner, plugin_context=ctx).execute(
            task_run,
            _beeline(plugins=[_binding(EXTENSION_OP_BEFORE)]),
            {"x": 1},
        )
        # op1 试了 3 次（传输失败会重试）+ op2 试了 1 次
        assert attempts["n"] == 4, attempts["n"]
        assert before_calls["n"] == 3, (
            f"per_attempt 的钩子跑了几次：{before_calls['n']}（应该每试一次都跑）"
        )


# ============================================================
# 三、补丁：只许往 list 追加
# ============================================================


class TestPatches:
    def test_a_patch_appends_to_the_declared_field(self):
        """插件声明过的字段 → 追加 ✓"""
        merged = merge_patch(
            {"notes": ["a"], "total": 3},
            {"append": {"notes": ["b"]}},
            allowed_targets=("notes",),
        )
        assert merged["notes"] == ["a", "b"]
        assert merged["total"] == 3

    def test_a_patch_may_not_touch_a_field_outside_the_declaration(self):
        """**越界要报错**——补丁是唯一能改产出内容的东西"""
        from runtime.plugins import PluginContractError

        with pytest.raises(PluginContractError, match="越界"):
            merge_patch(
                {"notes": ["a"]},
                {"append": {"other": ["b"]}},
                allowed_targets=("notes",),
            )

    def test_a_patch_may_not_change_an_existing_value(self):
        """**只许追加，不许改**——改会让「声明的契约」与「实际交付的」对不上"""
        from runtime.plugins import PluginContractError

        with pytest.raises(PluginContractError, match="不是列表"):
            merge_patch(
                {"total": 3},
                {"append": {"total": [4]}},
                allowed_targets=("total",),
            )

    def test_an_empty_whitelist_forbids_every_target(self):
        """**空白名单 = 一个都不许追加**（不是「都不限制」）

        「没声明范围」和「范围是全部」差着一整个世界：
        前者是「这类插件不许改产出」，后者是「这类插件能改任何字段」。
        """
        from runtime.plugins import PluginContractError

        with pytest.raises(PluginContractError, match="越界"):
            merge_patch(
                {"items": ["real"]},
                {"append": {"items": ["injected"]}},
                allowed_targets=(),
            )

    def test_the_after_hook_patch_reaches_the_next_component(self):
        """**执行后钩子追加的内容，下一跳看得见**"""
        def impl(view, params):
            return {"append": {"notes": ["来自钩子"]}}

        ctx = _context(
            impls={"op.impl": impl},
            point_overrides={
                EXTENSION_OP_AFTER: {
                    "view_keys": ("input", "output"),
                    "return_contract": "patch",
                    "granularity": "per_call",
                }
            },
        )
        seen: list[dict] = []

        def handler(payload):
            seen.append(payload)
            return {"ok": True}

        runner = HandlerRunner({"w": handler}, owner="t")
        task_run = _task_run()
        BeelineExecutor(runner, plugin_context=ctx, schemas=_SCHEMAS).execute(
            task_run,
            _beeline(plugins=[_binding(EXTENSION_OP_AFTER)]),
            {"x": 1},
        )
        assert seen[1].get("notes") == ["来自钩子"], seen[1]


# ============================================================
# 三之二、声明了却没人执行 / 崩了要落盘
# ============================================================


class TestTheGateIsNeverSilentlySkipped:
    def test_a_missing_context_does_not_bypass_the_admission_gate(self):
        """**没有插件上下文 → 不许悄悄跳过那道闸**（fail closed）

        声明了准入闸却因为「没给上下文」而不跑，
        链看起来是通的，而**守着它的那道闸一次都没执行过**。
        """
        calls: list[int] = []
        run = _task_run()
        BeelineExecutor(_runner(lambda p: (calls.append(1), {"ok": True})[1])).execute(
            run, _beeline(plugins=[_binding(EXTENSION_OP_BEFORE)]), {"x": 1}
        )
        assert calls == [], (run.status, calls)
        assert run.status == TaskRunStatus.FAILED

    def test_a_crashing_admission_plugin_is_recorded_as_a_plugin_failure(self):
        """**插件崩了 → 记 plugin_failure**（不是混成「准入没过」）"""
        def impl(view, params):
            raise RuntimeError("admission plugin crashed")

        ctx = _context(impls={"op.impl": impl})
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx
        ).execute(run, _beeline(plugins=[_binding(EXTENSION_OP_BEFORE)]), {"x": 1})

        failures = [e for e in run.event_log.entries if e.event == "plugin_failure"]
        assert failures, [(e.event, e.detail) for e in run.event_log.entries]

    def test_an_out_of_range_patch_is_recorded_as_a_plugin_failure(self):
        """**补丁越界 → 记 plugin_failure**（不是混成「这一跳失败」）"""
        ctx = _context(
            impls={"op.impl": lambda view, params: {"append": {"other": ["x"]}}}
        )
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx, schemas=_SCHEMAS
        ).execute(run, _beeline(plugins=[_binding(EXTENSION_OP_AFTER)]), {"x": 1})

        failures = [e for e in run.event_log.entries if e.event == "plugin_failure"]
        assert failures, [(e.event, e.detail) for e in run.event_log.entries]

    def test_a_bad_patch_never_reaches_the_next_component(self, plugin_registry):
        """**坏补丁不许直达有副作用的那一跳**

        补丁是唯一能改产出内容的东西；它合并完必须重新过形状闸，
        而且**要赶在下游之前**——盒级形状闸在整条链跑完之后才验，
        那时副作用已经发生了。
        """
        from core.models import (
            AcceptanceRule,
            ExceptionRule,
            FieldDef,
            SchemaDef,
        )
        from core.plugins import PluginSpec, PluginType
        from core.scaffold import BoxDeclaration, Component, assemble

        seen: list[dict] = []
        schemas = [
            SchemaDef(id="cr_in", fields=[FieldDef(name="text", type="string")]),
            SchemaDef(
                id="cr_mid",
                fields=[FieldDef(name="notes", type="array",
                                 items=FieldDef(name="note", type="string"))],
            ),
            SchemaDef(id="cr_out", fields=[FieldDef(name="ok", type="boolean")]),
        ]
        plugin_registry.register_plugin_type(PluginType(
            id="cr_bad_patch", allowed_hosts=("op",),
            extension_points=(EXTENSION_OP_AFTER,),
            implementation_key="cr.bad_patch", append_targets=("notes",),
        ))
        plugin_registry.register_implementation(
            "cr.bad_patch", lambda view, params: {"append": {"notes": [123]}}
        )
        box = assemble(
            BoxDeclaration(
                id="cr_box", task_type="cr", task_schema="cr_in",
                result_type="cr_result", result_schema="cr_out",
                display_name="CR box", value_stream="CR", beeline_id="cr_beeline",
                acceptance=[AcceptanceRule(metric="ok", op="eq", value=True)],
                exceptions=[ExceptionRule(condition="ok == false", action="no_deliver")],
            ),
            [
                Component(
                    op_id="a", input="cr_in", output="cr_mid", next=["b"],
                    handler=lambda p: {"notes": []},
                    plugins=[PluginSpec(id="cr_bad_patch",
                                        extension_point=EXTENSION_OP_AFTER)],
                ),
                Component(
                    op_id="b", input_from="a", input="cr_mid", output="cr_out",
                    leaves_artifact=True,
                    handler=lambda p: (seen.append(p["notes"]) or {"ok": True}),
                ),
            ],
            schemas=schemas,
        )
        run = _task_run()
        BeelineExecutor(
            HandlerRunner(box.handlers, owner="cr"),
            plugin_context=box.plugin_context,
            schemas=schemas,
        ).execute(run, box.beeline, {"text": "go"})
        assert not seen, (seen, run.status, run.result)


# ============================================================
# 三之三、**结论**与**事实**要分开记
# ============================================================


class TestTheConclusionAndTheFactAreSeparate:
    """**「这一跳的结论」与「handler 跑成了没有」是两个事实**

    handler 成功了、但这一跳后来被外层拒了（看门狗说太慢、补丁校验没过）
    → **结论**是 failed，可 **handler 已经动了真实世界** → 现场有残留。

    只数「completed 的跳」的话，那一跳被改成 failed 就从计数里消失了 →
    履约落 `failed` 而不是 `partially_completed` ✗
    （读者以为什么都没留下，而库里已经多了一条）。
    """

    def test_a_side_effect_survives_a_later_contract_failure(self):
        """**写库成功了，后面校验没过 → 落 partially_completed**"""
        writes: list[str] = []

        def handler(payload):
            writes.append("committed")
            return {"ok": True}

        def broken_around(view, params):
            view["call"]()
            return {"ok": "changed"}  # 改了产出 → 契约违反

        from tests.test_op_around_and_timeout import _around_context

        ctx, bindings = _around_context(("cr_after_write", broken_around))
        run = _task_run()
        route = _beeline(plugins=bindings)
        route.operations = route.operations[:1]
        route.operations[0].next = []
        route.operations[0].leaves_artifact = True
        BeelineExecutor(_runner(handler), plugin_context=ctx).execute(run, route, {})

        assert writes == ["committed"]
        assert run.status == TaskRunStatus.PARTIALLY_COMPLETED, (
            run.status, run.operations["op1"].status
        )

    def test_a_side_effect_survives_a_bad_patch(self):
        """**写库成功了，补丁越界 → 落 partially_completed**"""
        writes: list[str] = []

        def handler(payload):
            writes.append("committed")
            return {"ok": True, "notes": []}

        ctx = _context(
            impls={"op.impl": lambda view, params: {"append": {"other": ["x"]}}}
        )
        run = _task_run()
        route = _beeline(plugins=[_binding(EXTENSION_OP_AFTER)])
        route.operations = route.operations[:1]
        route.operations[0].next = []
        route.operations[0].leaves_artifact = True
        BeelineExecutor(
            _runner(handler), plugin_context=ctx, schemas=_SCHEMAS
        ).execute(run, route, {})

        assert writes == ["committed"]
        assert run.status == TaskRunStatus.PARTIALLY_COMPLETED, (
            run.status, run.operations["op1"].status
        )

    def test_a_per_attempt_admission_crash_is_a_plugin_failure(self):
        """**逐次准入的插件崩了 → 记 plugin_failure**（与 per_call 同一条口径）"""
        def crashed(view, params):
            raise RuntimeError("per_attempt plugin crashed")

        ctx = _context(
            impls={"op.impl": crashed},
            point_overrides={
                EXTENSION_OP_BEFORE: {
                    "view_keys": ("input",),
                    "return_contract": "verdict",
                    "granularity": "per_attempt",
                }
            },
        )
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx
        ).execute(run, _beeline(plugins=[_binding(EXTENSION_OP_BEFORE)]), {"x": 1})

        assert run.status == TaskRunStatus.FAILED
        failures = [e for e in run.event_log.entries if e.event == "plugin_failure"]
        assert failures, [(e.event, e.detail) for e in run.event_log.entries]

    def test_an_after_hook_that_changes_nothing_needs_no_schema(self):
        """**插件没要改产出 → 不校验形状**（也就不会因为没给 schema 而中止）

        「results 非空」不等于「有补丁」——
        无条件校验会让「什么都没改」也要求 schema。
        """
        ctx = _context(impls={"op.impl": lambda view, params: None})
        run = _task_run()
        BeelineExecutor(
            _runner(lambda p: {"ok": True}), plugin_context=ctx
        ).execute(run, _beeline(plugins=[_binding(EXTENSION_OP_AFTER)]), {"x": 1})

        assert run.status == TaskRunStatus.COMPLETED, [
            (e.event, e.detail) for e in run.event_log.entries
        ]


# ============================================================
# 四、业务侧真的在用（设计盒的打包闸）
# ============================================================


class TestTheBusinessGate:
    def test_the_design_box_mounts_the_package_gate(self):
        """设计盒的打包腿挂了准入闸 ✓"""
        from boxes.wms_design.beeline import COMPONENTS

        gate = next(
            c for c in COMPONENTS if c.op_id == "package_design"
        )
        assert [p.id for p in gate.plugins] == ["wms_design_package_gate"]
        assert gate.plugins[0].extension_point == EXTENSION_OP_BEFORE

    def test_the_gate_rejects_an_incomplete_evidence(self):
        """证据缺了 → 准入拒绝（不进打包）"""
        from boxes.wms_design.plugins import gate_design_package

        problems = gate_design_package(
            {"input": {"requirement_count": 3}},  # 少了别的
            {"required_fields": ["requirement_count", "expectations"]},
        )
        assert problems is not None
        assert any("expectations" in p for p in problems["problems"]), problems

    def test_a_complete_evidence_passes(self):
        from boxes.wms_design.plugins import gate_design_package

        assert gate_design_package(
            {"input": {"requirement_count": 3, "expectations": []}},
            {"required_fields": ["requirement_count", "expectations"]},
        ) is None
