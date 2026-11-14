"""tests.test_box_verify_plugin - 盒级核验钩子 + 产出形状闸

**这个模块守两件事**：

1. **产出的形状闸**（通用）：产出必须符合自己声明的 `result_schema`。
   以前产出从不过闸 → 缺字段 → 判据判「用不上」→ **照样交付**。
2. **盒级核验钩子**（业务）：产出定案后、验收之前，
   盒子自己的核验插件跑一遍；它说有问题 → 履约中止。

**为什么用设计盒做端到端**：它的 op handler 都是确定性的，
所以守卫不靠网络的运气。

**但它有一条腿真的会调 LLM**：`design_generator` → `bind_requirements()`
（`boxes/wms_design/binder.py`）——WMS 概念绑定走模型。
所以这些测试**显式申请 `stub_llm` 夹具**（仓里的规矩：桩要显式申请，
不做 autouse），不靠外网通不通。
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

from core.plugins import PluginContext
from kanban import trigger as trig
from kanban.trigger import _EXAMPLE_PAYLOADS, trigger_task
from runtime.plugins import EXTENSION_VERIFY
from runtime.store import TaskRunStore


BOX_ID = "wms_design_box"
TASK_TYPE = "handle_design_request"


def _run(payload: dict[str, Any] | None = None) -> tuple[dict, dict]:
    """跑一次设计盒的履约，返回 (响应, 落盘记录)"""
    if payload is None:
        payload = _EXAMPLE_PAYLOADS[(BOX_ID, TASK_TYPE)]
    store = TaskRunStore(os.path.join(tempfile.mkdtemp(prefix="m4_test_"), "runs.jsonl"))
    out = trigger_task(BOX_ID, TASK_TYPE, payload, store)
    return out, store.list_recent(limit=1)[0]


def _events_of(record: dict) -> list[str]:
    """记录里没有事件流（那是内存里的），这里从落盘的其它痕迹看"""
    return [str(k) for k in record.keys()]


# ============================================================
# 一、产出形状闸（通用）
# ============================================================


class TestOutputShapeGate:
    def test_a_conforming_output_goes_through(self, stub_llm):
        """产出符合契约 → 闸放行（这次履约按它本来的样子走完）"""
        out, record = _run()
        assert record["status"] != "failed" or "产出不合产出契约" not in (
            record.get("error_reason") or ""
        )

    def test_a_broken_output_stops_the_fulfillment(self, monkeypatch, stub_llm):
        """**产出不合契约 → 履约中止**，不许悄悄交付

        以前没有这道闸：缺一个必填字段 → 判据读不到 → 判「用不上」→
        不进失败也不进通过 → 照样 ACCEPTED。
        """
        from boxes.wms_design.runner import RUNNER

        real = dict(RUNNER._handlers)

        def _broken_package(input_data: dict[str, Any]) -> dict[str, Any]:
            out = dict(real["design_packager"](input_data))
            # 把一个声明为 boolean 的字段写成字符串 → 形状不合
            out["wms_binding"] = "不是布尔"
            return out

        monkeypatch.setitem(RUNNER._handlers, "design_packager", _broken_package)
        out, record = _run()

        assert record["status"] == "failed"
        reason = record.get("error_reason") or ""
        assert "产出不合产出契约" in reason, reason
        # 理由要说清**哪个字段、期望什么**——不然读者得自己去猜
        assert "wms_binding" in reason, reason

    def test_the_failure_is_persisted_not_just_raised(self, monkeypatch, stub_llm):
        """**不合契约也要先落盘再报错**——记录不许凭空消失

        这是本仓库的老纪律：先抛的话这次履约的记录就没了，
        而「记录消失」正是这套机制要防的那族病。
        """
        from boxes.wms_design.runner import RUNNER

        real = dict(RUNNER._handlers)

        def _broken_package(input_data: dict[str, Any]) -> dict[str, Any]:
            out = dict(real["design_packager"](input_data))
            out["wms_binding"] = "不是布尔"
            return out

        monkeypatch.setitem(RUNNER._handlers, "design_packager", _broken_package)
        out, record = _run()

        # 记录在（落盘了），而且带着失败理由
        assert record["status"] == "failed"
        assert record.get("error_reason")


# ============================================================
# 二、盒级核验钩子（业务插件）
# ============================================================


class TestBoxVerifyHook:
    def test_the_hook_is_registered_and_only_fires_on_a_box(self):
        from core.plugins import EXTENSION_POINTS

        point = EXTENSION_POINTS[EXTENSION_VERIFY]
        assert point.host_kinds == ("box",)
        assert point.view_keys == ("result",)
        assert point.return_contract == "verdict"

    def test_the_design_box_mounts_its_own_business_plugin(self):
        """**业务侧贡献的类型 + 参数 + 实现**，core 不沾手"""
        from boxes.wms_design import get_definition

        definition = get_definition()
        ids = [p.id for p in definition.plugins]
        assert "wms_design_output_check" in ids

        binding = next(
            p for p in definition.plugins if p.id == "wms_design_output_check"
        )
        assert binding.extension_point == EXTENSION_VERIFY
        assert binding.params["truth_checks"], "业务插件必须声明它要核对的规矩"

    def test_the_plugin_type_is_registered_by_the_business_side(self):
        """插件类型登记在 `boxes/*` 里，不在 core 里"""
        from core.plugins import PLUGIN_TYPES

        ptype = PLUGIN_TYPES["wms_design_output_check"]
        assert ptype.allowed_hosts == ("box",)
        assert ptype.extension_points == (EXTENSION_VERIFY,)
        assert ptype.params_model is not None

    def test_a_failed_check_stops_the_fulfillment(self, monkeypatch, stub_llm):
        """**核验没通过 → 履约中止**，而且问题清单要落盘

        注入一个「说有问题」的实现（换掉快照里的那份）。
        """
        ctx = trig.PLUGIN_CONTEXT_REGISTRY[BOX_ID]
        impls = dict(ctx.implementations)
        impls["wms_design.output_check"] = (
            lambda view, params: {"problems": ["注入的核验问题"]}
        )
        monkeypatch.setitem(
            trig.PLUGIN_CONTEXT_REGISTRY,
            BOX_ID,
            PluginContext(
                types=dict(ctx.types),
                extension_points=dict(ctx.extension_points),
                implementations=impls,
            ),
        )
        out, record = _run()

        assert record["status"] == "failed"
        reason = record.get("error_reason") or ""
        assert "注入的核验问题" in reason, reason

    def test_a_broken_plugin_and_a_failed_check_are_different(self, monkeypatch, stub_llm):
        """**插件自己坏了** ≠ **核验没通过**——读者要能分开看见

        前者是机制问题（插件抛异常），后者是业务问题（产出不合规矩）。
        """
        ctx = trig.PLUGIN_CONTEXT_REGISTRY[BOX_ID]
        impls = dict(ctx.implementations)

        def _boom(view, params):
            raise RuntimeError("插件自己炸了")

        impls["wms_design.output_check"] = _boom
        monkeypatch.setitem(
            trig.PLUGIN_CONTEXT_REGISTRY,
            BOX_ID,
            PluginContext(
                types=dict(ctx.types),
                extension_points=dict(ctx.extension_points),
                implementations=impls,
            ),
        )
        out, record = _run()

        assert record["status"] == "failed"
        reason = record.get("error_reason") or ""
        assert "插件自己炸了" in reason, reason
        # **核验没通过时那句话在**——两种失败的原因不一样
        assert "没通过" not in reason, reason

    def test_the_hook_is_really_called(self, monkeypatch, stub_llm):
        """**到达约定扩展点时确实执行**——不许「声明了却一次都没跑过」

        注入一个「数一数被调了几次」的实现，跑完看它是不是 ≥ 1。
        """
        ctx = trig.PLUGIN_CONTEXT_REGISTRY[BOX_ID]
        impls = dict(ctx.implementations)
        calls: list[int] = []

        def _counting(view, params):
            calls.append(1)
            return None

        impls["wms_design.output_check"] = _counting
        monkeypatch.setitem(
            trig.PLUGIN_CONTEXT_REGISTRY,
            BOX_ID,
            PluginContext(
                types=dict(ctx.types),
                extension_points=dict(ctx.extension_points),
                implementations=impls,
            ),
        )
        _run()
        assert calls, "核验钩子一次都没被调用——它挂在那儿是摆设"


# ============================================================
# 三、verdict 契约
# ============================================================


class TestVerdictContract:
    def test_a_verdict_must_be_none_or_a_problems_dict(self, plugin_registry):
        from runtime.plugins import PluginContractError, check_return
        from core.plugins import ExtensionPoint

        point = ExtensionPoint(
            id="v", host_kinds=("box",), return_contract="verdict"
        )
        assert check_return(point, None) is None
        assert check_return(point, {"problems": ["x"]}) == {"problems": ["x"]}
        with pytest.raises(PluginContractError):
            check_return(point, "我不该返回字符串")
        with pytest.raises(PluginContractError):
            check_return(point, {"不是 problems": []})


# ============================================================
# 四、业务侧能自己登记新类型（不改 core）
# ============================================================


class TestBusinessCanRegisterItsOwnPlugin:
    def test_a_new_business_type_needs_no_core_change(self, plugin_registry):
        """**新增一种业务插件不用改 core**——这就是「开放注册」的意义

        core 只管协议：类型登记、参数校验、扩展点、实现绑定。
        业务侧自己登记、自己实现、自己挂上。
        """
        from core.plugins import (
            ExtensionPoint,
            PluginSpec,
            PluginType,
            register_extension_point,
            register_implementation,
            register_plugin_type,
        )
        from core.scaffold import Component, assemble

        plugin_registry.register_extension_point(
            ExtensionPoint(id="biz.point", host_kinds=("op", "box"))
        )
        plugin_registry.register_plugin_type(
            PluginType(
                id="biz_owned_type",
                allowed_hosts=("op", "box"),
                extension_points=("biz.point",),
                implementation_key="biz.impl",
            )
        )
        plugin_registry.register_implementation("biz.impl", lambda view, params: None)

        from tests.test_scaffold import SCHEMAS, _declaration

        box = assemble(
            _declaration(),
            [
                Component(
                    op_id="demo_work",
                    type="demo_work",
                    input_from="external",
                    input="s_in",
                    output="s_out",
                    plugins=[
                        PluginSpec(id="biz_owned_type", extension_point="biz.point")
                    ],
                )
            ],
            schemas=SCHEMAS,
            handlers={"demo_work": lambda payload: {"ok": True}},
        )
        assert box.beeline.operations[0].plugins[0].id == "biz_owned_type"


# ============================================================
# 五、CR 补的五条守卫（2026-11-14）
# ============================================================


class TestSnapshotIsReallyFrozen:
    def test_the_snapshot_cannot_be_mutated(self):
        """**装配后的快照改不动**——那才是「冻结」

        `frozen=True` 只冻结**字段赋值**，冻结不了 dict 里的内容——
        `ctx.implementations[key] = x` 照样成功。
        那「装配期验过的那份 = 运行时跑的那份」就成了一句空话：
        装配之后换掉实现，运行时跑的就是**另一个**实现，而没人知道。
        """
        from boxes.wms_design import ASSEMBLED

        ctx = ASSEMBLED.plugin_context
        with pytest.raises(TypeError):
            ctx.implementations["queen.escalation"] = lambda *a: None
        with pytest.raises(TypeError):
            ctx.types["x"] = None
        with pytest.raises(TypeError):
            ctx.extension_points["x"] = None
        # 读还是读得到
        assert "queen.escalation" in ctx.implementations


class TestFailClosed:
    def test_a_verify_plugin_without_a_context_stops_the_fulfillment(
        self, monkeypatch, stub_llm
    ):
        """**声明了核验插件却找不到装配快照 → 履约中止**，不静默跳过

        静默跳过的话，读者以为它在守着，实际它一次都没跑过——
        那正是「声明了却没人执行」，比报错更坏。
        """
        from kanban import trigger as trig

        monkeypatch.delitem(trig.PLUGIN_CONTEXT_REGISTRY, BOX_ID, raising=False)
        out, record = _run()

        assert record["status"] == "failed"
        reason = record.get("error_reason") or ""
        assert "插件上下文" in reason, reason
        assert "不静默跳过" in reason or "核验不会执行" in reason, reason

    def test_a_box_without_a_verify_plugin_is_not_affected(self, stub_llm):
        """没挂核验插件的盒子 → 不跑核验，也**不**因此中止"""
        from kanban.trigger import BOX_REGISTRY

        other = "learning_growth_box"
        get_def, _bl = BOX_REGISTRY[other]
        assert not [
            p for p in get_def().plugins if p.extension_point == EXTENSION_VERIFY
        ], "这只盒子不该挂核验插件"

        store = TaskRunStore(
            os.path.join(tempfile.mkdtemp(prefix="m4_fc_"), "runs.jsonl")
        )
        out = trigger_task(
            other,
            "run_growth",
            _EXAMPLE_PAYLOADS[(other, "run_growth")],
            store,
        )
        assert out["status"] != "failed" or "插件上下文" not in (
            out.get("error_reason") or ""
        )


class TestVerdictContractIsStrict:
    def test_non_string_problems_are_refused(self):
        """`problems` 的元素必须是**能直接读懂的字符串**

        宽松的校验等于让「读不懂的问题」混进来，
        而读者看到的只是「核验没过」——不知道为什么。
        """
        from runtime.plugins import PluginContractError, check_return
        from core.plugins import ExtensionPoint

        point = ExtensionPoint(
            id="v", host_kinds=("box",), return_contract="verdict"
        )
        with pytest.raises(PluginContractError):
            check_return(point, {"problems": [{"detail": "x"}]})
        with pytest.raises(PluginContractError):
            check_return(point, {"problems": [""]})

    def test_extra_keys_are_refused(self):
        """**不许有多余的键**（仓里「未知字段当场报错」那条纪律）"""
        from runtime.plugins import PluginContractError, check_return
        from core.plugins import ExtensionPoint

        point = ExtensionPoint(
            id="v", host_kinds=("box",), return_contract="verdict"
        )
        with pytest.raises(PluginContractError):
            check_return(point, {"problems": [], "还有别的": 1})
