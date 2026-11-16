"""boxes.shopify_market_signal.pii —— 个人信息扫描与剔除

**这个模块是整只盒子的安全底线。**

市场信号盒去外部平台看公开讨论。公开讨论里**一定**混着这些东西：

- 想买的人留了邮箱（想让你联系他）
- 卖东西的人留了邮箱（想在你的帖子里打广告）
- 有人写了手机号

**带联系方式的帖子不是需求信号，是有人在私下交易。**
所以规则是：**整条丢掉**，而不是「洗一洗留下来」——
洗过的那条已经不像公开讨论了，它更像一条线索。

## 为什么不是「脱敏后保留」

脱敏后那条帖子看起来还是「有人想要 X」，但它的**语义**已经变了：
它是「某个人想要 X」，不是「外面有一类人在想 X」。
留着它，下游就会开始想「怎么联系这个人」——那正是我们要避免的。

所以：**带个人信息的候选一律丢弃，并计数**。
"""

from __future__ import annotations

import re
from typing import Any

#: 邮箱（保守：要求有 TLD）
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

#: 手机号：要求至少 9 位数字（避免把「24.5 inches」「12 designs」误判成电话）
_PHONE_CANDIDATE = re.compile(r"(?:\+?\d[\d\s().\-]{7,}\d)")
_DIGITS = re.compile(r"\d")


def _looks_like_phone(text: str) -> bool:
    """候选串里的数字够不够像一部电话

    「55% wool」是 4 位、「24.5 inches」是 3 位 → 不算；
    「+1 (415) 555-0134」是 11 位 → 算。
    """
    return len(_DIGITS.findall(text)) >= 9


def find_personal_info(text: str) -> list[str]:
    """找出文本里的个人信息片段（邮箱 / 手机号）

    返回**匹配的片段**（不是位置）——调用方要拿它做剔除决策。
    """
    if not text:
        return []
    hits = [m.group(0) for m in EMAIL_RE.finditer(text)]
    for match in _PHONE_CANDIDATE.finditer(text):
        candidate = match.group(0)
        if _looks_like_phone(candidate):
            hits.append(candidate.strip())
    return hits


def contains_personal_info(text: str) -> bool:
    """这条文本里有没有个人信息"""
    return bool(find_personal_info(text))


def scrub(text: str) -> tuple[str, list[str]]:
    """洗掉个人信息（**兜底用**）

    正常路径是整条丢弃（见 `filter_signals`）；这个函数是最后一道：
    万一某条信号绕过了过滤，产出里也不该留着一个邮箱。

    返回 `(洗过的文本, 被移除的片段)`。
    """
    if not text:
        return "", []
    removed: list[str] = []
    out = EMAIL_RE.sub("[已移除]", text)
    removed.extend(m.group(0) for m in EMAIL_RE.finditer(text))

    def _scrub_phone(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if not _looks_like_phone(candidate):
            return candidate
        removed.append(candidate.strip())
        return "[已移除]"

    out = _PHONE_CANDIDATE.sub(_scrub_phone, out)
    return out, removed


def filter_signals(
    signals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """**带个人信息的信号整条丢掉**

    返回 `(留下的, 丢掉的)`。丢掉的那些带 `pii_hits` 说明为什么丢。
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for signal in signals:
        text = " ".join(
            str(signal.get(key) or "")
            for key in ("topic", "snippet", "source_url")
        )
        hits = find_personal_info(text)
        if hits:
            dropped.append({**signal, "pii_hits": hits})
            continue
        kept.append(signal)
    return kept, dropped
