"""
core.scaffold - beeBox 装配器

**这个模块不含任何业务知识。** 它只认协议：一只盒子由「声明」+「组件链」组成，
装配器负责把它们组装成运行时认识的那四件套（声明 / 作业路线 / 执行器 / 报告），
并在组装的时候把自相矛盾的地方**当场挑出来**。

## 为什么要有它

以前新建一只盒子 = 复制一份模板再改。模板有 7 个文件几百行样板，
业务只占其中一小块；而且**声明与实现对不上的错，要等到履约跑起来才炸**：

- 判据写了一个产出里根本不存在的字段 → 永远读不到值 → 恒过或恒红
- 例外条件写成裸标识符 → 规则永远不触发，**安静地没做**
- op 的 `input_from` 指了一个不存在的 op → 声明落空

这些错**装配期就能查出来**。把它们从「事后被 `tests/test_box_standard.py`
发现」搬到「写声明的时候就被拒」，是这次升级真正的价值——
不是少写几百行样板。

## 组合式，不是继承式

盒子**不继承任何东西**。它提供数据（`BoxDeclaration` + `Component` 列表），
装配器负责组装 + 校验。

为什么不用继承式：盒子之间的差异是**形状**（几跳、什么契约），
不是**行为**（怎么做）。继承式会让「基类一改，全跟着改」——
一只盒子的重构掀翻所有盒子。

## core 只定协议，不定跳数

core **不内置**「默认三跳」这类形状。票据盒 2 跳、学习盒 4 跳、设计盒 7 跳，
形状本就不同；core 若内置一种形状，就等于把最多数盒子的形状硬塞给少数盒子。

core 只要求：**每一跳都得说清自己的输入形状、输出形状、以及谁来执行它。**

## 校验清单

见 [docs/architecture-upgrade.md §3.3]。每一条都在这里实现，
每一条都配一条会红的守卫（`tests/test_scaffold.py`）。

**这些校验不是凭空定的规则**：写这个模块之前，先拿现有全部已注册盒子
逐条验过一遍，全部成立——所以它们是这批盒子的**真实形状**。
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.beeline_models import (
    Bee,
    Beeline,
    ExternalSystem,
    Idempotency,
    NextRef,
    Operation,
)
from core.decl_checks import (
    check_acceptance_metrics,
    check_exception_conditions,
    check_maturity,
    check_op_schema_refs,
    check_schema_refs,
)
from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricsDef,
    PlacementDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    SchemaDef,
    TaskDef,
)


#: 一跳的实现。**盒子提供，core 只负责调用。**
Handler = Callable[[dict[str, Any]], dict[str, Any]]


# ============================================================
# 装配器装配的东西
# ============================================================


class Component(BaseModel):
    """盒子提供的一个业务组件（一跳）

    **一个组件 = 一跳**。它说清四件事：

    - 我是谁（`op_id` / `type`）
    - 我吃什么、吐什么（`input` / `output`，都是 schema id）
    - 我的数据从哪来（`input_from`）
    - 谁来实现我（`handler`）

    `next` 是**控制流**（执行顺序），`input_from` 是**数据来源**（声明）。
    这两件事在现有盒子里确实不同：`wms_design` 的 `check_consistency`
    声明的数据来源是 `generate_design_elements`，而执行时喂给它的是
    `verify_coverage` 的产出（两者形状相同，所以看不出差别）。
    所以装配器**分别**校验这两条边，不合并。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    op_id: str = Field(description="这一跳的标识（盒内唯一）")
    type: str = Field(
        default="",
        description="执行时的分派键（handler 表按它找实现）。留空 = 用 op_id",
    )
    input_from: str = Field(
        default="external",
        description="数据来源：external = 投料；否则是某个 op_id",
    )
    input: str = Field(description="输入的数据形状（schema id）")
    output: str = Field(description="输出的数据形状（schema id）")
    next: list[str] = Field(
        default_factory=list,
        description="执行完这一跳接下来跑哪几跳（op_id）",
    )
    bee_type: str = Field(
        default="",
        description="bee 标签（写进 beeline 给人看）。留空 = 用 op_id",
    )
    external_system: Optional[ExternalSystem] = None
    idempotency: Optional[Idempotency] = None
    leaves_artifact: bool = Field(
        default=False,
        description="这一跳成功后会留下产物或副作用",
    )
    handler: Optional[Handler] = Field(
        default=None,
        description="这一跳的实现。留空则从盒子的 handler 表里按 type 找",
    )

    def resolved_type(self) -> str:
        """执行时的分派键"""
        return self.type or self.op_id

    def resolved_bee_type(self) -> str:
        """写进 beeline 的 bee 标签"""
        return self.bee_type or self.op_id


class BoxDeclaration(BaseModel):
    """这只盒子是什么

    只装**声明**（事实与承诺），不装实现。实现是 `Component.handler` 和
    盒子的 handler 表。
    """

    id: str = Field(description="盒子唯一标识")
    version: int = Field(default=1, description="盒子版本（递增序列号）")
    description: str = Field(default="", description="这只盒子在做什么")

    # ---- 归属（这只盒子在企业里站在哪）----
    display_name: str = Field(default="", description="给人看的名字")
    value_stream: str = Field(default="", description="所属企业价值流")
    role: str = Field(default="", description="业务责任单元的职责")
    domain: str = Field(default="", description="所属业务领域")
    feeds_into: list[str] = Field(
        default_factory=list,
        description="本盒产出交出去给哪些 box_id（接线唯一声明）",
    )
    feedback_into: list[str] = Field(
        default_factory=list,
        description="本盒产出反馈给哪些 box_id（闭环边，不进拓扑排序）",
    )

    # ---- 投料入口（Intent）----
    task_type: str = Field(description="task 类型标识")
    task_schema: str = Field(description="投料的 schema id")
    task_trigger: Optional[str] = Field(default=None, description="触发条件描述")
    accepts_free_text: bool = Field(
        default=False,
        description="是否接收业务自然语言输入。默认否——猜错会让无关业务表述被路由进这只盒子",
    )
    subject_ref_field: Optional[str] = Field(
        default=None,
        description="投料里哪个字段是本次履约处理的业务对象的稳定标识",
    )
    subject_ref_from_result: Optional[str] = Field(
        default=None,
        description="产出里哪个键是这只盒子产生的业务对象标识",
    )
    subject_ref_subfield: Optional[str] = Field(
        default=None,
        description="从 subject_ref_field 指向的对象里取哪个键当业务对象标识",
    )
    subject_kind: Optional[str] = Field(
        default=None,
        description="业务对象的种类（requirement / ops_event …）",
    )

    # ---- 产出契约（Contract）----
    result_type: str = Field(description="业务结果类型标识")
    result_schema: str = Field(description="产出的 schema id")
    result_description: str = Field(
        default="",
        description="这个产出是什么（人话一句）。给读产出的人看",
    )
    acceptance: list[AcceptanceRule] = Field(default_factory=list)
    exceptions: list[ExceptionRule] = Field(default_factory=list)
    maturity: Literal["live", "skeleton"] = Field(
        default="live",
        description="live = 腿已实现，判据读数有意义；skeleton = 结构已立、腿还没实现",
    )
    artifact_view: str = Field(
        default="generic",
        description="产出的展示形态（盒子自己声明，看板不猜）",
    )
    empty_input_metric: Optional[str] = Field(
        default=None,
        description="投料条数指标的名字（空 = 不声明）",
    )

    # ---- 作业路线（Procedure）----
    beeline_id: str = Field(description="绑定的 beeline 标识")
    beeline_version: int = Field(default=1, description="绑定的 beeline 版本")
    beeline_description: str = Field(default="", description="这条作业路线在做什么")

    # ---- 政策（Policy）与度量（Metrics）----
    queen: QueenDef = Field(
        default_factory=lambda: QueenDef(
            authorization=QueenAuthorization(),
            rules=QueenRules(),
        )
    )
    metrics: MetricsDef = Field(default_factory=MetricsDef)


class AssemblyReport(BaseModel):
    """装配报告——装配过了什么、验过什么"""

    box_id: str
    op_count: int = 0
    schema_count: int = 0
    checks: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.box_id} 装配通过：{self.op_count} 跳 / "
            f"{self.schema_count} 个 schema / 验了 {len(self.checks)} 条"
        )


class AssembledBox(BaseModel):
    """装好的盒子

    **注意这里没有 runner（执行器）。**

    执行器住在 `runtime/`——「runtime 面向 core 的脚手架执行」，
    反过来 core 就得 import runtime，底层依赖上层。

    所以 core 交出去的是**协议数据**（声明 / 作业路线 / 实现表），
    由盒子的 `runner.py` 那行接到 runtime 的执行器上：

    ```python
    from runtime.runner import HandlerRunner
    RUNNER = HandlerRunner(ASSEMBLED.handlers, owner="<box_id>")
    ```
    """

    # **不许静默吞字段**（2026-11-04 补）：拼错当场报错。
    # 一跳的形状拼错了（`inpt=` 而不是 `input=`），吞掉的后果是
    # 「这一跳吃什么」变成空值，装配器还照样装得出来。
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    definition: BeeBoxDefinition
    beeline: Beeline
    handlers: dict[str, Handler] = Field(
        default_factory=dict,
        description="op.type → 实现。执行器按这个表分派",
    )
    report: AssemblyReport


class AssemblyError(Exception):
    """装配没过。

    **每一条问题都带能修它的材料**：是哪个 op、哪个 schema、现在是什么、
    应该是什么。装配器不负责修，但它负责让人知道该修哪儿。
    """

    def __init__(self, box_id: str, problems: list[str]) -> None:
        self.box_id = box_id
        self.problems = list(problems)
        body = "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(
            f"{box_id} 装配没过（{len(self.problems)} 条）：\n{body}"
        )


# ============================================================
# 装配
# ============================================================


def assemble(
    declaration: BoxDeclaration,
    components: list[Component],
    *,
    schemas: list[SchemaDef],
    handlers: Optional[dict[str, Handler]] = None,
) -> AssembledBox:
    """把「声明 + 组件链」装成运行时认识的盒子。

    Args:
        declaration: 这只盒子是什么
        components: 这只盒子的每一跳
        schemas: 这只盒子声明的业务 schema
        handlers: op.type → 实现。组件自己带了 handler 时以组件的为准

    Raises:
        AssemblyError: 声明自相矛盾。**一次报齐所有问题**——
            让人改一个报一次，等于把「填表」变成「猜题」。
    """
    box_id = declaration.id
    problems: list[str] = []
    checks: list[str] = []

    # ---- op 索引 ----
    by_id: dict[str, Component] = {}
    for comp in components:
        if comp.op_id in by_id:
            problems.append(
                f"op_id {comp.op_id!r} 重复出现了。盒内 op_id 必须唯一——"
                f"重复的 op 在作业路线里没法区分哪一步是哪一步"
            )
            continue
        by_id[comp.op_id] = comp
    checks.append("op_id 唯一")

    # ---- handler 表 ----
    table: dict[str, Handler] = dict(handlers or {})
    effective: dict[str, Handler] = {}
    for comp in components:
        op_type = comp.resolved_type()
        if comp.handler is not None:
            if op_type in table and table[op_type] is not comp.handler:
                problems.append(
                    f"op {comp.op_id!r}（type={op_type!r}）有两个实现："
                    f"组件自带一个，handler 表里还有一个。留一个就行"
                )
            effective[op_type] = comp.handler
        elif op_type in table:
            effective[op_type] = table[op_type]
    checks.append("每跳都有实现")

    # ---- C1：每跳都有 handler ----
    for comp in components:
        if comp.resolved_type() not in effective:
            problems.append(
                f"op {comp.op_id!r}（type={comp.resolved_type()!r}）没有实现："
                f"handler 表里没有这个 type。"
                f"已知的 type：{sorted(effective) or '（一张都没有）'}"
            )
    checks.append("C1 每跳有 handler")

    # ---- 入口 ----
    entries = [c for c in components if c.input_from == "external"]
    entry: Optional[Component] = None
    if not entries:
        problems.append(
            "没有入口跳：所有 op 的 input_from 都不是 'external'。"
            "作业路线得有一个从投料起步的跳"
        )
    elif len(entries) > 1:
        problems.append(
            f"有 {len(entries)} 个入口跳（input_from='external'）："
            f"{[c.op_id for c in entries]}。"
            f"运行时只认一个起点——投料该喂给哪一跳？"
        )
    else:
        entry = entries[0]
    checks.append("C3 恰好一个入口")

    # ---- C2：input_from 边 ----
    for comp in components:
        if comp.input_from == "external":
            continue
        source = by_id.get(comp.input_from)
        if source is None:
            problems.append(
                f"op {comp.op_id!r} 的 input_from={comp.input_from!r} 不存在。"
                f"这只盒子的 op 有：{sorted(by_id)}"
            )
            continue
        if source.output != comp.input:
            problems.append(
                f"op {comp.op_id!r} 声明从 {comp.input_from!r} 取数据，"
                f"但 {comp.input_from!r} 吐的是 {source.output!r}，"
                f"而 {comp.op_id!r} 吃的是 {comp.input!r}——形状对不上。"
                f"要么改 {comp.input_from!r} 的 output，要么改 {comp.op_id!r} 的 input"
            )
    checks.append("C2 数据来源自洽")

    # ---- C3：入口的输入 == 投料的形状 ----
    if entry is not None and entry.input != declaration.task_schema:
        problems.append(
            f"入口跳 {entry.op_id!r} 吃的是 {entry.input!r}，"
            f"但投料契约是 {declaration.task_schema!r}——投进来的东西不是它期待的形状"
        )
    checks.append("C3b 入口吃的就是投料")

    # ---- C4：next 边 ----
    incoming: dict[str, int] = {c.op_id: 0 for c in components}
    for comp in components:
        for target in comp.next:
            if target not in by_id:
                problems.append(
                    f"op {comp.op_id!r} 的 next 指向了不存在的 op {target!r}。"
                    f"这只盒子的 op 有：{sorted(by_id)}"
                )
                continue
            incoming[target] = incoming.get(target, 0) + 1
            nxt = by_id[target]
            if comp.output != nxt.input:
                problems.append(
                    f"op {comp.op_id!r} 跑完接着跑 {target!r}，"
                    f"但 {comp.op_id!r} 吐的是 {comp.output!r}，"
                    f"{target!r} 吃的是 {nxt.input!r}——"
                    f"运行时会把前者直接喂给后者，形状对不上"
                )
    checks.append("C4 执行顺序上的数据形状")

    # ---- C5：唯一入口 + 没有孤岛 + 没有环 ----
    starts = [c.op_id for c in components if incoming.get(c.op_id, 0) == 0]
    if len(starts) != 1:
        problems.append(
            f"作业路线有 {len(starts)} 个起点（没有任何 op 指向它们）：{starts}。"
            f"运行时要求恰好一个起点"
        )
    elif entry is not None and starts[0] != entry.op_id:
        problems.append(
            f"起点是 {starts[0]!r}，但 input_from='external' 的是 {entry.op_id!r}——"
            f"这两个得是同一个跳，否则投料喂不进去"
        )

    reachable = _reachable_from(components, starts[0]) if len(starts) == 1 else set()
    orphans = sorted(c.op_id for c in components if c.op_id not in reachable)
    if orphans:
        problems.append(
            f"这些 op 从起点走不到：{orphans}。"
            f"走不到的跳永远不跑，也不报错——链在某处断了"
        )
    checks.append("C5 唯一入口、无孤岛")

    cycle = _find_cycle(components)
    if cycle:
        problems.append(
            f"作业路线成环：{' -> '.join(cycle)}。"
            f"环上的顺序是任意的，跑起来会撞上「步数上限」"
        )
    checks.append("C5b 无环")

    # ---- 末跳 ----
    terminals = [c for c in components if not c.next]
    if len(terminals) != 1:
        problems.append(
            f"作业路线有 {len(terminals)} 个末跳：{[c.op_id for c in terminals]}。"
            f"一条链该有一个产出——产出契约只声明了一个（"
            f"result_schema={declaration.result_schema!r}）"
        )
    elif terminals[0].output != declaration.result_schema:
        problems.append(
            f"末跳 {terminals[0].op_id!r} 吐的是 {terminals[0].output!r}，"
            f"但产出契约声明的是 {declaration.result_schema!r}——"
            f"产出的形状跟声明的不是一回事"
        )
    checks.append("C10 末跳产出 == 产出契约")

    # ---- schema 存在性 ----
    # **判定归 `core/decl_checks.py`**（与体检台共用一份）。
    # 装配期和体检期问的是同一批事实，两处各写一份 = 两份事实 = 漂。
    schema_ids = {s.id for s in schemas}
    problems.extend(check_schema_refs(
        schema_ids=schema_ids,
        result_schema=declaration.result_schema,
        task_schemas={declaration.task_type: declaration.task_schema},
    ))
    problems.extend(check_op_schema_refs(
        schema_ids=schema_ids,
        op_refs=[
            (f"op {comp.op_id!r}", role, sid)
            for comp in components
            for role, sid in (("input", comp.input), ("output", comp.output))
        ],
    ))
    checks.append("C6/C7 引用的 schema 都存在")

    # ---- 判据 ----
    result_schema = next(
        (s for s in schemas if s.id == declaration.result_schema), None
    )
    result_fields = {f.name for f in result_schema.fields} if result_schema else set()

    problems.extend(check_acceptance_metrics(
        result_schema=declaration.result_schema,
        result_fields=result_fields,
        acceptance=declaration.acceptance,
    ))
    problems.extend(check_maturity(
        maturity=declaration.maturity,
        acceptance=declaration.acceptance,
    ))
    checks.append("C8/C11 判据读得到值")

    # ---- 例外 ----
    # 同样归 `core/decl_checks.py`——**条件语法只有一份**（`core/conditions.py`），
    # 判定也只有一份。这里和体检台跑同一段代码。
    problems.extend(check_exception_conditions(
        result_schema=declaration.result_schema,
        result_fields=result_fields,
        exceptions=declaration.exceptions,
    ))
    checks.append("C9 例外条件真的能触发")

    # ---- 业务对象标识 ----
    if declaration.subject_ref_field:
        task_schema = next(
            (s for s in schemas if s.id == declaration.task_schema), None
        )
        task_fields = {f.name for f in task_schema.fields} if task_schema else set()
        if declaration.subject_ref_field not in task_fields:
            problems.append(
                f"subject_ref_field={declaration.subject_ref_field!r} "
                f"不在投料 schema {declaration.task_schema!r} 里"
                f"（可用字段：{sorted(task_fields)}）——"
                f"声明了却拿不到，归档时这条履约记录归不到任何工作单元"
            )
    if declaration.subject_ref_subfield and not declaration.subject_ref_field:
        problems.append(
            "声明了 subject_ref_subfield 却没有 subject_ref_field——"
            "从哪个对象里取，总得先说清是哪个对象"
        )
    if (declaration.subject_ref_field or declaration.subject_ref_from_result) \
            and not declaration.subject_kind:
        problems.append(
            "声明了 subject_ref_field / subject_ref_from_result 却没有 subject_kind——"
            "「它关于哪一类业务对象」不能靠猜"
        )
    checks.append("C12 业务对象标识拿得到")

    # ---- 接线 ----
    if declaration.feeds_into and declaration.feeds_into == declaration.feedback_into:
        problems.append(
            "feeds_into 和 feedback_into 一模一样——"
            "「交出去让下游接着验」和「上一轮的结论回到起点」是两件事，"
            "混在一起闭环边会进拓扑排序，排序遇到环就静默按原顺序收尾"
        )
    checks.append("接线声明")

    if problems:
        raise AssemblyError(box_id, problems)

    # ---- 组装 ----
    beeline = Beeline(
        id=declaration.beeline_id,
        version=declaration.beeline_version,
        description=declaration.beeline_description or declaration.description,
        operations=[
            Operation(
                op_id=comp.op_id,
                type=comp.resolved_type(),
                input_from=comp.input_from,
                input=comp.input,
                output=comp.output,
                next=[NextRef(op_id=t) for t in comp.next],
                bee=Bee(type=comp.resolved_bee_type()),
                external_system=comp.external_system,
                idempotency=comp.idempotency,
                leaves_artifact=comp.leaves_artifact,
            )
            for comp in components
        ],
    )

    definition = BeeBoxDefinition(
        id=declaration.id,
        version=declaration.version,
        description=declaration.description,
        schemas=list(schemas),
        task=[
            TaskDef(
                type=declaration.task_type,
                task_schema=declaration.task_schema,
                beeline_id=declaration.beeline_id,
                beeline_version=declaration.beeline_version,
                trigger=declaration.task_trigger,
                accepts_free_text=declaration.accepts_free_text,
                subject_ref_field=declaration.subject_ref_field,
                subject_ref_from_result=declaration.subject_ref_from_result,
                subject_ref_subfield=declaration.subject_ref_subfield,
                subject_kind=declaration.subject_kind,
            )
        ],
        result=ResultDef(
            type=declaration.result_type,
            result_schema=declaration.result_schema,
            description=declaration.result_description,
            acceptance=list(declaration.acceptance),
            exceptions=list(declaration.exceptions),
            maturity=declaration.maturity,
            view=declaration.artifact_view,
            empty_input_metric=declaration.empty_input_metric,
        ),
        queen=declaration.queen,
        metrics=declaration.metrics,
        placement=PlacementDef(
            display_name=declaration.display_name,
            value_stream=declaration.value_stream,
            role=declaration.role,
            domain=declaration.domain,
            feeds_into=list(declaration.feeds_into),
            feedback_into=list(declaration.feedback_into),
        ),
    )

    # 执行器由 runtime 建（执行是执行层的事），**表是盒子的**——
    # core 不知道任何一只盒子的 op 名。

    report = AssemblyReport(
        box_id=box_id,
        op_count=len(components),
        schema_count=len(schemas),
        checks=checks,
    )
    return AssembledBox(
        definition=definition,
        beeline=beeline,
        handlers=effective,
        report=report,
    )


# ============================================================
# 图上的两个小工具
# ============================================================


def _reachable_from(components: list[Component], start: str) -> set[str]:
    """从起点沿 next 能走到的所有 op"""
    by_id = {c.op_id: c for c in components}
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen or current not in by_id:
            continue
        seen.add(current)
        for target in by_id[current].next:
            stack.append(target)
    return seen


def _find_cycle(components: list[Component]) -> list[str]:
    """找出环（返回环上的 op_id 路径）；没环返回空列表"""
    by_id = {c.op_id: c for c in components}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {c.op_id: WHITE for c in components}
    stack: list[str] = []

    def dfs(node: str) -> list[str]:
        color[node] = GRAY
        stack.append(node)
        for target in by_id[node].next:
            if target not in by_id:
                continue
            if color[target] == GRAY:
                return stack[stack.index(target):] + [target]
            if color[target] == WHITE:
                found = dfs(target)
                if found:
                    return found
        color[node] = BLACK
        stack.pop()
        return []

    for comp in components:
        if color[comp.op_id] == WHITE:
            found = dfs(comp.op_id)
            if found:
                return found
    return []
