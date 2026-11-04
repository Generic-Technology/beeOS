"""
core.decl_checks - 声明自洽性的**唯一判定处**

同一批事实会被两个地方问：

- **装配器**（`core/scaffold.py`）：写声明的时候，当场挑出自相矛盾的地方
- **体检台**（`runtime/box_health.py`）：读一只已经存在的盒子，问「它自洽吗」

两处各写一份 = 两份事实 = 漂。这个漂不是理论上的：
守卫原来用自己那套正则验例外条件，被验的代码用另一套，
两边选到**不同的字段**，于是守卫放行了一条实际永远不触发的规则
（见 `core/conditions.py` 的开头）。

所以：**事实判在这里，两个调用方都来这里取。**

## 这里判什么、不判什么

判的是「**声明自己跟自己对不对得上**」：

- 引用的 schema 存在吗
- 判据读得到值吗
- 例外条件真的能触发吗
- 成熟度跟它自己的声明对得上吗

**不判运行时行为**：盒子跑起来成不成功、判据过不过、产出对不对，
那是 acceptance 和履约的事。本模块不 import 任何盒子，也不认识
任何一只盒子的 op 名——它只认协议。

## 为什么要返回「问题列表」而不是抛异常

装配器要**一次报齐所有问题**（改一个报一次 = 把填表变成猜题），
体检台要**把每条问题挂在那条检查项下面**。
所以这里是纯函数：给事实，出问题清单。谁想抛谁抛。
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from core.conditions import condition_comparisons, condition_fields, condition_parts
from core.models import AcceptanceRule, ExceptionRule, SchemaDef


def schema_ids_of(schemas: Sequence[SchemaDef]) -> set[str]:
    """这只盒子声明了哪些 schema"""
    return {s.id for s in schemas}


def result_field_types(
    schemas: Sequence[SchemaDef], result_schema: str,
) -> dict[str, str]:
    """产出 schema 里每个字段的名字与类型

    产出 schema 不存在时返回空 dict——**引用不存在**由
    `check_schema_refs` 报，这里不重复报一遍。
    """
    for s in schemas:
        if s.id == result_schema:
            return {f.name: f.type for f in s.fields}
    return {}


def check_schema_refs(
    *,
    schema_ids: set[str],
    result_schema: str,
    task_schemas: Mapping[str, str],
) -> list[str]:
    """投料和产出指着的形状，都存在吗

    「存在」是这批盒子真实的形状：每只盒子的投料契约和产出契约
    都指向自己声明过的 schema。
    """
    problems: list[str] = []
    for task_type, sid in task_schemas.items():
        if sid not in schema_ids:
            problems.append(
                f"task {task_type!r} 的 task_schema 指向未声明的 schema {sid!r}。"
                f"这只盒子声明的 schema 有：{sorted(schema_ids)}"
            )
    if result_schema not in schema_ids:
        problems.append(
            f"result_schema 指向未声明的 schema {result_schema!r}。"
            f"这只盒子声明的 schema 有：{sorted(schema_ids)}"
        )
    return problems


def check_op_schema_refs(
    *,
    schema_ids: set[str],
    op_refs: Iterable[tuple[str, str, str]] = (),
) -> list[str]:
    """作业路线每一跳吃进去/吐出来的形状，都存在吗

    指着一个不存在的 schema id，声明就落空了：调用方按作业路线拼数据，
    拼出来的东西没有任何 schema 说它长什么样，也没有校验会响。
    「声明了却拿不到」的又一族。

    Args:
        op_refs: (label, role, schema_id) 三元组。label 由调用方给——
            这样「op 'x' 的 input」和「作业路线 'y' 里的 op 'x'」
            能各说各的，而判定逻辑是同一份。
    """
    problems: list[str] = []
    for label, role, sid in op_refs:
        if sid not in schema_ids:
            problems.append(
                f"{label} 的 {role} 指向未声明的 schema {sid!r}。"
                f"这只盒子声明的 schema 有：{sorted(schema_ids)}"
            )
    return problems


def check_acceptance_metrics(
    *,
    result_schema: str,
    result_fields: Iterable[str],
    acceptance: Sequence[AcceptanceRule],
) -> list[str]:
    """判据读得到值吗

    判据声明的 metric，产出 schema 里必须有对应字段。没有就意味着
    判据永远读不到值——要么恒判通过（假绿灯），要么恒判失败。
    两种都是坏结果。
    """
    fields = set(result_fields)
    problems: list[str] = []
    for rule in acceptance:
        if rule.metric not in fields:
            problems.append(
                f"判据 {rule.metric!r} 不在产出 schema {result_schema!r} 的字段里。"
                f"产出里可用的字段：{sorted(fields)}——"
                f"判据读不到值，会恒过或恒红"
            )
    return problems


def check_exception_conditions(
    *,
    result_schema: str,
    result_fields: Iterable[str],
    exceptions: Sequence[ExceptionRule],
) -> list[str]:
    """例外条件真的能触发吗

    条件读不到值时，判定器**不报错、不提示**，直接返回「不成立」。
    后果是这条规则写在那里、看上去在守着某件事，实际永远不会触发：
    构建失败不会 rollback，组件数为空不会 no_deliver。

    这比判据读不到值更隐蔽：判据读不到会判红（吵），
    例外读不到只是永远不响（**安静地没做**）。

    解析用的是 `core/conditions.py`——**必须和执行侧同一套**。
    守卫验的是它自己那套解析，等于放行了一条实际永远不触发的规则。
    """
    fields = set(result_fields)
    problems: list[str] = []
    for rule in exceptions:
        cond = rule.condition.strip()
        parts = condition_parts(cond)
        if not parts:
            problems.append(f"例外条件 {cond!r} 是空的——它永远不触发")
            continue
        for part in parts:
            comparisons = condition_comparisons(part)
            if not comparisons:
                problems.append(
                    f"例外条件 {cond!r} 里的 {part!r} 不是 `field op value` 三段式——"
                    f"裸标识符永远不触发"
                )
            elif len(comparisons) > 1:
                problems.append(
                    f"例外条件 {cond!r} 里的 {part!r} 有 {len(comparisons)} 个比较符"
                    f"（{comparisons}）——多半是漏了 AND。"
                    f"硬跑下去会在比较时抛 TypeError，把整次 acceptance 评估掀翻"
                )
        for field in condition_fields(cond):
            if field not in fields:
                problems.append(
                    f"例外条件 {cond!r} 会去读字段 {field!r}，"
                    f"但它不在产出 schema {result_schema!r} 里"
                    f"（可用字段：{sorted(fields)}）"
                    f"→ 读到的永远是 None → 这条规则永远不触发"
                )
    return problems


def check_maturity(
    *,
    maturity: str,
    acceptance: Sequence[AcceptanceRule],
) -> list[str]:
    """成熟度跟它自己的声明对得上吗（**装配期**这一层）

    验收层是「没有判据失败 → 判过」。所以一只**零判据**的 live 盒子
    会被判成「全过」——那是「看起来成功，其实什么也没验」的谎。

    **骨架盒零判据不在这里管**：装配器允许它——
    骨架盒是「还没写完」的起点，作者得先能把它装起来。
    「注册进来的骨架盒必须诚实」是**目录期**的事，见 `check_skeleton_is_honest`。
    """
    if maturity == "live" and not acceptance:
        return [
            "这只盒子是 live（腿已实现），却一条判据都没声明。"
            "零判据会被判成「全过」——那是「看起来成功，其实什么也没验」的谎。"
            "腿还没实现就写 maturity='skeleton'"
        ]
    return []


def check_skeleton_is_honest(
    *,
    maturity: str,
    acceptance: Sequence[AcceptanceRule],
    metric_names: Iterable[str],
) -> list[str]:
    """**注册进来的**骨架盒，声明得诚实吗

    骨架盒的判据是「它将来要验的东西」。产出**不带**那些量 →
    判据全红 → 「未实现」是真的。

    零判据就不行了：判据全红是「诚实的红」，零判据是「没有红」——
    验收层会判它「全过」。那只盒子看着成功，其实什么也没验。

    声明了指标也一样：骨架盒没有产出，声明了指标就是「声明了却拿不到」。

    **为什么这条不住在装配器里**：装配器允许骨架盒零判据——
    骨架盒是起点，不是成品。两条判定的**时机不同**：
    装配期问「这份声明能不能作为起点」，目录期问「这只盒子现在诚实吗」。
    """
    if maturity != "skeleton":
        return []
    problems: list[str] = []
    names = sorted(n for n in metric_names if n)
    if not acceptance:
        problems.append(
            "这只盒子是 skeleton（腿还没实现），却一条判据都没声明。"
            "骨架盒该真声明它将来要验的东西——产出不带这些量，判据全红，"
            "「未实现」才是真的。零判据会被判成「全过」，那是谎"
        )
    if names:
        problems.append(
            f"骨架盒没有产出，却声明了指标 {names}——声明了却拿不到"
        )
    return problems
