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

#: **完整漏斗保留多少个落地页**——
#: 比 TOP_LANDING_PAGES 多，因为复测要按页比，
#: 而「改过的那一页」未必在流量前 N 名里。
LANDING_FUNNEL_PAGES = 100

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


def _statement_pools(start: str, end: str) -> str:
    """来源 × AI 渠道 × 落地页 的交叉

    **「哪个流量池在漏水」是算得出来的**：有 session、没成交的池子
    就是漏水的池子。分开看来源和落地页看不出这个——
    「direct 108 个 session」和「这个页面 13 个 session」两张表，
    交叉起来才知道「这个页的 direct 流量转化 30%，search 流量转化 0%」。
    """
    return (
        "FROM sessions\n"
        "  SHOW sessions, sessions_that_completed_checkout\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  GROUP BY referrer_source, agentic_referring_channel, landing_page_path\n"
        f"  SINCE {_day(start)} UNTIL {_day(end)}\n"
        "  ORDER BY sessions DESC"
    )


def _statement_daily(start: str, end: str) -> str:
    """日级序列——改完第三天就能看方向，不用等满一个月"""
    return (
        "FROM sessions\n"
        "  SHOW sessions, sessions_that_completed_checkout\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  TIMESERIES day\n"
        f"  SINCE {_day(start)} UNTIL {_day(end)}\n"
        "  ORDER BY day ASC"
    )


def _statement_site_searches(start: str, end: str) -> str:
    """**买家在站内搜什么**

    这是真实买家的需求信号——比任何爬来的线索都强，
    而且不用爬、不用碰个人信息、零合规风险。
    """
    return (
        "FROM searches\n"
        "  SHOW searches, search_query\n"
        "  GROUP BY search_query\n"
        f"  SINCE {_day(start)} UNTIL {_day(end)}\n"
        "  ORDER BY searches DESC"
    )


def _statement_search_funnel(start: str, end: str) -> str:
    """站内搜索漏斗：搜了 → 点了结果 → 加购 → 成交

    「搜了 39 次，一单没成」和「搜了 39 次，4 单成交」是两个完全不同的问题——
    前者是站内搜索坏了（要找的人找不到），后者是搜索没问题。
    """
    return (
        "FROM search_conversions\n"
        "  SHOW sessions_with_searches, search_sessions_with_clicks,\n"
        "    search_sessions_with_cart_additions,\n"
        "    search_sessions_that_completed_checkout,\n"
        "    search_click_rate, search_added_to_cart_rate, search_conversion_rate\n"
        f"  SINCE {_day(start)} UNTIL {_day(end)}"
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
        "strategy": str(input_data.get("strategy") or ""),
        "market_signal_ref": input_data.get("market_signal_ref"),
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
            "strategy": str(input_data.get("strategy") or ""),
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
            "pool_rows": [],
            "daily_rows": [],
            "site_search_rows": [],
            "search_funnel_row": {},
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
    pools: Optional[ShopifyQLResult] = None
    daily: Optional[ShopifyQLResult] = None
    site_searches: Optional[ShopifyQLResult] = None
    search_funnel: Optional[ShopifyQLResult] = None
    for name, statement in (
        ("sources", _statement_sources(start, end)),
        ("terms", _statement_terms(start, end)),
        ("pools", _statement_pools(start, end)),
        ("daily", _statement_daily(start, end)),
        ("site_searches", _statement_site_searches(start, end)),
        ("search_funnel", _statement_search_funnel(start, end)),
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
        elif name == "terms":
            terms = result
        elif name == "pools":
            pools = result
        elif name == "site_searches":
            site_searches = result
        elif name == "search_funnel":
            search_funnel = result
        else:
            daily = result

    total_row = dict(totals.rows[0]) if totals.rows else {}
    landing_rows = [dict(r) for r in landing.rows]
    view_rows = [dict(r) for r in product_views.rows]
    source_rows = [dict(r) for r in sources.rows] if sources else []
    term_rows = [dict(r) for r in terms.rows] if terms else []

    return {
        "round_id": input_data["round_id"],
        "strategy": str(input_data.get("strategy") or ""),
        "market_signal_ref": input_data.get("market_signal_ref"),
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
        "pool_rows": [dict(r) for r in pools.rows] if pools else [],
        "daily_rows": [dict(r) for r in daily.rows] if daily else [],
        "site_search_rows": [dict(r) for r in site_searches.rows] if site_searches else [],
        "search_funnel_row": dict(search_funnel.rows[0]) if (search_funnel and search_funnel.rows) else {},
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
    funnel = input_data.get("search_funnel_row") or {}
    landing_rows = input_data["landing_rows"]

    excluded_prefixes = tuple(EXCLUDED_ENTRY_PATH_PREFIXES)
    qualified = 0
    landing_sum = 0
    top_landing: list[dict[str, Any]] = []
    # **每个落地页的完整漏斗**（不只是前 N 名）——
    # 复测要按页比，而「改过的那一页」未必在前 N 名里。
    landing_funnel: list[dict[str, Any]] = []
    for row in landing_rows:
        path = str(row.get("landing_page_path") or "")
        sessions = _int(row.get("sessions"))
        landing_sum += sessions
        if not path.startswith(excluded_prefixes):
            qualified += sessions
        if len(landing_funnel) < LANDING_FUNNEL_PAGES:
            landing_funnel.append({
                "landing_page_path": path,
                "sessions": sessions,
                "sessions_with_cart_additions": _int(
                    row.get("sessions_with_cart_additions")
                ),
                "sessions_that_reached_checkout": _int(
                    row.get("sessions_that_reached_checkout")
                ),
                "sessions_that_completed_checkout": _int(
                    row.get("sessions_that_completed_checkout")
                ),
            })
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
        "strategy": str(input_data.get("strategy") or ""),
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
        "landing_funnel": landing_funnel,
        "market_signals": [
            {
                "platform": str(m.get("platform") or ""),
                "topic": str(m.get("topic") or ""),
                "occurrences": m.get("occurrences"),
                "source_url": str(m.get("source_url") or ""),
                "captured_at": str(m.get("captured_at") or ""),
            }
            for m in list(
                ((input_data.get("market_signal_ref") or {}).get("signals") or [])
            )[:40]
        ],
        "site_search_terms": [
            {
                "query": str(r.get("search_query") or ""),
                "searches": _int(r.get("searches")),
            }
            for r in list(input_data.get("site_search_rows") or [])[:40]
            if str(r.get("search_query") or "").strip()
        ],
        "search_funnel": {
            "sessions_with_searches": _int(funnel.get("sessions_with_searches")),
            "search_sessions_with_clicks": _int(funnel.get("search_sessions_with_clicks")),
            "search_sessions_with_cart_additions": _int(
                funnel.get("search_sessions_with_cart_additions")
            ),
            "search_sessions_that_completed_checkout": _int(
                funnel.get("search_sessions_that_completed_checkout")
            ),
            "search_click_rate": _float(funnel.get("search_click_rate")),
            "search_added_to_cart_rate": _float(funnel.get("search_added_to_cart_rate")),
            "search_conversion_rate": _float(funnel.get("search_conversion_rate")),
        } if funnel else {},
        "traffic_pools": [
            {
                "referrer_source": str(r.get("referrer_source") or ""),
                "agentic_referring_channel": str(r.get("agentic_referring_channel") or ""),
                "landing_page_path": str(r.get("landing_page_path") or ""),
                "sessions": _int(r.get("sessions")),
                "sessions_that_completed_checkout": _int(
                    r.get("sessions_that_completed_checkout")
                ),
            }
            for r in list(input_data.get("pool_rows") or [])[:200]
        ],
        "daily_trend": [
            {
                "day": str(r.get("day") or ""),
                "sessions": _int(r.get("sessions")),
                "sessions_that_completed_checkout": _int(
                    r.get("sessions_that_completed_checkout")
                ),
            }
            for r in list(input_data.get("daily_rows") or [])[:120]
        ],
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
        "strategy": str(input_data.get("strategy") or ""),
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
        "landing_funnel": [],
        "market_signals": [
            {
                "platform": str(m.get("platform") or ""),
                "topic": str(m.get("topic") or ""),
                "occurrences": m.get("occurrences"),
                "source_url": str(m.get("source_url") or ""),
                "captured_at": str(m.get("captured_at") or ""),
            }
            for m in list(
                ((input_data.get("market_signal_ref") or {}).get("signals") or [])
            )[:40]
        ],
        "site_search_terms": [
            {
                "query": str(r.get("search_query") or ""),
                "searches": _int(r.get("searches")),
            }
            for r in list(input_data.get("site_search_rows") or [])[:40]
            if str(r.get("search_query") or "").strip()
        ],
        "search_funnel": {
            "sessions_with_searches": _int(funnel.get("sessions_with_searches")),
            "search_sessions_with_clicks": _int(funnel.get("search_sessions_with_clicks")),
            "search_sessions_with_cart_additions": _int(
                funnel.get("search_sessions_with_cart_additions")
            ),
            "search_sessions_that_completed_checkout": _int(
                funnel.get("search_sessions_that_completed_checkout")
            ),
            "search_click_rate": _float(funnel.get("search_click_rate")),
            "search_added_to_cart_rate": _float(funnel.get("search_added_to_cart_rate")),
            "search_conversion_rate": _float(funnel.get("search_conversion_rate")),
        } if funnel else {},
        "traffic_pools": [
            {
                "referrer_source": str(r.get("referrer_source") or ""),
                "agentic_referring_channel": str(r.get("agentic_referring_channel") or ""),
                "landing_page_path": str(r.get("landing_page_path") or ""),
                "sessions": _int(r.get("sessions")),
                "sessions_that_completed_checkout": _int(
                    r.get("sessions_that_completed_checkout")
                ),
            }
            for r in list(input_data.get("pool_rows") or [])[:200]
        ],
        "daily_trend": [
            {
                "day": str(r.get("day") or ""),
                "sessions": _int(r.get("sessions")),
                "sessions_that_completed_checkout": _int(
                    r.get("sessions_that_completed_checkout")
                ),
            }
            for r in list(input_data.get("daily_rows") or [])[:120]
        ],
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
