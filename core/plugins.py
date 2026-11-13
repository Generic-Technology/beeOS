"""
core.plugins - 插件协议与注册机制

**这个模块不含任何业务知识。** 它只认协议。

## 插件是什么

> **插件 = 挂在组件上的横切扩展。它不能独立存在。**

一个插件类型要能真的跑起来，必须说清三件事（少了任何一件，它就只是一个名字）：

| 要说清的 | 答什么 | 在哪 |
| --- | --- | --- |
| **宿主约束** | 允许挂在哪类宿主上 | `PluginType.allowed_hosts` |
| **扩展点契约** | 作用在哪个钩子上 | `PluginType.extension_points` |
| **实现绑定** | 实现在哪张表里 | `PluginType.implementation_key` |

## 为什么要「开放注册」

插件类型表**不是 core 独占的**：

- 通用插件（谁都要用的）→ **公共提供方**登记一次
- 业务插件（某个业务模块的）→ **所属业务模块**登记一次
- **使用方只声明绑定，不登记类型**

**为什么不能由 core 独占**：core 若独占类型表，新增一种业务插件就得**改内核**——
业务扩展又回到「改内核」，而内核的改动要动全部盒子。
「core 提供机制，boxes 定义业务组件和业务插件」这句话要求的就是
**注册表向业务侧开放**。

**登记必须受控**：受控的是**登记的形状与校验**（§`check_plugin_spec`），
不是**业务内容**。业务内容由业务侧贡献。

**同一个 id 只许登记一次**：两个来源说同一件事，迟早给出不同答案，
而到那时没人知道该信哪份。

## 装配期是唯一的解析点

插件绑定编好之后，运行时**只用装配结果**（那份冻结的快照），
**不重新查这张表**——否则装配期的校验形同虚设
（装配时存在的实现，运行时不保证在），而且那是**第二份事实**。

看 [docs/component-plugin-model.md](../../docs/component-plugin-model.md)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError


#: 宿主种类
#:
#: - `op` —— 作业组件（一跳）
#: - `box` —— 复合组件（整盒）
HostKind = Literal["op", "box"]

#: 插件的实现签名：吃**受限视图**（runtime 给的深拷贝）+ **自己的参数**，
#: 返回该扩展点约定的结果
#:
#: **为什么参数要另给一个槽**：视图是「宿主那边的数据」，
#: 参数是「插件自己声明时写下的配置」——两回事，混在一个 dict 里，
#: 「视图键」这个校验就说不清了。
PluginImpl = Callable[[Mapping[str, Any], Mapping[str, Any]], Any]


# ============================================================
# 协议形状
# ============================================================


class PluginSpec(BaseModel):
    """宿主上写的一个插件声明

    **不许静默吞字段**（与仓里其它模型同一条纪律）：拼错当场报错。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="插件类型 id")
    extension_point: str = Field(description="作用在哪个钩子上")
    params: dict[str, Any] = Field(
        default_factory=dict, description="插件参数（形状由类型定）"
    )


class PluginBinding(BaseModel):
    """装配产物：已校验、已定位实现的插件绑定

    **必须可序列化**：它要进 `Operation`（作业路线要落盘、要导出 JSON 做差分）。
    所以这里只放**实现键**，不放实现本体——实现跟 handler 一样走盒子给的表。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="插件类型 id")
    extension_point: str = Field(description="作用在哪个钩子上")
    params: dict[str, Any] = Field(default_factory=dict)
    implementation_key: str = Field(description="实现在哪张表里")


@dataclass(frozen=True)
class PluginType:
    """一种插件类型：宿主约束 + 扩展点契约 + 实现绑定"""

    id: str
    allowed_hosts: tuple[HostKind, ...]
    extension_points: tuple[str, ...]
    implementation_key: str = ""
    params_model: Optional[type[BaseModel]] = None
    summary: str = ""


@dataclass(frozen=True)
class ExtensionPoint:
    """一个扩展点（runtime 留的钩子）

    **由 runtime 登记**，core 不认具体钩子的名字——
    否则 core 就得反向依赖 runtime。

    `host_kinds` 说**这个钩子在哪类宿主上会触发**。
    这一维不能省：没有它，一个 op 级的插件可以挂到盒级钩子上，
    装配通过、运行时**永远不会被调用**——
    那正是「声明了却没人执行」：读者以为它在守着，其实它一次都没跑过。

    `max_bindings_per_host` 解决「一个钩子上能挂几个」：
    决策类钩子（升级决策）**只许一个**——两个实现同时决定这次履约的终态，
    那就没有「决策」了，只是两套事实打架。

    `view_keys` 是**这个钩子允许递给插件的键**——
    插件只能看见这里列出来的那些，多一个少一个都报错。
    「插件不许碰宿主内部」这条，靠的就是它（加上深拷贝）。

    `return_contract` 是**这个钩子允许插件返回什么**：
    - `none` —— 只能返回 `None`
    - `decision` —— 必须返回一个带 `decision` 键的 dict
    - `patch` —— 只能返回 `None` 或一份补丁（M5 才用）

    `return_required_fields` 是**返回值必须齐的字段**——
    决策类钩子不只收一个词：理由、命中的规则、用不上的规则都在里面，
    少一项读者就看不到发生了什么。

    `decision_values` 是**「decision」键的合法取值**（决策词表）——
    词表由 runtime 持有（`runtime/models.py` 是唯一真源），
    不认识的词按「要人看」处理，不猜。
    """

    id: str
    host_kinds: tuple[HostKind, ...]
    kind: Literal["decision", "action"] = "action"
    max_bindings_per_host: int = 1
    view_keys: tuple[str, ...] = ()
    return_contract: Literal["none", "decision", "patch"] = "none"
    return_required_fields: tuple[str, ...] = ()
    decision_values: tuple[str, ...] = ()
    summary: str = ""


# ============================================================
# 注册表
#
# 三张表都是**开放的**：core / runtime / boxes 都能登记。
# 登记主体是**归属方**（通用型 → 公共提供方；业务型 → 所属业务模块），
# 使用方只声明绑定（§`check_plugin_spec`）。
# ============================================================

PLUGIN_TYPES: dict[str, PluginType] = {}
EXTENSION_POINTS: dict[str, ExtensionPoint] = {}
IMPLEMENTATIONS: dict[str, PluginImpl] = {}


def register_plugin_type(ptype: PluginType) -> PluginType:
    """登记一种插件类型

    **同一个 id 只许登记一次**：两个来源说同一件事，迟早给出不同答案，
    而到那时没人知道该信哪份——那比「没人登记」更坏。
    """
    if ptype.id in PLUGIN_TYPES:
        raise ValueError(
            f"插件类型 {ptype.id!r} 登记过两次——"
            f"同一个 id 两个来源说同一件事，迟早给出不同答案"
        )
    PLUGIN_TYPES[ptype.id] = ptype
    return ptype


def register_extension_point(point: ExtensionPoint) -> ExtensionPoint:
    """登记一个扩展点（runtime 留的钩子）

    **重复登记同一个 id 且内容一样 = 没事**（模块被重新 import 过一次）；
    **内容不一样 = 报错**（两个来源说同一件事，迟早给出不同答案）。
    """
    existing = EXTENSION_POINTS.get(point.id)
    if existing is not None:
        if existing == point:
            return existing
        raise ValueError(
            f"扩展点 {point.id!r} 登记过两次，而且**内容不一样**——"
            f"同一个钩子两套定义，runtime 到底按哪个跑，得有个说法"
        )
    EXTENSION_POINTS[point.id] = point
    return point


def register_implementation(key: str, impl: PluginImpl) -> None:
    """登记一个插件实现

    **重复登记同一个键 = 报错**，除非还是同一个函数
    （模块被重新 import 过一次的情况，不该因此炸掉）。

    **不可调用的对象当场拒**：登记进去才发现调不动，
    那是运行时才炸——装配期能查的就别留到运行时。
    """
    if not callable(impl):
        raise ValueError(
            f"插件实现 {key!r} 不可调用——它是 {type(impl).__name__}"
        )
    existing = IMPLEMENTATIONS.get(key)
    if existing is not None:
        if existing is impl:
            return
        raise ValueError(
            f"插件实现 {key!r} 登记过两次，而且是**不同的实现**——"
            f"运行时到底用哪个，得有个说法；现在只有一个键，装不下两个"
        )
    IMPLEMENTATIONS[key] = impl


# ============================================================
# 校验
#
# **装配器与守卫调的是同一个函数**（老纪律：守卫必须用被验者自己的解析方式）。
# 抄一份规则到测试里 = 两份判定，迟早给出不同答案。
# ============================================================


def _format_validation_error(err: ValidationError) -> str:
    """把 pydantic 的报错压成一行人话"""
    parts = []
    for e in err.errors():
        loc = ".".join(str(x) for x in e.get("loc", ())) or "(根)"
        parts.append(f"{loc}: {e.get('msg', '不合法')}")
    return "；".join(parts)


def check_plugin_spec(spec: PluginSpec, *, host_kind: HostKind) -> list[str]:
    """校验一个插件声明。**返回问题列表（空 = 合法）。**

    每条问题都写清「哪里不对、现在是什么、应该是什么」——
    校验器不负责修，但它负责让人知道该修哪儿。
    """
    problems: list[str] = []

    ptype = PLUGIN_TYPES.get(spec.id)
    if ptype is None:
        problems.append(
            f"插件类型 {spec.id!r} 没登记——已登记的有："
            f"{sorted(PLUGIN_TYPES) or '（一个都没有）'}。"
            f"登记由插件类型的**归属方**做（通用型由公共提供方登记，"
            f"业务型由所属模块登记）；使用方只声明绑定"
        )
        # 类型都没有，后面的检查没意义（扩展点、参数、实现都无从谈起）
        return problems

    if host_kind not in ptype.allowed_hosts:
        problems.append(
            f"插件 {spec.id!r} 不允许挂在 {host_kind} 级宿主上"
            f"（它只允许 {list(ptype.allowed_hosts)}）"
        )

    point = EXTENSION_POINTS.get(spec.extension_point)
    if point is None:
        problems.append(
            f"扩展点 {spec.extension_point!r} 没注册——runtime 还没留这个钩子。"
            f"已注册的：{sorted(EXTENSION_POINTS) or '（一个都没有）'}"
        )
    else:
        if spec.extension_point not in ptype.extension_points:
            problems.append(
                f"插件 {spec.id!r} 不能作用在扩展点 {spec.extension_point!r} 上"
                f"（它允许的是 {list(ptype.extension_points)}）"
            )
        if host_kind not in point.host_kinds:
            # **「能挂上去」不等于「会被调用」**——
            # 扩展点只在它声明的宿主上触发。挂错宿主 = 永远不被调用，
            # 而读者以为它在守着（这就是「声明了却没人执行」）。
            problems.append(
                f"扩展点 {spec.extension_point!r} 只在 {list(point.host_kinds)} 级宿主上触发，"
                f"挂到 {host_kind} 级宿主上**永远不会被调用**——"
                f"换个作用点，或者让扩展点支持这种宿主"
            )

    if ptype.params_model is not None:
        try:
            ptype.params_model.model_validate(spec.params)
        except ValidationError as e:
            problems.append(
                f"插件 {spec.id!r} 的参数不合法：{_format_validation_error(e)}"
            )

    # **实现绑定**：空键 / 没登记 / 不可调用，一律装配期拒。
    # 空键尤其要拒：它会被「实现存在」那条检查跳过（空串是假值），
    # 于是装配通过、运行时才炸——那正是装配期该拦的事。
    if not ptype.implementation_key:
        problems.append(
            f"插件类型 {spec.id!r} 没有实现绑定（implementation_key 是空的）——"
            f"挂上去也不会被调用"
        )
    elif ptype.implementation_key not in IMPLEMENTATIONS:
        problems.append(
            f"插件 {spec.id!r} 的实现 {ptype.implementation_key!r} 没登记——"
            f"挂上去也不会被调用。已登记的实现："
            f"{sorted(IMPLEMENTATIONS) or '（一个都没有）'}"
        )
    elif not callable(IMPLEMENTATIONS[ptype.implementation_key]):
        problems.append(
            f"插件 {spec.id!r} 的实现 {ptype.implementation_key!r} 不可调用"
            f"（它是 {type(IMPLEMENTATIONS[ptype.implementation_key]).__name__}）"
        )

    return problems


def validated_params(spec: PluginSpec) -> dict[str, Any]:
    """校验并返回**规范化后的参数**（补默认值、转换类型）

    **为什么不能直接用 `spec.params`**：参数模型允许省略（有默认值），
    也允许字符串转成数字（`"4"` → `4`）。直接用原文的话，
    插件拿到的就少了默认值、或者拿到的是没转换的字符串——
    **声明说是一回事，传给插件是另一回事**。

    校验不过时抛 `ValidationError`——装配期已经先报过红了，
    走到这里说明参数是合法的。
    """
    ptype = PLUGIN_TYPES.get(spec.id)
    if ptype is None or ptype.params_model is None:
        return dict(spec.params)
    return ptype.params_model.model_validate(spec.params).model_dump()


def check_host_plugins(
    specs: list[PluginSpec],
    *,
    host_kind: HostKind,
    requires: Optional[list[str]] = None,
) -> list[str]:
    """校验一个宿主上的**整组**插件声明

    除了逐个声明的检查（§`check_plugin_spec`），还要看整组：
    - 同一宿主同一插件类型**只许挂一次**
    - 决策类扩展点**每个宿主只许一个绑定**
    - 宿主声明的**必需插件**都得挂上
    """
    problems: list[str] = []
    for spec in specs:
        problems.extend(check_plugin_spec(spec, host_kind=host_kind))

    # 同一宿主同一类型只挂一次
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec.id] = counts.get(spec.id, 0) + 1
    for pid, n in sorted(counts.items()):
        if n > 1:
            problems.append(
                f"插件类型 {pid!r} 在同一个宿主上挂了 {n} 次——"
                f"同一宿主同一类型只许挂一次"
            )

    # 决策类扩展点：一个宿主只许一个绑定
    per_point: dict[str, int] = {}
    for spec in specs:
        per_point[spec.extension_point] = per_point.get(spec.extension_point, 0) + 1
    for point_id, n in sorted(per_point.items()):
        point = EXTENSION_POINTS.get(point_id)
        if point is None:
            continue  # 「扩展点没注册」已经报过了，不重复
        if n > point.max_bindings_per_host:
            problems.append(
                f"扩展点 {point_id!r} 上挂了 {n} 个绑定，"
                f"但它每个宿主只许 {point.max_bindings_per_host} 个"
                + (
                    "——两个实现同时决定这次履约的终态，"
                    "那就没有「决策」了，只是两套事实打架"
                    if point.kind == "decision"
                    else ""
                )
            )

    # 宿主声明的必需插件都得挂上
    mounted = {s.id for s in specs}
    for req in requires or []:
        if req not in mounted:
            problems.append(
                f"宿主声明了必需插件 {req!r}，但没挂上——"
                f"已挂的：{sorted(mounted) or '（一个都没有）'}"
            )

    return problems


# ============================================================
# 冻结
# ============================================================


@dataclass(frozen=True)
class PluginContext:
    """装配时冻结的插件上下文——**运行时唯一认的东西**

    三张表都是**复制**进来的快照，不是活表的引用：
    运行时看到的必须跟装配时看到的一致。
    """

    types: Mapping[str, PluginType]
    extension_points: Mapping[str, ExtensionPoint]
    implementations: Mapping[str, PluginImpl]

    def implementation(self, key: str) -> PluginImpl:
        """按实现键取实现。取不到就报错——不许返回一个空壳。"""
        impl = self.implementations.get(key)
        if impl is None:
            raise KeyError(
                f"插件实现 {key!r} 不在装配快照里——"
                f"快照里有：{sorted(self.implementations) or '（一个都没有）'}"
            )
        return impl

    def has_type(self, type_id: str) -> bool:
        return type_id in self.types


def freeze_plugin_context() -> PluginContext:
    """把三张表复制一份快照

    **为什么冻结**：那张表还在被别处改动（别的盒子在 import 时登记自己的类型）。
    运行时若拿着活表，就可能看到装配之后才出现的东西——
    装配期验过的那份，就不是运行时跑的那份了。
    """
    return PluginContext(
        types=dict(PLUGIN_TYPES),
        extension_points=dict(EXTENSION_POINTS),
        implementations=dict(IMPLEMENTATIONS),
    )


def compile_bindings(specs: list[PluginSpec]) -> list[PluginBinding]:
    """把宿主上的插件声明编译成**可序列化的绑定**

    只编**已经过校验**的声明——装配器先调 `check_host_plugins`，
    过了才编。没过的已经报红了，不会走到这里。

    **参数存的是「规范化后的」那一份**（补了默认值、转换了类型），
    不是声明里的原文——插件拿到的必须跟声明说的一致。
    """
    out: list[PluginBinding] = []
    for spec in specs:
        ptype = PLUGIN_TYPES.get(spec.id)
        if ptype is None:
            continue  # 没登记的已经在校验阶段报红了
        out.append(
            PluginBinding(
                id=spec.id,
                extension_point=spec.extension_point,
                params=validated_params(spec),
                implementation_key=ptype.implementation_key,
            )
        )
    return out
