"""
runtime.box_health - 一只盒子的体检台

**它接受声明作为入参，不自己去找盒子。**

为什么：runtime 是内核，不许 import 盒子（`tests/test_layering.py` 管着这条）。
「这只盒子有哪些、有哪些作业路线、示例投料长什么样」是**调用方**的事
（看板/工作台知道注册表），本模块只回答「这份声明自不自洽」。

## 为什么要有一个共享的检查器

以前「盒子合不合规」这件事住在 `tests/test_box_standard.py` 里——
一条一条的 pytest 断言。给产品做体检台时如果照着抄一遍，
就会出现两套判定：守卫说绿、页面说红，而且没人知道该信哪个。

所以判定收在这里：**守卫和页面都调它**。一处判定，两处引用。

## 三态，不是两态

每条检查有三种结果：

| status | 意思 |
|---|---|
| `pass` | 验过了，成立 |
| `fail` | 验过了，不成立 |
| `unknown` | **没验到**（材料没给、或这件事现在算不出来） |

「没验到」不许混成「通过」——那是在给一件没验过的事发合格证。
看板和目录页必须让这三态**长得不一样**。

## 口径归 `core/decl_checks.py`

「引用存不存在」「判据读不读得到值」「例外条件能不能触发」
这几条判定住在 core，装配器（写声明时）和体检台（读声明时）共用一份。
本模块不重写它们——重写就是两份事实 = 漂。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from core.beeline_models import Beeline
from core.conditions import COMPARISON_PATTERN, condition_comparisons, condition_parts
from core.decl_checks import (
    check_acceptance_metrics,
    check_exception_conditions,
    check_maturity,
    check_op_schema_refs,
    check_schema_refs,
    check_skeleton_is_honest,
    check_subject_ref,
    result_field_types,
    schema_ids_of,
)
from core.metrics import orphan_computers, unwired
from core.models import BeeBoxDefinition
from runtime.acceptance import evaluate_condition
from runtime.contract import validate_payload


#: 验过了，成立
STATUS_PASS = "pass"
#: 验过了，不成立
STATUS_FAIL = "fail"
#: 没验到（材料没给 / 现在算不出来）
STATUS_UNKNOWN = "unknown"

ALL_STATUSES = (STATUS_PASS, STATUS_FAIL, STATUS_UNKNOWN)


# ============================================================
# 数据形状
# ============================================================


@dataclass(frozen=True)
class HealthCheck:
    """一条检查的结论

    Attributes:
        id: 检查项的稳定标识（页面和守卫都按它认人）
        title: 一句话说这条查什么
        why: **为什么重要**——不写这一栏，看板就只是红绿两色，
            看的人不知道红在哪、值不值得修
        status: 三态之一（pass / fail / unknown）
        detail: 证据文字
        evidence: 结构化证据（问题清单、读数…）
    """

    id: str
    title: str
    why: str
    status: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "why": self.why,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class BoxInspection:
    """一只盒子拿来体检的全部材料

    **材料由调用方准备**：runtime 不许 import 盒子，所以「这只盒子是
    哪只、有哪些作业路线、示例投料长什么样、有没有执行器」
    都得从外面递进来。

    递不进来的东西，对应的检查项就是 `unknown`——**没验到**，
    不是「通过」。
    """

    box_id: str
    definition: BeeBoxDefinition
    #: 作业路线 id → 作业路线
    beelines: Mapping[str, Beeline] = field(default_factory=dict)
    #: task 类型 → 示例投料
    sample_payloads: Mapping[str, Any] = field(default_factory=dict)
    #: 有没有执行器（None = 不知道）
    has_runner: Optional[bool] = None
    #: 指标采集算法表（None = 不知道）
    metric_computers: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class BoxHealth:
    """一只盒子的体检结论"""

    box_id: str
    checks: tuple[HealthCheck, ...]

    @property
    def passed(self) -> list[HealthCheck]:
        return [c for c in self.checks if c.status == STATUS_PASS]

    @property
    def failed(self) -> list[HealthCheck]:
        return [c for c in self.checks if c.status == STATUS_FAIL]

    @property
    def unknown(self) -> list[HealthCheck]:
        return [c for c in self.checks if c.status == STATUS_UNKNOWN]

    @property
    def verdict(self) -> str:
        """这只盒子整体体检结论

        有红的 → 红；有「没验到」的但没红的 → **没验到**（不是绿）；
        全过 → 绿。

        「没验到」绝不并进「绿」：那是给没验过的东西发合格证。
        """
        if self.failed:
            return STATUS_FAIL
        if self.unknown:
            return STATUS_UNKNOWN
        return STATUS_PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "box_id": self.box_id,
            "verdict": self.verdict,
            "counts": {
                STATUS_PASS: len(self.passed),
                STATUS_FAIL: len(self.failed),
                STATUS_UNKNOWN: len(self.unknown),
            },
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass(frozen=True)
class CatalogHealth:
    """整个目录的体检结论"""

    boxes: tuple[BoxHealth, ...]
    #: 目录级的问题（接线指向不存在的盒子等）
    problems: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": (
                STATUS_FAIL
                if self.problems or any(b.verdict == STATUS_FAIL for b in self.boxes)
                else STATUS_UNKNOWN
                if any(b.verdict == STATUS_UNKNOWN for b in self.boxes)
                else STATUS_PASS
            ),
            "counts": {
                "boxes": len(self.boxes),
                "boxes_failed": sum(1 for b in self.boxes if b.verdict == STATUS_FAIL),
                "boxes_unknown": sum(1 for b in self.boxes if b.verdict == STATUS_UNKNOWN),
                "boxes_passed": sum(1 for b in self.boxes if b.verdict == STATUS_PASS),
            },
            "problems": list(self.problems),
            "boxes": [b.to_dict() for b in self.boxes],
        }


# ============================================================
# 检查项
# ============================================================


def _verdict(
    check_id: str,
    title: str,
    why: str,
    problems: Sequence[str],
    *,
    missing: Sequence[str] = (),
    missing_note: str = "",
) -> HealthCheck:
    """把一份问题清单变成一条检查结论

    有问题 → 红；没问题但**有验不到的** → 没验到；否则绿。
    """
    if problems:
        return HealthCheck(
            check_id, title, why, STATUS_FAIL,
            "\n".join(problems), {"problems": list(problems)},
        )
    if missing:
        return HealthCheck(
            check_id, title, why, STATUS_UNKNOWN,
            missing_note, {"missing": list(missing)},
        )
    return HealthCheck(check_id, title, why, STATUS_PASS, "", {})


def inspect_box(inspection: BoxInspection) -> BoxHealth:
    """给一只盒子做体检 → 每条检查一个结论"""
    d = inspection.definition
    declared = schema_ids_of(d.schemas)
    types = result_field_types(d.schemas, d.result.result_schema)
    fields = set(types)

    checks: list[HealthCheck] = []

    # ---- 1 引用的形状都存在吗 ----
    checks.append(_verdict(
        "schemas_resolve",
        "投料和产出的形状都存在",
        "投料契约和产出契约都指着这只盒子声明过的形状。"
        "指空了就是「声明了却拿不到」——要么恒绿，要么恒红。",
        check_schema_refs(
            schema_ids=declared,
            result_schema=d.result.result_schema,
            task_schemas={t.type: t.task_schema for t in d.task},
        ),
    ))

    op_refs: list[tuple[str, str, str]] = []
    for beeline_id, beeline in inspection.beelines.items():
        for op in beeline.operations:
            for role, sid in (("input", op.input), ("output", op.output)):
                op_refs.append(
                    (f"作业路线 {beeline_id!r} 的 op {op.op_id!r}", role, sid)
                )
    checks.append(_verdict(
        "beeline_schemas_resolve",
        "作业路线每一跳的形状都存在",
        "每一跳吃进去、吐出来的形状都必须是这只盒子声明过的。"
        "指着一个不存在的形状，调用方拼出来的数据没有任何契约说它长什么样。",
        check_op_schema_refs(schema_ids=declared, op_refs=op_refs),
    ))

    # ---- 2 判据读得到值吗 ----
    checks.append(_verdict(
        "acceptance_readable",
        "判据读得到值",
        "判据声明的每个量，产出里都得真有那个字段。"
        "读不到值 = 恒过或恒红，两种都是坏结果。",
        check_acceptance_metrics(
            result_schema=d.result.result_schema,
            result_fields=fields,
            acceptance=d.result.acceptance,
        ),
    ))

    # ---- 3 例外条件读得到吗 ----
    checks.append(_verdict(
        "exceptions_readable",
        "例外条件读得到值",
        "例外条件读不到值时**不报错也不提示**，直接算「不成立」。"
        "后果是规则永远不触发——安静地没做，比判据读不到更隐蔽。",
        check_exception_conditions(
            result_schema=d.result.result_schema,
            result_fields=fields,
            exceptions=d.result.exceptions,
        ),
    ))

    # ---- 4 例外条件真的能响吗 ----
    checks.append(_verdict(
        "exceptions_fire",
        "例外条件真的能触发",
        "「字段存在」是弱证明：字段在，但取值类型对不上、比较符写反了，"
        "规则照样永远不响。这里反着验——造一个该触发的产出，看它响不响。",
        _condition_fire_problems(d, types),
    ))

    # ---- 5 条件里的值类型对得上吗 ----
    checks.append(_verdict(
        "exception_value_types",
        "条件里的值类型对得上字段类型",
        "字段在、规则能被合成值点亮，都证明不了这一条："
        "生产里那个字段存的是布尔，拿它跟空串比，永远是假。",
        _condition_type_problems(d, types),
    ))

    # ---- 6 示例投料投得出去吗 ----
    payload_problems, payload_missing = _example_payload_problems(inspection)
    checks.append(_verdict(
        "example_submittable",
        "示例投料投得出去",
        "页面把示例投料直接显示出来，用户复制粘贴就能用。"
        "示例不满足投料契约，那份示例就是假的——照着投只会得到一句看不懂的报错。",
        payload_problems,
        missing=payload_missing,
        missing_note=(
            "这些 task 没有示例投料，验不了："
            + "、".join(payload_missing)
        ),
    ))

    # ---- 7 成熟度跟自己的声明对得上吗 ----
    checks.append(_verdict(
        "maturity_honest",
        "成熟度跟自己的声明对得上",
        "零判据的盒子会被判成「全过」——那是「看起来成功，其实什么也没验」的谎。"
        "骨架盒该真声明它将来要验的东西，让「未实现」是真的。",
        check_maturity(
            maturity=d.result.maturity,
            acceptance=d.result.acceptance,
        ) + check_skeleton_is_honest(
            maturity=d.result.maturity,
            acceptance=d.result.acceptance,
            metric_names=[
                m.name
                for group in (d.metrics.quality, d.metrics.latency, d.metrics.cost)
                for m in group
            ],
        ),
    ))

    # ---- 8 展示位填了吗 ----
    checks.append(_verdict(
        "display_fields_filled",
        "展示位填了",
        "展示位留空串跟没声明一样——铭牌照样显示「—」。"
        "接线不在这里（它在协作边界声明里），这里管的是「这只盒子叫什么、"
        "在价值流哪一段、什么职责」。",
        _display_problems(d),
    ))

    # ---- 9 业务对象标识拿得到吗 ----
    checks.append(_verdict(
        "subject_ref_resolvable",
        "业务对象标识拿得到",
        "这只盒子的履约归不到哪个工作单元，全看这几条声明："
        "标识从投料里取还是从产出里取。说不清 = 这条记录在归档里凭空消失，"
        "任务列表里也看不见它。",
        _subject_ref_problems(d),
    ))

    # ---- 10 声明的指标有人算吗 ----
    checks.append(_verdict(
        "metrics_wired",
        "声明的指标有人算",
        "指标写了 target 却没人采：看板上那个数字是空的。"
        "反过来，算法表里多出来的名字也永远不会出现在看板上。",
        _metrics_problems(inspection),
        missing=[] if inspection.metric_computers is not None else ["指标采集算法表"],
        missing_note="没给指标采集算法表，这一项验不了",
    ))

    # ---- 11 有执行器吗 ----
    checks.append(_verdict(
        "runner_present",
        "有执行器",
        "声明了却跑不了 = 又一族的「声明了却拿不到」。",
        [] if inspection.has_runner is not False else ["这只盒子没有执行器"],
        missing=[] if inspection.has_runner is not None else ["执行器"],
        missing_note="没给执行器信息，这一项验不了",
    ))

    return BoxHealth(box_id=inspection.box_id, checks=tuple(checks))


def inspect_catalog(inspections: Sequence[BoxInspection]) -> CatalogHealth:
    """给整个目录做体检"""
    boxes = tuple(inspect_box(i) for i in inspections)
    return CatalogHealth(boxes=boxes, problems=tuple(wiring_problems(inspections)))


def wiring_problems(inspections: Sequence[BoxInspection]) -> list[str]:
    """接线指向的盒子都在吗

    **接线不在盒子的声明里了**（2026-11-06 退休）——
    它是**协作边界**的事实，住在 `fulfillment_scopes/<边界>/`。

    所以「指着一个不存在的盒子」现在由**协作边界装配器**判
    （`core/decl_checks.py` 的 D2：边两端都得在协作边界的成员清单里），
    而协作边界装配器判得更早、也更全（它还查 `to_task` 是不是真的 task 入口）。

    **这里再判一遍 = 两份判定**，而两份判定迟早给出不同答案。
    所以这段只保留一个**结论**：接线由别处管，不在盒子的体检项里
    （runtime 不知道「协作边界」是什么——它只需要知道那不是这只盒子的体检项）。
    """
    return []


# ============================================================
# 各条检查的内部判定
# ============================================================


def _condition_fire_problems(
    d: BeeBoxDefinition, types: Mapping[str, str],
) -> list[str]:
    """每条例外条件，在产出里**真的存在一种取值**能让它成立吗

    「字段在 schema 里」是弱证明。这一条问的是更硬的问题：
    **这条规则要求一个该字段装不下的值**——那它就永远不响。

    已犯过：`next_requirement_seeded` 是 boolean，却被拿去跟 `''` 比。
    生产里那个字段装的是 True/False，跟空串比永远是假——
    规则写在那里，看上去在守着某件事，实际一次都没触发过。

    ## 为什么不能拿「照着条件造的值」去验

    照着条件造值，等于**用规则自己的假设去证明规则**：
    造出来的值必然让规则成立，于是这条检查永远是绿的。
    那不叫验证，那叫自我循环。

    所以这里造的值**按字段声明的类型来**：
    整数字段就是整数，布尔字段就是布尔。
    于是「整数字段被要求等于 0.5」这类错就现形了。

    ## 复合类型放行

    object / array / enum / ref 这些类型，从字面量看不出「该装什么」，
    造不出候选值 → **不判**。硬判就是拿猜的当事实。
    """
    problems: list[str] = []
    for rule in d.result.exceptions:
        parts = condition_parts(rule.condition)
        if not parts:
            continue  # 形状问题由别的检查报
        if any(len(condition_comparisons(part)) != 1 for part in parts):
            continue  # 形状不对，这条例外本来就不该被拿来验

        merged: dict[str, Any] = {}
        impossible: list[str] = []
        for part in parts:
            field, op, literal = _split_comparison(part)
            if not field or field not in types:
                continue  # 字段不存在由别的检查报
            candidates = _candidate_values(types[field], literal, op)
            if not candidates:
                continue  # 这种类型造不出候选值 → 不判
            satisfying = next(
                (c for c in candidates
                 if evaluate_condition(part, {field: c}).matched),
                None,
            )
            if satisfying is None:
                impossible.append(
                    f"{field}（{types[field]}）装不下 {literal!r}，"
                    f"这个比较永远不成立"
                )
            else:
                merged[field] = satisfying

        if impossible:
            problems.append(
                f"例外条件 {rule.condition!r} 永远不可能成立："
                + "；".join(impossible)
                + "——这条规则从来就没响过"
            )
            continue

        if merged:
            outcome = evaluate_condition(rule.condition, merged)
            if not outcome.matched:
                reason = f"（{outcome.reason}）" if outcome.reason else ""
                problems.append(
                    f"例外条件 {rule.condition!r} 在一个本该触发的产出上"
                    f"（{merged}）没有触发{reason}——这条规则从来就没响过"
                )
    return problems


def _condition_type_problems(
    d: BeeBoxDefinition, types: Mapping[str, str],
) -> list[str]:
    """条件里的字面量类型，必须跟产出字段的类型对得上"""
    problems: list[str] = []
    for rule in d.result.exceptions:
        for part in condition_parts(rule.condition):
            field, _op, value = _split_comparison(part)
            if field not in types:
                continue  # 字段不存在由别的检查报
            literal_type = _literal_type(value)
            if not _types_compatible(types[field], literal_type):
                problems.append(
                    f"例外条件 {rule.condition!r} 里 {field!r} 在产出 schema 里是 "
                    f"{types[field]}，条件却拿它跟 {literal_type} 类型的值比——"
                    f"这条规则永远不响"
                )
    return problems


def _example_payload_problems(
    inspection: BoxInspection,
) -> tuple[list[str], list[str]]:
    """示例投料满足投料契约吗"""
    d = inspection.definition
    problems: list[str] = []
    missing: list[str] = []
    for task in d.task:
        schema = d.get_schema(task.task_schema)
        if schema is None:
            continue  # 形状问题由别的检查报
        if task.type not in inspection.sample_payloads:
            missing.append(task.type)
            continue
        errors = validate_payload(
            inspection.sample_payloads[task.type], schema, schemas=d.schemas,
        )
        if errors:
            problems.append(
                f"{task.type} 的示例投料不满足 {task.task_schema}："
                + "; ".join(errors[:5])
            )
    return problems, missing


def _display_problems(d: BeeBoxDefinition) -> list[str]:
    """展示位填了吗

    **原来这里还管接线**（`placement.feeds_into` / `feedback_into`）——
    接线退休后归协作边界声明，这里只管展示位。
    """
    problems: list[str] = []
    for key in ("display_name", "value_stream", "role"):
        if not getattr(d, key, ""):
            problems.append(f"展示位 {key} 是空的——跟没声明一样")
    return problems


def _subject_ref_problems(d: BeeBoxDefinition) -> list[str]:
    """业务对象标识拿得到吗（每个 task 各判一遍）"""
    problems: list[str] = []
    for task in d.task:
        schema = d.get_schema(task.task_schema)
        task_fields = {f.name for f in schema.fields} if schema else set()
        result_schema = d.get_schema(d.result.result_schema)
        result_fields = (
            {f.name for f in result_schema.fields} if result_schema else set()
        )
        problems.extend(check_subject_ref(
            result_schema=d.result.result_schema,
            result_fields=result_fields,
            task_fields=task_fields,
            subject_ref_field=task.subject_ref_field,
            subject_ref_from_result=task.subject_ref_from_result,
            subject_ref_subfield=task.subject_ref_subfield,
            subject_kind=task.subject_kind,
        ))
    return problems


def _metrics_problems(inspection: BoxInspection) -> list[str]:
    """声明的指标有人算吗；算法表里有没有没人声明的"""
    computers = inspection.metric_computers
    if computers is None:
        return []
    d = inspection.definition
    problems: list[str] = []
    for name in unwired(d, dict(computers)):
        problems.append(
            f"指标 {name!r} 声明了，却没有采集算法——看板上那个数字是空的"
        )
    for name in orphan_computers(d, dict(computers)):
        problems.append(
            f"采集算法表里有 {name!r}，但这只盒子没声明它——它永远不会出现在看板上"
        )
    return problems


# ============================================================
# 小工具
# ============================================================


def _split_comparison(part: str) -> tuple[str, str, str]:
    """把 `field op value` 拆成三元组

    用的是 `core/conditions.py` 的那个正则——**必须和执行侧同一套**。
    """
    m = COMPARISON_PATTERN.search(part)
    if m is None:
        return ("", "", "")
    return (
        part[: m.start()].strip(),
        m.group(1),
        part[m.end():].strip().strip("\"'"),
    )


#: 造不出候选值的类型 → **不判**。
#: object / array / enum / ref 这些，从字面量看不出「该装什么」，
#: 硬判就是拿猜的当事实。
_SCALAR_TYPES = ("boolean", "integer", "number", "string")

#: 每种标量类型的「几个可能的取值」。
#: 覆盖不了所有取值——所以它是**下界**：找不到候选值就说「装不下」，
#: 找到了就说明至少有一种取值能成立。
_BASE_VALUES: dict[str, list[Any]] = {
    "boolean": [True, False],
    "integer": [0, 1, 2, -1, 5],
    "number": [0.0, 1.0, 2.5, -1.0, 100.0],
    "string": ["", "a", "0", "1", "true", "false"],
}

#: 表示「这个字面量装不进这个类型」
_NO_CAST = object()


def _cast_to_type(literal: str, field_type: str) -> Any:
    """把条件里的字面量转成字段声明的类型；转不了就说转不了"""
    if field_type == "boolean":
        if literal == "true":
            return True
        if literal == "false":
            return False
        return _NO_CAST
    if field_type == "integer":
        try:
            return int(literal)
        except (TypeError, ValueError):
            return _NO_CAST
    if field_type == "number":
        try:
            return float(literal)
        except (TypeError, ValueError):
            return _NO_CAST
    if field_type == "string":
        return literal
    return _NO_CAST


def _typed_satisfying(op: str, value: Any) -> Any:
    """造一个能让 `field op value` 成立的值（**保持字段的类型**）"""
    if isinstance(value, bool):
        if op == "!=":
            return not value
        return value
    if isinstance(value, int):
        if op == ">":
            return value + 1
        if op == "<":
            return value - 1
        if op == "!=":
            return value + 1
        return value
    if isinstance(value, float):
        if op == ">":
            return value + 1.0
        if op == "<":
            return value - 1.0
        if op == "!=":
            return value + 1.0
        return value
    if isinstance(value, str):
        if op == "!=":
            return value + " "
        return value
    return value


def _candidate_values(field_type: str, literal: str, op: str) -> list[Any]:
    """这个字段（按它声明的类型）可能装的几个值

    空列表 = 这种类型造不出候选值 → 调用方**不判**。
    """
    if field_type not in _SCALAR_TYPES:
        return []
    candidates = list(_BASE_VALUES.get(field_type, []))
    casted = _cast_to_type(literal, field_type)
    if casted is not _NO_CAST:
        candidates.append(casted)
        tuned = _typed_satisfying(op, casted)
        # 再调回字段的类型（整数 +1 还是整数；布尔取反还是布尔）
        if field_type == "integer" and isinstance(tuned, (int, float)):
            candidates.append(int(tuned))
        else:
            candidates.append(tuned)
    return candidates


def _literal_type(value: str) -> str:
    """字面量的类型"""
    if value in ("true", "false"):
        return "boolean"
    try:
        int(value)
        return "integer"
    except (TypeError, ValueError):
        pass
    try:
        float(value)
        return "number"
    except (TypeError, ValueError):
        pass
    return "string"


def _types_compatible(field_type: str, literal_type: str) -> bool:
    """产出字段的类型能不能跟条件里的字面量比"""
    if field_type in ("object", "array", "enum") or field_type.startswith("ref:"):
        return True  # 这几种类型从字面量看不出该比什么，放行
    if field_type == "boolean":
        return literal_type == "boolean"
    if field_type in ("integer", "number"):
        return literal_type in ("integer", "number")
    if field_type == "string":
        return literal_type == "string"
    return True
