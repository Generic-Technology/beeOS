"""
runtime.watchdog - 通用插件：执行外层看门狗

**这是「公共提供方」登记的一种通用插件**：
任何一跳都可能跑太久，所以由 runtime 登记一次，
使用方（盒子）只声明绑定（core 不认具体插件，runtime 提供机制与通用件）。

## 它做什么

挂在 `op.around` 上，**包住这一跳的整次执行**（含内部所有尝试），
量一下花了多久：超过 `seconds` → **抛错** → 这一跳失败。

## 它**不是**一个「到点就掐」的闸

**同进程杀不掉线程**（`threading.Thread` 没有安全的强制终止），
也没有能掐死的子进程可杀 → 所以它是**返回之后判慢**：
`call()` 一直不返回，它就永远报不了。

**这是它跟「中断」的区别，必须说在前面**：
- 它能判的是「这一跳**最终**花了多久」；
- 它不能保证「第 N 秒就结束等待」。

要防「一直不返回」，得靠 handler 自己的 I/O 超时
（LLM 腿有默认超时）。

## 它**不**做什么

**不判有副作用的跳失败**。

那一跳可能已经提交了代码、部署了服务、落了库。
说它「没成」而实际上它成了 → 下游重跑它 → **副作用做两遍**。
所以那种跳上它只**记一条观测**（`observe` 口子），
不判失败。

**它凭什么知道有没有副作用**：around 的视图里有宿主那一跳的声明
（`view["op"]`）——插件得自己看得见这件事，不能靠约定。

看 [docs/component-plugin-model.md](../../docs/component-plugin-model.md)。
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from core.plugins import (
    PluginSpec,
    PluginType,
    register_implementation,
    register_plugin_type,
)
from runtime.plugins import EXTENSION_OP_AROUND


#: 插件类型 id（装配期按它查类型与实现）
WATCHDOG_TYPE_ID = "op_watchdog"

#: 实现键（实现本体走盒子给的那张表，跟 handler 一样）
WATCHDOG_IMPLEMENTATION_KEY = "op_watchdog"


class WatchdogParams(BaseModel):
    """看门狗的参数

    **不许静默吞字段**（与仓里其它模型同一条纪律）：拼错当场报错。
    """

    model_config = ConfigDict(extra="forbid")

    seconds: float = Field(
        gt=0,
        description="整跳（连内部重试）最多花多久；超过就算这一跳没成",
    )


def _watchdog(view: Mapping[str, Any], params: Mapping[str, Any]) -> Any:
    """看门狗本体

    **只包住、不改内容**：返回值原样是 `call()` 的结果
    （around 的三条锁之一：返回值必须等于 `call()` 的返回值）。
    """
    host = view["op"]
    limit = float(params["seconds"])

    started = time.monotonic()
    output = view["call"]()
    elapsed = time.monotonic() - started

    if elapsed <= limit:
        return output

    detail = {
        "source": "watchdog",
        "threshold_seconds": limit,
        "elapsed_seconds": round(elapsed, 3),
    }

    # **有副作用 → 不判失败**，但**必须留下观测**
    # （理由见模块头）。观测走 `observe` 口子：只加事实，不改产出、不改状态。
    if host.get("leaves_artifact") or host.get("idempotency_mode") == "forbidden":
        view["observe"]("op_budget_exceeded", detail)
        return output

    raise TimeoutError(
        f"这一跳用了 {elapsed:.1f}s，超过看门狗的 {limit}s 上限"
    )


WATCHDOG_PLUGIN_TYPE = PluginType(
    id=WATCHDOG_TYPE_ID,
    allowed_hosts=("op",),
    extension_points=(EXTENSION_OP_AROUND,),
    implementation_key=WATCHDOG_IMPLEMENTATION_KEY,
    params_model=WatchdogParams,
    summary="整跳超时就算这一跳没成（只对没有副作用的跳）",
)


def watchdog_spec(seconds: float) -> PluginSpec:
    """盒子用这个声明挂看门狗

    **为什么给盒子一个函数而不是一个现成的常量**：
    上限是**业务判断**（这一跳最多该花多久），每只盒子不一样。
    """
    return PluginSpec(
        id=WATCHDOG_TYPE_ID,
        extension_point=EXTENSION_OP_AROUND,
        params={"seconds": seconds},
    )


# 模块被 import 时登记一次
# （`register_plugin_type` 对同 id 重复登记会报错——
#  模块只会被 import 一次，所以这里不会触发。）
register_plugin_type(WATCHDOG_PLUGIN_TYPE)
register_implementation(WATCHDOG_IMPLEMENTATION_KEY, _watchdog)
