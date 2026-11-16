#!/usr/bin/env python3
"""scripts/run_shopify_round.py —— 跑一轮店铺经营闭环

**一条命令跑完这一轮**：市场信号 → 快照 → 诊断 → 获客实验 → 经营档案（三次投递）。

```
shopify_market_signal_box
        ↓  market_signal_ref
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

## 站外需求拿不到，**链照跑**

市场信号是**可选上下文**（还没配凭证 → 交不出来）。
它没交付，这一轮就只带店里的数，诊断把站外需求记成未知项——
**链不该因为「没配凭证」而断**，那是去配凭证的事，
不是「这一轮做不成」。

## 默认是**只读 + 出方案**

不加 `--apply` 时，实验盒**不写店里的页面**：它只起草内容，
产出里能看到那份施工单。真的写要同时满足两件事：

```bash
export SHOPIFY_ALLOW_WRITES=1     # 进程级写闸
python scripts/run_shopify_round.py --apply
```

外加投料里的逐次意图（`--apply` 就是它）。
**只配了写权限 token ≠ 允许写**——两件事必须分别显式开。

## 这一轮打哪种仗

`--strategy conversion_lift`（默认，有成交的商品页优先）
或 `--strategy ai_referral`（有 AI 引荐但零成交的商品页优先）。
**策略要投进快照**——诊断盒是从快照产出里读它的。

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


#: 默认关键词（店里的品类词）
DEFAULT_KEYWORDS = [
    "personalized christmas stocking",
    "pet memorial gift",
    "custom pet portrait gift",
    "personalized gift for him",
]


def _keywords_from_last_diagnosis(store: TaskRunStore) -> list[str]:
    """**从上一轮诊断里拿要找什么**——上一轮说的断点，就是这一轮要找的信号

    `list_recent` 已经按时间**倒序**（新的在前），
    所以从头扫、碰到第一条诊断就停——再往前翻就拿到更老的那轮了。

    **只取「词」，不取整句**：`target_scenario` 是一句人话
    （「访客从首页…未继续进入商品详情页…」），
    拿它去 GSC / Reddit 里搜，搜出来的是零——
    **搜的是需求词，不是假设句**。
    所以短的（≤ 40 字，像 `direct` 这样的渠道词）才收，
    长句一律丢掉，退回店里的品类词。
    """
    try:
        records = store.list_recent(limit=60)
    except Exception:
        return []
    out: list[str] = []
    for record in records:
        if record.get("box_id") != "shopify_traffic_diagnosis_box":
            continue
        artifact = record.get("artifact") or {}
        for key in ("prioritized_channel", "target_scenario"):
            value = str(artifact.get(key) or "").strip()
            if value and len(value) <= 40 and value not in out:
                out.append(value)
        if out:
            break
    return out[:4]


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
    parser.add_argument("--keywords", default="",
                        help="这一轮要找什么需求（逗号分隔）。"
                             "不给就从上一轮诊断的假设里拿")
    parser.add_argument("--strategy", default="conversion_lift",
                        choices=["conversion_lift", "ai_referral"],
                        help="这一轮打哪种仗（决定下一轮改哪个落地页）："
                             "conversion_lift=有成交的商品页优先（默认）；"
                             "ai_referral=有 AI 引荐流量但零成交的页优先")
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

    # ---- 0) 市场信号（链首）：站外的公开需求 ----
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if not keywords:
        keywords = _keywords_from_last_diagnosis(store) or DEFAULT_KEYWORDS
    market = trigger_task(
        "shopify_market_signal_box", "capture_market_signals",
        {
            "round_id": round_id,
            "keywords": keywords,
            "platforms": [],          # 空 = 不去任何地方（默认；给了就显式填）
            "window_days": args.days,
        },
        store,
        chain_ref=round_id, chain_kind="operating_round",
    )
    print(f"[round] 市场信号: {market['status']}/{market['acceptance_status']}"
          f" | 关键词={keywords}", file=sys.stderr)
    market_result = _artifact(market)
    for st in market_result.get("platform_status") or []:
        print(f"         {st.get('platform')}: {st.get('status')}"
              f" {st.get('detail')}", file=sys.stderr)
    if market_result.get("signals"):
        print(f"         取到 {len(market_result['signals'])} 条需求信号",
              file=sys.stderr)

    # ---- 1) 快照（接住市场信号）----
    #
    # **市场信号是「可选上下文」**：它没交付，链不该断。
    # 断链的理由是「没取到信号」（还没配凭证），
    # 那是要去配凭证的事，不是「这一轮做不成」。
    # 但**接上去的东西必须是真的**——所以只有它真的交付了才交接。
    market_handoff = {}
    if market.get("status") == "completed" and market.get("acceptance_status") == "accepted":
        market_handoff = handoff_payload(
            SCOPE_ID, SNAPSHOT, {"shopify_market_signal_box": market["task_run_id"]}
        )
    else:
        print("[round] 市场信号这次没交付（站外需求拿不到）"
              "→ 快照只带店里的数，诊断会把站外需求记成未知项",
              file=sys.stderr)

    snapshot = trigger_task(
        SNAPSHOT, "capture_store_snapshot",
        {
            "round_id": round_id,
            "store_domain": domain,
            "window_start": start,
            "window_end": end,
            # **策略要投进去**：诊断盒是从快照产出里读策略的，
            # 不投 → 策略永远是空 → 规则一律退回 conversion_lift，
            # 「这一轮打哪种仗」就成了命令行上的摆设。
            "strategy": args.strategy,
        },
        store,
        handoff=market_handoff,
        chain_ref=round_id, chain_kind="operating_round",
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
