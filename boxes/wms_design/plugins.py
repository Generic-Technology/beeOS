"""
boxes.wms_design.plugins - 这只盒子自己的业务插件

**插件类型、参数模型、实现都在这里**——
core 只定协议，runtime 只提供钩子与执行机制，
**业务插件归业务侧**：要新增一种业务插件，改的是这个文件，不是 core。

## 这只盒子的核验插件

`wms_design_output_check` 挂在盒级核验钩子（`box.verify`）上，
在**产出定案之后、验收之前**跑：

> **产出里那些说「我过了」的标志位，要跟实际内容对得上。**

**为什么这是业务、不是形状**：
产出契约（`schema_design_package`）声明的是**字段在不在、类型对不对**。
它表达不了「**这个标志位说通过了，可它对应的内容其实是空的**」。

这只盒子有明说的规矩：

> 「判了就要带出来」——下游（测试盒 / 分析盒）要靠 `expectations`
> 才知道该验什么。少了它，下游只能拿需求描述去猜。

**以前没人核对这件事**：判据读的是**盒子自己算的标志位**
（`expectation_completeness` / `wms_binding` / `rule_consistency`），
盒子说「我过了」就过了——没人回头看一眼它说的对不对。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.plugins import (
    PluginType,
    register_implementation,
    register_plugin_type,
)


#: 插件类型 id（业务专有——core 里没有它）
OUTPUT_CHECK_PLUGIN = "wms_design_output_check"

#: 实现键（实现在哪张表里）
OUTPUT_CHECK_IMPL = "wms_design.output_check"

#: 核验钩子（runtime 留的钩子，runtime/plugins.py 登记）
EXTENSION_VERIFY = "box.verify"


class TruthCheck(BaseModel):
    """一条「自报 ↔ 实际」的对照

    **不许静默吞字段**：写错当场报错。
    """

    model_config = ConfigDict(extra="forbid")

    flag: str = Field(
        description="产出里那个「我过了」的标志位路径（如 expectation_completeness）",
    )
    content: str = Field(
        description="标志位对应的实际内容路径（如 expectations）",
    )
    expect: Literal["non_empty", "empty", "zero"] = Field(
        default="non_empty",
        description=(
            "标志位为 True 时，实际内容要满足什么："
            "non_empty = 不许空；empty = 必须是空；zero = 必须是 0（计数类）"
        ),
    )


class DesignOutputCheckParams(BaseModel):
    """设计盒的产出自检规则——**业务规矩**，不是形状"""

    model_config = ConfigDict(extra="forbid")

    truth_checks: list[TruthCheck] = Field(
        default_factory=list,
        description="要核对的「自报标志位 ↔ 实际内容」对照表",
    )


def _resolve_path(payload: Any, path: str) -> tuple[bool, Any]:
    """按点号路径取值。返回 (找得到吗, 值)

    **找得到但值是空**和**压根没这个键**是两回事，报错时要分开说。
    """
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _is_empty(value: Any) -> bool:
    """这个值算不算「空」

    **空**：`None` / 空串 / 空数组 / 空对象。
    **不算空**：`0` 和 `False`——它们是**真实的值**
    （覆盖率 0% 是「覆盖不到」，False 是「没通过」），
    把它们当「空」等于把「没过」说成「没算」，那是骗人。
    """
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple, set)):
        return len(value) == 0
    return False


def _matches(value: Any, expect: str) -> bool:
    """实际内容满不满足这条对照的要求"""
    if expect == "non_empty":
        return not _is_empty(value)
    if expect == "empty":
        return _is_empty(value)
    if expect == "zero":
        return value == 0
    return False


def check_design_output(
    view: dict[str, Any], params: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """核对设计包的产出：自报的标志位要跟实际内容对得上

    **通过就返回 `None`**（这个钩子的契约：`None` = 通过）。
    **不通过返回 `{"problems": [...]}`** —— 问题清单要能直接读懂。

    **这只盒子的三态口径**（业务知识就在这里）：
    标志位可能是 `True` / `False` / `"n/a"`——
    `"n/a"` 是「这条判据这次用不上」，**不是「我过了」**，
    更不是「我没说」。所以只有 `True` 才触发核对。

    `False` 也不管：那是**有据可查的失败**，判据会红、看板会显示——
    插件不去重复判据的活，只核对「自报与实际对不上」。

    `view` 是 runtime 给的**受限视图**（深拷贝），`params` 是这只盒子
    自己声明的规则（也逐次深拷贝）——插件改不动宿主的任何东西。
    """
    result = view.get("result") or {}
    rules = DesignOutputCheckParams.model_validate(params or {})

    problems: list[str] = []
    for check in rules.truth_checks:
        found, flag = _resolve_path(result, check.flag)
        if not found:
            problems.append(
                f"产出里没有「{check.flag}」这个标志位——连自报都没有"
            )
            continue
        if flag is not True:
            # False = 有据可查的失败（判据会红）；"n/a" = 这条判据这次用不上。
            # 两种都不是「我过了」，插件不重复判据的活。
            continue
        content_found, content = _resolve_path(result, check.content)
        if not content_found:
            problems.append(
                f"产出说「{check.flag}」过了，但产出里没有「{check.content}」——"
                f"自报与实际对不上"
            )
            continue
        if not _matches(content, check.expect):
            problems.append(
                f"产出说「{check.flag}」过了，但「{check.content}」"
                f"不满足「{check.expect}」——自报与实际对不上"
            )

    if not problems:
        return None
    return {"problems": problems}


def register_design_output_check() -> None:
    """登记这只盒子的业务插件

    **登记主体是业务侧**（通用型插件才由公共提供方登记）。

    **由盒子的包入口（`__init__.py`）显式调用**——
    登记必须早于装配（装配器要认得这个插件类型，I5），
    而「早于装配」这件事写在本子里才看得见。
    """
    register_plugin_type(
        PluginType(
            id=OUTPUT_CHECK_PLUGIN,
            allowed_hosts=("box",),
            extension_points=(EXTENSION_VERIFY,),
            implementation_key=OUTPUT_CHECK_IMPL,
            params_model=DesignOutputCheckParams,
            summary="设计盒的产出自检：自报的标志位要跟实际内容对得上",
        )
    )
    register_implementation(OUTPUT_CHECK_IMPL, check_design_output)


# ============================================================
# op 级插件：打包之前的准入闸
# ============================================================

#: 打包准入插件的类型 id（业务专有）
PACKAGE_GATE_PLUGIN = "wms_design_package_gate"

#: 它的实现键
PACKAGE_GATE_IMPL = "wms_design.package_gate"

#: op 级「执行前」钩子（runtime 留的钩子）
EXTENSION_OP_BEFORE = "op.before"


class DesignPackageGateParams(BaseModel):
    """设计包打包前的准入规则——**业务规矩**

    **不许静默吞字段**：写错当场报错。
    """

    model_config = ConfigDict(extra="forbid")

    required_fields: list[str] = Field(
        default_factory=list,
        description="证据里必须带到、且不许是空的字段名",
    )


def gate_design_package(
    view: dict[str, Any], params: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """**打包之前**：证据齐不齐？

    **为什么这是业务**：设计盒的规矩是「判了就要带出来」——
    证据腿算出来的东西，必须**原样**进到打包的产出里。

    **以前这一段没有闸**：证据腿漏了字段，打包腿照样把包打出来交付了。
    这条腿上翻过好几次车（`requirement_count` 漏在外面、
    evidence 那一跳漏过好几次东西）——每次都是**交付出去之后**才发现的。

    **这个闸在打包之前**：不齐就不许打包 ✗（不是交付完了再回头说）。

    **通过返回 `None`，不通过返回 `{"problems": [...]}`**——
    准入钩子用的是 `verdict` 契约 ✓。
    """
    input_data = view.get("input") or {}
    rules = DesignPackageGateParams.model_validate(params or {})

    problems: list[str] = []
    for name in rules.required_fields:
        if name not in input_data:
            problems.append(
                f"证据里没有「{name}」——不能就这么打包交付"
            )
        elif input_data[name] is None:
            problems.append(
                f"证据里的「{name}」是空的——不能就这么打包交付"
            )

    if not problems:
        return None
    return {"problems": problems}


def register_design_package_gate() -> None:
    """登记这个 op 级业务插件"""
    register_plugin_type(
        PluginType(
            id=PACKAGE_GATE_PLUGIN,
            allowed_hosts=("op",),
            extension_points=(EXTENSION_OP_BEFORE,),
            implementation_key=PACKAGE_GATE_IMPL,
            params_model=DesignPackageGateParams,
            summary="设计包打包前的准入：证据必须齐（业务规矩，不是形状）",
        )
    )
    register_implementation(PACKAGE_GATE_IMPL, gate_design_package)
