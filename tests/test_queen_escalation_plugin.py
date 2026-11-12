"""tests.test_queen_escalation_plugin - 升级决策收编成盒级插件之后

**这个模块守的是「搬迁没改行为」**。

升级决策以前是 runtime 里一个直接调用的函数，
现在收编成**盒级插件** `queen_escalation`：

- 规则住在**插件参数**里（`queen.rules` 那边不再有它）
- runtime 在**升级决策钩子**（`box.escalate`）上调用它
- 它返回**完整决策对象**（理由 / 命中的规则 / 用不上的规则都在里面）

所以这里守三件事：

1. **规则搬迁了**：各盒子的升级规则在插件参数里，与旧位置逐条相同
2. **行为没变**：走插件那条链出来的决策，跟直接求值一致
3. **旧位置退休了**：再往 `queen.rules.exception_handling` 里写升级规则，当场报错
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.models import QueenRules
from core.plugins import (
    EXTENSION_POINTS,
    PLUGIN_TYPES,
    PluginSpec,
    check_plugin_spec,
)
from kanban.trigger import BOX_REGISTRY, PLUGIN_CONTEXT_REGISTRY
from runtime.plugins import EXTENSION_ESCALATE, call_plugins
from runtime.queen import decide_escalation, queen_escalation_plugin


# ============================================================
# 1. 规则搬迁了
# ============================================================


class TestRulesMoved:
    def test_every_registered_box_mounts_the_escalation_plugin(self):
        """每只盒子都挂「升级决策」插件（盒级宿主）"""
        for box_id, (get_def, _bl) in BOX_REGISTRY.items():
            definition = get_def()
            ids = [p.id for p in definition.plugins]
            assert ids == ["queen_escalation"], f"{box_id}: 插件 {ids}"

    def test_the_rules_live_in_the_plugin_params(self):
        """规则的**唯一真源**是插件参数"""
        for box_id, (get_def, _bl) in BOX_REGISTRY.items():
            definition = get_def()
            binding = definition.plugins[0]
            assert binding.extension_point == EXTENSION_ESCALATE
            assert "escalation_rules" in binding.params, box_id

    def test_the_old_place_is_empty(self):
        """旧位置（`queen.rules.exception_handling`）里不再有升级规则"""
        for box_id, (get_def, _bl) in BOX_REGISTRY.items():
            definition = get_def()
            handling = definition.queen.rules.exception_handling or {}
            assert "escalation_rules" not in handling, (
                f"{box_id}: 升级规则还在旧位置——两份事实"
            )

    def test_writing_the_rules_to_the_old_place_is_rejected(self):
        """再往旧位置写升级规则 → **当场报错**

        「声明了却没人执行」比不声明更坏——
        规则写在旧位置，runtime 只读插件参数，
        读者却以为它在守着。
        """
        with pytest.raises(ValidationError, match="退休了"):
            QueenRules(
                exception_handling={
                    "escalation_rules": [
                        {"trigger": "x > 0", "action": "escalate_to_human"},
                    ],
                }
            )


# ============================================================
# 2. 行为没变
# ============================================================


class TestBehaviourUnchanged:
    def test_the_plugin_path_matches_the_direct_decision(self):
        """**走插件那条链**（快照 → 绑定 → 实现 → 参数）出来的决策，
        跟直接求值**完全一致**。

        这是「搬迁没改行为」的证明：链路上任何一环走偏，
        决策就会不一样。
        """
        cases = [
            ({"build_failed": True},
             [{"trigger": "build_failed == true", "action": "escalate_to_human"}]),
            ({"build_failed": False},
             [{"trigger": "build_failed == true", "action": "escalate_to_human"}]),
            ({"amount": 1},
             [{"trigger": "amount < 10", "action": "auto_resolve"}]),
            ({"amount": 1}, []),
            ({"no_such_metric": 1},
             [{"trigger": "no_such_metric > 3", "action": "escalate_to_human"}]),
        ]
        box_id = next(iter(BOX_REGISTRY))
        context = PLUGIN_CONTEXT_REGISTRY[box_id]
        get_def, _bl = BOX_REGISTRY[box_id]
        binding = get_def().plugins[0]

        for result, rules in cases:
            direct = decide_escalation(result, [], rules).to_dict()
            via_plugin = call_plugins(
                context,
                [binding.model_copy(update={"params": {"escalation_rules": rules}})],
                extension_point=EXTENSION_ESCALATE,
                source={"result": result, "failed_rules": []},
            )[0]
            assert via_plugin == direct, (result, rules)

    def test_the_decision_carries_all_four_fields(self):
        """决策**不只是一个词**：理由、命中的规则、用不上的规则都在里面"""
        decision = queen_escalation_plugin(
            # **产出里根本没有这个字段** → 这条规则「用不上」
            {"result": {"amount": 1}, "failed_rules": []},
            {"escalation_rules": [
                {"trigger": "no_such_metric > 3", "action": "escalate_to_human"},
            ]},
        )
        assert set(decision) == {
            "decision", "reason", "triggered_rule", "unusable_rules",
        }
        assert decision["unusable_rules"], "用不上的规则被吞了"

    def test_a_box_without_rules_gets_the_same_default(self):
        """**没挂插件**与**挂了插件但规则为空** → 决策**逐字相同**

        「升级 = 交给人」是 runtime 的默认口径，
        不许自己决定不升级。
        """
        from runtime.queen import default_escalation_decision

        without_plugin = default_escalation_decision()
        with_empty_rules = queen_escalation_plugin(
            {"result": {}, "failed_rules": []}, {"escalation_rules": []}
        )
        assert with_empty_rules == without_plugin


# ============================================================
# 3. 机制本身
# ============================================================


class TestPluginMechanism:
    def test_the_plugin_type_is_registered(self):
        """通用型插件由**公共提供方**（runtime）登记一次"""
        ptype = PLUGIN_TYPES["queen_escalation"]
        assert ptype.allowed_hosts == ("box",)
        assert ptype.extension_points == (EXTENSION_ESCALATE,)
        assert ptype.implementation_key == "queen.escalation"

    def test_the_escalate_extension_point_is_registered(self):
        """升级决策钩子是**决策类**：每个宿主只许一个绑定"""
        point = EXTENSION_POINTS[EXTENSION_ESCALATE]
        assert point.kind == "decision"
        assert point.max_bindings_per_host == 1
        assert point.return_contract == "decision"

    def test_the_binding_is_serializable(self):
        """绑定要进 definition（要落盘、要导出差分）→ 必须可序列化"""
        import json

        box_id = next(iter(BOX_REGISTRY))
        get_def, _bl = BOX_REGISTRY[box_id]
        payload = json.loads(get_def().model_dump_json())
        assert payload["plugins"][0]["id"] == "queen_escalation"
        assert payload["plugins"][0]["implementation_key"] == "queen.escalation"

    def test_the_spec_is_rejected_on_a_wrong_host_kind(self):
        """升级决策是**盒级**插件，挂到跳上要报错"""
        problems = check_plugin_spec(
            PluginSpec(id="queen_escalation", extension_point=EXTENSION_ESCALATE),
            host_kind="op",
        )
        assert any("不允许挂在 op 级宿主上" in p for p in problems), problems
