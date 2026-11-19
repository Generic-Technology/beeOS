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
from typing import Any, Callable, Mapping, Optional, Sequence

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

#: op 级「执行前」钩子（准入）
#:
#: **看得到这一跳的输入**，返回 None = 放行；
#: 返回问题清单 = **拒绝执行这一跳**（履约中止）。
EXTENSION_OP_BEFORE = "op.before"

#: op 级「执行后」钩子（收尾）
#:
#: **看得到这一跳的输入与输出**，返回 None = 没什么要补的；
#: 返回 `{"append": {字段: [要追加的项]}}` = 往产出里**追加**。
EXTENSION_OP_AFTER = "op.after"

#: op 级「执行外层」钩子（包裹）
#:
#: **多拿到一个 `call()`**——执行器递给它一个可调用，
#: 调它 = 真正执行这一跳（含它内部的所有尝试）。
#:
#: **它是「第三种形状」**：`before` / `after` 是两头（看得见输入、看得见输出，
#: 但看不见执行本身）；`around` 包住执行，所以「跑了多久」「被调了几次」
#: 这些问题问得到它。
EXTENSION_OP_AROUND = "op.around"

#: 盒级核验钩子
#:
#: **触发时机**：产出定案之后、验收之前。
#:
#: **为什么在验收之前**：判据读的正是产出里的字段。
#: 产出缺了什么，判据会判「用不上」——不吵，也不挡，
#: 于是照样交付（这就是「产出从不过契约闸」那族病）。
#: 核验钩子跑在验收前面，缺的东西就**当场说**，而不是让判据悄悄 n/a。
EXTENSION_VERIFY = "box.verify"


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
    register_extension_point(
        ExtensionPoint(
            id=EXTENSION_OP_BEFORE,
            host_kinds=("op",),
            kind="action",
            max_bindings_per_host=1,
            view_keys=("input",),
            return_contract="verdict",
            granularity="per_call",
            summary="这一跳执行之前：看得到输入，可以拒绝执行",
        )
    )
    register_extension_point(
        ExtensionPoint(
            id=EXTENSION_OP_AFTER,
            host_kinds=("op",),
            kind="action",
            max_bindings_per_host=1,
            view_keys=("input", "output"),
            return_contract="patch",
            granularity="per_call",
            summary="这一跳执行之后：看得到输入与输出，可以往产出里追加",
        )
    )
    register_extension_point(
        ExtensionPoint(
            id=EXTENSION_OP_AROUND,
            host_kinds=("op",),
            kind="action",
            # **可以叠多层**（中间件语义：先声明的包住后声明的）——
            # 叠层之间不冲突（它们是**套着**的，不是**并排**的）。
            # 但不许无限叠：4 层足够表达「计时 + 留痕 + 看门狗」这类组合。
            max_bindings_per_host=4,
            # **第三个键 `op` = 宿主那一跳的声明**——
            # 插件得自己看得见「这一跳有没有副作用」：
            # 判「超时了就算失败」这种事，只有在**没有副作用**时才成立
            # （有副作用时，说它「没成」是撒谎 → 下游重跑 → 副作用做两遍）。
            #
            # **第四个键 `observe` = 记一条观测**——
            # 包裹层能看见「这一跳太慢」，但它**不许改产出**，
            # 也不许在有副作用时判失败 → 那条观测怎么回到宿主？
            # 就靠这个口子：**只加事实，不改产出、不改状态**。
            view_keys=("input", "call", "op", "observe"),
            return_contract="around",
            granularity="per_call",
            summary="包住这一跳的执行：拿得到 call()，也拿得到产出",
        )
    )
    register_extension_point(
        ExtensionPoint(
            id=EXTENSION_VERIFY,
            host_kinds=("box",),
            kind="action",
            # **一个宿主一个核验插件**——要多个核验，合并成一个。
            # 两个核验各说各话，读者不知道该信谁的。
            max_bindings_per_host=1,
            view_keys=("result",),
            return_contract="verdict",
            summary="产出定案后的自检点：这只盒子自己核验自己的产出",
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


class PluginRejected(Exception):
    """**插件拒绝**——不是插件坏了，是它说「不行」

    两者要分开：
    - 插件坏了（抛异常）→ 机制问题
    - 插件拒绝（准入没过）→ 业务问题

    混成一个，读者就分不清「这个能力没跑起来」和「它跑起来了、结论是不行」。
    """


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
        # **补丁的形状只有一个**：`{"append": {字段: [要追加的项, ...]}}`
        #
        # **不许有别的键**：补丁是唯一能改产出内容的东西，
        # 形状放开 = 「改」与「追加」分不清了——
        # 而「改」会让下游读到的产出与声明的契约对不上。
        if value is None:
            return None
        if not isinstance(value, dict) or "append" not in value:
            raise PluginContractError(
                f"扩展点 {point.id!r} 的补丁只许是 "
                f"{{'append': {{字段: [要追加的项]}}}}，收到的是 {value!r}"
            )
        extra = sorted(set(value) - {"append"})
        if extra:
            raise PluginContractError(
                f"扩展点 {point.id!r} 的补丁只许有 'append'，多了：{extra}"
            )
        append = value["append"]
        if not isinstance(append, dict):
            raise PluginContractError(
                f"扩展点 {point.id!r} 的 'append' 必须是 dict，"
                f"收到的是 {type(append).__name__}"
            )
        for field, items in append.items():
            if not isinstance(items, list):
                raise PluginContractError(
                    f"扩展点 {point.id!r} 的 'append.{field}' 必须是列表，"
                    f"收到的是 {type(items).__name__}"
                )
        return value
    if point.return_contract == "verdict":
        # **核验结论**：None = 通过；{"problems": [str, ...]} = 不通过。
        #
        # **严格**：只认 `{"problems": [非空字符串, ...]}`——
        # 元素必须是**能直接读懂的字符串**（不是对象、不是空串），
        # 而且**不许有多余的键**（仓里「未知字段当场报错」那条纪律）。
        # 宽松的校验等于让「读不懂的问题」混进来，
        # 而读者看到的就只是「核验没过」——不知道为什么。
        if value is None:
            return None
        if not isinstance(value, dict) or "problems" not in value:
            raise PluginContractError(
                f"扩展点 {point.id!r} 要 None（通过）或 "
                f"{{'problems': [str, ...]}}（不通过），收到的是 {value!r}"
            )
        extra = sorted(set(value) - {"problems"})
        if extra:
            raise PluginContractError(
                f"扩展点 {point.id!r} 的核验结论只许有 'problems'，多了：{extra}"
            )
        problems = value["problems"]
        if not isinstance(problems, list):
            raise PluginContractError(
                f"扩展点 {point.id!r} 的 'problems' 必须是列表，"
                f"收到的是 {type(problems).__name__}"
            )
        if not problems:
            # **空清单不许冒充「通过」**——
            # 通过只有一种写法：返回 None。
            # 插件要是「忘了填清单」，空清单就会让它**静默放行**：
            # 读者看到的是「核验过了」，实际它一次都没核。
            # 宁可红一次，也不要假装绿。
            raise PluginContractError(
                f"扩展点 {point.id!r} 的核验结论不许带空清单——"
                f"**通过请返回 None**；不通过请至少列出一条问题"
            )
        bad = [p for p in problems if not isinstance(p, str) or not p.strip()]
        if bad:
            raise PluginContractError(
                f"扩展点 {point.id!r} 的 'problems' 每一条都必须是非空字符串——"
                f"读不懂的问题等于没报：{bad!r}"
            )
        return value
    if point.return_contract == "around":
        # **around 的返回值不在这里校验**——
        # 它必须和「`call()` 到底返回了什么、抛没抛、调用了几次」对照，
        # 那是 `_check_around_layer` 的活（它手上有记账）。
        # 在这里只能看到返回值本身，**看不到它有没有被改过**。
        raise PluginContractError(
            f"扩展点 {point.id!r} 的返回值由 around 专用校验器判"
            "（要连着 call() 的记账一起看），不走这里"
        )
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
# 执行外层（around）：把插件包在执行外面
# ============================================================


def _same_value(a: Any, b: Any) -> bool:
    """两个值「是不是同一个东西」

    **逐层递归比，类型也要一样**：
    - 顶层一样不够：`{"ok": True}` 换成 `{"ok": 1}` 在 Python 里 `==` 成立
      （`True == 1`），只比顶层会放过这种**换类型**；
    - **类型要比**（`bool` 与 `int` 是两个类型）：值相等但类型换了，
      交出去的东西就不是声明过的那个形状了。

    **为什么不能就地比**：插件拿到的就是 `call()` 的返回值本体，
    它就地改完再返回，两边是**同一个对象** → 比出来「一样」✗。
    所以比的是**快照**（见 `_make_around_layer`）。
    """
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return False
        return all(_same_value(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_same_value(x, y) for x, y in zip(a, b))
    return a == b


def _make_around_layer(
    context: PluginContext,
    binding: PluginBinding,
    point: ExtensionPoint,
    inner: Callable[[], Any],
    source: Mapping[str, Any],
) -> Callable[[], Any]:
    """做一层 around（返回「跑这一层」的函数）

    **三条锁**（docs §4.6.8）：
    1. `call()` 至多调一次——第二次**当场抛错**，不执行第二次；
    2. 返回值必须**等于** `call()` 的返回值（不许改产出）；
    3. `call()` 必须被调用过恰好一次（不许跳过执行）。

    **为什么 `call` 必须是普通函数**：`build_view` 会对视图做深拷贝，
    而深拷贝对**函数**是原子的（返回同一个函数对象）。
    用可调用对象的话，拷贝出来的是**另一个**对象——
    里面的记账（调了几次）就记在副本上了，机制自己看不见。
    """
    state: dict[str, Any] = {"called": 0, "raised": False, "value": None}

    def call() -> Any:
        state["called"] += 1
        if state["called"] > 1:
            raise PluginContractError(
                "call() 只能调一次——插件自己再调一次 = 私设重试，"
                "实际调用次数就不可预测了"
            )
        try:
            value = inner()
        except BaseException:
            state["raised"] = True
            raise
        # **存的是快照**，不是那个对象本身——
        # 插件拿到的就是 `value` 本体，它就地改完再返回，
        # 两边是同一个对象 → 直接比会「一样」✗（就地改产出就漏过去了）。
        state["value"] = copy.deepcopy(value)
        return value

    def run_layer() -> Any:
        impl = context.implementation(binding.implementation_key)
        view = build_view(point, {**dict(source), "call": call})
        try:
            # **插件自己抛的异常原样往上抛**——
            # 包裹层抛异常是在说「这一跳不行」，那是**对执行的判断**，
            # 不是「这个能力没跑起来」（契约违反才是）。
            result = impl(view, copy.deepcopy(binding.params))
        except PluginContractError as e:
            # **机制自己抛的**（比如第二次调 `call()`）= 契约违反
            # → 插件失败（这个能力没按规矩跑）。
            raise PluginFailure(binding.id, point.id, e) from e
        except Exception:
            raise
        _check_around_layer(binding, point, state, result)
        return result

    return run_layer


def _check_around_layer(
    binding: PluginBinding,
    point: ExtensionPoint,
    state: Mapping[str, Any],
    result: Any,
) -> None:
    """校验这一层 around 有没有违反三条锁"""
    called = int(state["called"])
    if called == 0:
        raise PluginFailure(
            binding.id,
            point.id,
            PluginContractError(
                "around 插件没有调用 call()——这一跳根本没执行，"
                "而它装作成功（下游会拿到上一跳的旧输入）"
            ),
        )
    if called > 1:
        raise PluginFailure(
            binding.id,
            point.id,
            PluginContractError(
                f"around 插件调用了 call() {called} 次——只许一次"
            ),
        )
    if state["raised"]:
        raise PluginFailure(
            binding.id,
            point.id,
            PluginContractError(
                "around 插件把 call() 的失败吞掉了，正常返回了一个值——"
                "不许把失败变成成功"
            ),
        )
    if not _same_value(result, state["value"]):
        raise PluginFailure(
            binding.id,
            point.id,
            PluginContractError(
                "around 插件改动了产出——它必须原样返回 call() 的结果"
                "（产出内容只许由业务插件按自己声明的范围改）"
            ),
        )


def call_around_plugins(
    context: PluginContext,
    bindings: Sequence[PluginBinding],
    *,
    extension_point: str,
    source: Mapping[str, Any],
    execute: Callable[[], Any],
) -> Any:
    """把一层层 around 插件包在执行外面，跑完拿产出

    **嵌套顺序 = 宿主声明顺序**（先声明的在外层，像中间件）。
    `execute` 是**真正的执行**（执行器递给插件的那个 `call()` 最终落到它）。
    """
    point = context.extension_points.get(extension_point)
    if point is None:
        raise KeyError(
            f"扩展点 {extension_point!r} 不在装配快照里——"
            f"快照里有：{sorted(context.extension_points) or '（一个都没有）'}"
        )

    layers = [b for b in bindings if b.extension_point == extension_point]
    if not layers:
        return execute()

    # 从内到外构建（声明顺序是从外到内 → 反着建）
    runner = execute
    for binding in reversed(layers):
        runner = _make_around_layer(context, binding, point, runner, source)
    return runner()


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


# ============================================================
# 核验钩子：产出定案后，盒子自己核验自己的产出
# ============================================================


def verify_output(
    context: PluginContext,
    bindings: Sequence[PluginBinding],
    *,
    extension_point: str = EXTENSION_VERIFY,
    source: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """跑核验插件，返回**问题清单**（空 = 全过）

    **返回清单而不是抛异常**，是因为「核验没通过」和「插件自己坏了」
    是两回事，读者要能分开看见：

    - 核验没通过 → 履约中止，原因是**业务问题**（缺了什么）
    - 插件自己坏了 → 履约中止，原因是**机制问题**（插件抛异常）

    插件抛异常时这里**不吞**，它会冒成 `PluginFailure`——
    由调用方按「插件坏了」那条路处理。
    """
    results = call_plugins(
        context,
        bindings,
        extension_point=extension_point,
        source=source or {},
    )
    problems: list[str] = []
    for value in results:
        if not value:
            continue
        problems.extend(str(p) for p in (value.get("problems") or []))
    return problems


def verdict_problems(results: Sequence[Any]) -> list[str]:
    """verdict 类钩子的返回值 → **问题清单**（空 = 通过）

    多个插件的问题**按宿主声明顺序**连起来——
    读者要能看出「一共被拒了几条、每条为什么」。
    """
    problems: list[str] = []
    for value in results:
        if not value:
            continue
        problems.extend(str(p) for p in (value.get("problems") or []))
    return problems


def merge_patch(
    output: dict[str, Any],
    patch: Any,
    *,
    allowed_targets: Sequence[str] = (),
) -> dict[str, Any]:
    """把插件返回的补丁合并进产出

    **只许往 list 尾部追加**：
    - 不许改已有值（那会让「声明的契约」与「实际交付的」对不上）
    - 不许往不在 `allowed_targets` 里的字段追加
      （那是**业务插件自己声明**的范围——机制只负责执行这条声明）
    - 不许往不是 list 的字段追加（那就不是「追加」，是「改」）

    **合并后要重新过形状闸**：那是 `runtime/finalize.py` 的活
    （盒级形状闸在整条链跑完之后验最终产出 ✓）。
    """
    if not patch:
        return output
    merged = dict(output)
    for field, items in (patch.get("append") or {}).items():
        # **空白名单 = 一个都不许追加**（不是「都不限制」）——
        # 「没声明范围」和「范围是全部」差着一整个世界：
        # 前者是「这类插件不许改产出」，后者是「这类插件能改任何字段」。
        # 插件类型没声明 `append_targets` 却返回了补丁 → 契约违反 ✓
        if field not in allowed_targets:
            raise PluginContractError(
                f"补丁想追加到「{field}」，但这类插件只许追加到 "
                f"{list(allowed_targets) or '（一个都没有——这类插件不许改产出）'}"
                "——越界了"
            )
        current = merged.get(field)
        if current is None:
            current = []
        if not isinstance(current, list):
            raise PluginContractError(
                f"补丁想追加到「{field}」，但它现在不是列表"
                f"（{type(current).__name__}）——那就不是「追加」，是「改」"
            )
        merged[field] = list(current) + list(items)
    return merged
