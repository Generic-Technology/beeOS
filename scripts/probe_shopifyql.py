#!/usr/bin/env python3
"""scripts/probe_shopifyql.py —— ShopifyQL 只读取数探测

**它回答一个问题：这家店的哪些指标，API 真的取得到。**

为什么单独一个脚本而不是直接写盒子：
盒子产出里的每个数字都要有出处（`source`）。
「出处是什么」得先**实测**——后台看得到 ≠ API 取得到，
而且 Shopify 侧这两年动过好几次（ShopifyQL 弃用过、
`parseWarnings` 是 2026-10 才加的、Web Pixels 是另一条路）。

**探测结果就是快照盒判据的来源**：取得到的进「订单侧/会话侧可判」，
取不到的进「待验证」，**不许写 0**。

## 用法

```bash
export SHOPIFY_ADMIN_TOKEN=shpat_xxx      # 只读 + read_reports
export SHOPIFY_STORE_DOMAIN=giftsjourneyrebirth.myshopify.com

python scripts/probe_shopifyql.py --days 28          # 真跑
python scripts/probe_shopifyql.py --dry-run           # 只看语句，不发请求
```

**凭证不进命令行、不进产出**：只从环境变量读，报告里也不会出现。

## 输出

一份「能取到 / 取不到 / 需换源」的清单（JSON + 人话表格）。
退出码：全部取到 = 0；有取不到的 = 1（**取不到是结论，不是脚本失败**）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fulfillment_scopes.shopify.auth import (  # noqa: E402
    ShopifyAuthError,
    load_env_file,
    resolve_token,
)
from boxes.shopify_snapshot.shopifyql_client import (  # noqa: E402
    DOMAIN_ENV,
    TOKEN_ENV,
    ShopifyQLClient,
    ShopifyQLError,
    ShopifyQLPermissionError,
    ShopifyQLParseError,
    ShopifyQLThrottledError,
    ShopifyQLTooLargeError,
)


@dataclass
class ProbeResult:
    """一条探测的结果"""

    name: str
    ok: bool
    kind: str = ""          # 失败形态：permission / parse / too_large / throttled / empty
    detail: str = ""
    columns: tuple[str, ...] = ()
    row_count: int = 0
    sample: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "kind": self.kind,
            "detail": self.detail,
            "columns": list(self.columns),
            "row_count": self.row_count,
            "sample": self.sample,
            "warnings": list(self.warnings),
        }


#: 要探的语句——**跟快照盒用的是同一批形状**
PROBE_STATEMENTS: dict[str, str] = {
    "totals": (
        "FROM sessions\n"
        "  SHOW sessions, online_store_visitors, pageviews,\n"
        "    sessions_with_cart_additions, sessions_that_reached_checkout,\n"
        "    sessions_that_completed_checkout, conversion_rate\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  SINCE {start} UNTIL {end}"
    ),
    "landing_pages": (
        "FROM sessions\n"
        "  SHOW sessions, sessions_with_cart_additions,\n"
        "    sessions_that_reached_checkout, sessions_that_completed_checkout\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  GROUP BY landing_page_path\n"
        "  SINCE {start} UNTIL {end}\n"
        "  ORDER BY sessions DESC"
    ),
    "sources": (
        "FROM sessions\n"
        "  SHOW sessions, sessions_that_completed_checkout\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  GROUP BY referrer_source, agentic_referring_channel\n"
        "  SINCE {start} UNTIL {end}\n"
        "  ORDER BY sessions DESC"
    ),
    "product_views": (
        "FROM sessions\n"
        "  SHOW sessions\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  GROUP BY session_viewed_product\n"
        "  SINCE {start} UNTIL {end}"
    ),
    "search_terms": (
        "FROM sessions\n"
        "  SHOW sessions, sessions_that_completed_checkout\n"
        "  WHERE human_or_bot_session = 'human'\n"
        "  GROUP BY referrer_terms\n"
        "  SINCE {start} UNTIL {end}\n"
        "  ORDER BY sessions DESC"
    ),
}


def _window(days: int) -> tuple[str, str]:
    """整日窗口：**不含当天未走完的部分**

    当天的数会随着新订单一直涨——同一段窗口一小时后再查会更高。
    基线要能复现，就不能含当天。
    """
    from datetime import date, timedelta

    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def _classify(exc: Exception) -> tuple[str, str]:
    """失败形态 → (kind, 人话)"""
    if isinstance(exc, ShopifyQLPermissionError):
        return "permission", (
            "权限不够——shopifyqlQuery 需要 read_reports scope + "
            "Level 2 受保护客户数据审批"
        )
    if isinstance(exc, ShopifyQLParseError):
        return "parse", "语句或字段名不被这个 API 版本接受"
    if isinstance(exc, ShopifyQLTooLargeError):
        return "too_large", "响应过大——缩窗口 / 少维度 / 小 LIMIT（等也没用）"
    if isinstance(exc, ShopifyQLThrottledError):
        return "throttled", "被限流——等 windowResetAt 再试"
    if isinstance(exc, ShopifyQLError):
        return "error", str(exc)
    return "error", f"{type(exc).__name__}: {exc}"


def probe(
    client: ShopifyQLClient,
    statements: dict[str, str],
    *,
    opener: Callable[[Any, float], Any] | None = None,
) -> list[ProbeResult]:
    """逐条探测——**一条失败不影响别的**"""
    results: list[ProbeResult] = []
    for name, statement in statements.items():
        probe_client = client
        if opener is not None:
            probe_client = ShopifyQLClient(
                store_domain=client.store_domain,
                token=client.token,
                api_version=client.api_version,
                opener=opener,
            )
        try:
            result = probe_client.query(statement)
        except Exception as exc:  # noqa: BLE001 - 探测要把失败形态记下来
            kind, detail = _classify(exc)
            results.append(ProbeResult(name=name, ok=False, kind=kind, detail=detail))
            continue

        sample = dict(result.rows[0]) if result.rows else {}
        results.append(ProbeResult(
            name=name,
            ok=True,
            columns=result.columns,
            row_count=len(result.rows),
            sample=sample,
            warnings=result.warnings,
        ))
    return results


def _report(results: list[ProbeResult]) -> str:
    lines = ["ShopifyQL 只读取数探测", ""]
    for r in results:
        if r.ok:
            lines.append(f"  [OK]   {r.name}: {r.row_count} 行")
            if r.columns:
                lines.append(f"         列：{', '.join(r.columns)}")
            if r.sample:
                lines.append(f"         样本：{json.dumps(r.sample, ensure_ascii=False)}")
            for w in r.warnings:
                lines.append(f"         警告：{w}")
        else:
            lines.append(f"  [FAIL] {r.name}（{r.kind}）：{r.detail}")
    failed = [r for r in results if not r.ok]
    lines.append("")
    lines.append(
        f"合计 {len(results)} 条，取到 {len(results) - len(failed)} 条，"
        f"取不到 {len(failed)} 条"
    )
    if failed:
        lines.append("")
        lines.append("**取不到的量要进快照盒的 `unverifiable`，不许写 0。**")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ShopifyQL 只读取数探测")
    parser.add_argument("--days", type=int, default=28, help="回看多少天（默认 28）")
    parser.add_argument("--api-version", default="2026-10", help="Admin API 版本（**要用 2026-10**：agentic_referring_channel 等维度 2026-10 才有）")
    parser.add_argument("--dry-run", action="store_true", help="只打印语句，不发请求")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    start, end = _window(args.days)
    statements = {
        name: stmt.format(start=start, end=end)
        for name, stmt in PROBE_STATEMENTS.items()
    }

    if args.dry_run:
        for name, statement in statements.items():
            print(f"--- {name} ---")
            print(statement)
        return 0

    load_env_file()   # 填过 ~/.config/beeos/shopify.env 就不用 export
    domain = os.environ.get(DOMAIN_ENV, "").strip()
    if not domain:
        print(f"没配 {DOMAIN_ENV}——店铺域名从环境变量读", file=sys.stderr)
        return 2
    try:
        # **没有长期 token 就现换一张**（client credentials，24 小时，用完即弃）
        token, _source = resolve_token(domain)
    except ShopifyAuthError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    client = ShopifyQLClient(
        store_domain=domain, token=token, api_version=args.api_version
    )
    results = probe(client, statements)

    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        print(_report(results))

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
