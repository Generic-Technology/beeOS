#!/usr/bin/env python3
"""scripts/run_shopify_round.py —— 跑一轮店铺经营闭环

**一条命令跑完这一轮**：快照 → 诊断 → 获客实验 → 经营档案（三次投递）。

```
shopify_snapshot_box
        ↓  snapshot_ref
shopify_traffic_diagnosis_box
        ↓  diagnosis_ref
shopify_acquisition_experiment_box
        ↓  experiment_ref
shopify_archive_box   （快照/诊断/实验各投一次 → 同一条经营记录）
```

接线**不在这份脚本里抄**——它从协作边界声明读
（`fulfillment_scopes/shopify/scope.py` 是唯一事实源）。
脚本只负责「按声明发起交接」。

## 默认是**只读 + 出方案**

不加 `--apply` 时，实验盒**不写店里的页面**：它只起草内容，
产出里能看到那份施工单。真的写要同时满足两件事：

```bash
export SHOPIFY_ALLOW_WRITES=1     # 进程级写闸
python scripts/run_shopify_round.py --apply
```

外加投料里的逐次意图（`--apply` 就是它）。
**只配了写权限 token ≠ 允许写**——两件事必须分别显式开。

## 凭证只从环境变量读

`SHOPIFY_ADMIN_TOKEN` / `SHOPIFY_STORE_DOMAIN`。
**不进命令行、不进产出、不进日志**。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fulfillment_scopes import handoff_payload  # noqa: E402
from fulfillment_scopes.shopify.auth import (  # noqa: E402
    ShopifyAuthError,
    load_env_file,
    resolve_token,
)
from fulfillment_scopes.shopify.vocabulary import (  # noqa: E402
    ALLOW_WRITES_ENV,
    DOMAIN_ENV,
    TOKEN_ENV,
)
from kanban.trigger import trigger_task  # noqa: E402
from runtime.store import TaskRunStore  # noqa: E402

SCOPE_ID = "shopify_store"
SNAPSHOT = "shopify_snapshot_box"
DIAGNOSIS = "shopify_traffic_diagnosis_box"
EXPERIMENT = "shopify_acquisition_experiment_box"
ARCHIVE = "shopify_archive_box"


def _window(days: int) -> tuple[str, str]:
    """整日窗口：**不含当天**（当天的数会随着新订单一直涨）"""
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def _round_id(store_domain: str, window_end: str) -> str:
    """这一轮的号：**店铺 + 那一周**

    快照、诊断、实验三次投递归成**同一条**经营记录，靠的就是这个号。
    """
    shop = store_domain.split(".")[0]
    year, week, _ = date.fromisoformat(window_end).isocalendar()
    return f"{shop}@{year}-W{week:02d}"


def _artifact(run: dict[str, Any]) -> dict[str, Any]:
    """这次履约的产出

    **履约记录里它叫 `artifact`**，不叫 `result`——
    找错字段名就会以为「产出丢了」，其实它在。
    """
    return run.get("artifact") or run.get("result") or {}


def _require_result(name: str, run: dict[str, Any]) -> dict[str, Any]:
    """这一步交付了吗——没交付就停，不带着空产出往下跑"""
    status = run.get("status")
    acceptance = run.get("acceptance_status")
    artifact = _artifact(run)
    reasons = artifact.get("unverified") or artifact.get("unknowns") or []

    if status == "waiting_external":
        # 停在等人判定：合同里有例外条款升级了。
        raise SystemExit(
            f"[round] {name} 停在**等人判定**（验收没过，合同要求升级）"
            f"——链条停在这儿。\n"
            f"         这次为什么没成：{reasons or '（产出里没写原因，去看履约记录）'}\n"
            f"         放行与否：{run.get('error_reason') or ''}"
        )
    if status != "completed":
        raise SystemExit(
            f"[round] {name} 没跑完（status={status}）——链条停在这儿。\n"
            f"         原因：{run.get('error_reason') or reasons or '（看履约记录）'}"
        )
    if acceptance != "accepted":
        raise SystemExit(
            f"[round] {name} 被拒（acceptance={acceptance}）——链条停在这儿。\n"
            f"         它说清了什么：{reasons or '（产出里没有未知项，去看履约记录）'}"
        )
    return artifact


def _line(name: str, run: dict[str, Any]) -> str:
    result = _artifact(run)
    bits = [f"{name}: {run.get('status')}/{run.get('acceptance_status')}"]
    for key in ("qualified_sessions", "bottleneck_stage", "hypothesis",
                "landing_page", "applied", "record_kind"):
        if key in result:
            bits.append(f"{key}={result[key]}")
    return "  " + " | ".join(str(b) for b in bits)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑一轮店铺经营闭环")
    parser.add_argument("--days", type=int, default=28, help="回看多少天（默认 28）")
    parser.add_argument("--round-id", default="", help="这一轮的号（默认按窗口推）")
    parser.add_argument("--apply", action="store_true",
                        help="让实验盒真的落笔（还需要 %s=1）" % ALLOW_WRITES_ENV)
    parser.add_argument("--store", default="logs/task_runs.jsonl",
                        help="履约记录落盘位置")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args(argv)

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

    if args.apply and os.environ.get(ALLOW_WRITES_ENV, "").strip() != "1":
        print(
            f"要落笔，但 {ALLOW_WRITES_ENV} 不是 1——"
            "「配了写权限」和「允许写」是两件事，后者必须显式开。",
            file=sys.stderr,
        )
        return 2

    start, end = _window(args.days)
    round_id = args.round_id or _round_id(domain, end)
    store = TaskRunStore(args.store)

    print(f"[round] 轮次={round_id} 窗口={start}..{end}", file=sys.stderr)

    # ---- 1) 快照 ----
    snapshot = trigger_task(
        SNAPSHOT, "capture_store_snapshot",
        {
            "round_id": round_id,
            "store_domain": domain,
            "window_start": start,
            "window_end": end,
        },
        store,
    )
    print(_line("快照", snapshot), file=sys.stderr)
    snap_result = _require_result("快照盒", snapshot)

    # ---- 2) 诊断（模型判定）----
    diagnosis = trigger_task(
        DIAGNOSIS, "diagnose_traffic", {}, store,
        handoff=handoff_payload(SCOPE_ID, DIAGNOSIS, {SNAPSHOT: snapshot["task_run_id"]}),
        chain_ref=round_id, chain_kind="operating_round",
    )
    print(_line("诊断", diagnosis), file=sys.stderr)
    diag_result = _require_result("诊断盒", diagnosis)

    # ---- 3) 获客实验（设计 + 落笔）----
    experiment = trigger_task(
        EXPERIMENT, "run_acquisition_experiment",
        {"apply": bool(args.apply)}, store,
        handoff=handoff_payload(SCOPE_ID, EXPERIMENT, {DIAGNOSIS: diagnosis["task_run_id"]}),
        chain_ref=round_id, chain_kind="operating_round",
    )
    print(_line("实验", experiment), file=sys.stderr)
    exp_result = _artifact(experiment)
    if experiment.get("acceptance_status") != "accepted":
        if args.apply:
            # 真投放却没成 → **停下**，别把这一轮记成做成了
            _require_result("实验盒", experiment)
        # 干跑：这一轮本来就不交付实验段（no_deliver），链条继续往下走
        print("[round] 实验盒：干跑（没要求落笔）→ 方案已出，这一轮不交付实验段",
              file=sys.stderr)

    # ---- 4) 经营档案：三次投递，同一个 round_id → 一条记录 ----
    archived = []
    # **实验段只在真的落笔、真的交付时才归档**——
    # 干跑的方案没交付，它不该混进「这一轮经营发生了什么」。
    parts = [(snapshot, "快照"), (diagnosis, "诊断")]
    if experiment.get("acceptance_status") == "accepted":
        parts.append((experiment, "实验"))
    for upstream, label in parts:
        archived.append(trigger_task(
            ARCHIVE, "archive_operating_round",
            {"round_id": round_id}, store,
            handoff=handoff_payload(SCOPE_ID, ARCHIVE, {upstream["box_id"]: upstream["task_run_id"]}),
            chain_ref=round_id, chain_kind="operating_round",
        ))
    for run in archived:
        kind = _artifact(run).get("record_kind", "?")
        print(_line(f"档案({kind})", run), file=sys.stderr)

    if args.json:
        print(json.dumps({
            "round_id": round_id,
            "window": [start, end],
            "applied": bool(args.apply),
            "snapshot": snap_result,
            "diagnosis": diag_result,
            "experiment": exp_result,
            "archive": [r.get("result") for r in archived],
        }, ensure_ascii=False, indent=2))
        return 0

    print()
    print("=" * 60)
    print(f"轮次 {round_id}｜窗口 {start}..{end}")
    print("-" * 60)
    print(f"合格访问：{snap_result.get('qualified_sessions')} / "
          f"全部真人 session {snap_result.get('total_sessions')}")
    print(f"断点：{diag_result.get('bottleneck_stage')}｜"
          f"假设：{diag_result.get('hypothesis')}")
    print(f"落地页：{exp_result.get('landing_page')}｜"
          f"落笔：{exp_result.get('applied')}")
    for change in exp_result.get("changes") or []:
        print(f"  · {change}")

    # **写前 → 写后**：写营业中的页面，必须让人一眼看到改了什么
    baseline = exp_result.get("baseline_fields") or {}
    applied = exp_result.get("applied_fields") or {}
    if applied:
        print("  —— 写前 → 写后 ——")
        for key in ("title", "descriptionHtml", "seo.title", "seo.description", "tags"):
            before, after = baseline.get(key), applied.get(key)
            if before == after:
                continue
            b = str(before)
            a = str(after)
            print(f"    {key}:")
            print(f"      旧：{b[:140]}{'…' if len(b) > 140 else ''}")
            print(f"      新：{a[:140]}{'…' if len(a) > 140 else ''}")
    if not exp_result.get("applied"):
        draft = exp_result.get("draft") or {}
        if draft:
            print("  （没落笔——下面是那份施工单，review 之后再开 --apply）")
            print(f"    标题：{draft.get('title')}")
            print(f"    SEO：{draft.get('seo.title')}")
            body = str(draft.get('descriptionHtml') or '')
            print(f"    正文：{body[:160]}{'…' if len(body) > 160 else ''}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
