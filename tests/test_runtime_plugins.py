"""tests.test_runtime_plugins - 插件执行机制：runtime 真的能把它调起来

**这个模块守的是「机制成立」这件事**，不是某只盒子的行为。

机制的三条纪律，各配一条**会红的**守卫：

1. **插件只看见扩展点声明的键**（多一个少一个都报错），
   而且拿到的是**深拷贝**——它改自己的那份，改不到宿主的。
2. **插件只能返回扩展点约定的东西**（决策类钩子必须返回带 `decision` 键的 dict，
   不是光一个词——理由、命中的规则、用不上的规则都在里面）。
3. **插件失败 → 履约中止，不降级**；状态**按阶段**落；
   失败**必须落盘**（只在内存里抛一下 = 等于没发生过）。

**「深拷贝不是隔离」也要守住这条边界**：它保证的是
「插件无法通过传入视图直接修改宿主数据」，
**不保证**同进程插件的闭包、全局引用和外部副作用。
"""

from __future__ import annotations

import pytest

from core.plugins import (
    ExtensionPoint,
    PluginBinding,
    PluginContext,
    PluginType,
    register_extension_point,
    register_implementation,
    register_plugin_type,
)
from runtime.models import TaskRunStatus, create_task_run
from runtime.plugins import (
    EXTENSION_ESCALATE,
    PluginContractError,
    PluginFailure,
    abort_on_plugin_failure,
    build_view,
    call_plugins,
    check_return,
)


# ============================================================
# 工具
# ============================================================


@pytest.fixture
def plugin_registry():
    """登记表是全局的——测试要把它恢复原样"""
    from core import plugins as plugins_mod

    saved = (
        dict(plugins_mod.PLUGIN_TYPES),
        dict(plugins_mod.EXTENSION_POINTS),
        dict(plugins_mod.IMPLEMENTATIONS),
    )
    try:
        yield plugins_mod
    finally:
        plugins_mod.PLUGIN_TYPES.clear()
        plugins_mod.PLUGIN_TYPES.update(saved[0])
        plugins_mod.EXTENSION_POINTS.clear()
        plugins_mod.EXTENSION_POINTS.update(saved[1])
        plugins_mod.IMPLEMENTATIONS.clear()
        plugins_mod.IMPLEMENTATIONS.update(saved[2])


def _context(
    plugin_registry,
    *,
    impl,
    point_id: str = "test.point",
    view_keys: tuple = ("result",),
    return_contract: str = "decision",
    kind: str = "decision",
    max_per_host: int = 1,
) -> PluginContext:
    """登记一套东西，冻结成装配快照"""
    plugin_registry.register_extension_point(
        ExtensionPoint(
            id=point_id,
            kind=kind,
            max_bindings_per_host=max_per_host,
            view_keys=view_keys,
            return_contract=return_contract,
        )
    )
    plugin_registry.register_plugin_type(
        PluginType(
            id="test_plugin",
            allowed_hosts=("box",),
            extension_points=(point_id,),
            implementation_key="test_impl",
        )
    )
    plugin_registry.register_implementation("test_impl", impl)
    return plugin_registry.freeze_plugin_context()


def _binding(**overrides) -> PluginBinding:
    base = dict(
        id="test_plugin",
        extension_point="test.point",
        params={},
        implementation_key="test_impl",
    )
    base.update(overrides)
    return PluginBinding(**base)


def _task_run() -> "object":
    return create_task_run(
        originator="tester",
        intent="verify_plugin_mechanism",
        contract_ref="c",
        release_id="r",
        beeline_id="b",
        beeline_version=1,
        runtime_id="rt",
        instance_id="i",
    )


def _plugin_failure_entry(run):
    """从事件流里挑出那条 `plugin_failure`

    **别拿最后一条**：状态转换自己也会追加一条 `status_change`，
    而顺序是「先记失败，再转状态」——所以失败那条在状态变更**之前**。
    """
    for entry in reversed(run.event_log.entries):
        if entry.event == "plugin_failure":
            return entry
    raise AssertionError("事件流里没有 plugin_failure")


# ============================================================
# 一、受限视图
# ============================================================


class TestRestrictedView:
    def test_the_plugin_only_sees_the_declared_keys(self, plugin_registry):
        seen: dict = {}

        def impl(view, params):
            seen.update(view)
            return {"decision": "human_review"}

        ctx = _context(plugin_registry, impl=impl, view_keys=("result",))
        call_plugins(
            ctx, [_binding()],
            extension_point="test.point",
            source={"result": {"x": 1}},
        )
        assert set(seen) == {"result"}

    def test_an_extra_key_is_refused(self, plugin_registry):
        """runtime 不许把扩展点没声明的东西递进去"""

        def impl(view, params):  # pragma: no cover —— 不该被调到
            raise AssertionError("不该被调用")

        ctx = _context(plugin_registry, impl=impl, view_keys=("result",))
        with pytest.raises(PluginFailure) as e:
            call_plugins(
                ctx, [_binding()],
                extension_point="test.point",
                source={"result": {"x": 1}, "secret": "不该给"},
            )
        assert "secret" in str(e.value)

    def test_a_missing_key_is_refused(self, plugin_registry):
        """少递一个键 = 插件拿不到它要的东西，出来的结果必然不对"""

        def impl(view, params):  # pragma: no cover —— 不该被调到
            raise AssertionError("不该被调用")

        ctx = _context(plugin_registry, impl=impl, view_keys=("result", "failed_rules"))
        with pytest.raises(PluginFailure):
            call_plugins(
                ctx, [_binding()],
                extension_point="test.point",
                source={"result": {"x": 1}},
            )

    def test_the_plugin_cannot_change_the_host_data_through_the_view(self, plugin_registry):
        """**深拷贝**：插件改视图，改的是副本

        这条保证的是「插件无法通过传入视图直接修改宿主数据」。
        """
        host = {"result": {"count": 1}}

        def impl(view, params):
            view["result"]["count"] = 999
            view["injected"] = True
            return {"decision": "human_review"}

        ctx = _context(plugin_registry, impl=impl, view_keys=("result",))
        call_plugins(ctx, [_binding()], extension_point="test.point", source=host)
        assert host == {"result": {"count": 1}}


# ============================================================
# 二、返回契约
# ============================================================


class TestReturnContract:
    def test_a_decision_hook_requires_a_decision_object(self, plugin_registry):
        """决策类钩子必须返回一个**带 decision 键的 dict**

        只返回一个词，就把理由、命中的规则、用不上的规则扔了——
        那正是「用不上的规则必须看得见」这条纪律要防的事。
        """

        def impl(view, params):
            return "human_review"  # ← 光一个词，不行

        ctx = _context(plugin_registry, impl=impl, return_contract="decision")
        with pytest.raises(PluginFailure) as e:
            call_plugins(
                ctx, [_binding()],
                extension_point="test.point",
                source={"result": {}},
            )
        assert "decision" in str(e.value)

    def test_a_none_hook_refuses_a_value(self, plugin_registry):
        def impl(view, params):
            return {"whatever": 1}

        ctx = _context(plugin_registry, impl=impl, return_contract="none")
        with pytest.raises(PluginFailure):
            call_plugins(
                ctx, [_binding()],
                extension_point="test.point",
                source={"result": {}},
            )

    def test_the_full_decision_object_is_accepted(self, plugin_registry):
        """四项都在的决策对象，原样带回来"""
        decision = {
            "decision": "human_review",
            "reason": "验收没过",
            "triggered_rule": "count > 0",
            "unusable_rules": ["x > 0（产出里没有 x）"],
        }

        def impl(view, params):
            return decision

        ctx = _context(plugin_registry, impl=impl, return_contract="decision")
        out = call_plugins(
            ctx, [_binding()],
            extension_point="test.point",
            source={"result": {}},
        )
        assert out == [decision]


# ============================================================
# 三、顺序与失败
# ============================================================


class TestCallOrderAndFailure:
    def test_the_plugins_run_in_the_order_the_host_declared(self, plugin_registry):
        """按**宿主声明的顺序**，不按注册顺序

        注册顺序取决于 import 顺序，不能让它影响业务行为。
        """
        order: list[str] = []

        def impl_a(view, params):
            order.append("a")
            return {"decision": "human_review"}

        def impl_b(view, params):
            order.append("b")
            return {"decision": "human_review"}

        ctx = _context(plugin_registry, impl=impl_a, max_per_host=5)
        plugin_registry.register_implementation("test_impl_2", impl_b)
        ctx = plugin_registry.freeze_plugin_context()

        call_plugins(
            ctx,
            [_binding(implementation_key="test_impl"), _binding(implementation_key="test_impl_2")],
            extension_point="test.point",
            source={"result": {}},
        )
        assert order == ["a", "b"]

    def test_a_raising_plugin_becomes_a_plugin_failure(self, plugin_registry):
        def impl(view, params):
            raise RuntimeError("插件自己炸了")

        ctx = _context(plugin_registry, impl=impl)
        with pytest.raises(PluginFailure) as e:
            call_plugins(
                ctx, [_binding()],
                extension_point="test.point",
                source={"result": {}},
            )
        assert e.value.plugin_id == "test_plugin"
        assert e.value.extension_point == "test.point"
        assert "插件自己炸了" in str(e.value.cause)


# ============================================================
# 四、失败怎么落
# ============================================================


class TestFailureLands:
    def test_a_failure_after_the_chain_aborts_to_failed(self, plugin_registry):
        """链走完之后（`completed`）插件失败 → `failed`

        `completed → partially_completed` **不被状态机允许**，
        所以这里只能落 `failed`——语义也对：没交付，且没人接。
        """
        def impl(view, params):
            raise RuntimeError("升级决策插件炸了")

        ctx = _context(plugin_registry, impl=impl)
        failure = PluginFailure("test_plugin", "test.point", RuntimeError("升级决策插件炸了"))

        run = _task_run()
        run.transition_to(TaskRunStatus.RUNNING)
        run.transition_to(TaskRunStatus.COMPLETED)

        abort_on_plugin_failure(run, failure)
        assert run.status == TaskRunStatus.FAILED
        entry = _plugin_failure_entry(run)
        assert "升级决策插件炸了" in (entry.detail or {}).get("error", "")

    def test_the_failure_is_recorded_in_the_event_log(self, plugin_registry):
        """失败必须落盘——只在内存里抛一下 = 等于没发生过"""
        def impl(view, params):  # pragma: no cover
            raise RuntimeError("x")

        ctx = _context(plugin_registry, impl=impl)
        failure = PluginFailure("test_plugin", "test.point", RuntimeError("x"))

        run = _task_run()
        run.transition_to(TaskRunStatus.RUNNING)
        abort_on_plugin_failure(run, failure)

        events = [e.event for e in run.event_log.entries]
        assert "plugin_failure" in events
        entry = _plugin_failure_entry(run)
        assert entry.detail["plugin_id"] == "test_plugin"
        assert entry.detail["extension_point"] == "test.point"

    def test_a_failure_mid_chain_with_residue_becomes_partially_completed(self, plugin_registry):
        """链途中失败 + 有残留 → `partially_completed`"""
        def impl(view, params):  # pragma: no cover
            raise RuntimeError("x")

        ctx = _context(plugin_registry, impl=impl)
        failure = PluginFailure("test_plugin", "test.point", RuntimeError("x"))

        run = _task_run()
        run.transition_to(TaskRunStatus.RUNNING)
        abort_on_plugin_failure(run, failure, leaves_artifact=True)
        assert run.status == TaskRunStatus.PARTIALLY_COMPLETED

    def test_a_failure_mid_chain_without_residue_becomes_failed(self, plugin_registry):
        """链途中失败 + 无残留 → `failed`"""
        def impl(view, params):  # pragma: no cover
            raise RuntimeError("x")

        ctx = _context(plugin_registry, impl=impl)
        failure = PluginFailure("test_plugin", "test.point", RuntimeError("x"))

        run = _task_run()
        run.transition_to(TaskRunStatus.RUNNING)
        abort_on_plugin_failure(run, failure, leaves_artifact=False)
        assert run.status == TaskRunStatus.FAILED

    def test_an_illegal_transition_is_not_forced(self, plugin_registry):
        """状态机不允许的状态**不硬改**——硬转会抛异常

        但失败已经落盘了，读者至少看得见发生过什么。
        """
        def impl(view, params):  # pragma: no cover
            raise RuntimeError("x")

        ctx = _context(plugin_registry, impl=impl)
        failure = PluginFailure("test_plugin", "test.point", RuntimeError("x"))

        run = _task_run()
        run.transition_to(TaskRunStatus.RUNNING)
        run.transition_to(TaskRunStatus.COMPLETED)
        run.transition_to(TaskRunStatus.FAILED)  # 终态

        abort_on_plugin_failure(run, failure)  # 不许抛
        assert run.status == TaskRunStatus.FAILED
        assert "plugin_failure" in [e.event for e in run.event_log.entries]


# ============================================================
# 五、runtime 留的钩子
# ============================================================


class TestRuntimeExtensionPoints:
    def test_the_escalate_hook_is_registered(self):
        """runtime 留的钩子在这里——core 不认钩子的名字"""
        from core.plugins import EXTENSION_POINTS

        point = EXTENSION_POINTS[EXTENSION_ESCALATE]
        assert point.kind == "decision"
        assert point.max_bindings_per_host == 1
        assert point.view_keys == ("result", "failed_rules")
        assert point.return_contract == "decision"
