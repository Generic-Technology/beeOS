"""
core.conditions - 例外条件的语法

**这个模块不含任何业务知识。** 它只认一种形状：

    field op value          （可用 AND 连接多段）

`field` 是产出里的字段名，`op` 是比较符（>= <= == != > <），`value` 是字面量。

## 为什么它住在 core 而不是 runtime

因为**它是协议，不是执行**：声明里写的例外条件，runtime 要按同一套解析去读值，
装配期也要按同一套解析去验「这条规则真的能触发」。

两处各写一份 = 两份事实 = 漂。
以前就踩过：守卫原来用 `re.split(r"(>=|<=|==|!=|>|<)", cond, maxsplit=1)` 取
**位置最靠前**的比较符，而执行侧按 `(">=", "<=", "==", "!=", ">", "<")` 的
**优先级顺序**找第一个出现的。条件里有两个比较符时两边选到**不同的字段**——
守卫验的是它自己那套解析，于是放行了一条实际永远不触发的规则。

已犯过：`"gap_count > 0 and next_requirement_seed == ''"`
守卫解析出 `gap_count`（存在）→ 放行；
实际解析先撞上 `==`，字段变成
`"gap_count > 0 and next_requirement_seed"` → 读不到 → 永不触发。

所以：**一处定义，多处引用**。`runtime/acceptance.py` 从这里取，
`core/scaffold.py`（装配器）也从这里取。

## 为什么 core 不该反向依赖 runtime

`core/` 是底层（声明与协议的形状），`runtime/` 是执行层。
装配器在 core 里，它要验「每条例外条件都真的能触发」——
那就得用同一套解析，而这套解析若留在 runtime，core 就得 import runtime，
底层反过来依赖上层。**所以语法归 core，执行归 runtime。**
"""

from __future__ import annotations

import re


#: 条件里的比较符。**顺序有意义**：
#: 解析时按这个顺序找第一个出现的，所以 `>=` 必须排在 `>` 前面，
#: `<=` 排在 `<` 前面——否则 `a >= 1` 会被切成 `a ` 和 `= 1`。
COMPARISON_OPS: tuple[str, ...] = (">=", "<=", "==", "!=", ">", "<")

#: 找比较符的正则。**执行侧求值也用这一个**——
#: 装配期验「这条规则能触发」和运行期真的去触发，必须是同一套切法。
COMPARISON_PATTERN = re.compile(r"(>=|<=|==|!=|>|<)")

#: 切 AND 的正则。**要求两侧有空白**——
#: 否则 `brand` 里的 `and` 会被当成连接词。
_AND_SPLIT = re.compile(r"\s+AND\s+", flags=re.IGNORECASE)


def condition_parts(condition: str) -> list[str]:
    """把一个 condition 按 AND 切成若干段

    AND 大小写都认：写成小写 `and` 是很自然的手滑，而它的后果是
    整条规则静默失效——**不报错、不提示**，比抛异常更难发现。

    切分要求 AND 两侧有空白，避免把字段名里的字母误当连接词
    （`brand` 里也有 `and`）。
    """
    return [
        part.strip()
        for part in _AND_SPLIT.split(condition.strip())
        if part.strip()
    ]


def condition_fields(condition: str) -> list[str]:
    """这个条件实际会去读哪些字段

    **必须和执行侧的解析用同一套。**
    守卫用被验者自己的解析，才能保证「验的就是那个」。
    """
    fields: list[str] = []
    for part in condition_parts(condition):
        match = COMPARISON_PATTERN.search(part)
        if match is None:
            continue  # 裸标识符：不读任何字段
        fields.append(part[: match.start()].strip())
    return fields


def condition_comparisons(part: str) -> list[str]:
    """一段条件里出现了哪几个比较符

    **必须正好一个**。两个比较符多半是漏了 AND：
    `a > 1 and b == 2` 会被切成两段（各一个），而 `a > 1 b == 2`
    是一段两个——执行时在比较时抛 TypeError，把整次 acceptance 评估掀翻。
    """
    return COMPARISON_PATTERN.findall(part)
