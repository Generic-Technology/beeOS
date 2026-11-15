"""
boxes.receipt.plugins - 票据盒自己的业务插件

**插件类型、参数模型、实现都在这里**——
core 只定协议，runtime 只提供钩子与执行机制，
**业务插件归业务侧**：要新增一种业务插件，改的是这个文件，不是 core。

## 这只盒子的核验插件

`receipt_output_check` 挂在盒级核验钩子（`box.verify`）上，
在**产出定案之后、验收之前**跑：

> **产出自报的算术标志位，要跟产出里的金额对得上。**

**为什么这是业务、不是形状**：产出契约声明的是「字段在不在、类型对不对」，
它表达不了「**这几个标志位说的是不是真的**」。

这只盒子的判据读的是**盒子自己算的标志位**
（`line_arithmetic` / `sum_arithmetic` / `total_arithmetic`）——
盒子说「算过了」就过了。
没人回头核对：它算的那份数，跟最终带进产出里的那份数，
**是不是同一份**。

**算术规则住在 `boxes/receipt/arithmetic.py`**——
runner 与这个插件共用同一份（各写各的必然漂）。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from boxes.receipt.arithmetic import (
    lines_arithmetic,
    sum_matches,
    total_matches,
)
from core.plugins import (
    PluginType,
    register_implementation,
    register_plugin_type,
)


#: 插件类型 id（业务专有——core 里没有它）
OUTPUT_CHECK_PLUGIN = "receipt_output_check"

#: 实现键（实现在哪张表里）
OUTPUT_CHECK_IMPL = "receipt.output_check"

#: 核验钩子（runtime 留的钩子，runtime/plugins.py 登记）
EXTENSION_VERIFY = "box.verify"


class ReceiptOutputCheckParams(BaseModel):
    """票据盒的产出自检规则——**业务规矩**，不是形状

    **不许静默吞字段**：写错当场报错。
    """

    model_config = ConfigDict(extra="forbid")

    check_lines: bool = Field(
        default=True, description="核「每行 数量×原价×折扣 = 金额」",
    )
    check_sum: bool = Field(
        default=True, description="核「Σ行金额 = 小计」",
    )
    check_total: bool = Field(
        default=True, description="核「小计 + 税 − 另减的优惠 = 实付」",
    )
    tolerance: float = Field(
        default=0.01,
        description="金额容差（留给浮点和两位小数）——**它是业务规矩**，不是技术细节",
    )


def check_receipt_output(
    view: dict[str, Any], params: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """核验票据产出的算术自洽

    **通过就返回 `None`**（这个钩子的契约：`None` = 通过）。
    **不通过返回 `{"problems": [...]}`** —— 问题清单要能直接读懂。

    **只核对「说『过了』的那些标志位」**：
    说「没过」（False）的，验收判据会红、看板会显示——
    那是判据的活，插件不重复干。
    说「用不上」（`"n/a"`）的，这张票本来就没那一栏，不该算它不对。

    `view` 是 runtime 给的**受限视图**（深拷贝），`params` 是这只盒子
    自己声明的规则（也逐次深拷贝）——插件改不动宿主的任何东西。
    """
    result = view.get("result") or {}
    rules = ReceiptOutputCheckParams.model_validate(params or {})
    items = result.get("items") or []
    tol = rules.tolerance

    problems: list[str] = []

    if rules.check_lines and result.get("line_arithmetic") is True:
        verdict, bad = lines_arithmetic(items, tolerance=tol)
        if verdict is None:
            # **自报「过了」，实际是「用不上」**——
            # 一行单价都没有时，这条判据**根本没验过**，
            # 说它「过了」就是**假装验过了**。
            # （「假装验过」比「算错」更难查：读者看到的是「全过」。）
            problems.append(
                "产出说「每行金额都对得上」，但用产出里的行重算，"
                "**根本没法验**（没有单价 / 没有可核验的行）——"
                "说「过了」就是假装验过了"
            )
        elif verdict is False:
            problems.append(
                f"产出说「每行金额都对得上」，但用产出里的行重算，"
                f"第 {bad} 行对不上"
            )

    if rules.check_sum and result.get("sum_arithmetic") is True:
        if not sum_matches(items, result.get("subtotal"), tolerance=tol):
            problems.append(
                "产出说「Σ行金额 = 小计」，但用产出里的行金额重算，对不上"
            )

    if rules.check_total and result.get("total_arithmetic") is True:
        if not total_matches(
            result.get("subtotal"),
            result.get("tax"),
            result.get("discount"),
            result.get("included_discount"),
            result.get("total"),
            tolerance=tol,
        ):
            problems.append(
                "产出说「小计 + 税 − 优惠 = 实付」，但用产出里的金额重算，对不上"
            )

    if not problems:
        return None
    return {"problems": problems}


def register_receipt_output_check() -> None:
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
            params_model=ReceiptOutputCheckParams,
            summary="票据盒的产出自检：自报的算术标志位要跟产出里的金额对得上",
        )
    )
    register_implementation(OUTPUT_CHECK_IMPL, check_receipt_output)
