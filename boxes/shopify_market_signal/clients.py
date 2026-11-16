"""boxes.shopify_market_signal.clients —— 四个平台的只读客户端

**全部走官方 API**，不抓 ToS 禁止的路径；**只取公开内容**。

## 统一的接口

每个客户端都返回 `(signals, status)`：

- `signals`：`[{platform, topic, occurrences, source_url, captured_at, snippet}]`
- `status`：`{platform, status, detail}`，`status` ∈ `observed` / `unverifiable`

**取不到就返回 unverifiable + 原因**，不抛异常——
一个平台挂了不该拖垮整轮（`market_signal.py` 里写了这条）。

## 哪些验过、哪些没验

- **GSC 的 CSV 路径**：用夹具测过 ✓（格式容错，GSC 导出表头会变）
- **GSC 的 API 路径 / Reddit / Etsy / Pinterest**：**代码按各自官方文档写的，
  但没有真凭证跑过**（凭证在你手上）。
  → 所以盒子整体标 `skeleton`，等第一次真跑过再标 `live`。
"""

from __future__ import annotations

import csv
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from fulfillment_scopes.shopify.market_signal import (
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

    两条路：
    - **API**：`GSC_ACCESS_TOKEN` + `GSC_SITE_URL` 在环境变量里 → 自动跑
    - **CSV**：投料给一个导出的 CSV 路径 → 立刻能跑（不用授权）

    GSC 导出的 CSV 表头每次可能变（"Top queries" / "Query"），
    所以这里按表头**猜列名**，不写死。
    """

    API_URL = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"

    def __init__(
        self,
        access_token: str = "",
        site_url: str = "",
        csv_path: str = "",
        opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
        now: Callable[[], str] = _now_iso,
    ) -> None:
        self.access_token = access_token
        self.site_url = site_url
        self.csv_path = csv_path
        self.opener = opener
        self.now = now

    def fetch(self, keywords: list[str], window_days: int) -> FetchResult:
        if self.csv_path:
            return self._from_csv()
        if not (self.access_token and self.site_url):
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable",
                "没配 GSC_ACCESS_TOKEN / GSC_SITE_URL，也没给 CSV 路径",
            ))
        try:
            return self._from_api(window_days)
        except urllib.error.HTTPError as exc:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable", f"HTTP {exc.code}：{exc.reason}"))
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable", f"取数失败：{exc}"))

    # ---- CSV ----

    def _from_csv(self) -> FetchResult:
        if not os.path.exists(self.csv_path):
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable", f"CSV 不存在：{self.csv_path}"))
        try:
            with open(self.csv_path, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.reader(fh))
        except OSError as exc:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable", f"CSV 读不了：{exc}"))

        if not rows:
            return FetchResult(status=PlatformStatus("gsc", "unverifiable", "CSV 是空的"))

        header_idx, header = _find_header_row(rows)
        if header is None:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable", "CSV 里找不到表头行（含 clicks / impressions 的那行）"))

        cols = {name: idx for idx, name in enumerate(header)}
        q_col = _pick_col(cols, ("top queries", "query", "search query", "search term"))
        c_col = _pick_col(cols, ("clicks",))
        i_col = _pick_col(cols, ("impressions",))
        if q_col is None:
            return FetchResult(status=PlatformStatus(
                "gsc", "unverifiable", "CSV 表头里找不到查询词列（Top queries / Query）"))

        captured = self.now()
        signals = []
        for row in rows[header_idx + 1:]:
            if len(row) <= q_col:
                continue
            topic = (row[q_col] or "").strip()
            if not topic or topic.lower() in ("total", "总计"):
                continue
            clicks = _safe_int(row[c_col]) if c_col is not None and len(row) > c_col else None
            impressions = _safe_int(row[i_col]) if i_col is not None and len(row) > i_col else None
            signals.append({
                "platform": "gsc",
                "topic": topic,
                "occurrences": clicks if clicks is not None else impressions,
                "source_url": f"gsc://{self.site_url or 'gsc'}/search-analytics/{topic}",
                "captured_at": captured,
                "snippet": (
                    f"clicks={clicks} impressions={impressions}"
                    if clicks is not None or impressions is not None else ""
                ),
            })
            if len(signals) >= MAX_SIGNALS_PER_PLATFORM:
                break

        return FetchResult(
            signals=signals,
            status=PlatformStatus("gsc", "observed", f"CSV {len(signals)} 条", len(signals)),
        )

    # ---- API ----

    def _from_api(self, window_days: int) -> FetchResult:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=window_days)
        body = json.dumps({
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query"],
            "rowLimit": 250,
        }).encode("utf-8")
        site = urllib.parse.quote(self.site_url, safe="")
        payload = _http_json(
            self.API_URL.format(site=site),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
            data=body,
            method="POST",
        )
        captured = self.now()
        signals = []
        for row in (payload.get("rows") or [])[:MAX_SIGNALS_PER_PLATFORM]:
            keys = row.get("keys") or []
            topic = str(keys[0]) if keys else ""
            if not topic:
                continue
            signals.append({
                "platform": "gsc",
                "topic": topic,
                "occurrences": row.get("clicks"),
                "source_url": f"gsc://{self.site_url}/search-analytics/{topic}",
                "captured_at": captured,
                "snippet": (
                    f"clicks={row.get('clicks')} impressions={row.get('impressions')} "
                    f"position={row.get('position')}"
                ),
            })
        return FetchResult(
            signals=signals,
            status=PlatformStatus("gsc", "observed", f"API {len(signals)} 条", len(signals)),
        )


# ============================================================
# Reddit —— 公开讨论里的「在找什么」
# ============================================================


class RedditClient:
    """Reddit Data API（**官方 API，不爬**）

    `client_credentials` 授权：**不需要用户账号密码** → 只要注册一个 app
    拿到 client_id / client_secret 就行。

    我们只看**公开子版**的公开搜索结果，产出只到「需求主题 + 出处」这一层。
    """

    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    SEARCH_URL = "https://oauth.reddit.com/r/{sub}/search"

    DEFAULT_SUBS = ["gifts", "petloss", "Christmas", "PersonalizedGifts"]

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        subs: Optional[list[str]] = None,
        user_agent: str = "beeOS-market-signal/0.1",
        opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
        now: Callable[[], str] = _now_iso,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.subs = subs or list(self.DEFAULT_SUBS)
        self.user_agent = user_agent
        self.opener = opener
        self.now = now

    def fetch(self, keywords: list[str], window_days: int) -> FetchResult:
        if not (self.client_id and self.client_secret):
            return FetchResult(status=PlatformStatus(
                "reddit", "unverifiable",
                "没配 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET（官方 API 要先注册 app）"))
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
        basic = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        import base64

        cred = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
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

    只取公开的在售商品标题/描述 —— 用来看**同类目买家在找什么**。
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
                "etsy", "unverifiable", "没配 ETSY_API_KEY（官方 API 要先注册 app）"))
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

    只取公开 pin 的标题/描述 —— 用来看「这类东西在 Pinterest 上怎么被搜」。
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
                "没配 PINTEREST_ACCESS_TOKEN（官方 API 要先注册 app）"))
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


def _find_header_row(rows: list[list[str]]) -> tuple[int, list[str]]:
    """找出 GSC 导出 CSV 的表头行

    GSC 的导出前面可能有标题行、可能有筛选行 —— 表头是**含 clicks 或
    impressions 的那一行**。
    """
    for idx, row in enumerate(rows[:20]):
        lowered = [str(c or "").strip().lower() for c in row]
        if any("clicks" in c for c in lowered) or any("impressions" in c for c in lowered):
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
