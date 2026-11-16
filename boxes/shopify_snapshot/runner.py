"""boxes.shopify_snapshot.runner —— 店铺快照盒的三跳实现

op 链：
  1. `snapshot_intake`    认下这一轮的口径
  2. `snapshot_query`     真去 Shopify 取数（只读）
  3. `snapshot_packager`  算合格访问 + 装出带状态的快照

## 取数失败分两种，处理方式不同

| 失败 | 处理 |
|---|---|
| 总量 / 落地页取不到 | **中止**——本轮没有事实，硬出一个快照就是编一个 |
| 来源 / 搜索词取不到 | **继续**，标 `unverifiable` + 原因 |

「继续」的那部分要在产出里留痕：档案里出现「来源维度这次没取到」，
比它悄悄消失强——消失的维度会被下一个人当成「没有来源」。

## 合格访问**只算一次**

`qualified_sessions` 是从落地页分组行里**一次算出来的**，
不是「查一次总数、再查一次排除后的数、相减」。
同一个量在产物里出现两次，两处必须来自同一次计算——
两次查询之间店铺还在接单，相减出来的数没有意义。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from runtime.runner import HandlerRunner

from boxes.shopify_snapshot.shopifyql_client import (
    ShopifyQLClient,
    ShopifyQLError,
    ShopifyQLPermissionError,
    ShopifyQLResult,
    ShopifyQLTooLargeError,
)
from fulfillment_scopes.shopify.vocabulary import (
    EXCLUDED_ENTRY_PATH_PREFIXES,
    METRIC_STATUS,
    QUALIFIED_SESSION_RULE,
)

#: 落地页分组取多少行（只取流量前 N 页——尾部落地页对结论没影响）
TOP_LANDING_PAGES = 25
TOP_SOURCES = 25
TOP_SEARCH_TERMS = 25

#: 来源 / 搜索词这两条查询取不到时**不算中止**（辅助维度）
OPTIONAL_QUERIES = ("sources", "terms")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_client(store_domain: str) -> ShopifyQLClient:
    """构造只读客户端

    凭证从环境变量读，**不进投料**——
    投料里出现 token，它就会一路进产出、进归档、进日志。
    测试里替换这个函数就能塞假客户端。
    """
    return ShopifyQLClient.from_env(store_domain)


def _day(value: str) -> str:
    """ISO 时间 → `YYYY-MM-DD`（ShopifyQL 的 SINCE/UNTIL 要日期）"""
    return str(value or "")[:10]


def _statement_totals(start: str, end: str) -> str:
    return (
        "FROM sessions\n"
        "  SHOW sessions, online_store_visitors, pageviews,\n"
        "    sessions_with_cart_additions, sessions_that_reached_checkout,\n"
        "    sessions_that_completed_checkout, conversion_rate,\n"
        "    products_viewed, collections_viewed, searches_submitted\n"
        "  WHERE human_or_bot_session = 'human'\n"
        f"  SINCE {_day(start)} UNTIL {_day(end)}"
    )


def _statement_product_views(start: str, end: str) -> str:
    """**看了商品页的 session 有多少**

    四段断点的第四段（no_product_view）就靠它。
    `products_viewed` 是「看了几个不同商品」，不是 session 数——
    要数 session，得按 `session_viewed_product` 这个布尔维度分组。
    """
    return (
        "FROM sessions\n"
        "  SHOW sessions\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  GROUP BY session_viewed_product\n"
        f"  SINCE {_day(start)} UNTIL {_day(end)}"
    )


def _statement_landing(start: str, end: str) -> str:
    return (
        "FROM sessions\n"
        "  SHOW sessions, sessions_with_cart_additions,\n"
        "    sessions_that_reached_checkout, sessions_that_completed_checkout\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  GROUP BY landing_page_path\n"
        f"  SINCE {_day(start)} UNTIL {_day(end)}\n"
        "  ORDER BY sessions DESC"
    )


def _statement_sources(start: str, end: str) -> str:
    return (
        "FROM sessions\n"
        "  SHOW sessions, sessions_that_completed_checkout\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  GROUP BY referrer_source, agentic_referring_channel\n"
        f"  SINCE {_day(start)} UNTIL {_day(end)}\n"
        "  ORDER BY sessions DESC"
    )


def _statement_terms(start: str, end: str) -> str:
    return (
        "FROM sessions\n"
        "  SHOW sessions, sessions_that_completed_checkout\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  GROUP BY referrer_terms\n"
        f"  SINCE {_day(start)} UNTIL {_day(end)}\n"
        "  ORDER BY sessions DESC"
    )


# ============================================================
# op 1：认下这一轮的口径
# ============================================================


def snapshot_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：认下这一轮的口径（窗口 + 合格访问规则）"""
    round_id = str(input_data.get("round_id") or "").strip()
    if not round_id:
        raise ValueError(
            "投料里没有 round_id——四只盒子靠它认出「同一轮」，"
            "没有它这次快照归不到任何一轮经营"
        )

    store_domain = str(input_data.get("store_domain") or "").strip()
    if not store_domain:
        raise ValueError("投料里没有 store_domain——不知道去哪家店取数")

    window_start = str(input_data.get("window_start") or "").strip()
    window_end = str(input_data.get("window_end") or "").strip()
    if not window_start or not window_end:
        raise ValueError(
            "投料里缺统计窗口——窗口必须写清，"
            "「过去 30 天」这种说法每次跑的边界都不一样"
        )
    if window_start > window_end:
        raise ValueError(
            f"窗口反了：start={window_start} > end={window_end}"
        )

    return {
        "round_id": round_id,
        "store_domain": store_domain,
        "window_start": window_start,
        "window_end": window_end,
        "qualified_session_rule": QUALIFIED_SESSION_RULE,
        "excluded_entry_paths": list(EXCLUDED_ENTRY_PATH_PREFIXES),
    }


# ============================================================
# op 2：真去取数
# ============================================================


def snapshot_query_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：取数（只读）

    **取不到就不产出量**——不是产出 0。
    总量或落地页取不到 → 这一跳标记 `fetch_ok = False`，
    原因写进 `unverified`，op 3 产出一份**什么量都没有**的快照：
    判据因此全红，整轮被拒，**不进档案**。
    """
    start = input_data["window_start"]
    end = input_data["window_end"]

    def failed(reason: str) -> dict[str, Any]:
        return {
            "round_id": input_data["round_id"],
            "source": "",
            "api_version": "",
            "retrieved_at": _now(),
            "totals": None,
            "landing_rows": [],
            "view_rows": [],
            "warnings": [],
            "pages_fetched": 0,
            "source_rows": [],
            "term_rows": [],
            "unverified": [reason],
            "fetch_ok": False,
        }

    try:
        client = _build_client(input_data["store_domain"])
    except ShopifyQLPermissionError as exc:
        # **凭证没配**——这条腿还没在真店上跑过。
        # 产出里不写任何量（不写 0），判据因此全红 → 整轮被拒 → 不进档案。
        return failed(str(exc))

    try:
        totals = client.query(_statement_totals(start, end))
        landing = client.query(_statement_landing(start, end))
        product_views = client.query(_statement_product_views(start, end))
    except ShopifyQLPermissionError as exc:
        return failed(f"权限不够：{exc}")
    except ShopifyQLTooLargeError as exc:
        return failed(f"响应过大：{exc}")
    except ShopifyQLError as exc:
        return failed(f"取数失败：{exc}")

    warnings: list[str] = []
    unverified: list[str] = []

    sources: Optional[ShopifyQLResult] = None
    terms: Optional[ShopifyQLResult] = None
    for name, statement in (
        ("sources", _statement_sources(start, end)),
        ("terms", _statement_terms(start, end)),
    ):
        try:
            result = client.query(statement)
        except ShopifyQLPermissionError:
            # 权限问题不是「这条查询取不到」，是整个盒子跑不了——**中止**
            raise
        except (ShopifyQLError, ShopifyQLTooLargeError) as exc:
            unverified.append(f"{name}：{exc}")
            continue
        warnings.extend(result.warnings)
        if name == "sources":
            sources = result
        else:
            terms = result

    total_row = dict(totals.rows[0]) if totals.rows else {}
    landing_rows = [dict(r) for r in landing.rows]
    view_rows = [dict(r) for r in product_views.rows]
    source_rows = [dict(r) for r in sources.rows] if sources else []
    term_rows = [dict(r) for r in terms.rows] if terms else []

    return {
        "round_id": input_data["round_id"],
        "source": totals.source,
        "api_version": totals.api_version,
        "retrieved_at": totals.retrieved_at or _now(),
        "totals": {
            "sessions": _int(total_row.get("sessions")),
            "online_store_visitors": _int(total_row.get("online_store_visitors")),
            "pageviews": _int(total_row.get("pageviews")),
            "sessions_with_cart_additions": _int(
                total_row.get("sessions_with_cart_additions")
            ),
            "sessions_that_reached_checkout": _int(
                total_row.get("sessions_that_reached_checkout")
            ),
            "sessions_that_completed_checkout": _int(
                total_row.get("sessions_that_completed_checkout")
            ),
            "conversion_rate": _float(total_row.get("conversion_rate")),
            "products_viewed": _int(total_row.get("products_viewed")),
            "collections_viewed": _int(total_row.get("collections_viewed")),
            "searches_submitted": _int(total_row.get("searches_submitted")),
        },
        "landing_rows": landing_rows,
        "view_rows": view_rows,
        "warnings": warnings,
        "pages_fetched": totals.pages_fetched,
        "source_rows": source_rows,
        "term_rows": term_rows,
        "unverified": unverified,
        "fetch_ok": True,
    }


def _run_required(
    client: ShopifyQLClient, statement: str, label: str
) -> ShopifyQLResult:
    """必须取到的查询——取不到就中止，不硬出一个空快照"""
    try:
        return client.query(statement)
    except (ShopifyQLPermissionError, ShopifyQLTooLargeError):
        raise
    except ShopifyQLError as exc:
        raise ValueError(
            f"{label} 这条查询取不到：{exc}——本轮没有事实，"
            "硬出一个空快照等于编一个"
        ) from exc


# ============================================================
# op 3：算数 + 装产出
# ============================================================


def snapshot_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：算合格访问（**一次**）并装出带状态的快照

    **取不到的时候，这里不产出任何量。**
    产出里那些量字段会**缺席**（不是 0）→ 判据全红 → 整轮被拒 →
    不进档案。这就是「取不到不写 0」的落地形态。
    """
    if not input_data.get("fetch_ok"):
        return _empty_snapshot(input_data)

    totals = input_data["totals"]
    landing_rows = input_data["landing_rows"]

    excluded_prefixes = tuple(EXCLUDED_ENTRY_PATH_PREFIXES)
    qualified = 0
    landing_sum = 0
    top_landing: list[dict[str, Any]] = []
    for row in landing_rows:
        path = str(row.get("landing_page_path") or "")
        sessions = _int(row.get("sessions"))
        landing_sum += sessions
        if not path.startswith(excluded_prefixes):
            qualified += sessions
        if len(top_landing) < TOP_LANDING_PAGES:
            top_landing.append({
                "landing_page_path": path,
                "sessions": sessions,
                "sessions_that_completed_checkout": _int(
                    row.get("sessions_that_completed_checkout")
                ),
            })

    total_sessions = _int(totals.get("sessions"))

    # **排除数按分组合计算**，不是「总量 - 合格数」——
    # 总量里可能有分组结果没覆盖的 session（没有落地页的），
    # 那个差值不是「被排除的」，是「没归到任何落地页的」。
    excluded_sessions = max(0, landing_sum - qualified)

    # **口径自洽是「事实」，不是「判据」**：落地页分组加起来未必等于总量
    # （有些 session 没有落地页，Shopify 就不会单独列出来）。
    # 不一致要写进产出（让读的人知道），但**不能拿它当验收闸**——
    # 拿它当闸的话，盒子永远交不了货，而它其实只说明了一种口径差异。
    consistent = landing_sum == total_sessions

    source = str(input_data.get("source") or "")
    retrieved_at = str(input_data.get("retrieved_at") or _now())

    metrics: list[dict[str, Any]] = []
    unverified = list(input_data.get("unverified") or [])

    def add_metric(name: str, value: Any, note: str = "") -> None:
        # **值是 None = 这次没取到，不是观测值。**
        # 标成 observed 就是在说谎——下游（模型、诊断盒）会拿它当事实用。
        # 规矩跟「取不到不写 0」是同一条：没测到的量，**连状态都不能伪装成测到了**。
        if value is None:
            metrics.append({
                "name": name,
                "value": None,
                "status": "unverifiable",
                "source": source,
                "retrieved_at": retrieved_at,
                "note": note or "这次没取到这个量（查询没返回它）",
            })
            return
        metrics.append({
            "name": name,
            "value": value,
            "status": "observed",
            "source": source,
            "retrieved_at": retrieved_at,
            "note": note,
        })

    # 第四段断点（no_product_view）的依据：
    # 看了商品页的 session vs 没看商品页的 session。
    # 按 `session_viewed_product` 分组数出来——**products_viewed 是「几个不同商品」，
    # 不是 session 数**，拿它当这一段的依据会算错。
    with_product_view = 0
    without_product_view = 0
    for row in input_data.get("view_rows") or []:
        sessions = _int(row.get("sessions"))
        flag = row.get("session_viewed_product")
        if flag in (True, "true", "True", 1, "1"):
            with_product_view += sessions
        else:
            without_product_view += sessions

    add_metric("total_sessions", total_sessions)
    add_metric("qualified_sessions", qualified)
    add_metric("excluded_entry_sessions", excluded_sessions)
    add_metric("online_store_visitors", totals.get("online_store_visitors"))
    add_metric("pageviews", totals.get("pageviews"))
    add_metric("sessions_with_cart_additions", totals.get("sessions_with_cart_additions"))
    add_metric("sessions_that_reached_checkout", totals.get("sessions_that_reached_checkout"))
    add_metric("sessions_that_completed_checkout", totals.get("sessions_that_completed_checkout"))
    add_metric("conversion_rate", totals.get("conversion_rate"))
    add_metric("sessions_with_product_view", with_product_view)
    add_metric("sessions_without_product_view", without_product_view)
    add_metric("products_viewed", totals.get("products_viewed"))
    add_metric("collections_viewed", totals.get("collections_viewed"))
    add_metric("searches_submitted", totals.get("searches_submitted"))

    if not consistent:
        # **只写进未知项，不塞进 metrics 数组**——
        # metrics 里的 value 是 number，标志位是 boolean，
        # 混进去会让形状闸当场拦下整份产出（实测踩过）。
        unverified.append(
            f"落地页分组合计 {landing_sum} ≠ 总量 {total_sessions}"
            f"（差 {total_sessions - landing_sum}）"
            "——这些 session 的首个落地页不在分组结果里，"
            "合格访问按分组结果算，**不代表总量里没有被算到的部分**"
        )

    status_complete = _metrics_are_honest(metrics, unverified)

    return {
        "round_id": input_data["round_id"],
        "observed_at": retrieved_at,
        "window_start": input_data.get("window_start", ""),
        "window_end": input_data.get("window_end", ""),
        "store_domain": input_data.get("store_domain", ""),
        "source": source,
        "api_version": str(input_data.get("api_version") or ""),
        "total_sessions": total_sessions,
        "qualified_sessions": qualified,
        "excluded_entry_sessions": excluded_sessions,
        "online_store_visitors": totals.get("online_store_visitors"),
        "pageviews": totals.get("pageviews"),
        "sessions_with_cart_additions": totals.get("sessions_with_cart_additions"),
        "sessions_that_reached_checkout": totals.get("sessions_that_reached_checkout"),
        "sessions_that_completed_checkout": totals.get("sessions_that_completed_checkout"),
        "conversion_rate": totals.get("conversion_rate"),
        "qualified_session_rule": QUALIFIED_SESSION_RULE,
        "excluded_entry_paths": list(EXCLUDED_ENTRY_PATH_PREFIXES),
        "metrics": metrics,
        "unverified": unverified,
        "data_present": True,
        "metric_status_complete": status_complete,
        "source_present": bool(source),
        "session_count_consistent": consistent,
        "top_landing_pages": top_landing,
        "top_sources": [
            {
                "referrer_source": str(r.get("referrer_source") or ""),
                "agentic_referring_channel": str(r.get("agentic_referring_channel") or ""),
                "sessions": _int(r.get("sessions")),
                "sessions_that_completed_checkout": _int(
                    r.get("sessions_that_completed_checkout")
                ),
            }
            for r in list(input_data.get("source_rows") or [])[:TOP_SOURCES]
        ],
        "top_search_terms": [
            {
                "term": str(r.get("referrer_terms") or ""),
                "sessions": _int(r.get("sessions")),
            }
            for r in list(input_data.get("term_rows") or [])[:TOP_SEARCH_TERMS]
            if str(r.get("referrer_terms") or "").strip()
        ],
    }


def _empty_snapshot(input_data: dict[str, Any]) -> dict[str, Any]:
    """**什么量都没取到**时的那份快照

    刻意不写 0：**0 是观测值，「没取到」不是**。
    量字段在这里**缺席** → 判据读不到值 → 全红 → 整轮被拒 → 不进档案。
    """
    reason = "; ".join(input_data.get("unverified") or []) or "这次什么都没取到"
    return {
        "round_id": input_data["round_id"],
        "observed_at": input_data.get("retrieved_at") or _now(),
        "window_start": input_data.get("window_start", ""),
        "window_end": input_data.get("window_end", ""),
        "store_domain": input_data.get("store_domain", ""),
        "api_version": str(input_data.get("api_version") or ""),
        "qualified_session_rule": QUALIFIED_SESSION_RULE,
        "excluded_entry_paths": list(EXCLUDED_ENTRY_PATH_PREFIXES),
        "metrics": [],
        "unverified": [reason],
        "data_present": False,
        "metric_status_complete": False,
        "source_present": False,
        "top_landing_pages": [],
        "top_sources": [],
        "top_search_terms": [],
    }


def _metrics_are_honest(metrics: list[dict[str, Any]], unverified: list[str]) -> bool:
    """每条指标都带合法状态吗

    observed 必须有 source；unverifiable 必须有 note。
    **这条判断由取数盒自己算，但档案盒会自己再算一遍**——
    盒子说「我过了」不算数，有人得回头核对它说的对不对。
    """
    for metric in metrics:
        status = metric.get("status")
        if status not in METRIC_STATUS:
            return False
        if status == "observed" and not str(metric.get("source") or "").strip():
            return False
        if status == "unverifiable" and not str(metric.get("note") or "").strip():
            return False
    return True


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


SHOPIFY_SNAPSHOT_HANDLERS: dict[str, Any] = {
    "snapshot_intake": snapshot_intake_handler,
    "snapshot_query": snapshot_query_handler,
    "snapshot_packager": snapshot_packager_handler,
}

RUNNER = HandlerRunner(SHOPIFY_SNAPSHOT_HANDLERS, owner="shopify_snapshot_box")
