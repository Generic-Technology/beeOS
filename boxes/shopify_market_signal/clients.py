"""boxes.shopify_market_signal.clients —— 四个平台的只读客户端

**全部走官方 API**，不抓 ToS 禁止的路径；**只取公开内容**。

## 统一的接口

每个客户端都返回 `(signals, status)`：

- `signals`：`[{platform, topic, occurrences, source_url, captured_at, snippet}]`
- `status`：`{platform, status, detail}`，`status` ∈ `observed` / `unverifiable`

**取不到就返回 unverifiable + 原因**，不抛异常——
一个平台挂了不该拖垮整轮（`market_signal.py` 里写了这条）。

## 哪些验过、哪些没验

- **GSC 的 CSV 路径**：夹具测过 ✓，而且 2026-11-18 **在真店上真跑过** ✓
  （读真导出的**中文表头** CSV，取到 25 条需求词）
- **GSC 的 API 路径 / Reddit / Etsy / Pinterest**：**代码按各自官方文档写的，
  但没有真凭证跑过**（凭证在你手上）——
  那些平台跑不到时标 `unverifiable` + 原因，其余平台照常。
"""

from __future__ import annotations

import csv
import io
import json
import os
import urllib.error
import urllib.parse
import zipfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from fulfillment_scopes.shopify.market_signal import (
    GSC_MAX_SIGNALS,
    MAX_SIGNALS_PER_PLATFORM,
    REQUEST_INTERVAL_SECONDS,
)


@dataclass
class PlatformStatus:
    """一个平台这次取没取到"""

    platform: str
    status: str = "observed"      # observed / unverifiable
    detail: str = ""
    signal_count: int = 0


@dataclass
class FetchResult:
    signals: list[dict[str, Any]] = field(default_factory=list)
    status: PlatformStatus = field(default_factory=lambda: PlatformStatus("unknown"))
    #: **页面信号**——站外「我们的哪一页有展示、点了没、排第几」
    #: （只有 GSC 有：Google 告诉我们自己的页面在搜索结果里的样子）
    page_signals: list[dict[str, Any]] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_json(
    url: str,
    *,
    headers: dict[str, str],
    data: Optional[bytes] = None,
    method: str = "GET",
    timeout: float = 30.0,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_text(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float = 30.0,
) -> str:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ============================================================
# Google Search Console —— 我们自己店的搜索词（第一方数据）
# ============================================================


class GscClient:
    """Google Search Console 的搜索分析

    **这是我们自己店的官方数据**——真实买家搜了什么词、点没点。
    比任何站外爬来的线索都强，而且零合规风险。

    三条路：
    - **API**：`GSC_ACCESS_TOKEN` + `GSC_SITE_URL` 在环境变量里 → 自动跑
    - **API（OAuth）**：`GSC_CLIENT_ID` + `GSC_CLIENT_SECRET` +
      `GSC_REFRESH_TOKEN` + `GSC_SITE_URL`
      → 每轮自动换 token 自动查，**不用再手动导出 CSV**
      （`scripts/connect_platform.py --platform gsc --gsc-oauth` 走完整个流程）
    - **CSV / 导出包**：投料一个导出的路径 → 立刻能跑（不用授权）

    **导出包怎么认表**：Search Console 一次导出**好几张表**
    （热门查询 / 排名靠前的网页 / 日期 / 国家地区 / 设备 / 搜索结果呈现），
    让使用者自己挑一张丢进来 = 挑错就白搭——
    所以这里**按表头自己认**：第一列是「热门查询」就是词表，
    是「排名靠前的网页」就是页面表，其余的（日期 / 设备…）忽略。
    """

    API_URL = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
    TOKEN_URL = "https://oauth2.googleapis.com/token"

    #: **GSC 导出的表头有中英文两套**——界面语言变了，列名就变。
    #: 这里按名字**猜列**，两套都认（写死一套 = 换个界面就读不出来）。
    QUERY_COLS = ("top queries", "query", "search query", "search term",
                  "热门查询", "查询", "搜索字词", "搜索词", "热门搜索字词")
    CLICKS_COLS = ("clicks", "点击次数", "点击", "点击数")
    IMPRESSIONS_COLS = ("impressions", "展示", "展示次数", "曝光", "曝光次数")
    POSITION_COLS = ("position", "排名")
    CTR_COLS = ("ctr", "点击率")
    #: **页面表**的列名（中文界面导出的是「排名靠前的网页」）
    PAGE_COLS = ("top pages", "page", "pages", "url", "landing page",
                 "排名靠前的网页", "热门网页", "网页", "页面", "网址")

    def __init__(
        self,
        access_token: str = "",
        site_url: str = "",
        csv_path: str = "",
        refresh_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
        now: Callable[[], str] = _now_iso,
    ) -> None:
        self.access_token = access_token
        self.site_url = site_url
        self.csv_path = csv_path
        # **OAuth 那条路**：配一次，之后每轮自动换 token 自动查
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.opener = opener
        self.now = now
        self._cached_token = ""

    def fetch(self, keywords: list[str], window_days: int) -> FetchResult:
        if self.csv_path:
            return self._from_csv()
        if not self.site_url:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable",
                "没配 GSC_SITE_URL，也没给导出的文件路径",
            ))
        # **OAuth 那条路也算数**：refresh_token 在 → 现场换一张 access_token
        if not (self.access_token or self._has_refresh_token()):
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable",
                "没配 GSC_ACCESS_TOKEN / GSC_REFRESH_TOKEN，也没给导出的文件路径",
            ))
        try:
            return self._from_api(window_days)
        except urllib.error.HTTPError as exc:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable", f"HTTP {exc.code}：{exc.reason}"))
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable", f"取数失败：{exc}"))

    def _has_refresh_token(self) -> bool:
        return bool(self.refresh_token and self.client_id and self.client_secret)

    # ---- CSV / 导出包 ----

    def _from_csv(self) -> FetchResult:
        try:
            tables = _read_tables(self.csv_path)
        except (OSError, zipfile.BadZipFile) as exc:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable", f"导出文件读不了：{exc}"))

        query_rows: Optional[list[list[str]]] = None
        page_rows: Optional[list[list[str]]] = None
        for rows in tables:
            kind = _classify_table(rows)
            if kind == "queries" and query_rows is None:
                query_rows = rows
            elif kind == "pages" and page_rows is None:
                page_rows = rows

        if query_rows is None and page_rows is None:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable",
                "导出的文件里没有认得的表——"
                "要「热门查询」或「排名靠前的网页」那张表"
                "（Search Console 一次导出好几张表）"))

        signals = self._parse_queries(query_rows) if query_rows else []
        page_signals = self._parse_pages(page_rows) if page_rows else []
        if not signals and not page_signals:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable",
                "认得表了，但里面一行数据都没有"))

        detail = f"读到 {len(signals)} 条需求词"
        if page_signals:
            detail += f"、{len(page_signals)} 个页面"
        return FetchResult(
            signals=signals,
            page_signals=page_signals,
            status=PlatformStatus("gsc", "observed", detail, len(signals)),
        )

    def _parse_queries(self, rows: list[list[str]]) -> list[dict[str, Any]]:
        """**热门查询表**——买家搜了什么词"""
        header_idx, header = _find_header_row(rows)
        if not header:
            return []
        cols = {name: idx for idx, name in enumerate(header)}
        q_col = _pick_col(cols, self.QUERY_COLS)
        if q_col is None:
            return []
        c_col = _pick_col(cols, self.CLICKS_COLS)
        i_col = _pick_col(cols, self.IMPRESSIONS_COLS)
        p_col = _pick_col(cols, self.POSITION_COLS)
        ctr_col = _pick_col(cols, self.CTR_COLS)

        captured = self.now()
        signals: list[dict[str, Any]] = []
        for row in rows[header_idx + 1:]:
            if len(row) <= q_col:
                continue
            topic = (row[q_col] or "").strip()
            if not topic or topic.lower() in ("total", "总计", "合计"):
                continue
            clicks = _safe_int(row[c_col]) if c_col is not None and len(row) > c_col else None
            impressions = _safe_int(row[i_col]) if i_col is not None and len(row) > i_col else None
            position = _safe_float(row[p_col]) if p_col is not None and len(row) > p_col else None
            ctr = _safe_ctr(row[ctr_col]) if ctr_col is not None and len(row) > ctr_col else None
            signals.append({
                "platform": "gsc",
                "topic": topic,
                # **需求大小 = 展示数**（有多少人在门外搜这个词），
                # 不是点击数——点击数是**我们接住了多少**，不是需求。
                # 「搜了 59 次、点了 0 次」恰恰是最该看的需求。
                "occurrences": impressions if impressions is not None else clicks,
                "clicks": clicks,
                "position": position,
                "ctr_percent": ctr,
                "source_url": f"gsc://{self.site_url or 'gsc'}/search-analytics/{topic}",
                "captured_at": captured,
                "snippet": (
                    f"clicks={clicks} impressions={impressions}"
                    + (f" ctr={ctr}%" if ctr is not None else "")
                    + (f" pos={position}" if position is not None else "")
                ),
                # **内部排序用**（不进产出形状——产出形状是固定的）
                "_demand": impressions if impressions is not None else (clicks or 0),
                "_clicks": clicks if clicks is not None else 0,
            })

        # **按需求大小排**（展示多的排前面）——
        # GSC 自己导出的顺序是**按点击数**排的，
        # 于是「搜了很多次、一次没点」的那些词全被压在后面看不见，
        # 而那些词恰恰是我们最该去接的。
        signals.sort(key=lambda s: (-int(s["_demand"]), -int(s["_clicks"])))
        signals = signals[:GSC_MAX_SIGNALS]
        for sig in signals:
            sig.pop("_demand", None)
            sig.pop("_clicks", None)
        return signals

    def _parse_pages(self, rows: list[list[str]]) -> list[dict[str, Any]]:
        """**排名靠前的网页表**——我们的哪一页有展示、点了没、排第几

        这是**站外需求落到我们页面上的样子**：
        「有展示、0 点击」= Google 已经把这一页摆给人了，只是没人点进来
        → 标题/描述没接住（药方：把买家搜的词写进去）。

        **没有这张表，选页只能靠词面猜**——
        而词面猜会猜错（实测把 1 session 的月亮灯页选成目标）。
        """
        header_idx, header = _find_header_row(rows)
        if not header:
            return []
        cols = {name: idx for idx, name in enumerate(header)}
        p_col = _pick_col(cols, self.PAGE_COLS)
        if p_col is None:
            return []
        c_col = _pick_col(cols, self.CLICKS_COLS)
        i_col = _pick_col(cols, self.IMPRESSIONS_COLS)
        pos_col = _pick_col(cols, self.POSITION_COLS)
        ctr_col = _pick_col(cols, self.CTR_COLS)

        captured = self.now()
        out: list[dict[str, Any]] = []
        for row in rows[header_idx + 1:]:
            if len(row) <= p_col:
                continue
            url = (row[p_col] or "").strip()
            if not url or url.lower() in ("total", "总计", "合计"):
                continue
            out.append({
                "platform": "gsc",
                "path": _url_to_path(url),
                "url": url,
                "impressions": (
                    _safe_int(row[i_col]) if i_col is not None and len(row) > i_col else None
                ),
                "clicks": (
                    _safe_int(row[c_col]) if c_col is not None and len(row) > c_col else None
                ),
                "position": (
                    _safe_float(row[pos_col]) if pos_col is not None and len(row) > pos_col else None
                ),
                "ctr_percent": (
                    _safe_ctr(row[ctr_col]) if ctr_col is not None and len(row) > ctr_col else None
                ),
                "captured_at": captured,
            })

        # **按展示数排**——展示多的先看
        out.sort(key=lambda s: -(s.get("impressions") or 0))
        return out[:MAX_PAGE_SIGNALS]

    def _token(self) -> str:
        """**拿一张能用的 access token**

        - 已经有 access_token → 直接用；
        - 有 refresh_token → 用它换一张新的（**配一次，之后每轮自动换**）；
        - 都没有 → 空（调用方会说清楚缺哪个）。
        """
        if self._cached_token:
            return self._cached_token
        if self.access_token:
            self._cached_token = self.access_token
            return self._cached_token
        if not self._has_refresh_token():
            return ""
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")
        payload = _http_json(
            self.TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=body,
            method="POST",
        )
        self._cached_token = str(payload.get("access_token") or "")
        return self._cached_token

    # ---- API ----

    def _from_api(self, window_days: int) -> FetchResult:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=window_days)
        site = urllib.parse.quote(self.site_url, safe="")
        token = self._token()
        if not token:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable",
                "有 refresh_token 但换不到 access_token（client_id / client_secret 不对？）"))

        captured = self.now()
        # **两次查询**：
        # ① 按词——「买家搜了什么」（需求大小）
        # ② 按 **词 × 页**——「哪个页面在为什么词排名」
        #    这一条是 CSV 拿不到的（导出没有这个维度），
        #    而它才是「选页不用猜」的关键。
        queries = self._api_query(
            site, token, start, end, ["query"], row_limit=250)
        pairs = self._api_query(
            site, token, start, end, ["query", "page"], row_limit=1000)

        signals: list[dict[str, Any]] = []
        for row in queries:
            topic = str(row.get("query") or "").strip()
            if not topic:
                continue
            clicks = _safe_int(row.get("clicks"))
            impressions = _safe_int(row.get("impressions"))
            signals.append({
                "platform": "gsc",
                "topic": topic,
                "occurrences": impressions if impressions is not None else clicks,
                "clicks": clicks,
                "position": _safe_float(row.get("position")),
                "ctr_percent": _safe_float(row.get("ctr")),
                "source_url": f"gsc://{self.site_url or 'gsc'}/search-analytics/{topic}",
                "captured_at": captured,
                "snippet": (
                    f"clicks={clicks} impressions={impressions}"
                    + (f" ctr={row.get('ctr')}" if row.get("ctr") is not None else "")
                    + (f" pos={row.get('position')}" if row.get("position") is not None else "")
                ),
                "_demand": impressions if impressions is not None else (clicks or 0),
                "_clicks": clicks if clicks is not None else 0,
            })
        signals.sort(key=lambda s: (-int(s["_demand"]), -int(s["_clicks"])))
        signals = signals[:GSC_MAX_SIGNALS]
        for sig in signals:
            sig.pop("_demand", None)
            sig.pop("_clicks", None)

        page_signals = self._page_signals_from_pairs(pairs, captured)
        return FetchResult(
            signals=signals,
            page_signals=page_signals,
            status=PlatformStatus(
                "gsc", "observed",
                f"API 取到 {len(signals)} 条需求词、{len(page_signals)} 个页面",
                len(signals),
            ),
        )

    def _api_query(
        self,
        site: str,
        token: str,
        start: Any,
        end: Any,
        dimensions: list[str],
        row_limit: int,
    ) -> list[dict[str, Any]]:
        body = json.dumps({
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": dimensions,
            "rowLimit": row_limit,
        }).encode("utf-8")
        payload = _http_json(
            self.API_URL.format(site=site),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            data=body,
            method="POST",
        )
        rows = payload.get("rows") or []
        return [dict(r) for r in rows if isinstance(r, dict)]

    def _page_signals_from_pairs(
        self, pairs: list[dict[str, Any]], captured: str
    ) -> list[dict[str, Any]]:
        """**词 × 页**→ 页面信号（每个词 × 每个页面一条）

        **为什么要按词分开**：同一页可能为好几个词排名，
        而「哪个词把它摆在第 15 位」正是我们要写进标题的那句话。
        """
        out: list[dict[str, Any]] = []
        for row in pairs:
            page = str(row.get("page") or "").strip()
            if not page:
                continue
            clicks = _safe_int(row.get("clicks"))
            impressions = _safe_int(row.get("impressions"))
            out.append({
                "platform": "gsc",
                "path": _url_to_path(page),
                "url": page,
                "query": str(row.get("query") or "").strip(),
                "impressions": impressions,
                "clicks": clicks,
                "position": _safe_float(row.get("position")),
                "ctr_percent": _safe_float(row.get("ctr")),
                "captured_at": captured,
            })
        out.sort(key=lambda s: -(s.get("impressions") or 0))
        return out[:MAX_PAGE_SIGNALS]


# ============================================================
# Reddit —— 公开讨论里的「在找什么」
# ============================================================


class RedditClient:
    """Reddit Data API（**官方 API，不爬**）

    `client_credentials` 授权：**不需要用户账号密码** → 只要注册一个 app
    拿到 client_id / client_secret 就行。
    （Reddit 官方：script 类应用 = confidential client，正好用这个授权方式；
    请求要打到 `oauth.reddit.com`，不是 www。）

    我们只看**公开子版**的公开搜索结果，产出只到「需求主题 + 出处」这一层。

    ## User-Agent 必须按 Reddit 的格式写

    Reddit 官方要求：`<platform>:<app ID>:<version> (by /u/<用户名>)`，
    并明确说「默认的 User-Agent（如 Python/urllib）会被**大幅限制**」。
    → 这里默认按格式拼一个，联系方式用 `REDDIT_USER_AGENT` 覆盖。
    """

    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    SEARCH_URL = "https://oauth.reddit.com/r/{sub}/search"

    DEFAULT_SUBS = ["gifts", "petloss", "Christmas", "PersonalizedGifts"]

    #: 按 Reddit 要求的格式拼的默认 User-Agent
    DEFAULT_USER_AGENT = "linux:beeos-market-signal:v0.1 (by /u/giftsjourneyrebirth)"

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        subs: Optional[list[str]] = None,
        user_agent: str = "",
        device_id: str = "",
        opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
        now: Callable[[], str] = _now_iso,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.subs = subs or list(self.DEFAULT_SUBS)
        # **环境变量优先**——联系方式（Reddit 用户名）各人不同
        self.user_agent = (
            user_agent
            or os.environ.get("REDDIT_USER_AGENT", "").strip()
            or self.DEFAULT_USER_AGENT
        )
        # **device_id**（installed app 那条路要用）——它必须**稳定**
        # （重新换 token 时要复用同一个），所以放在环境变量里，不每次现生成。
        self.device_id = device_id or os.environ.get("REDDIT_DEVICE_ID", "").strip()
        self.opener = opener
        self.now = now

    def fetch(self, keywords: list[str], window_days: int) -> FetchResult:
        if not self.client_id:
            return FetchResult(status=PlatformStatus(
                "reddit", "unverifiable",
                "没配 REDDIT_CLIENT_ID —— **Reddit 现在不发自助凭证了**："
                "官方《Responsible Builder Policy》要求先获批"
                "（自助发证 2025-11-11 关闭；官方三条路：Devvit / "
                "人工申请 support ticket / 商业合作）。"
                "拿到凭证后填进 ~/.config/beeos/shopify.env 即可"))
        if not self.client_secret and not self.device_id:
            return FetchResult(status=PlatformStatus(
                "reddit", "unverifiable",
                "既没 secret（script app）也没 device_id（installed app）——"
                "两个都没有就换不了 token"))
        try:
            token = self._token()
        except urllib.error.HTTPError as exc:
            return FetchResult(status=PlatformStatus(
                "reddit", "unverifiable", f"换 token 失败 HTTP {exc.code}"))
        except (urllib.error.URLError, ValueError) as exc:
            return FetchResult(status=PlatformStatus(
                "reddit", "unverifiable", f"连不上 Reddit：{exc}"))

        captured = self.now()
        signals: list[dict[str, Any]] = []
        failures: list[str] = []
        for keyword in keywords:
            for sub in self.subs:
                try:
                    payload = self._search(token, sub, keyword)
                except urllib.error.HTTPError as exc:
                    failures.append(f"r/{sub}「{keyword}」HTTP {exc.code}")
                    continue
                except (urllib.error.URLError, ValueError) as exc:
                    failures.append(f"r/{sub}「{keyword}」{exc}")
                    continue
                for child in (payload.get("data") or {}).get("children") or []:
                    data = child.get("data") or {}
                    title = str(data.get("title") or "").strip()
                    if not title:
                        continue
                    signals.append({
                        "platform": "reddit",
                        "topic": title[:180],
                        "occurrences": int(data.get("num_comments") or 0),
                        "source_url": "https://www.reddit.com" + str(data.get("permalink") or ""),
                        "captured_at": captured,
                        "snippet": str(data.get("selftext") or "")[:280],
                    })
                if len(signals) >= MAX_SIGNALS_PER_PLATFORM:
                    break
            if len(signals) >= MAX_SIGNALS_PER_PLATFORM:
                break

        detail = f"{len(signals)} 条" + (f"；{len(failures)} 个查询失败" if failures else "")
        status = "observed" if signals else ("unverifiable" if failures else "observed")
        return FetchResult(
            signals=signals[:MAX_SIGNALS_PER_PLATFORM],
            status=PlatformStatus("reddit", status, detail, len(signals)),
        )

    def _token(self) -> str:
        """换一张 token（**两条官方支持的路**）

        | app 类型 | 授权方式 | 要什么 |
        |---|---|---|
        | script / web（confidential client） | `client_credentials` | client_id + **secret** |
        | installed（非机密 client） | `https://oauth.reddit.com/grants/installed_client` | client_id + **device_id**（没有 secret） |

        **两条都要用 HTTP Basic Auth 传 client 凭证**；
        installed 那条把 secret 留空即可。

        **为什么两条都留着**：script app 的 secret 在 Reddit 那边
        只显示一次，用户拿不到时整条路就断了——
        而 installed 那条完全不需要 secret。
        """
        import base64

        if self.client_secret:
            grant = {"grant_type": "client_credentials"}
            password = self.client_secret
        elif self.device_id:
            grant = {
                "grant_type": "https://oauth.reddit.com/grants/installed_client",
                "device_id": self.device_id,
            }
            password = ""
        else:
            raise ValueError(
                "要 secret（script app）或 device_id（installed app）"
                "——两个都没有就换不了 token"
            )

        cred = base64.b64encode(
            f"{self.client_id}:{password}".encode()
        ).decode()
        body = urllib.parse.urlencode(grant).encode()
        request = urllib.request.Request(
            self.TOKEN_URL,
            data=body,
            headers={
                "Authorization": f"Basic {cred}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return str(payload.get("access_token") or "")

    def _search(self, token: str, sub: str, keyword: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({
            "q": keyword,
            "restrict_sr": "1",
            "sort": "relevance",
            "t": "month",
            "limit": 25,
        })
        url = f"{self.SEARCH_URL.format(sub=sub)}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": self.user_agent,
            },
        )
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))


# ============================================================
# Etsy —— 同类目买家的「我想要但没有」
# ============================================================


class EtsyClient:
    """Etsy 官方 API v3（**官方 API，不爬**）

    ## 它**不是需求信号源**（2026-11 核实）

    我们要的是「**别人卖什么、买家搜什么**」——跨店的公开需求。
    但 Etsy 官方 API 的形状是「**卖家管自己的店**」：

    - 我们的代码调的是 `/v3/application/listings/active`——
      **`/v3/application/` 命名空间 = 拿令牌的那家店的商品**，
      不是全站的；而且它要 OAuth Bearer（2026-02 起还要
      `x-api-key` 里带 `keystring:secret`），只发 key 会被 401/403。
    - 想跨店看别人的商品，Etsy **只准 scraping**（官方明令禁止）；
      跨店 OAuth 又需要**对方卖家授权**——那不是我们要的信号。

    → 所以这里的 Etsy 是「**我们自己也在 Etsy 开店时的运营工具**」，
    不是站外需求源。凭证有了它能查自己店的商品，
    但**别指望它给出「买家在找什么」**。

    保留实现是因为：政策会变（就像 Reddit 那样），
    代码留着，真变成可用了不用重写。
    """

    LISTINGS_URL = "https://openapi.etsy.com/v3/application/listings/active"

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
        now: Callable[[], str] = _now_iso,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.opener = opener
        self.now = now

    def fetch(self, keywords: list[str], window_days: int) -> FetchResult:
        if not self.api_key:
            return FetchResult(status=PlatformStatus(
                "etsy", "unverifiable",
                "**Etsy 给不了站外需求信号**：它的 API 是给你管**自己店**的"
                "（/v3/application/ 只返回你店的商品，还要 OAuth），"
                "跨店数据官方只准 scraping（明令禁止）。"
                "需要的是「买家搜什么」的话，用 GSC。"))
        captured = self.now()
        signals: list[dict[str, Any]] = []
        failures: list[str] = []
        for keyword in keywords:
            query = urllib.parse.urlencode({"keywords": keyword, "limit": 25})
            url = f"{self.LISTINGS_URL}?{query}"
            request = urllib.request.Request(
                url, headers={"x-api-key": self.api_key})
            try:
                with urllib.request.urlopen(request, timeout=30) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                failures.append(f"「{keyword}」HTTP {exc.code}")
                continue
            except (urllib.error.URLError, ValueError) as exc:
                failures.append(f"「{keyword}」{exc}")
                continue
            for item in payload.get("results") or []:
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                signals.append({
                    "platform": "etsy",
                    "topic": title[:180],
                    "occurrences": 1,
                    "source_url": str(item.get("url") or ""),
                    "captured_at": captured,
                    "snippet": str(item.get("description") or "")[:280],
                })
            if len(signals) >= MAX_SIGNALS_PER_PLATFORM:
                break

        detail = f"{len(signals)} 条" + (f"；{len(failures)} 个查询失败" if failures else "")
        status = "observed" if signals else ("unverifiable" if failures else "observed")
        return FetchResult(
            signals=signals[:MAX_SIGNALS_PER_PLATFORM],
            status=PlatformStatus("etsy", status, detail, len(signals)),
        )


# ============================================================
# Pinterest —— 视觉搜索意图
# ============================================================


class PinterestClient:
    """Pinterest API v5（**官方 API，不爬**）

    ## 它**不是需求信号源**（2026-11 核实）

    我们要的是「**别人在搜什么、别人在 pin 什么**」——全站的公开需求。
    但 Pinterest 官方 v5 的形状是「**广告主/商家管自己的账号**」：

    - 官方文档写明：`pin search` **只覆盖你自己的 Pin**，
      「search across all Pins is a beta **not available to every app**」；
    - 注册还要**商业账号**（个人账号不能注册 app）+
      app 申请**每个工作日审一次**；Trial 额度 1000 请求/天。

    → 所以这里的 Pinterest 是「**我们自己也在 Pinterest 开店时的运营工具**」，
    不是站外需求源。**别指望它给出「别人在搜什么」**。

    保留实现是因为：政策会变，代码留着，真变成可用了不用重写。
    """

    SEARCH_URL = "https://api.pinterest.com/v5/pins/search"

    def __init__(
        self,
        access_token: str = "",
        opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
        now: Callable[[], str] = _now_iso,
    ) -> None:
        self.access_token = access_token
        self.opener = opener
        self.now = now

    def fetch(self, keywords: list[str], window_days: int) -> FetchResult:
        if not self.access_token:
            return FetchResult(status=PlatformStatus(
                "pinterest", "unverifiable",
                "**Pinterest 给不了站外需求信号**：官方 pin 搜索只覆盖"
                "**你自己的 Pin**，全站搜索是 beta、不给普通 app；"
                "注册还要商业账号 + 每个工作日审一次。"
                "需要「别人在搜什么」的话，用 GSC。"))
        captured = self.now()
        signals: list[dict[str, Any]] = []
        failures: list[str] = []
        for keyword in keywords:
            query = urllib.parse.urlencode({"query": keyword, "page_size": 25})
            url = f"{self.SEARCH_URL}?{query}"
            request = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {self.access_token}"})
            try:
                with urllib.request.urlopen(request, timeout=30) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                failures.append(f"「{keyword}」HTTP {exc.code}")
                continue
            except (urllib.error.URLError, ValueError) as exc:
                failures.append(f"「{keyword}」{exc}")
                continue
            for item in payload.get("items") or []:
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                pin_id = str(item.get("id") or "")
                signals.append({
                    "platform": "pinterest",
                    "topic": title[:180],
                    "occurrences": 1,
                    "source_url": f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else "",
                    "captured_at": captured,
                    "snippet": str(item.get("description") or "")[:280],
                })
            if len(signals) >= MAX_SIGNALS_PER_PLATFORM:
                break

        detail = f"{len(signals)} 条" + (f"；{len(failures)} 个查询失败" if failures else "")
        status = "observed" if signals else ("unverifiable" if failures else "observed")
        return FetchResult(
            signals=signals[:MAX_SIGNALS_PER_PLATFORM],
            status=PlatformStatus("pinterest", status, detail, len(signals)),
        )


# ============================================================
# 工具
# ============================================================


#: **导出包里每张表的第一列**——它是那张表的维度名
_QUERY_FIRST_COLUMNS = {
    "top queries", "query", "search query", "search term", "queries",
    "热门查询", "查询", "搜索字词", "搜索词",
}
_PAGE_FIRST_COLUMNS = {
    "top pages", "page", "pages", "url", "landing page",
    "排名靠前的网页", "热门网页", "网页", "页面", "网址",
}

#: **页面信号最多留多少条**（我们自己站的数据，不算抓站，但也要有上限）
MAX_PAGE_SIGNALS = 50


def _read_tables(path: str) -> list[list[list[str]]]:
    """把一个路径里的所有 CSV 表读出来

    **路径可以是**：单个 CSV / Search Console 导出的 zip / 一个目录。

    **为什么认 zip**：Search Console 一次导出**好几张表**
    （热门查询 / 排名靠前的网页 / 日期 / 国家地区 / 设备 / 搜索结果呈现），
    让使用者自己挑一张丢进来 = 挑错就白搭——
    所以这里**按表头自己认表**，丢整个导出包进来也行。
    """
    target = Path(path)
    texts: list[str] = []
    if target.is_dir():
        for csv_file in sorted(target.glob("*.csv")):
            texts.append(csv_file.read_text(encoding="utf-8-sig", errors="replace"))
    elif target.suffix.lower() == ".zip":
        with zipfile.ZipFile(target) as archive:
            for name in sorted(archive.namelist()):
                if name.lower().endswith(".csv"):
                    texts.append(archive.read(name).decode("utf-8-sig", errors="replace"))
    else:
        texts.append(target.read_text(encoding="utf-8-sig", errors="replace"))
    out: list[list[list[str]]] = []
    for text in texts:
        rows = list(csv.reader(io.StringIO(text)))
        if rows:
            out.append(rows)
    return out


def _classify_table(rows: list[list[str]]) -> str:
    """这张表是什么——`queries` / `pages` / 认不出

    **按第一列判**：Search Console 导出的每张表，第一列都是它的维度名
    （热门查询 / 排名靠前的网页 / 日期 / 国家地区 / 设备 / 搜索结果呈现）。
    日期、设备那些表**不影响选页**——它们是「整个站」的数，不是「需求」。
    """
    header_idx, header = _find_header_row(rows)
    if not header:
        return ""
    first = str(header[0] or "").strip().lower()
    if first in _QUERY_FIRST_COLUMNS:
        return "queries"
    if first in _PAGE_FIRST_COLUMNS:
        return "pages"
    return ""


def _url_to_path(url: str) -> str:
    """URL → 路径（`/products/xxx`）——要和快照里的 landing_page_path 对得上"""
    text = str(url or "").strip()
    if not text:
        return ""
    if text.startswith("/"):
        return text
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme and parsed.netloc:
        return parsed.path or "/"
    return text


def _find_header_row(rows: list[list[str]]) -> tuple[int, list[str]]:
    """找出 GSC 导出 CSV 的表头行

    GSC 的导出前面可能有标题行、可能有筛选行 —— 表头是**含 clicks 或
    impressions 的那一行**。

    **中英文表头都认**：Search Console 的界面语言变了，列名就跟着变
    （中文界面导出的是「热门查询,点击次数,展示,点击率,排名」）。
    写死一套 = 用户换个界面就读不出来，而且**读不出来时还看不出为什么**。
    """
    for idx, row in enumerate(rows[:20]):
        lowered = [str(c or "").strip().lower() for c in row]
        if any("clicks" in c for c in lowered) or any("impressions" in c for c in lowered):
            return idx, [str(c or "").strip() for c in row]
        if any(c in ("点击次数", "点击", "点击数", "展示", "展示次数", "曝光") for c in lowered):
            return idx, [str(c or "").strip() for c in row]
    return -1, []


def _pick_col(cols: dict[str, int], names: tuple[str, ...]) -> Optional[int]:
    for name in names:
        for header, idx in cols.items():
            if header.lower() == name:
                return idx
    for name in names:
        for header, idx in cols.items():
            if name in header.lower():
                return idx
    return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    """**排名这类小数**——「25.00」要读成 25.0，不能读成空"""
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _safe_ctr(value: Any) -> Optional[float]:
    """**点击率**——统一成**百分比**（2.48 = 2.48%）

    **同一个列，两种单位**：英文导出的 CTR 是**比例**（0.033），
    中文导出的是**百分数字符串**（「2.48%」）。
    不统一的话，两个文件读出来的同一个量会差 100 倍——
    而「同一个量只能有一个来源」。
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("%"):
        try:
            return float(text[:-1].strip())
        except ValueError:
            return None
    try:
        return float(text.replace(",", "").strip()) * 100.0
    except ValueError:
        return None
