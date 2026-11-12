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
    check_subject_ref,
)
from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricsDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    SchemaDef,
    TaskDef,
)
from core.plugins import (
    PluginContext,
    PluginSpec,
    check_host_plugins,
    compile_bindings,
    freeze_plugin_context,
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

    # **不许静默吞字段**（2026-11-04 补）：拼错当场报错。
    # 一跳的形状拼错了（`inpt=` 而不是 `input=`），吞掉的后果是
    # 「这一跳吃什么」变成空值，装配器还照样装得出来。
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

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
    #: 这一跳挂的**插件声明**（横切能力：权限、审计、限流…）。
    #:
    #: **插件必有宿主**：声明只可能出现在这里或 `BoxDeclaration.plugins` 两处，
    #: 没有第三种放法 → 插件不可能悬空。
    #: **插件不改形状**：插件没有 input/output 字段，它改不了宿主的形状。
    plugins: list[PluginSpec] = Field(default_factory=list)
    #: 这一跳**必需**的插件类型 id。缺了装配期就报错，不等到运行时才发现。
    requires_plugins: list[str] = Field(default_factory=list)
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

    # **不许静默吞字段**（2026-11-04 补）
    #
    # 以前这里是 pydantic 的默认行为：**不认识的参数照单全收，然后丢掉**。
    # 于是 `trigger=(...)`（写错了，应该是 `task_trigger`）会**安静地**
    # 变成「没声明触发条件」——新协议最大的卖点是「装配期就报错」，
    # 结果它自己在第一道门上就把拼错的字段吞了。
    #
    # 这不是假设：迁 `wms_requirement` 时它真的发生过一次——
    # 旧文件里 `trigger=(` 顶格写歪了，转换时认不出来，
    # 结果**触发条件整段消失**，差分比对当场抓到（触发条件 null）。
    # 如果差分没跑，那就是一只「看起来装好了、其实少了半截」的盒子。
    #
    # `extra="forbid"` = 拼错当场报错，报错里写清「你写的不是这个字段」。
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="盒子唯一标识")
    version: int = Field(default=1, description="盒子版本（递增序列号）")
    description: str = Field(default="", description="这只盒子在做什么")

    # ---- 展示位（这只盒子在企业里站在哪）----
    display_name: str = Field(default="", description="给人看的名字")
    value_stream: str = Field(default="", description="所属企业价值流")
    role: str = Field(default="", description="业务责任单元的职责")
    #
    # **这里没有 `domain` / `feeds_into` / `feedback_into`**（2026-11-06 退休）。
    #
    # 盒子之间怎么连，是**协作边界**的事实，住在 `fulfillment_scopes/<边界>/` 的声明里——
    # 不再是每只盒子各写一遍。理由见 `docs/fulfillment-scope.md`：
    # 照抄 `feeds_into` 会漏掉真实依赖（8 条交接里只有 5 条被声明过），
    # 而两份事实并存必然漂，漂了不报错。

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

    # ---- 插件（盒级）----
    #
    #: 挂在**整盒**上的插件声明（横切能力：升级决策、审计、限流…）。
    #:
    #: 跟作业组件的 `plugins` 分开，是因为宿主不同：
    #: 这里的宿主是「整盒」，运行时在**盒级扩展点**上调用它。
    plugins: list[PluginSpec] = Field(default_factory=list)
    #: 整盒**必需**的插件类型 id。缺了装配期就报错。
    requires_plugins: list[str] = Field(default_factory=list)


class AssemblyReport(BaseModel):
    """装配报告——装配过了什么、验过什么"""

    # **不许静默吞字段**（2026-11-04 补）：报告是给人看的，
    # 报告里少一项 = 有人以为验过了而其实没验。
    model_config = ConfigDict(extra="forbid")

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
    #: 装配时**冻结**的插件上下文（类型表 / 扩展点表 / 实现表的快照）。
    #:
    #: **运行时唯一认的东西**：它不重新查全局注册表——
    #: 那样装配期的校验就形同虚设（装配时存在的实现，运行时不保证在），
    #: 而且那是**第二份事实**。
    plugin_context: PluginContext = Field(
        default_factory=freeze_plugin_context,
        description="插件类型 / 扩展点 / 实现的冻结快照",
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


# ============================================================
# 形状相容：上一跳的产出能不能喂给下一跳
# ============================================================
#
# ## 为什么不是「id 相等」
#
# 装配器原来判「上一跳的 output == 下一跳的 input」——**字符串相等**。
# 那比运行时严：
#
# - 运行时下一跳收到的是上一跳的**整个产出**（`runtime/executor.py`
#   的 `op_input = dict(op_output)`），
# - 而契约校验只查 schema **声明过的**字段（`runtime/contract.py`
#   的 `validate_payload` 遍历 `schema.fields`），
# - 所以「上一跳多吐几个字段」在运行时**完全合法**。
#
# 装配期却会为它报红——**假红**：一只盒子装不上，
# 而它跑起来好好的。这类假红最坏：人会以为「装配器挑刺」，
# 于是把多出来的字段删掉去迁就检查器，而不是把检查器改对。
#
# ## 这里判的是「**保证**喂得进」，不是「形状对得上」
#
# 下一跳要一个必填字段，上游有它——**还不够**：
# 上游那个字段必须是**必填**的。上游声明成可选 = 它**可能不给**，
# 那这条边就不保证喂得进（运行时可能真的缺）。
#
# 这正是「泛型约束」在 Python 里该有的语义——
# **结构化子类型（协变）**：下游要的，上游**保证**给；
# 上游多给的，下游可以不要。
# Python 的泛型是擦除的、只在静态检查期起作用；
# 而这一条能在**装配期**算出来，所以它在装配期算，不在类型检查期算。
#
# 判不了的时候（schema 压根不存在）返回 None：
# 那由 C6/C7 报「schema 不存在」，别在这儿重复报一遍。


def _types_compatible(out_type: str, in_type: str) -> bool:
    """上一跳吐的类型，下一跳吃不吃得下"""
    if out_type == in_type:
        return True
    if in_type == "number_or_na":
        # 下游接受「数字或 n/a」——上游给数字就行
        return out_type in ("number", "integer")
    if in_type == "object":
        return out_type == "object" or out_type.startswith("ref:")
    if in_type.startswith("ref:"):
        # ref 装的是整份上游产出；上游给个对象也接得住
        return out_type.startswith("ref:") or out_type == "object"
    return False


def _shape_gaps(
    schema_by_id: dict[str, SchemaDef],
    from_id: str,
    to_id: str,
) -> Optional[list[str]]:
    """from 的产出能不能喂给 to

    Returns:
        None = 相容（或判不了，交给别的检查报）
        list[str] = 不相容，每条说清**差在哪**（缺哪个字段 / 类型对不上）
    """
    from_schema = schema_by_id.get(from_id)
    to_schema = schema_by_id.get(to_id)
    if from_schema is None or to_schema is None:
        return None

    from_fields = {f.name: f for f in from_schema.fields}
    gaps: list[str] = []
    for want in to_schema.fields:
        if not want.required:
            # 下游自己要的可选字段，上游不给也接得住
            continue
        got = from_fields.get(want.name)
        if got is None:
            gaps.append(f"要必填字段 {want.name!r}（{want.type}），上游没吐")
            continue
        if not got.required:
            # **上游声明成可选 = 它可能不给**——
            # 「可能给」不等于「保证给」。这条边不保证喂得进。
            gaps.append(
                f"要必填字段 {want.name!r}（{want.type}），"
                f"上游那个字段是**可选**的——上游可能不给，这条边喂不进去"
            )
            continue
        if not _types_compatible(got.type, want.type):
            gaps.append(
                f"要必填字段 {want.name!r} 是 {want.type}，上游吐的是 {got.type}"
            )
            continue
        if want.type == "enum" and want.values and got.values:
            if not set(got.values) <= set(want.values):
                gaps.append(
                    f"要必填字段 {want.name!r} 只接受 {want.values}，"
                    f"上游可能吐 {sorted(set(got.values) - set(want.values))}"
                )
    return gaps


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

    # schema 索引（形状判定要用：字段集 + 必填性 + 类型）
    schema_by_id: dict[str, SchemaDef] = {s.id: s for s in schemas}

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
            gaps = _shape_gaps(schema_by_id, source.output, comp.input)
            if gaps:
                problems.append(
                    f"op {comp.op_id!r} 声明从 {comp.input_from!r} 取数据，"
                    f"但 {comp.input_from!r} 吐的是 {source.output!r}，"
                    f"而 {comp.op_id!r} 吃的是 {comp.input!r}——形状对不上：\n"
                    + "".join(f"    - {g}\n" for g in gaps)
                    + f"  要么改 {comp.input_from!r} 的 output，要么改 {comp.op_id!r} 的 input"
                )
    checks.append("C2 数据来源自洽")

    # ---- C3：入口的输入 == 投料的形状 ----
    if entry is not None and entry.input != declaration.task_schema:
        # 投料整份喂给入口 → 投料必须**保证**有入口要的必填字段。
        # 投料比入口多带几栏是合法的（入口只读它要的那几栏）。
        gaps = _shape_gaps(schema_by_id, declaration.task_schema, entry.input)
        if gaps:
            problems.append(
                f"入口跳 {entry.op_id!r} 吃的是 {entry.input!r}，"
                f"但投料契约是 {declaration.task_schema!r}——"
                f"投进来的东西不是它期待的形状：\n"
                + "".join(f"    - {g}\n" for g in gaps)
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
                # **上一跳多吐几个字段是合法的**：运行时下一跳收到的是
                # 上一跳的整个产出，契约只查声明过的字段，多余的被忽略。
                # 所以这里问的是「下一跳要的，上一跳**保证**给吗」。
                gaps = _shape_gaps(schema_by_id, comp.output, nxt.input)
                if gaps:
                    problems.append(
                        f"op {comp.op_id!r} 跑完接着跑 {target!r}，"
                        f"但 {comp.op_id!r} 吐的是 {comp.output!r}，"
                        f"{target!r} 吃的是 {nxt.input!r}——"
                        f"运行时会把前者直接喂给后者，形状对不上：\n"
                        + "".join(f"    - {g}\n" for g in gaps)
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
    # 判定归 `core/decl_checks.py`（与体检台共用一份）。
    task_schema = next(
        (s for s in schemas if s.id == declaration.task_schema), None
    )
    task_fields = {f.name for f in task_schema.fields} if task_schema else set()
    problems.extend(check_subject_ref(
        result_schema=declaration.result_schema,
        result_fields=result_fields,
        task_fields=task_fields,
        subject_ref_field=declaration.subject_ref_field,
        subject_ref_from_result=declaration.subject_ref_from_result,
        subject_ref_subfield=declaration.subject_ref_subfield,
        subject_kind=declaration.subject_kind,
    ))
    checks.append("C12 业务对象标识拿得到")

    # ---- 接线 ----
    #
    # **这里不再校验接线**（2026-11-06 退休）：接线搬到协作边界声明之后，
    # 「边指向的盒子存不存在」「闭环边有没有混进交接边」都是**边界级**的事，
    # 由协作边界装配器判（`core/decl_checks.py` 的 D2 / D7 / D11）。
    #
    # **一份事实只放一处**：这里再判一遍，就是两份判定，
    # 而两份判定迟早给出不同答案（边界里那份才是对的）。
    checks.append("接线（在协作边界声明里校验）")

    # ---- 插件 ----
    #
    # **判定归 `core/plugins.py`**（装配器与守卫共用一份）——
    # 抄一份规则到测试里 = 两份判定，迟早给出不同答案。
    #
    # 查的是**一整组**：逐个声明（类型 / 宿主 / 扩展点 / 参数 / 实现）
    # + 整组（同一类型不重复、决策钩子唯一、必需插件已挂上）。
    problems.extend(check_host_plugins(
        list(declaration.plugins),
        host_kind="box",
        requires=list(declaration.requires_plugins),
    ))
    for comp in components:
        problems.extend(check_host_plugins(
            list(comp.plugins),
            host_kind="op",
            requires=list(comp.requires_plugins),
        ))
    checks.append("插件：类型 / 宿主 / 扩展点 / 参数 / 重复 / 实现 / 必需")

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
                plugins=compile_bindings(list(comp.plugins)),
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
        plugins=compile_bindings(list(declaration.plugins)),
        display_name=declaration.display_name,
        value_stream=declaration.value_stream,
        role=declaration.role,
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
        # **冻结**插件上下文：运行时只认这份快照，不查活表。
        plugin_context=freeze_plugin_context(),
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
