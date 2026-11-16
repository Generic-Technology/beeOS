#!/usr/bin/env python3
"""scripts/rollback_shopify_experiment.py —— 把某个商品页改回改之前的样子

**为什么需要它**：写操作会误伤。实测踩过两次：

1. 写了 `seo.description`，**SEO 标题变空了**（Shopify 的 seo 是整体替换语义）；
2. 标签被整体替换，**分类标签（For Baby + Kids 之类）没了**——
   分类标签是把商品挂到分类页上的，删了商品可能就从分类里消失。

所以每次写入都存了**改前基线**（`baseline_fields`），这个脚本用它改回去。

## 用法

```bash
# 1) 找到那次写入的 task_run_id（看 logs/task_runs.jsonl 里 shopify_acquisition_experiment_box 的记录）
python scripts/rollback_shopify_experiment.py --run-id <task_run_id>

# 不给 --run-id 就回滚最近一次写入
python scripts/rollback_shopify_experiment.py --latest
```

**需要写闸开着**（`SHOPIFY_ALLOW_WRITES=1`）——回滚也是写。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from boxes.shopify_acquisition_experiment.shopify_writer import (  # noqa: E402
    ShopifyWriteError,
    ShopifyWriter,
)
from fulfillment_scopes.shopify.auth import (  # noqa: E402
    ShopifyAuthError,
    load_env_file,
    resolve_token,
)
from fulfillment_scopes.shopify.vocabulary import DOMAIN_ENV  # noqa: E402

DEFAULT_STORE = "logs/task_runs.jsonl"


def _load_runs(path: str) -> list[dict]:
    if not os.path.exists(path):
        raise SystemExit(f"找不到履约记录：{path}")
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _applied_runs(runs: list[dict]) -> list[dict]:
    """所有「真的写上去了」的实验记录"""
    out = []
    for run in runs:
        if run.get("box_id") != "shopify_acquisition_experiment_box":
            continue
        artifact = run.get("artifact") or {}
        if artifact.get("applied") is True and artifact.get("baseline_fields"):
            out.append(run)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把某个商品页改回改之前的样子")
    parser.add_argument("--run-id", default="", help="那次写入的 task_run_id")
    parser.add_argument("--latest", action="store_true", help="回滚最近一次写入")
    parser.add_argument("--store", default=DEFAULT_STORE, help="履约记录位置")
    parser.add_argument("--dry-run", action="store_true", help="只看要改回什么，不写")
    args = parser.parse_args(argv)

    load_env_file()
    runs = _load_runs(args.store)
    applied = _applied_runs(runs)
    if not applied:
        raise SystemExit("履约记录里没有「真的写上去了」的实验——没什么可回滚")

    if args.run_id:
        target = next((r for r in applied if r.get("task_run_id") == args.run_id), None)
        if target is None:
            raise SystemExit(f"没找到那次写入：{args.run_id}")
    else:
        target = applied[-1]

    artifact = target["artifact"]
    baseline = artifact.get("baseline_fields") or {}
    handle = artifact.get("landing_page") or ""
    if not handle:
        raise SystemExit("那次记录里没有落地页——改不了")

    print(f"回滚目标：{handle}")
    print(f"写入记录：{target['task_run_id']}（{target.get('started_at')}）")
    print("改回：")
    for key in ("title", "descriptionHtml", "seo.title", "seo.description", "tags"):
        print(f"   {key} = {str(baseline.get(key))[:100]}")

    if args.dry_run:
        print("\n（--dry-run：没写）")
        return 0

    domain = os.environ.get(DOMAIN_ENV, "").strip()
    try:
        _token, _source = resolve_token(domain)
        writer = ShopifyWriter.from_env()
    except (ShopifyAuthError, ShopifyWriteError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    try:
        current = writer.read_product(handle)
        if not current:
            raise SystemExit(f"按 handle={handle!r} 没找到商品")
        result = writer.update_product(current["id"], {
            "title": baseline.get("title"),
            "descriptionHtml": baseline.get("descriptionHtml"),
            "seo.title": baseline.get("seo.title"),
            "seo.description": baseline.get("seo.description"),
            "tags": baseline.get("tags"),
        })
    except (ShopifyWriteError, SystemExit) as exc:
        print(f"\n回滚失败：{exc}", file=sys.stderr)
        return 1

    errors = result.get("user_errors") or []
    if errors:
        print("\nShopify 拒了：", "; ".join(errors), file=sys.stderr)
        return 1

    print("\n改回后的值：")
    for key, value in (result.get("applied_fields") or {}).items():
        print(f"   {key} = {str(value)[:100]}")
    print("\n✓ 已改回改之前的样子")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
