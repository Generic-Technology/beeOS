"""boxes.shopify_market_signal.runner —— 市场信号盒的三跳实现

op 链：
  1. `market_signal_intake`   认下这一轮要找什么
  2. `market_signal_work`     各平台取回信号 → **带个人信息的整条丢掉**
  3. `market_signal_packager` 装成需求主题清单

## 它是只读盒

**它不写任何东西**——不发消息、不建受众、不改页面。
产出只是一份「外面在找什么」的清单，供诊断盒参考。

## 个人信息：丢掉，不是洗白

带邮箱/手机号的候选**整条丢掉**并计数。
理由见 `pii.py`：洗过的那条已经不像公开讨论了，它更像一条线索——
留着它，下游就会开始想「怎么联系这个人」。

## 一个平台挂了不拖垮整轮

取不到 → 那个平台 `unverifiable` + 原因，其余平台照常。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from runtime.runner import HandlerRunner

from boxes.shopify_market_signal import clients as clients_mod
from boxes.shopify_market_signal import pii
from fulfillment_scopes.shopify.market_signal import (
    PLATFORM_CREDENTIALS,
    PLATFORMS,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _build_clients(
    platforms: list[str], gsc_csv_path: str
) -> dict[str, Any]:
    """按平台造客户端（**凭证只从环境变量读**）"""
    out: dict[str, Any] = {}
    if "gsc" in platforms:
        out["gsc"] = clients_mod.GscClient(
            access_token=_env("GSC_ACCESS_TOKEN"),
            site_url=_env("GSC_SITE_URL"),
            csv_path=gsc_csv_path,
        )
    if "reddit" in platforms:
        out["reddit"] = clients_mod.RedditClient(
            client_id=_env("REDDIT_CLIENT_ID"),
            client_secret=_env("REDDIT_CLIENT_SECRET"),
        )
    if "etsy" in platforms:
        out["etsy"] = clients_mod.EtsyClient(
            api_key=_env("ETSY_API_KEY"),
            api_secret=_env("ETSY_API_SECRET"),
        )
    if "pinterest" in platforms:
        out["pinterest"] = clients_mod.PinterestClient(
            access_token=_env("PINTEREST_ACCESS_TOKEN"),
        )
    return out


# ============================================================
# op 1：认下这一轮要找什么
# ============================================================


def market_signal_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：认下这一轮要找什么需求"""
    round_id = str(input_data.get("round_id") or "").strip()
    if not round_id:
        raise ValueError(
            "投料里没有 round_id——市场信号要跟同一轮经营对上，"
            "否则它不知道自己在为什么服务"
        )

    keywords = [
        str(k).strip() for k in (input_data.get("keywords") or []) if str(k).strip()
    ]
    if not keywords:
        raise ValueError(
            "投料里没有 keywords——没有要找的东西，这一跳就没有意义"
        )

    platforms = [
        str(p).strip() for p in (input_data.get("platforms") or []) if str(p).strip()
    ] or list(PLATFORMS)
    unknown = [p for p in platforms if p not in PLATFORMS]
    if unknown:
        raise ValueError(
            f"不认识的平台：{unknown}——认得的是 {PLATFORMS}"
        )

    window_days = int(input_data.get("window_days") or 28)
    if window_days <= 0:
        raise ValueError(f"window_days={window_days} 不合理")

    return {
        "round_id": round_id,
        "keywords": keywords[:10],
        "platforms": platforms,
        "window_days": window_days,
        "gsc_csv_path": str(input_data.get("gsc_csv_path") or "").strip(),
    }


# ============================================================
# op 2：各平台取回 → 丢带个人信息的
# ============================================================


def market_signal_work_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：各平台取回信号，并**丢掉带个人信息的**"""
    plan = input_data
    keywords = list(plan.get("keywords") or [])
    platforms = list(plan.get("platforms") or [])
    window_days = int(plan.get("window_days") or 28)
    csv_path = str(plan.get("gsc_csv_path") or "")

    built = _build_clients(platforms, csv_path)

    signals: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for platform in platforms:
        client = built.get(platform)
        if client is None:
            statuses.append({
                "platform": platform, "status": "unverifiable",
                "detail": "这个平台没有对应实现", "signal_count": 0,
            })
            continue
        result = client.fetch(keywords, window_days)
        kept, removed = pii.filter_signals(result.signals)
        signals.extend(kept)
        dropped.extend(removed)
        statuses.append({
            "platform": platform,
            "status": result.status.status,
            "detail": result.status.detail,
            "signal_count": len(kept),
        })

    return {
        "round_id": plan["round_id"],
        "signals": signals,
        "platform_status": statuses,
        "pii_removed": dropped,
    }


# ============================================================
# op 3：装成产出
# ============================================================


def market_signal_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：装成需求主题清单

    **最后一道路闸**：把整份产出再扫一遍个人信息。
    上一步丢过了，但这里再扫一次——**产出里绝不该有一个邮箱**。
    """
    signals = list(input_data.get("signals") or [])
    statuses = list(input_data.get("platform_status") or [])
    dropped = list(input_data.get("pii_removed") or [])

    # 兜底再洗一遍：万一某条绕过了过滤
    cleaned: list[dict[str, Any]] = []
    remaining = 0
    removed_here = 0
    for signal in signals:
        item = dict(signal)
        for field in ("topic", "snippet"):
            text = str(item.get(field) or "")
            if not text:
                continue
            scrubbed, hits = pii.scrub(text)
            item[field] = scrubbed
            removed_here += len(hits)
        # **洗完之后还剩几个**——这才是「产出里有没有个人信息」
        joined = " ".join(
            str(item.get(key) or "")
            for key in ("topic", "snippet", "source_url")
        )
        remaining += len(pii.find_personal_info(joined))
        cleaned.append(item)

    # 没出处的信号不算数（它可能是编的）
    with_source = [
        s for s in cleaned if str(s.get("source_url") or "").strip()
    ]
    # **一条都没有也算不通过**——「什么都没取到」不是合格的产出。
    # （空集上「每条都有出处」在数学上恒真，但业务上它意味着这轮白跑了。）
    source_present = bool(with_source) and len(with_source) == len(cleaned)

    # 每个平台都交代了取没取到，**且至少有一个真的取到了**
    status_complete = (
        bool(statuses)
        and all(
            str(s.get("status") or "") in ("observed", "unverifiable")
            for s in statuses
        )
        and any(str(s.get("status") or "") == "observed" for s in statuses)
    )

    return {
        "round_id": input_data["round_id"],
        "observed_at": _now(),
        "signals": with_source,
        "platform_status": statuses,
        "pii_removed_count": len(dropped) + removed_here,
        "pii_remaining_count": remaining,
        # **「干净」= 有信号 且 没有个人信息**——
        # 一条都没取到时，「没有个人信息」是空集上的真话，
        # 但这一轮其实白跑了——所以它判红。
        "pii_clean": bool(cleaned) and remaining == 0,
        "signal_status_complete": status_complete,
        "source_present": source_present,
    }


SHOPIFY_MARKET_SIGNAL_HANDLERS: dict[str, Any] = {
    "market_signal_intake": market_signal_intake_handler,
    "market_signal_work": market_signal_work_handler,
    "market_signal_packager": market_signal_packager_handler,
}

RUNNER = HandlerRunner(
    SHOPIFY_MARKET_SIGNAL_HANDLERS, owner="shopify_market_signal_box"
)
