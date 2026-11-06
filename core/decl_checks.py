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

from typing import Iterable, Mapping, Optional, Sequence

from core.conditions import condition_comparisons, condition_fields, condition_parts
from core.contract import (
    INCOMPATIBLE,
    UNKNOWN,
    check_schema_compatibility,
)
from core.models import (
    AcceptanceRule,
    BoxFacts,
    FulfillmentScopeDeclaration,
    Edge,
    EdgeKind,
    ExceptionRule,
    RuleKind,
    SchemaDef,
)


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


def check_subject_ref(
    *,
    result_schema: str,
    result_fields: Iterable[str],
    task_fields: Iterable[str],
    subject_ref_field: Optional[str] = None,
    subject_ref_from_result: Optional[str] = None,
    subject_ref_subfield: Optional[str] = None,
    subject_kind: Optional[str] = None,
) -> list[str]:
    """业务对象标识拿得到吗

    **这只盒子的履约归不到哪个工作单元**，全看这几条声明：
    标识从投料里取、从产出里取、还是从对象里取——说错一个，
    这条记录就在归档里凭空消失（「声明了却拿不到」那族病）。

    ## 为什么「从产出里取」必须验产出 schema 里真有那个键

    `subject_ref_from_result` 说「产出里的哪个键是我造出来的对象的号」。
    产出 schema 里没有这个键 → 运行时跑完取不到 →
    **这次履约被拒**（契约被破了，当场报错，不静默降级成空）。

    所以这不只是「少了个号」，而是「这只盒子跑一次就废一次」。
    装配期就该拦住。

    已犯过（2026-11-05）：学习协作边界 4 只盒子的产出里明明有 `record_id`
    （required=True，「同一件事重记还是同一个号」），却**没声明**
    `subject_ref_from_result`——于是记录归不到任何工作单元，
    任务列表里看不见它们，看板上只剩「这只盒子跑过 N 次」。
    那是「明明产出里有，却没声明，所以拿不到」。
    """
    fields = set(result_fields)
    task = set(task_fields)
    problems: list[str] = []

    if subject_ref_from_result and subject_ref_from_result not in fields:
        problems.append(
            f"subject_ref_from_result={subject_ref_from_result!r} "
            f"不在产出 schema {result_schema!r} 里"
            f"（可用字段：{sorted(fields)}）——"
            f"声明了却拿不到：跑完取不到这个键，这次履约会被当场拒绝"
        )
    if subject_ref_field and subject_ref_field not in task:
        problems.append(
            f"subject_ref_field={subject_ref_field!r} 不在投料 schema 里"
            f"（可用字段：{sorted(task)}）——"
            f"声明了却拿不到，归档时这条履约记录归不到任何工作单元"
        )
    if subject_ref_subfield and not subject_ref_field:
        problems.append(
            "声明了 subject_ref_subfield 却没有 subject_ref_field——"
            "从哪个对象里取，总得先说清是哪个对象"
        )
    if (subject_ref_field or subject_ref_from_result) and not subject_kind:
        problems.append(
            "声明了 subject_ref_field / subject_ref_from_result 却没有 subject_kind——"
            "「它关于哪一类业务对象」不能靠猜"
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


# ============================================================
# 边界级校验（D1–D15）
#
# 盒子级判的是「一只盒子自不自洽」（C1–C12），
# 边界级判的是「**一个协作边界的结构对不对**」（D1–D15）。
#
# **边界级要更多信息**（别的盒子声明了什么），所以由调用方
# （注册表 / 装配器）把信息收集好传进来——core 仍然不认识任何一只盒子。
#
# **判定分三层**（见 `docs/fulfillment-scope.md` §5.7）：
#   ① 声明合法性（D1–D4 / D7–D8 / D10 / D12 / D14–D15）← 本模块
#   ② 边界业务规则（rules）                                 ← `core/fulfillment_scope.py`
#   ③ 展示适用性（汇点唯一 / 环上相邻有交接）              ← `core/wiring.py`
#
# **D5（汇点唯一）不在 ①**：那是**画图**的要求，不是**声明合法**的要求。
# ============================================================


def check_domain_boxes(
    declaration: FulfillmentScopeDeclaration,
    boxes: Mapping[str, BoxFacts],
) -> list[str]:
    """D1 + 「孤岛必须显式点名」

    **D1**：协作边界声明里写的盒子，都得真实存在。
    **孤岛**：边界里的盒子在本边界一条边都没有 → 要么是「加进协作边界忘了接边」，
    要么是「它业务上就是独立的」——**后者必须显式点名**，
    否则「加进协作边界忘了接边」会静默通过。
    """
    problems: list[str] = []
    member_ids = set(declaration.boxes)

    for box_id in declaration.boxes:
        if box_id not in boxes:
            problems.append(
                f"D1 协作边界 {declaration.id!r} 的成员 {box_id!r} 不在注册表里——"
                f"协作边界声明写了一个不存在的盒子"
            )

    # 孤岛：在本边界里既不是任何边的上游，也不是任何边的下游
    touched: set[str] = set()
    for e in declaration.edges:
        touched.add(e.from_box)
        touched.add(e.to_box)
    # 被规则显式点名的盒子算「说过了」
    named: set[str] = {
        ex.box_id for rule in declaration.rules for ex in rule.exemptions
    }
    for box_id in declaration.boxes:
        if box_id not in touched and box_id not in named:
            problems.append(
                f"孤岛：协作边界 {declaration.id!r} 的成员 {box_id!r} 在本边界一条边都没有。"
                f"要么给它接上边，要么在某条规则的豁免里**显式点名**"
                f"（不能静默地孤着——那分不清是「忘了接边」还是「它就是独立的」）"
            )
    return problems


def check_domain_edges(
    declaration: FulfillmentScopeDeclaration,
    boxes: Mapping[str, BoxFacts],
) -> list[str]:
    """D2 / D7 / D8 / D10 / D12 —— 边本身的合法性"""
    problems: list[str] = []
    member_ids = set(declaration.boxes)

    # D8：判重键 = 完整通道标识，**不是 (from, to, kind)**
    seen: dict[tuple[str, str, str, str, str], Edge] = {}
    for e in declaration.edges:
        key = e.channel_key
        if key in seen:
            problems.append(
                f"D8 重复的边：{e.from_box!r} → {e.to_box!r}"
                f"（to_task={e.to_task!r} via={e.via!r} kind={e.kind.value!r}）"
                f"与前面那条重复"
            )
        else:
            seen[key] = e

    for e in declaration.edges:
        # D2 / D10：边的两端
        if not e.external and e.from_box not in member_ids:
            problems.append(
                f"D2 边 {e.from_box!r} → {e.to_box!r}：上游 {e.from_box!r} 不在本边界 "
                f"{declaration.id!r} 的成员里（要么加进成员，要么声明 external=True）"
            )
        if e.to_box not in member_ids:
            problems.append(
                f"D2 边 {e.from_box!r} → {e.to_box!r}：下游 {e.to_box!r} 不在本边界 "
                f"{declaration.id!r} 的成员里"
            )

        # D7：归档边**不是交接**，所以不许带通道
        if e.kind == EdgeKind.archive and e.via:
            problems.append(
                f"D7 归档边 {e.from_box!r} → {e.to_box!r} 带了 via={e.via!r}——"
                f"归档归属不是数据交接，不该有通道"
            )

        # handoff 必须有通道；反馈才允许明确声明没有通道。
        if e.kind == EdgeKind.handoff and (not e.via or e.no_channel):
            problems.append(
                f"D12 交接边 {e.from_box!r} → {e.to_box!r} 必须声明 via，"
                "且不得声明 no_channel=True"
            )
        elif e.via and e.no_channel:
            problems.append(
                f"D12 边 {e.from_box!r} → {e.to_box!r} 同时写了 via={e.via!r} "
                f"和 no_channel=True——两者互斥"
            )
        elif not e.via and not e.no_channel and e.kind != EdgeKind.archive:
            problems.append(
                f"D12 边 {e.from_box!r} → {e.to_box!r}（{e.kind.value}）"
                f"既没有 via 也没写 no_channel——"
                f"「这条边通不通」要显式说出来，不能靠沉默"
            )

        # to_task 要指得到
        facts = boxes.get(e.to_box)
        if facts is not None and e.to_task:
            if e.to_task not in facts.task_types:
                problems.append(
                    f"边 {e.from_box!r} → {e.to_box!r} 的 to_task={e.to_task!r} "
                    f"不是 {e.to_box!r} 的 task 入口（它有：{list(facts.task_types)}）"
                )
        if facts is not None and not e.to_task and len(facts.task_types) > 1:
            problems.append(
                f"边 {e.from_box!r} → {e.to_box!r} 没写 to_task，"
                f"而 {e.to_box!r} 有多个 task 入口（{list(facts.task_types)}）——"
                f"指不到具体入口"
            )
    return problems


def check_domain_channels(
    declaration: FulfillmentScopeDeclaration,
    boxes: Mapping[str, BoxFacts],
) -> list[str]:
    """D3a / D3b / D3c —— 每条 handoff 边都要有一条真能走通的通道

    **D3c 用的是「声明层兼容判定」**（`core.contract.check_schema_compatibility`），
    不是「拿一份样例去试」——后者只能证明「**存在**一个合规产出能过」，
    不能证明「**所有**合规产出都能过」（`docs/fulfillment-scope.md` §5.6）。
    """
    problems: list[str] = []
    for e in declaration.edges:
        if e.kind != EdgeKind.handoff:
            continue
        if not e.via:
            continue  # D12 已经报过

        down = boxes.get(e.to_box)
        up = boxes.get(e.from_box)
        if down is None or up is None:
            continue  # D1 已经报过

        # D3a：via 必须是下游投料里的 `ref:` 字段
        task_schema = down.task_schema(e.to_task)
        if task_schema is None:
            problems.append(
                f"D3a 边 {e.from_box!r} → {e.to_box!r}："
                f"拿不到 {e.to_box!r} 的投料形状（to_task={e.to_task!r}）"
            )
            continue
        field = next((f for f in task_schema.fields if f.name == e.via), None)
        if field is None:
            problems.append(
                f"D3a 边 {e.from_box!r} → {e.to_box!r}：via={e.via!r} 不在 "
                f"{e.to_box!r} 的投料 {task_schema.id!r} 里"
                f"（现有字段：{[f.name for f in task_schema.fields]}）"
            )
            continue
        if not field.type.startswith("ref:"):
            problems.append(
                f"D3a 边 {e.from_box!r} → {e.to_box!r}：via={e.via!r} 的类型是 "
                f"{field.type!r}，不是 ref:——交接通道必须是 ref 字段"
            )
            continue

        # D3b：ref 指向的形状要在**下游自己的** schemas 里能解析
        target_id = field.type[4:]
        target = down.schemas.get(target_id)
        if target is None:
            problems.append(
                f"D3b 边 {e.from_box!r} → {e.to_box!r}：via={e.via!r} 指向 "
                f"{target_id!r}，但 {e.to_box!r} 自己没声明这个 schema"
            )
            continue

        # D3c：声明层兼容判定
        out = up.result_schema_def()
        if out is None:
            continue  # 上游的产出形状缺失，是盒子级的问题
        compat = check_schema_compatibility(
            out,
            target,
            upstream_schemas=up.schemas,
            downstream_schemas=down.schemas,
        )
        if compat.status == INCOMPATIBLE:
            problems.append(
                f"D3c 边 {e.from_box!r} → {e.to_box!r}（via={e.via!r}）："
                f"上游的产出形状**喂不进**下游声明的接料形状——{compat.reason}"
            )
        elif compat.status == UNKNOWN:
            problems.append(
                f"D3c 边 {e.from_box!r} → {e.to_box!r}（via={e.via!r}）判不出来"
                f"（unknown）：{compat.reason}。"
                f"**去把上游的声明改精确**——上游说得越含糊，这块空白越大"
            )
    return problems


def check_domain_rules(
    declaration: FulfillmentScopeDeclaration,
    boxes: Mapping[str, BoxFacts],
) -> list[str]:
    """D14 / D15 —— 豁免机制本身要合法"""
    problems: list[str] = []
    member_ids = set(declaration.boxes)

    for rule in declaration.rules:
        for ex in rule.exemptions:
            # D14：豁免的盒子得在这个协作边界里
            if ex.box_id not in member_ids:
                problems.append(
                    f"D14 规则 {rule.kind.value!r} 的豁免点名了 {ex.box_id!r}，"
                    f"但它不在协作边界 {declaration.id!r} 的成员里"
                )
            if not ex.reason.strip():
                problems.append(
                    f"D14 规则 {rule.kind.value!r} 对 {ex.box_id!r} 的豁免没写理由——"
                    f"「为什么豁免」要能被人读出来"
                )
            # D15：must = 不得有对应的边
            if ex.mode == "must":
                forbidden = {
                    RuleKind.all_archived: EdgeKind.archive,
                    RuleKind.all_handoff: EdgeKind.handoff,
                }.get(rule.kind)
                if forbidden is not None:
                    bad = [
                        e for e in declaration.edges
                        if e.from_box == ex.box_id and e.kind == forbidden
                    ]
                    for e in bad:
                        problems.append(
                            f"D15 规则 {rule.kind.value!r} 声明 {ex.box_id!r} "
                            f"**不得**有 {forbidden.value} 边"
                            f"（实际有：{e.from_box!r} → {e.to_box!r}）"
                        )
    return problems
