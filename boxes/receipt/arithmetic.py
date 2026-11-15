"""
boxes.receipt.arithmetic - 票据盒的业务算术

**这几条是业务规矩，不是通用算术**，所以它们住在盒子里：

- 「5.83折」→ 0.583（只认 0 < 折数 < 10；写 10 是模型在编）
- 行金额 = 数量 × 原价 × 折扣，**或者** POS 把它抹零到元
- Σ行金额 = 小计
- 小计 + 税 − **另减的**优惠 = 实付
  （POS 小票通常**没有税**只有优惠；优惠还可能**已经含在合计里**，
  减不减它结果差 2.60——实测六贤记那张就是这么对上的）

## 为什么单独一个模块

**runner 与业务插件共用这一份。**
各写各的必然漂：一边改了公式，另一边还在按旧的核对，
而读者看到的是「核验过了」。

见 [docs/component-plugin-model.md](docs/component-plugin-model.md)。
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

#: 金额容差——留给浮点和两位小数
TOLERANCE = 0.01


def num(value: Any) -> float | None:
    """把模型给的数字洗干净：¥/$/逗号/全角 → float，拿不准就 None

    拿不准一律 None，不猜。宁可让算术校验失败，也不要把 12 读成 1.2。
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    s = re.sub(r"[^\d.\-]", "", value)
    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def discount_multiplier(raw: Any) -> Optional[float]:
    """票面折数 → 支付比例。「5.83折」→ 0.583，「8折」→ 0.8

    返回 None 表示「这一行没折扣」，调用方按全价验。

    只认 0 < 折数 < 10。写 10（= 不打折）是模型在编——
    「没有折扣」的诚实写法是 null，编一个 10 出来会让真正的
    漏抄折扣看起来像正常的全价行。
    """
    zhe = num(raw)
    if zhe is None or not (0 < zhe < 10):
        return None
    return zhe / 10.0


def line_amount_ok(
    qty: float,
    price: float,
    multiplier: Optional[float],
    amount: float,
    *,
    tolerance: float = TOLERANCE,
) -> bool:
    """行金额对不对得上（含行级折扣 + 抹零）

    两种合法：

    1. 算得开——`数量 × 原价 × 折扣` 直接等于行金额（容差 0.01，
       留给浮点和两位小数）。

    2. 抹零——小票常把行金额**抹零到元**。实测 MUJI：
       498 × 5.83折 = 290.334，票面印的是 290.00。
       这不是算错，是 POS 在这一行就取整了。

    为什么把抹零写成判据的一部分而不是一句「容差放宽点」：
    写成规则，误差就有上限（<1 元），金额读错 0.9 元这种真错
    仍然会被拦；写成模糊容差，容差就成了没人敢动的橡皮。
    它的盲区也照实说：金额被误读成比正确值小不到 1 元时会放过。
    """
    expected = qty * price * (1.0 if multiplier is None else multiplier)
    if abs(expected - amount) <= tolerance:
        return True
    # 抹零到元（向下取整）
    return math.floor(expected + 1e-9) == amount


def sum_matches(
    items: Any, subtotal: Any, *, tolerance: float = TOLERANCE
) -> bool:
    """Σ行金额 == 小计

    **行金额为空的行不算**（它连金额都没有，加不上去）。
    """
    sub = num(subtotal)
    if sub is None:
        return False
    line_sum = 0.0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        amt = num(it.get("amount"))
        if amt is not None:
            line_sum += amt
    return abs(line_sum - sub) <= tolerance


def total_matches(
    subtotal: Any,
    tax: Any,
    discount: Any,
    included_discount: Any,
    total: Any,
    *,
    tolerance: float = TOLERANCE,
) -> bool:
    """小计 + 税 − **另减的**优惠 == 实付

    减的应该是「另减的那部分」= discount − included_discount。
    （含在合计里的那笔已经算过了，再减一次就成 37.10，跟实付对不上。）

    **折让不可能比「折让金额」还多**：included > discount 一定是读错了
    （MUJI 的「实物总额(9%)」是税率不是金额，模型可能把它填进来）。
    这种就当没含，照常减——宁可让票红，也不要拿一个错数把公式凑平。
    """
    sub = num(subtotal)
    tot = num(total)
    if sub is None or tot is None:
        return False
    disc = num(discount)
    included = num(included_discount)
    if included is not None and (disc is None or included > disc):
        included = None
    deducted = (disc or 0.0) - (included or 0.0)
    return abs(sub + (num(tax) or 0.0) - deducted - tot) <= tolerance


def lines_arithmetic(
    items: Any, *, tolerance: float = TOLERANCE
) -> tuple[Optional[bool], list[int]]:
    """每行 数量 × 原价 × 折扣 == 金额

    返回 `(三态结论, 坏行号)`：
    - 结论 `None` —— **用不上**（压根没行可验）
    - 结论 `True` / `False` —— 验得出来，对 / 不对

    **「用不上」的两种情况**：没行；或者有结构化的行、但一行单价都没有
    （票上大概率没这一栏，餐馆结账单很常见）。

    **前提是有结构化的行**：一堆读不成对象的行同样「没有单价」，
    但那是模型抄砸了，不是票上没这栏。
    不加这个前提，垃圾行会被当成「不适用」白放行——比算错还难查。
    """
    rows = 0
    struct_rows = 0
    rows_with_price = 0
    bad: list[int] = []
    for i, it in enumerate(items or [], start=1):
        rows += 1
        if not isinstance(it, dict):
            # **读不成对象的行 = 坏行**，不是「这条判据用不上」。
            # 一堆读不成对象的行同样「没有单价」，
            # 但那是模型抄砸了，不是票上没这栏——
            # 当成「不适用」白放行，比算错还难查。
            bad.append(i)
            continue
        struct_rows += 1
        qty = num(it.get("qty"))
        price = num(it.get("unit_price"))
        amt = num(it.get("amount"))
        if price is not None:
            rows_with_price += 1
        ok = (
            qty is not None
            and price is not None
            and amt is not None
            and line_amount_ok(
                qty,
                price,
                discount_multiplier(it.get("discount_rate")),
                amt,
                tolerance=tolerance,
            )
        )
        if not ok:
            bad.append(i)
    if rows == 0:
        return None, bad
    if struct_rows > 0 and rows_with_price == 0:
        return None, bad
    return (not bad), bad
