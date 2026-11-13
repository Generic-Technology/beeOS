"""
runtime.plugins - 插件执行机制

core 定**协议**，runtime 真的把插件**调起来**。

## 分工

| 层 | 装什么 |
| --- | --- |
| `core/plugins.py` | 协议形状、注册表、**校验**（装配期用） |
| `runtime/plugins.py` | **扩展点钩子**（runtime 留的口子）、**调用接口**、失败处理 |
| `boxes/*` | 业务插件类型、参数模型、实现 |

## 三条纪律（机制层面的，不是约定层面的）

1. **插件只看见扩展点声明的那些键**（`view_keys`）——
   多一个键、少一个键都报错。
   「不许触碰宿主内部」靠的是这条 **加上深拷贝**：
   插件拿到的是副本，它改自己的那份，改不到宿主的。
   *但这条**不保证**同进程插件的闭包、全局引用和外部副作用——
   要管住那些得靠进程隔离，那是能力层的事。*
2. **插件只能返回扩展点约定的东西**（`return_contract`）——
   决策类钩子必须返回一个带 `decision` 键的 dict，
   不是光一个词（理由、命中的规则、用不上的规则都在里面，见 `runtime/queen.py`）。
3. **插件失败 → 履约中止，不降级**。
   状态**按失败发生的阶段**落（链途中 / 链走完之后），
   而且失败**必须落盘**——只在内存里抛一下，等于没发生过。

看 [docs/component-plugin-model.md](../../docs/component-plugin-model.md)。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional, Sequence

from core.plugins import (
    ExtensionPoint,
    PluginBinding,
    PluginContext,
    register_extension_point,
)
from runtime.models import ALL_DECISIONS, ALLOWED_TRANSITIONS, TaskRun, TaskRunStatus


#: 升级决策钩子（盒级）
#:
#: **触发条件**：产出验收失败 **且** 例外写了 `escalate`。
#: 它**不是**通用的「执行后」——挂在上面的是「这次履约的终态由谁定」。
EXTENSION_ESCALATE = "box.escalate"


def register_runtime_extension_points() -> None:
    """登记 runtime 留的钩子

    **这些钩子由 runtime 留，不由 core 定义**——
    core 要是认钩子的名字，就等于反向依赖 runtime 了。

    **`host_kinds` 不能省**：钩子只在它声明的宿主上触发。
    没有这一维，op 级的插件可以挂到盒级钩子上，
    装配通过、运行时永远不被调用——
    「声明了却没人执行」，装配期本该拦住的。
    """
    register_extension_point(
        ExtensionPoint(
            id=EXTENSION_ESCALATE,
            host_kinds=("box",),
            kind="decision",
            max_bindings_per_host=1,
            view_keys=("result", "failed_rules"),
            return_contract="decision",
            # **四项都要齐**：决策 + 理由 + 命中的规则 + 用不上的规则。
            # 少一项读者就看不到发生了什么（尤其「用不上的规则」——
            # 一条指向产出里没有的量的规则，永远不触发，
            # 读者以为它在守着，其实它一次都没触发过）。
            return_required_fields=(
                "decision", "reason", "triggered_rule", "unusable_rules",
            ),
            # **决策词表的唯一真源在 runtime/models.py**——
            # 这里只引用它，不另抄一份。
            decision_values=ALL_DECISIONS,
            summary="产出验收失败、且例外要求升级时的决策点",
        )
    )


# 模块被 import 时登记一次。
# （`register_extension_point` 对「同 id 同内容」是幂等的，所以重复 import 无害。）
register_runtime_extension_points()


# ============================================================
# 失败
# ============================================================


class PluginFailure(Exception):
    """插件调用失败——宿主要据此中止履约

    **两种失败都归它**：插件自己抛了异常，
    或者插件返回了扩展点约定之外的东西（契约违反）。
    两者对宿主的意义一样：**这个能力没成，履约不能假装它成了**。
    """

    def __init__(self, plugin_id: str, extension_point: str, cause: BaseException) -> None:
        self.plugin_id = plugin_id
        self.extension_point = extension_point
        self.cause = cause
        super().__init__(
            f"插件 {plugin_id!r} 在扩展点 {extension_point!r} 上失败：{cause}"
        )


class PluginContractError(Exception):
    """插件返回的东西不符合扩展点的返回契约"""


# ============================================================
# 调用
# ============================================================


def build_view(point: ExtensionPoint, source: Mapping[str, Any]) -> dict[str, Any]:
    """按扩展点声明的键取**受限视图**

    **多一个键、少一个键都报错**——
    多递一个键 = runtime 把不该给的东西给出去了；
    少递一个键 = 插件拿不到它要的东西，出来的结果必然不对。

    取出来的是**深拷贝**：插件改它，改的是副本。
    """
    missing = [k for k in point.view_keys if k not in source]
    if missing:
        raise PluginContractError(
            f"扩展点 {point.id!r} 要的视图键缺了：{missing}；"
            f"现有的：{sorted(source)}"
        )
    extra = [k for k in source if k not in point.view_keys]
    if extra:
        raise PluginContractError(
            f"扩展点 {point.id!r} 只许看 {list(point.view_keys)}，"
            f"这些不许递进去：{extra}"
        )
    return {k: copy.deepcopy(source[k]) for k in point.view_keys}


def check_return(point: ExtensionPoint, value: Any) -> Any:
    """校验插件返回的东西符合扩展点的返回契约

    **决策类钩子要三样齐**：
    1. 是一个 dict
    2. **必须带的字段都在**（理由、命中的规则、用不上的规则——
       少一项读者就看不到发生了什么）
    3. `decision` 的取值在**合法词表**里

    第 3 条为什么重要：插件返回了 runtime 不认识的决策词，
    以前会被当成「一个正常的业务决定」放行——于是履约按错的词落状态，
    而没人知道插件说了一句 runtime 不懂的话。
    **不认识的词 = 契约错误 = 履约中止**，不是「猜一个」。
    """
    if point.return_contract == "none":
        if value is not None:
            raise PluginContractError(
                f"扩展点 {point.id!r} 只许返回 None，插件返回了 {type(value).__name__}"
            )
        return None
    if point.return_contract == "decision":
        if not isinstance(value, dict):
            raise PluginContractError(
                f"扩展点 {point.id!r} 要一个 dict（决策 + 理由 + 命中的规则 + "
                f"用不上的规则），收到的是 {type(value).__name__}"
            )
        missing = [f for f in point.return_required_fields if f not in value]
        if missing:
            raise PluginContractError(
                f"扩展点 {point.id!r} 的返回值缺字段：{missing}"
                f"（收到 {value!r}）——缺一项读者就看不到发生了什么"
            )
        allowed = point.decision_values
        if allowed and value.get("decision") not in allowed:
            raise PluginContractError(
                f"扩展点 {point.id!r} 的决策词 {value.get('decision')!r} 不在合法词表里："
                f"{list(allowed)}——runtime 不猜它是什么意思"
            )
        return value
    if point.return_contract == "patch":
        if value is not None and not isinstance(value, dict):
            raise PluginContractError(
                f"扩展点 {point.id!r} 只许返回 None 或一份补丁 dict，"
                f"收到的是 {type(value).__name__}"
            )
        return value
    raise PluginContractError(f"扩展点 {point.id!r} 的返回契约 {point.return_contract!r} 不认识")


def call_plugins(
    context: PluginContext,
    bindings: Sequence[PluginBinding],
    *,
    extension_point: str,
    source: Mapping[str, Any],
) -> list[Any]:
    """调用挂在某个扩展点上的插件

    **按宿主声明的顺序**（`bindings` 的顺序）——
    不按注册顺序：注册顺序取决于 import 顺序，不能让它影响业务行为。
    """
    point = context.extension_points.get(extension_point)
    if point is None:
        raise KeyError(
            f"扩展点 {extension_point!r} 不在装配快照里——"
            f"快照里有：{sorted(context.extension_points) or '（一个都没有）'}"
        )

    results: list[Any] = []
    for binding in bindings:
        if binding.extension_point != extension_point:
            continue
        try:
            impl = context.implementation(binding.implementation_key)
            view = build_view(point, source)
            # **参数也逐次深拷贝**：视图是副本，参数也必须是——
            # 否则插件改了自己的配置，改的是**原始声明**，
            # 下一次履约拿到的配置就变了（而且看不出是谁改的）。
            value = impl(view, copy.deepcopy(binding.params))
            results.append(check_return(point, value))
        except Exception as e:
            # 契约违反也是失败：这个能力没成，履约不能假装它成了
            raise PluginFailure(binding.id, extension_point, e) from e
    return results


# ============================================================
# 失败怎么落
# ============================================================


def abort_on_plugin_failure(
    task_run: TaskRun,
    failure: PluginFailure,
    *,
    leaves_artifact: bool = False,
) -> None:
    """插件失败 → 履约中止（不降级），状态按**阶段**落，失败**落盘**

    **状态按阶段落**，因为状态机是白名单：

    | 失败发生在 | 当前状态 | 落哪 |
    | --- | --- | --- |
    | 链**途中** | `running` | 有残留 → `partially_completed`，否则 → `failed` |
    | 链**走完之后**（升级决策） | `completed` | `failed`（`completed → partially_completed` 不被允许） |

    **落盘**：事件流里记一条 `plugin_failure`，
    状态 reason 里写清「哪个插件、在哪个扩展点、怎么失败的」。
    只在内存里抛一下 = 等于没发生过。
    """
    reason = str(failure)
    task_run.event_log.append(
        event="plugin_failure",
        detail={
            "plugin_id": failure.plugin_id,
            "extension_point": failure.extension_point,
            "error": str(failure.cause),
        },
    )

    current = task_run.status
    if current == TaskRunStatus.COMPLETED:
        target = TaskRunStatus.FAILED
    elif current == TaskRunStatus.RUNNING:
        target = (
            TaskRunStatus.PARTIALLY_COMPLETED
            if leaves_artifact
            else TaskRunStatus.FAILED
        )
    else:
        # **状态机不允许改，就不硬改**——
        # 硬转会抛 `illegal task run transition`，
        # 而那时失败已经落盘了，读者至少看得见发生过什么。
        return

    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        return
    task_run.transition_to(target, reason=reason)
