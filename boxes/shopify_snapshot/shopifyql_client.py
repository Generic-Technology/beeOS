"""boxes.shopify_snapshot.shopifyql_client —— 只读 ShopifyQL 客户端

**它只发只读查询**：ShopifyQL 是聚合查询语言，本身不改任何东西。
客户端里**没有任何写接口**——这是刻意的，不是遗漏。

## 错误形态：HTTP 200 也可能是失败

Shopify 官方文档写得很清楚，四种失败长得完全不一样
（[Handle errors and budgets](https://shopify.dev/docs/apps/build/shopifyql/graphql-admin-api/errors-limits-and-performance)）：

| 形态 | 长什么样 | 怎么办 |
|---|---|---|
| 语法错误 | HTTP **200**，`parseErrors` 非空，`tableData` 是 null | **不是限流**，是语句写错了 → 改语句 |
| 权限不足 | `errors[].extensions.code == ACCESS_DENIED` | 要 `read_reports` + Level 2 受保护客户数据 |
| 响应过大 | `errors[].extensions.code == RESPONSE_TOO_LARGE` | **等也没用** → 缩窗口 / 少维度 / 小 LIMIT |
| 被限流 | HTTP **200**，`errors[].extensions.code == THROTTLED` | 等 `extensions.cost.windowResetAt` |

**只看 HTTP 状态码的客户端，会把「语法错了」当成「查到了空数据」**——
然后产出里写下一片 0。���是「取不到」被写成了「观测值」。

## 凭证不进产出，也不进日志

token 只从环境变量读，**永远不出现在返回的 dict 里**，
异常消息里也不带（只带「该配哪个环境变量」）。
`stability-plan.md` 有一条：凭证不进入结果。

## 分页：`LIMIT n OFFSET m`

官方文档要求 `LIMIT` 配 `ORDER BY` 才稳定（结果顺序不保证）。
不翻页 = 丢掉第 251 行以后的落地页 = **少算流量**，
而少算的流量不会报错，只会让结论悄悄偏。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fulfillment_scopes.shopify.auth import resolve_token
from fulfillment_scopes.shopify.auth import ShopifyAuthError
from fulfillment_scopes.shopify.vocabulary import (
    DOMAIN_ENV as SHOPIFY_STORE_DOMAIN_ENV,
)
from fulfillment_scopes.shopify.vocabulary import (
    TOKEN_ENV as SHOPIFY_ADMIN_TOKEN_ENV,
)

#: 凭证从环境变量读，**不进投料、不进产出、不进日志**。
#: 环境变量名住在**边界**里（`fulfillment_scopes/shopify/vocabulary.py`）——
#: 读盒子和写盒子都要用同一套名字，两边各写一份就是两份事实。
TOKEN_ENV = SHOPIFY_ADMIN_TOKEN_ENV
DOMAIN_ENV = SHOPIFY_STORE_DOMAIN_ENV

#: `parseWarnings` 是 2026-10 才加的字段；老版本请求它会自身报字段错
PARSE_WARNINGS_SINCE = "2026-10"

#: 一次查询最多等多久（秒）——**超时不是失败，是「这次没取到」**
DEFAULT_TIMEOUT = 30.0

#: 被限流时最多等几轮（每一轮等到 windowResetAt）
MAX_THROTTLE_WAITS = 3


class ShopifyQLError(RuntimeError):
    """查询失败——**失败要说清是哪一种失败**"""


class ShopifyQLPermissionError(ShopifyQLError):
    """权限不够（`read_reports` / 受保护客户数据审批）"""


class ShopifyQLTooLargeError(ShopifyQLError):
    """响应过大——**等也没用**，必须把查询改小"""


class ShopifyQLThrottledError(ShopifyQLError):
    """被限流"""


class ShopifyQLParseError(ShopifyQLError):
    """语句写错了（HTTP 200，错误在 `parseErrors` 里）"""


@dataclass(frozen=True)
class ShopifyQLResult:
    """一次查询的结果

    `rows` 是 `list[dict]`——列名是 key，值是原始值。
    """

    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    source: str
    api_version: str
    retrieved_at: str
    warnings: tuple[str, ...] = ()
    cost: Optional[dict[str, Any]] = None
    pages_fetched: int = 1


@dataclass
class ShopifyQLClient:
    """只读 ShopifyQL 客户端

    `opener` 可以注入（测试里塞假响应，不打真网络）。
    """

    store_domain: str
    token: str
    api_version: str = "2026-10"
    timeout: float = DEFAULT_TIMEOUT
    opener: Optional[Callable[[urllib.request.Request, float], Any]] = None
    sleeper: Callable[[float], None] = time.sleep
    now: Callable[[], str] = lambda: ""
    pages_fetched: int = field(default=0, init=False)

    # ---- 构造 ----

    @classmethod
    def from_env(cls, store_domain: str = "", **kwargs: Any) -> "ShopifyQLClient":
        """从环境变量读凭证与店铺域名

        凭证**不进投料**——投料里出现 token，它就会进产出、进归档、进日志。

        **没有长期 token 也能跑**：有 client_id + client_secret 就现换一张
        （client credentials，24 小时有效期，用完即弃）。
        """
        domain = (store_domain or os.environ.get(DOMAIN_ENV, "")).strip()
        if not domain:
            raise ShopifyQLPermissionError(
                f"没配 {DOMAIN_ENV}——店铺域名从环境变量读，不走投料"
            )
        try:
            token, _source = resolve_token(domain)
        except ShopifyAuthError as exc:
            raise ShopifyQLPermissionError(str(exc)) from exc
        return cls(store_domain=domain, token=token, **kwargs)

    # ---- 查询 ----

    def query(self, statement: str, *, page_size: int = 250, max_pages: int = 20) -> ShopifyQLResult:
        """跑一条 ShopifyQL 语句

        **分页自己做**：语句里不带 LIMIT/OFFSET，由这里加——
        否则每次翻页都要重写一遍语句，而漏翻一页就是少算流量。
        """
        rows: list[dict[str, Any]] = []
        columns: tuple[str, ...] = ()
        warnings: list[str] = []
        cost: Optional[dict[str, Any]] = None
        retrieved_at = self.now()
        source = f"shopifyql:{statement}"
        pages = 0

        offset = 0
        while True:
            paged = f"{statement} LIMIT {page_size} OFFSET {offset}"
            payload = self._post(paged)
            columns = payload.columns or columns
            rows.extend(payload.rows)
            warnings.extend(payload.warnings)
            cost = payload.cost or cost
            pages += 1
            self.pages_fetched = pages
            if len(payload.rows) < page_size or pages >= max_pages:
                break
            offset += page_size

        return ShopifyQLResult(
            columns=columns,
            rows=tuple(rows),
            source=source,
            api_version=self.api_version,
            retrieved_at=retrieved_at,
            warnings=tuple(warnings),
            cost=cost,
            pages_fetched=pages,
        )

    # ---- HTTP ----

    def _post(self, statement: str) -> ShopifyQLResult:
        """发一次 GraphQL 请求，把四种失败形态分开"""
        url = (
            f"https://{self.store_domain}/admin/api/{self.api_version}/graphql.json"
        )
        selection = "tableData { columns { name dataType displayName } rows } parseErrors"
        if self._supports_parse_warnings():
            selection += " parseWarnings"
        body = json.dumps({
            "query": (
                "query RunShopifyQL($statement: String!) {"
                f" shopifyqlQuery(query: $statement) {{ {selection} }} }}"
            ),
            "variables": {"statement": statement},
        }).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": self.token,
                "User-Agent": "beeOS-shopify-snapshot/0.1",
            },
        )

        try:
            if self.opener is not None:
                response = self.opener(request, self.timeout)
                raw = response.read()
            else:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    raw = resp.read()
        except urllib.error.HTTPError as exc:  # pragma: no cover - 网络异常
            if exc.code in (401, 403):
                # 401/403 在这里几乎总是**权限**问题，不是网络问题——
                # 说清缺什么，人就不用自己猜了。
                raise ShopifyQLPermissionError(
                    f"HTTP {exc.code}：权限不够——shopifyqlQuery 需要 "
                    "read_reports scope + Level 2 受保护客户数据审批"
                ) from exc
            raise ShopifyQLError(f"HTTP {exc.code}：ShopifyQL 请求失败") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - 网络异常
            raise ShopifyQLError(f"连不上店铺：{exc.reason}") from exc

        return self._read_payload(json.loads(raw.decode("utf-8")))

    def _read_payload(self, payload: dict[str, Any]) -> ShopifyQLResult:
        """把响应读成结果——**先看错误，再看数据**"""
        errors = payload.get("errors") or []
        if errors:
            self._raise_for_graphql_errors(errors)

        node = (payload.get("data") or {}).get("shopifyqlQuery")
        if node is None:
            raise ShopifyQLError(
                "响应里没有 shopifyqlQuery 节点——可能不是一次成功的查询"
            )

        parse_errors = node.get("parseErrors") or []
        if parse_errors:
            raise ShopifyQLParseError(
                "ShopifyQL 语句写错了（HTTP 200，错误在 parseErrors 里）："
                + "; ".join(str(e) for e in parse_errors)
            )

        table = node.get("tableData")
        if table is None:
            raise ShopifyQLError(
                "tableData 是 null——没有报错但也没有数据，不能当成「查到了 0」"
            )

        columns = tuple(
            str(c.get("name")) for c in (table.get("columns") or []) if c.get("name")
        )
        rows = [dict(row) for row in (table.get("rows") or [])]
        warnings = tuple(str(w) for w in (node.get("parseWarnings") or []))
        cost = (payload.get("extensions") or {}).get("shopifyqlCost")

        return ShopifyQLResult(
            columns=columns,
            rows=tuple(rows),
            source="shopifyql",
            api_version=self.api_version,
            retrieved_at=self.now(),
            warnings=warnings,
            cost=cost,
        )

    def _raise_for_graphql_errors(self, errors: list[dict[str, Any]]) -> None:
        """按 `extensions.code` 分派——**不同的错要不同的处理**"""
        codes = [
            str((e.get("extensions") or {}).get("code") or "") for e in errors
        ]
        messages = "; ".join(str(e.get("message") or e) for e in errors)

        if "THROTTLED" in codes:
            reset_at = self._window_reset_at(errors)
            if reset_at and self.pages_fetched < MAX_THROTTLE_WAITS:
                wait = max(0.0, reset_at - _now_epoch())
                self.sleeper(wait)
            raise ShopifyQLThrottledError(
                f"被限流了：{messages}（等到 windowResetAt 再重试）"
            )

        if "RESPONSE_TOO_LARGE" in codes:
            raise ShopifyQLTooLargeError(
                f"响应过大：{messages}——等也没用，要缩窗口 / 少维度 / 小 LIMIT"
            )

        if "ACCESS_DENIED" in codes:
            raise ShopifyQLPermissionError(
                f"权限不够：{messages}——"
                "shopifyqlQuery 需要 read_reports scope + Level 2 受保护客户数据审批"
            )

        raise ShopifyQLError(f"GraphQL 报错：{messages}")

    def _supports_parse_warnings(self) -> bool:
        """`parseWarnings` 是 2026-10 才加的"""
        try:
            major, minor = (int(x) for x in self.api_version.split("-")[0].split(".")[:2])
        except (ValueError, TypeError):
            return False
        return (major, minor) >= (2026, 10)

    @staticmethod
    def _window_reset_at(errors: list[dict[str, Any]]) -> Optional[float]:
        """被限流时，预算恢复的时间点"""
        for error in errors:
            cost = (error.get("extensions") or {}).get("cost") or {}
            reset = cost.get("windowResetAt")
            if reset:
                from datetime import datetime

                try:
                    return datetime.fromisoformat(str(reset)).timestamp()
                except ValueError:
                    return None
        return None


def _now_epoch() -> float:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).timestamp()
