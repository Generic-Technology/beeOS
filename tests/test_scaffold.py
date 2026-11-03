"""tests.test_scaffold - 装配器：协议能不能挡住自相矛盾的声明

**这个模块守的是「装配期就报错」这件事。**

以前这些错要等到履约跑起来才炸（或者更糟——永远不炸）：
判据写了一个产出里不存在的字段、例外条件写成裸标识符、
op 的数据来源指了一个不存在的 op、作业路线成环、末跳吐的形状跟声明的产出契约不是一回事。

现在它们在**装配的时候**就被拒了。所以每条校验都配一个**会红的守卫**：
这里造一只故意写错的盒子，断言装配器拒它，并且**说清楚该怎么改**。

## 另一半：拿全部已注册盒子过一遍装配器

协议不是照着想象写的，是照着现有盒子验过的——
装回去的东西必须跟原来**一模一样**。这一条是协议完整性的证明：
新协议表达不了某只盒子，就说明协议漏了东西，而不是那只盒子该改。

见 `docs/architecture-upgrade.md`。
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from pydantic import BaseModel, ValidationError

from core.beeline_models import Beeline
from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    FieldDef,
    ResultDef,
    SchemaDef,
)
from core.scaffold import (
    AssemblyError,
    BoxDeclaration,
    Component,
    assemble,
)
from scripts.scaffold_migrate import (
    components_from_beeline,
    declaration_from_definition,
)

REPO = pathlib.Path(__file__).resolve().parent.parent


# ============================================================
# 一只合法的最小盒子
# ============================================================


def _handler(input_data: dict) -> dict:
    return {"ok": True, "count": 1}


SCHEMAS = [
    SchemaDef(
        id="s_in",
        fields=[FieldDef(name="text", type="string", required=True)],
    ),
    SchemaDef(
        id="s_out",
        fields=[
            FieldDef(name="ok", type="boolean"),
            FieldDef(name="count", type="number"),
        ],
    ),
]


def _declaration(**overrides) -> BoxDeclaration:
    base = dict(
        id="demo_box",
        version=1,
        description="演示盒",
        display_name="演示盒",
        domain="Demo",
        value_stream="Demo",
        task_type="run_demo",
        task_schema="s_in",
        result_type="demo_result",
        result_schema="s_out",
        acceptance=[AcceptanceRule(metric="ok", op="eq", value=True)],
        exceptions=[ExceptionRule(condition="count > 0", action="escalate")],
        beeline_id="beeline_demo_v1",
        beeline_version=1,
    )
    base.update(overrides)
    return BoxDeclaration(**base)


def _components(**overrides) -> list[Component]:
    base = dict(
        op_id="demo_work",
        type="demo_work",
        input_from="external",
        input="s_in",
        output="s_out",
        next=[],
        handler=_handler,
    )
    base.update(overrides)
    return [Component(**base)]


def _assemble(declaration=None, components=None, schemas=None, handlers=None):
    return assemble(
        declaration if declaration is not None else _declaration(),
        components if components is not None else _components(),
        schemas=SCHEMAS if schemas is None else schemas,
        handlers=handlers,
    )


def _broken(**overrides) -> AssemblyError:
    """装一只故意写坏的盒子，断言它被拒"""
    with pytest.raises(AssemblyError) as excinfo:
        _assemble(**overrides)
    return excinfo.value


# ============================================================
# 装配器的基本行为
# ============================================================


class TestAssembleHappyPath:
    def test_a_valid_box_assembles(self):
        box = _assemble()
        assert box.definition.id == "demo_box"
        assert box.beeline.id == "beeline_demo_v1"
        assert box.report.op_count == 1
        assert box.report.checks, "报告里要记下验过什么"

    def test_the_handler_table_covers_every_op(self):
        box = _assemble()
        assert "demo_work" in box.handlers

    def test_the_handler_table_can_come_from_the_box_instead_of_the_component(self):
        """盒子也可以把实现放在自己的 handler 表里，由装配器按 type 接上"""
        box = _assemble(
            components=[Component(
                op_id="demo_work", type="demo_work",
                input_from="external", input="s_in", output="s_out",
            )],
            handlers={"demo_work": _handler},
        )
        assert box.handlers["demo_work"] is _handler

    def test_the_report_says_what_it_checked(self):
        box = _assemble()
        joined = " ".join(box.report.checks)
        assert "handler" in joined
        assert "入口" in joined


# ============================================================
# 每条校验：一只故意写坏的盒子
# ============================================================


class TestC1EveryOpHasAHandler:
    def test_an_op_without_an_implementation_is_refused(self):
        err = _broken(components=[Component(
            op_id="demo_work", type="demo_work",
            input_from="external", input="s_in", output="s_out",
        )])
        assert "没有实现" in str(err)
        assert "demo_work" in str(err)


class TestC2DataSourceIsSelfConsistent:
    def test_input_from_pointing_at_a_nonexistent_op_is_refused(self):
        err = _broken(components=[
            _components()[0],
            Component(
                op_id="demo_second", type="demo_second",
                input_from="demo_nowhere", input="s_out", output="s_out",
                handler=_handler,
            ),
        ])
        assert "不存在" in str(err)
        assert "demo_nowhere" in str(err)

    def test_input_shape_mismatching_the_source_output_is_refused(self):
        err = _broken(components=[
            _components()[0],
            Component(
                op_id="demo_second", type="demo_second",
                input_from="demo_work", input="s_in", output="s_out",
                handler=_handler,
            ),
        ])
        assert "形状对不上" in str(err)


class TestC3ExactlyOneEntry:
    def test_two_entries_are_refused(self):
        err = _broken(components=[
            _components()[0],
            Component(
                op_id="demo_second", type="demo_second",
                input_from="external", input="s_in", output="s_out",
                handler=_handler,
            ),
        ])
        assert "入口跳" in str(err)

    def test_no_entry_at_all_is_refused(self):
        """一个都没有 external 入口 → 投料没有地方可去"""
        err = _broken(components=[
            Component(
                op_id="demo_a", type="demo_a",
                input_from="demo_b", input="s_out", output="s_out",
                next=["demo_b"], handler=_handler,
            ),
            Component(
                op_id="demo_b", type="demo_b",
                input_from="demo_a", input="s_out", output="s_out",
                next=["demo_a"], handler=_handler,
            ),
        ])
        assert "没有入口跳" in str(err)

    def test_the_entry_must_eat_the_payload_shape(self):
        err = _broken(components=[Component(
            op_id="demo_work", type="demo_work",
            input_from="external", input="s_out", output="s_out",
            handler=_handler,
        )])
        assert "投料契约" in str(err)


class TestC4ExecutionOrderCarriesTheRightShape:
    def test_next_edge_with_a_mismatching_shape_is_refused(self):
        """运行时会把上一跳的产出**直接**喂给下一跳——
        形状对不上，那一跳拿到的就是它没声明期待的东西
        """
        err = _broken(components=[
            Component(
                op_id="demo_first", type="demo_first",
                input_from="external", input="s_in", output="s_out",
                next=["demo_second"], handler=_handler,
            ),
            Component(
                op_id="demo_second", type="demo_second",
                input_from="demo_first", input="s_in", output="s_out",
                handler=_handler,
            ),
        ])
        assert "形状对不上" in str(err)

    def test_next_pointing_at_a_nonexistent_op_is_refused(self):
        err = _broken(components=[Component(
            op_id="demo_work", type="demo_work",
            input_from="external", input="s_in", output="s_out",
            next=["demo_nowhere"], handler=_handler,
        )])
        assert "不存在的 op" in str(err)


class TestC5NoOrphansNoCycles:
    def test_an_unreachable_op_is_refused(self):
        """走不到的跳永远不跑，也不报错——链在某处断了"""
        err = _broken(components=[
            _components()[0],
            Component(
                op_id="demo_orphan", type="demo_orphan",
                input_from="external", input="s_in", output="s_out",
                handler=_handler,
            ),
        ])
        assert "走不到" in str(err)

    def test_a_cycle_is_refused(self):
        err = _broken(components=[
            Component(
                op_id="demo_a", type="demo_a",
                input_from="external", input="s_in", output="s_out",
                next=["demo_b"], handler=_handler,
            ),
            Component(
                op_id="demo_b", type="demo_b",
                input_from="demo_a", input="s_out", output="s_out",
                next=["demo_a"], handler=_handler,
            ),
        ])
        assert "成环" in str(err)


class TestC6C7SchemasExist:
    def test_an_op_naming_an_undeclared_schema_is_refused(self):
        err = _broken(components=[Component(
            op_id="demo_work", type="demo_work",
            input_from="external", input="s_missing", output="s_out",
            handler=_handler,
        )])
        assert "未声明的 schema" in str(err)

    def test_a_result_schema_that_does_not_exist_is_refused(self):
        err = _broken(declaration=_declaration(result_schema="s_missing"))
        assert "result_schema" in str(err)

    def test_a_task_schema_that_does_not_exist_is_refused(self):
        err = _broken(declaration=_declaration(task_schema="s_missing"))
        assert "task_schema" in str(err)


class TestC8AcceptanceCanReadAValue:
    def test_a_metric_absent_from_the_result_schema_is_refused(self):
        """判据读不到值 → 恒过（假绿灯）或恒红"""
        err = _broken(declaration=_declaration(
            acceptance=[AcceptanceRule(metric="no_such_metric", op="gte", value=0)],
        ))
        assert "no_such_metric" in str(err)
        assert "判据" in str(err)

    def test_a_live_box_with_zero_acceptance_is_refused(self):
        """零判据会被判成「全过」——那是「看起来成功，其实什么也没验」的谎"""
        err = _broken(declaration=_declaration(acceptance=[]))
        assert "一条判据都没声明" in str(err)

    def test_a_skeleton_box_with_zero_acceptance_is_allowed(self):
        """骨架盒的判据是「将来要验的东西」，现在不验是诚实的"""
        box = _assemble(declaration=_declaration(acceptance=[], maturity="skeleton"))
        assert box.definition.result.maturity == "skeleton"


class TestC9ExceptionsCanActuallyFire:
    def test_a_bare_identifier_condition_is_refused(self):
        """裸标识符永远不触发——规则写在那里，安静地没做"""
        err = _broken(declaration=_declaration(
            exceptions=[ExceptionRule(condition="some_flag", action="escalate")],
        ))
        assert "三段式" in str(err)

    def test_two_comparisons_in_one_part_are_refused(self):
        """多半是漏了 AND——硬跑下去会在比较时抛 TypeError"""
        err = _broken(declaration=_declaration(
            exceptions=[ExceptionRule(
                condition="count > 0 ok == True", action="escalate",
            )],
        ))
        assert "漏了 AND" in str(err)

    def test_a_condition_reading_an_absent_field_is_refused(self):
        """读到的永远是 None → 这条规则永远不触发"""
        err = _broken(declaration=_declaration(
            exceptions=[ExceptionRule(condition="no_such_field > 0", action="escalate")],
        ))
        assert "永远不触发" in str(err)


class TestC10TheEndOfTheChainIsTheDeclaredArtifact:
    def test_the_terminal_output_must_match_the_result_schema(self):
        err = _broken(
            declaration=_declaration(result_schema="s_in"),
            components=[Component(
                op_id="demo_work", type="demo_work",
                input_from="external", input="s_in", output="s_out",
                handler=_handler,
            )],
        )
        assert "产出的形状跟声明的不是一回事" in str(err)


class TestC12SubjectRefIsObtainable:
    def test_a_subject_ref_field_absent_from_the_task_schema_is_refused(self):
        """声明了却拿不到 → 归档时这条履约记录归不到任何工作单元"""
        err = _broken(declaration=_declaration(
            subject_ref_field="no_such_field", subject_kind="requirement",
        ))
        assert "声明了却拿不到" in str(err)

    def test_subject_ref_without_a_kind_is_refused(self):
        """「它关于哪一类业务对象」不能靠猜"""
        err = _broken(declaration=_declaration(subject_ref_field="text"))
        assert "不能靠猜" in str(err)


class TestWiringDeclaration:
    def test_feeds_into_equal_to_feedback_into_is_refused(self):
        """闭环边混进 feeds_into，拓扑排序遇到环就静默按原顺序收尾"""
        err = _broken(declaration=_declaration(
            feeds_into=["other_box"], feedback_into=["other_box"],
        ))
        assert "两件事" in str(err)


class TestErrorQuality:
    def test_all_problems_are_reported_at_once(self):
        """一次报齐——让人改一个报一次，等于把「填表」变成「猜题」"""
        err = _broken(
            declaration=_declaration(
                acceptance=[AcceptanceRule(metric="no_such_metric", op="gte", value=0)],
                exceptions=[ExceptionRule(condition="no_such_field > 0", action="escalate")],
            ),
            components=[Component(
                op_id="demo_work", type="demo_work",
                input_from="external", input="s_in", output="s_out",
            )],
        )
        joined = " ".join(err.problems)
        assert "no_such_metric" in joined
        assert "no_such_field" in joined
        assert "没有实现" in joined

    def test_the_error_names_the_box(self):
        err = _broken(declaration=_declaration(
            acceptance=[AcceptanceRule(metric="no_such_metric", op="gte", value=0)],
        ))
        assert err.box_id == "demo_box"
        assert "demo_box" in str(err)

    def test_every_problem_says_what_to_do(self):
        """报错要带能修它的材料——只说「不合法」等于没说"""
        err = _broken(declaration=_declaration(
            acceptance=[AcceptanceRule(metric="no_such_metric", op="gte", value=0)],
        ))
        problem = next(p for p in err.problems if "no_such_metric" in p)
        assert "产出 schema" in problem
        assert "可用的字段" in problem


# ============================================================
# 协议完整性：拿全部已注册盒子过一遍装配器
# ============================================================


class TestEveryRegisteredBoxSurvivesAssembly:
    """新协议表达不了某只盒子 → 协议漏了东西，不是那只盒子该改

    这条守卫是协议与现实之间的**唯一接口**：协议改了、盒子加了，
    这里都会响。
    """

    @staticmethod
    def _boxes():
        from kanban.trigger import BOX_REGISTRY, RUNNER_REGISTRY

        for box_id, (get_def, beeline_loaders) in sorted(BOX_REGISTRY.items()):
            yield box_id, get_def(), beeline_loaders, RUNNER_REGISTRY[box_id]

    def test_every_registered_box_can_be_expressed_in_the_new_protocol(self):
        for box_id, definition, beeline_loaders, runner in self._boxes():
            for _beeline_id, load in beeline_loaders.items():
                beeline = load()
                declaration = declaration_from_definition(definition, beeline=beeline)
                components = components_from_beeline(beeline)
                box = assemble(
                    declaration,
                    components,
                    schemas=definition.schemas,
                    handlers=runner.handlers,
                )
                assert box.definition.id == box_id

    def test_assembling_a_box_back_gives_the_same_declaration(self):
        """装回去的东西必须跟原来**一模一样**——
        差一个字段，迁移就悄悄改了盒子的行为
        """
        for box_id, definition, beeline_loaders, runner in self._boxes():
            for _beeline_id, load in beeline_loaders.items():
                beeline = load()
                declaration = declaration_from_definition(definition, beeline=beeline)
                components = components_from_beeline(beeline)
                box = assemble(
                    declaration,
                    components,
                    schemas=definition.schemas,
                    handlers=runner.handlers,
                )
                assert box.definition == definition, f"{box_id} 的声明装回去不一样了"
                assert box.beeline == beeline, f"{box_id} 的作业路线装回去不一样了"

    def test_every_op_of_every_box_has_an_implementation(self):
        for box_id, definition, beeline_loaders, runner in self._boxes():
            for _beeline_id, load in beeline_loaders.items():
                beeline = load()
                for op in beeline.operations:
                    assert runner.supports(op.type), (
                        f"{box_id} 的 op {op.op_id!r}（type={op.type!r}）"
                        f"在它自己的 runner 里没有实现"
                    )


# ============================================================
# 迁移期的路径纪律
# ============================================================


def _module_of(box_id: str) -> str:
    """box_id → 它在 boxes/ 下的模块名

    从注册表推（不手抄名单）——手抄的名册必漂：
    加一只盒子忘了改名单，那条守卫就永远绿着 = 形同虚设。
    """
    from kanban.trigger import BOX_REGISTRY

    get_def = BOX_REGISTRY[box_id][0]
    parts = get_def.__module__.split(".")
    # 迁移前是 "boxes.<mod>.definition"，迁移后是 "boxes.<mod>"——
    # 两种都取第二段。
    return parts[1]


def _all_modules() -> list[str]:
    """注册表里全部盒子的模块名（从注册表推）"""
    from kanban.trigger import BOX_REGISTRY

    return [_module_of(box_id) for box_id in sorted(BOX_REGISTRY)]


def _definition_source(module: str) -> str:
    return (REPO / "boxes" / module / "definition.py").read_text(encoding="utf-8")


def _uses_legacy_path(module: str) -> bool:
    """这只盒子还在旧写法上（手写 BeeBoxDefinition）"""
    return "BeeBoxDefinition(" in _definition_source(module)


#: 还在**旧写法**上的盒子模块——**写死的迁移台账**。
#:
#: **为什么手写而不是自动推导**：自动推导的话，
#: 「新盒子必须走装配器」这条守卫就形同虚设——
#: 有人手写一只新盒子用了旧写法，派生集合跟着它一起变，守卫永远绿。
#:
#: 手写清单是**意图**（「这些盒子还没迁」），派生集合是**事实**
#: （「这些盒子确实还在旧写法」）。两者必须对上：
#: 对不上时红，并说清该改哪一边。
#:
#: **每迁走一只，就从这里划掉一条**——迁到最后这个清单是空的。
LEGACY_BOX_MODULES = (
    "wms_analysis",
    "wms_development",
    "wms_ops",
    "wms_requirement",
    "wms_system",
    "wms_testing",
)


class TestOnePathPerBox:
    """一只盒子只走一条路径——不许两套规矩并行

    迁移期必然有两套路径并存（这是计划内的）。但**一只盒子内部**
    不许同时出现两套写法：那种盒子「一半新一半旧」，
    读的人得同时记两套规矩，改的时候容易只改一半。
    """

    def test_no_box_mixes_the_two_paths(self):
        for module in _all_modules():
            src = _definition_source(module)
            uses_new = "BoxDeclaration(" in src
            uses_old = "BeeBoxDefinition(" in src
            assert not (uses_new and uses_old), (
                f"{module} 的 definition.py 同时用了 BoxDeclaration 和 "
                f"BeeBoxDefinition——一只盒子只走一条路径"
            )

    def test_a_migrated_box_assembles_itself(self):
        """走了新路径的盒子，**必须真的在装配**——
        声明写在文件里却没人装它，等于没迁移
        """
        migrated = []
        for module in _all_modules():
            if "BoxDeclaration(" not in _definition_source(module):
                continue
            migrated.append(module)
            init_src = (REPO / "boxes" / module / "__init__.py").read_text(
                encoding="utf-8"
            )
            assert "assemble(" in init_src, (
                f"{module} 已经用 BoxDeclaration 声明了，却没有调用装配器——"
                f"声明写在文件里没人装，等于没迁移"
            )
        assert migrated, "一只盒子都没迁——迁移期的守卫自己先失效了"

    def test_the_migration_is_actually_moving(self):
        """迁过的盒子要真的能装出来，而且装出来的东西跟旧的一样

        这条不是「格式对不对」，是**行为没变**。
        """
        import importlib

        from kanban.trigger import BOX_REGISTRY

        for box_id in sorted(BOX_REGISTRY):
            module = _module_of(box_id)
            if "BoxDeclaration(" not in _definition_source(module):
                continue
            # 装配在 import 时就发生——导入得进去，说明装得出来
            importlib.import_module(f"boxes.{module}")
            assert box_id in BOX_REGISTRY

    def test_no_new_box_may_use_the_legacy_path(self):
        """**新盒子必须走装配器**——手写也不例外

        脚手架现在直接生成新写法，所以正常路径下这只守卫不会红。
        它红只有一个原因：**有人手写了一只新盒子，用了旧写法**。
        那种盒子不会被装配器校验——判据读不到字段、例外永远不触发，
        要等履约跑起来才炸（或者永远不炸）。

        对不上时红，并说清该改哪一边：
        - 派生集合里多出来的 = 新写的旧盒子 → 把它改成新写法
        - 台账里多出来的 = 已经迁走了却忘了划掉 → 划掉它
        """
        derived = {m for m in _all_modules() if _uses_legacy_path(m)}
        recorded = set(LEGACY_BOX_MODULES)

        assert derived == recorded, (
            "旧路径台账跟实际对不上：\n"
            f"  实际还在旧写法：{sorted(derived)}\n"
            f"  台账记着：      {sorted(recorded)}\n"
            f"  新写的旧盒子：  {sorted(derived - recorded)}（改成新写法）\n"
            f"  忘了划掉的：    {sorted(recorded - derived)}（从台账里划掉）"
        )


class TestTheModelsDoNotSilentlyDropDeclarations:
    """**写了却没存**当场报错，不许静默吞

    实测过：票据盒给 `ResultDef` 写了 `description=...`、
    给 `ExceptionRule` 写了 `reason=...`——而当时这两个字段**并不存在**，
    pydantic 默认 `extra="ignore"` → **静默丢弃**。

    后果：作者写的那句说明**从来没被存下来**，代码却看着像写了。
    这是「声明了却拿不到」那族病在**模型层**的表现——
    你以为声明了，其实没声明。

    所以 core 的模型一律 `extra="forbid"`：不认识的参数当场报错，
    当场说得清是哪个字段、哪个类。
    """

    def test_an_unknown_field_is_rejected_not_swallowed(self):
        with pytest.raises(ValidationError) as excinfo:
            ResultDef(
                type="x", result_schema="y", acceptance=[], exceptions=[],
                nosuchfield=1,
            )
        assert "nosuchfield" in str(excinfo.value)

    def test_the_result_description_is_actually_kept(self):
        """写了就得存下来——不然写它干什么"""
        result = ResultDef(
            type="x", result_schema="y", acceptance=[], exceptions=[],
            description="这个产出是什么",
        )
        assert result.description == "这个产出是什么"

    def test_the_exception_reason_is_actually_kept(self):
        """例外条款的「为什么」要送到收到的人手里"""
        rule = ExceptionRule(
            condition="items_count == 0", action="no_deliver",
            reason="照片太糊 / 不是小票 / 拍的是二维码，别硬编",
        )
        assert "别硬编" in rule.reason

    def test_every_core_model_forbids_extra_fields(self):
        """不只是这两个——core 里的每个模型都不许静默吞"""
        import core.models as m

        for name in dir(m):
            obj = getattr(m, name)
            if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
                continue
            # **只看 core.models 自己定义的模型**——
            # `BaseModel` 是从 pydantic 导入进来的，它不属于这一层。
            if obj.__module__ != m.__name__:
                continue
            config = obj.model_config or {}
            assert config.get("extra") == "forbid", (
                f"core.models.{name} 还会静默吞掉不认识的参数"
            )


# ============================================================
# 分层：装配器住在 core，不许反向依赖执行层
# ============================================================


class TestScaffoldStaysInTheLowerLayer:
    def test_core_scaffold_does_not_import_runtime(self):
        """「runtime 面向 core 的脚手架执行」——反过来 core 就依赖上层了

        这条不是洁癖：写装配器的时候真的差点 import 了
        `runtime.runner.HandlerRunner`。所以钉住。
        """
        path = REPO / "core" / "scaffold.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(m.name for m in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                offenders.append(node.module)
        runtime_imports = [m for m in offenders if m.startswith("runtime")]
        assert runtime_imports == [], (
            f"core/scaffold.py 反向依赖了执行层：{runtime_imports}"
        )

    def test_core_conditions_is_the_single_source_for_the_condition_grammar(self):
        """条件语法只有一处定义——两处各写一份 = 两份事实 = 漂"""
        from core.conditions import condition_fields, condition_parts
        from runtime.acceptance import condition_fields as runtime_fields
        from runtime.acceptance import condition_parts as runtime_parts

        cond = "count > 0 and other == 'x'"
        assert runtime_parts(cond) == condition_parts(cond)
        assert runtime_fields(cond) == condition_fields(cond)
