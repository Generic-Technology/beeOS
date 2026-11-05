"""
tests.test_box_health - 体检检查器本身的守卫

**这里验的是「检查器」**，不是某一只盒子。
某一只盒子自不自洽由 `tests/test_box_standard.py` 验（它调这个检查器）。

三件事：

1. **三态是真的三态**——「没验到」不许混成「通过」；
2. **每条检查都能被弄红，而且因为正确的理由红**——
   一条永远绿的检查等于没有检查（这族病：「写下来了」不等于「会跑」）；
3. **检查器不自己找盒子**——它接受声明作为入参。

第 2 条是重点。以前「盒子合不合规」住在 pytest 断言里，
守卫红不红全靠它自己那几行代码；搬进检查器之后，
**检查器自己会不会一直绿**就成了新的风险点——
一个永远说「通过」的检查器比没有检查器更坏：它发的是合格证。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from core.beeline_models import Bee, Beeline, Operation
from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    FieldDef,
    MetricsDef,
    PlacementDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    SchemaDef,
    TaskDef,
)
from runtime.box_health import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNKNOWN,
    BoxInspection,
    inspect_box,
    inspect_catalog,
    wiring_problems,
)


REPO = pathlib.Path(__file__).resolve().parent.parent
RUNTIME_DIR = REPO / "runtime"


# ============================================================
# 合成盒子
# ============================================================


def _definition(**over) -> BeeBoxDefinition:
    """一只自洽的合成盒子；over 用来把它弄坏"""
    base = dict(
        id="sample_box",
        version=1,
        description="合成盒子：只用来验检查器",
        schemas=[
            SchemaDef(
                id="schema_sample_in",
                fields=[FieldDef(name="topic", type="string")],
            ),
            SchemaDef(
                id="schema_sample_out",
                fields=[
                    FieldDef(name="item_count", type="integer"),
                    FieldDef(name="has_issue", type="boolean"),
                ],
            ),
        ],
        task=[
            TaskDef(
                type="do_sample",
                task_schema="schema_sample_in",
                beeline_id="beeline_sample",
                beeline_version=1,
            ),
        ],
        result=ResultDef(
            type="sample_result",
            result_schema="schema_sample_out",
            acceptance=[AcceptanceRule(metric="item_count", op="gte", value=1)],
            exceptions=[
                ExceptionRule(
                    condition="item_count > 0",
                    action="escalate",
                    reason="有东西要人看",
                ),
            ],
        ),
        queen=QueenDef(
            authorization=QueenAuthorization(),
            rules=QueenRules(),
        ),
        metrics=MetricsDef(),
        placement=PlacementDef(
            display_name="样例盒",
            value_stream="样例价值流",
            role="做样例",
            domain="样例域",
            feeds_into=[],
            feedback_into=[],
        ),
    )
    base.update(over)
    return BeeBoxDefinition(**base)


def _beeline(input_id="schema_sample_in", output_id="schema_sample_out") -> Beeline:
    return Beeline(
        id="beeline_sample",
        version=1,
        description="样例作业路线",
        operations=[
            Operation(
                op_id="op_sample",
                type="op_sample_type",
                input_from="external",
                input=input_id,
                output=output_id,
                next=[],
                bee=Bee(type="op_sample_type"),
            ),
        ],
    )


def _inspection(definition=None, **over) -> BoxInspection:
    kwargs = dict(
        box_id="sample_box",
        definition=definition or _definition(),
        beelines={"beeline_sample": _beeline()},
        sample_payloads={"do_sample": {"topic": "任意"}},
        has_runner=True,
        metric_computers={},
    )
    kwargs.update(over)
    return BoxInspection(**kwargs)


def _status(inspection: BoxInspection, check_id: str) -> str:
    health = inspect_box(inspection)
    return next(c for c in health.checks if c.id == check_id).status


# ============================================================
# 1. 三态
# ============================================================


class TestThreeStates:
    def test_a_clean_box_passes_everything(self):
        health = inspect_box(_inspection())
        assert health.verdict == STATUS_PASS, (
            f"自洽的合成盒子没全过："
            + "; ".join(f"{c.id}={c.status}" for c in health.checks)
        )

    def test_missing_material_is_unknown_not_pass(self):
        """材料没给 → 没验到，**不是通过**

        这是本模块最要紧的一条。「没验到」混成「通过」=
        给一件没验过的事发合格证。
        """
        inspection = _inspection(has_runner=None)
        assert _status(inspection, "runner_present") == STATUS_UNKNOWN

    def test_unknown_never_becomes_the_verdict_of_a_pass(self):
        """全是「没验到」的盒子，整体结论也不是「通过」"""
        inspection = _inspection(has_runner=None, metric_computers=None)
        health = inspect_box(inspection)
        assert health.verdict == STATUS_UNKNOWN
        assert health.verdict != STATUS_PASS

    def test_a_failure_outranks_an_unknown(self):
        """有红的就报红——「没验到」不能把红盖掉"""
        definition = _definition(result=ResultDef(
            type="sample_result",
            result_schema="schema_sample_out",
            acceptance=[AcceptanceRule(metric="no_such_metric", op="gte", value=1)],
            exceptions=[],
        ))
        health = inspect_box(_inspection(definition, has_runner=None))
        assert health.verdict == STATUS_FAIL
        assert health.failed, "有红的却没进 failed 列表"
        assert health.unknown, "没验到的那几项也该单独列出来"


# ============================================================
# 2. 每条检查都能被弄红，而且因正确的理由红
# ============================================================


class TestEveryCheckCanGoRed:
    """**一条永远绿的检查等于没有检查**

    每条检查都造一个「正好把它弄坏」的盒子，断言它转红，
    而且红的原因说得清是哪一条坏了。
    """

    def test_schemas_resolve(self):
        definition = _definition(schemas=[
            SchemaDef(id="schema_sample_in", fields=[FieldDef(name="topic", type="string")]),
            # 产出 schema 不在里面 → 引用落空
        ])
        assert _status(_inspection(definition), "schemas_resolve") == STATUS_FAIL

    def test_beeline_schemas_resolve(self):
        """作业路线那一跳指着一个没声明的形状 → 声明落空"""
        inspection = _inspection(
            beelines={"beeline_sample": _beeline(output_id="no_such_schema")},
        )
        assert _status(inspection, "beeline_schemas_resolve") == STATUS_FAIL

    def test_acceptance_readable(self):
        definition = _definition(result=ResultDef(
            type="sample_result",
            result_schema="schema_sample_out",
            acceptance=[AcceptanceRule(metric="no_such_metric", op="gte", value=1)],
            exceptions=[],
        ))
        assert _status(_inspection(definition), "acceptance_readable") == STATUS_FAIL

    def test_exceptions_readable(self):
        definition = _definition(result=ResultDef(
            type="sample_result",
            result_schema="schema_sample_out",
            acceptance=[AcceptanceRule(metric="item_count", op="gte", value=1)],
            exceptions=[
                ExceptionRule(condition="no_such_field > 0", action="escalate", reason=""),
            ],
        ))
        assert _status(_inspection(definition), "exceptions_readable") == STATUS_FAIL

    def test_exceptions_fire(self):
        """规则要求一个**该字段装不下的值** → 永远不响

        用一个整数字段去比 0.5：整数字段装不下 0.5，
        所以这条规则在任何产出上都不可能成立。
        """
        definition = _definition(result=ResultDef(
            type="sample_result",
            result_schema="schema_sample_out",
            acceptance=[AcceptanceRule(metric="item_count", op="gte", value=1)],
            exceptions=[
                ExceptionRule(condition="item_count == 0.5", action="escalate", reason=""),
            ],
        ))
        assert _status(_inspection(definition), "exceptions_fire") == STATUS_FAIL

    def test_exception_value_types(self):
        """布尔字段被拿去跟空串比 → 永远不响"""
        definition = _definition(result=ResultDef(
            type="sample_result",
            result_schema="schema_sample_out",
            acceptance=[AcceptanceRule(metric="item_count", op="gte", value=1)],
            exceptions=[
                ExceptionRule(condition="has_issue == ''", action="escalate", reason=""),
            ],
        ))
        assert _status(_inspection(definition), "exception_value_types") == STATUS_FAIL

    def test_example_submittable(self):
        """示例投料缺必填字段 → 照着投只会得到一句看不懂的报错"""
        inspection = _inspection(sample_payloads={"do_sample": {}})
        assert _status(inspection, "example_submittable") == STATUS_FAIL

    def test_maturity_honest(self):
        """live 却一条判据都没声明 → 「看起来成功，其实什么也没验」"""
        definition = _definition(result=ResultDef(
            type="sample_result",
            result_schema="schema_sample_out",
            acceptance=[],
            exceptions=[],
        ))
        assert _status(_inspection(definition), "maturity_honest") == STATUS_FAIL

    def test_placement_filled(self):
        """归属留空 → 跟没声明一样"""
        definition = _definition(placement=PlacementDef(
            display_name="", value_stream="", role="",
        ))
        assert _status(_inspection(definition), "placement_filled") == STATUS_FAIL

    def test_subject_ref_resolvable(self):
        """声明了产出里根本没有的键 → 认领不到，记录归不到工作单元"""
        definition = _definition(task=[TaskDef(
            type="do_sample",
            task_schema="schema_sample_in",
            beeline_id="beeline_sample",
            beeline_version=1,
            subject_ref_from_result="no_such_key",
            subject_kind="sample",
        )])
        assert _status(_inspection(definition), "subject_ref_resolvable") == STATUS_FAIL

    def test_metrics_wired(self):
        """声明了指标却没有采集算法 → 看板上那个数字是空的"""
        definition = _definition(metrics=MetricsDef(quality=[{
            "name": "sample_rate",
            "definition": "样例比率",
            "target": 1.0,
            "unit": "pct",
            "direction": "higher_is_better",
        }]))
        assert _status(_inspection(definition), "metrics_wired") == STATUS_FAIL

    def test_runner_present(self):
        assert _status(_inspection(has_runner=False), "runner_present") == STATUS_FAIL

    def test_every_check_id_has_a_case_here(self):
        """**检查项清单不许悄悄变长**

        加一条新的检查却忘了给它造一个「能弄红」的case，
        那条检查就没人验过它会不会一直绿。
        """
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        covered: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name.startswith("test_")
            ):
                for call in ast.walk(node):
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "_status"
                        and len(call.args) >= 2
                        and isinstance(call.args[1], ast.Constant)
                    ):
                        covered.add(call.args[1].value)
        declared = {c.id for c in inspect_box(_inspection()).checks}
        assert declared == covered, (
            "检查项清单和「能弄红」的case对不上：\n"
            f"  多出来的检查项：{sorted(declared - covered)}\n"
            f"  多出来的 case：{sorted(covered - declared)}"
        )


# ============================================================
# 3. 目录级
# ============================================================


class TestCatalog:
    def test_wiring_to_a_missing_box_is_reported(self):
        definition = _definition(placement=PlacementDef(
            display_name="样例盒",
            value_stream="样例价值流",
            role="做样例",
            feeds_into=["no_such_box"],
        ))
        problems = wiring_problems([_inspection(definition)])
        assert problems and "no_such_box" in problems[0]

    def test_wiring_inside_the_catalog_is_fine(self):
        other = _definition(id="other_box")
        definition = _definition(placement=PlacementDef(
            display_name="样例盒",
            value_stream="样例价值流",
            role="做样例",
            feeds_into=["other_box"],
        ))
        assert wiring_problems([
            _inspection(definition),
            _inspection(other, box_id="other_box"),
        ]) == []

    def test_catalog_health_counts_every_box(self):
        catalog = inspect_catalog([_inspection(), _inspection(box_id="other_box")])
        assert len(catalog.boxes) == 2
        assert catalog.to_dict()["counts"]["boxes"] == 2


# ============================================================
# 4. 检查器不自己找盒子
# ============================================================


class TestCheckerStaysGeneric:
    def test_the_checker_imports_no_box(self):
        """检查器接受声明作为入参——它不该自己认盒子

        它要是自己 import 盒子，内核就反向依赖产品了；
        它要是自己认 box_id 字面量，就把某一只盒子的知识写进了内核。
        """
        offenders = []
        for path in sorted(RUNTIME_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    offenders += [
                        f"{path.name}: {a.name}"
                        for a in node.names
                        if a.name.startswith("boxes")
                    ]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("boxes"):
                        offenders.append(f"{path.name}: {node.module}")
        assert not offenders, f"内核反向依赖了盒子：{offenders}"

    def test_the_checker_does_not_name_any_box(self):
        """检查器的源码里不许出现任何一只盒子的 id

        用 **ast** 扫（标识符 + 非 docstring 字符串），不是文本 find：
        注释里可以自由讨论业务（解释「为什么这条规则指向一个拿不到的量」
        就该提业务名），代码里则一个字都不该有。
        """
        from kanban.trigger import BOX_REGISTRY

        offenders = []
        for path in sorted(RUNTIME_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    text = ast.get_docstring(node, clean=False)
                    if text is not None:
                        docstrings.add(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    candidates = [node.id]
                elif isinstance(node, ast.Attribute):
                    candidates = [node.attr]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in docstrings:
                        continue
                    candidates = [node.value]
                else:
                    continue
                for candidate in candidates:
                    for box_id in BOX_REGISTRY:
                        if box_id in candidate:
                            offenders.append(f"{path.name}: {candidate!r}")
        assert not offenders, f"内核里写死了盒子 id：{sorted(set(offenders))}"
