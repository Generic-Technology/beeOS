"""boxes.shopify_traffic_diagnosis.runner —— 获客诊断盒的三跳实现

op 链：
  1. `diagnosis_intake`    接住这一轮的快照
  2. `diagnosis_work`      **模型判定断点**（带产出校验）
  3. `diagnosis_packager`  装成一个假设 + 证据 + 未知项

## 为什么断点判定走模型

四段漏斗（曝光 → 点击 → session → 商品浏览）里，
**曝光和点击没有数据源**：Shopify 的 `sessions` schema 只给 session 侧。
所以「断在哪一段」这个问题，规则算不出来——规则只能算 session 侧那几段。

模型能读快照说人话，但模型**会编**。所以它的产出必须被校验：

| 模型说 | 校验 | 不通过怎么办 |
|---|---|---|
| `bottleneck_stage` | 必须在四段（+「分不出单一断点」）里 | 整个结论作废 |
| `bottleneck_evidence` | 每一条都必须是快照里**真实存在的指标名** | **整份结论作废**（重试一次）|
| `hypothesis` | 只能有一个，不能空 | 整个结论作废 |
| `unknowns` | 「分不出单一断点」时必须非空 | 整个结论作废 |

**模型不产出任何数字**：它只引用指标名，不报数值——
数字由快照盒算，模型报了也不作数（同一个量只能有一个来源）。

## 校验不过 = 没有结论

不是「凑合收下」，也不是「降级成规则版」：
**结论作废 → 产出空结论 → 判据全红 → 整轮被拒 → 不进档案**。

带一次**带反馈的重试**：把「你上次哪一条不合格」写回提示词再问一遍。
只重试一次——再错就是这件事这轮做不成，不该无限问下去。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from runtime.llm import LLMError, complete_json
from runtime.runner import HandlerRunner

from fulfillment_scopes.shopify.vocabulary import BOTTLENECK_STAGES


#: 模型的回答格式——**字段名和产出契约对齐**，少一个字段就作废
_DIAGNOSIS_PROMPT = """\
你是一家 Shopify 店铺（卖个性化定制礼物）的获客诊断员。
下面是这个店**这一轮窗口**的只读快照事实。

【快照事实】
{facts}

【要判断的事】
流量断在哪一段。只有这几段可选：
- no_impression：没有曝光（分发 / SEO / 内容 / 广告没启动）
- no_click：有曝光没点击（素材 / 受众 / 卖点不匹配）
- no_session：有点击没 session（链接 / 追踪 / 落地页有问题）
- no_product_view：有 session 没商品浏览（落地页与商品承接有问题）
- no_single_bottleneck：分不出单一断点（数据量不够时选它）

【硬要求】
1. 只给**一个**断点。
2. bottleneck_evidence 里每一条都必须是从【快照事实】的 metrics 里**原样抄来的指标名**。
   抄不出来就别写——**不许编指标名**，编出来的证据比没有证据更坏。
3. hypothesis 写**一个**主问题假设。不要写三个，不要写「可能 A 也可能 B」。
4. unknowns 写这次判不出来的地方；选了 no_single_bottleneck 时**必须**写。
5. **不许断言「商品被 AI 引用」**。你只能看到「有多少 session 是被 AI 引荐来的」，
   那是**引荐来源**，不是「被 AI 引用」的证据。这两件事不是一回事。
6. target_landing_page 从【快照事实】的 top_landing_pages 里挑一个
   （取路径里 /products/ 后面那段 handle）；没有就留空字符串。
7. **不要输出任何数字**——数值由快照盒算，你只引用指标名。
8. 只用 JSON 回答，格式：
{{"bottleneck_stage": "...", "bottleneck_evidence": ["..."],
  "hypothesis": "...", "target_customer": "...", "target_scenario": "...",
  "prioritized_channel": "...", "target_landing_page": "...",
  "unknowns": ["..."]}}
"""

#: 校验不过时，把问题写回去再问一次（**只一次**）
_RETRY_PROMPT = """\
你上一次的回答不合格，问题如下：

{problems}

请重新回答，**逐条改正上面这些问题**。
其余要求不变：只给一个断点；证据只能引用【快照事实】里真实存在的指标名；
只给一个假设；不许断言「被 AI 引用」；不要输出数字；只用 JSON。

【快照事实】
{facts}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _facts_for_prompt(snapshot: dict[str, Any]) -> str:
    """给模型看的快照事实（只给事实，不给它结论）

    **不给它断点枚举之外的引导**——枚举在提示词里写着，
    这里是「有哪些数字可用」。
    """
    import json

    facts = {
        "metrics": snapshot.get("metrics") or [],
        "top_landing_pages": snapshot.get("top_landing_pages") or [],
        "top_sources": snapshot.get("top_sources") or [],
        "top_search_terms": snapshot.get("top_search_terms") or [],
        # **流量池**：来源 × AI 渠道 × 落地页，各自带 session 与成交
        "traffic_pools": snapshot.get("traffic_pools") or [],
        # **日级序列**：改完之后看方向用的
        "daily_trend": snapshot.get("daily_trend") or [],
        # **买家在站内搜什么** + 搜索漏斗——真实买家的需求信号
        "site_search_terms": snapshot.get("site_search_terms") or [],
        "search_funnel": snapshot.get("search_funnel") or {},
        # **站外的公开需求**（上一跳市场信号盒带来）
        "market_signals": snapshot.get("market_signals") or [],
        "unverified": snapshot.get("unverified") or [],
    }
    return json.dumps(facts, ensure_ascii=False, indent=2)


def _metric_names(snapshot: dict[str, Any]) -> set[str]:
    """快照里真实存在的指标名——**证据只能引用这些**"""
    names: set[str] = set()
    for metric in snapshot.get("metrics") or []:
        name = str(metric.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _landing_handles(snapshot: dict[str, Any]) -> set[str]:
    """落地页候选的 handle（从 `/products/<handle>` 里取）"""
    handles: set[str] = set()
    for row in snapshot.get("top_landing_pages") or []:
        path = str(row.get("landing_page_path") or "")
        if "/products/" in path:
            handle = path.split("/products/", 1)[1].strip("/")
            if handle:
                handles.add(handle)
    return handles


def leaking_pools(snapshot: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    """**有流量但没成交的池子**——规则算的，不问模型

    「哪个池子在漏水」是**算得出来的**：有 session、成交为 0 的池子就是。
    算得出来的事不要交给模型（它可能编、也可能漏）。

    返回按 session 降序的池子列表。
    """
    out = []
    for pool in snapshot.get("traffic_pools") or []:
        sessions = int(pool.get("sessions") or 0)
        sales = int(pool.get("sessions_that_completed_checkout") or 0)
        if sessions <= 0 or sales > 0:
            continue
        out.append({
            "referrer_source": str(pool.get("referrer_source") or ""),
            "agentic_referring_channel": str(pool.get("agentic_referring_channel") or ""),
            "landing_page_path": str(pool.get("landing_page_path") or ""),
            "sessions": sessions,
        })
    out.sort(key=lambda r: r["sessions"], reverse=True)
    return out[:limit]


def choose_landing_page(snapshot: dict[str, Any]) -> tuple[str, str]:
    """**下一轮该改哪个落地页——规则说了算，不交给模型**

    **两条策略**（这一轮打哪种仗由投料声明，见 `TARGET_STRATEGIES`）：

    - `conversion_lift`（默认）：**有成交的商品页**优先。
      那些页面已经在成交了，改承接的边际收益最大，也最不容易白改。
    - `ai_referral`：**有 AI 引荐流量但零成交**的商品页优先——
      用来验证「AI 引荐来的流量能不能变成成交」：
      那些页面已经在被 AI 引荐了，只是没接住。

    **为什么不交给模型**：实测它挑了「11 sessions / 0 成交」那个页面，
    而「11 sessions / 1 成交」的圣诞袜页才是销冠——
    **流量大 ≠ 值得改**。有成交的页面说明意图已经在那儿了，
    承接一改，转化提升的边际收益更大。

    **为什么策略要显式**：换了假设，规则不会自己跟着变。
    藏在代码里的规则会继续按上一个假设选页。

    返回 `(handle, 说明)`。
    """
    strategy = str(snapshot.get("strategy") or "conversion_lift")
    rows = []
    for row in snapshot.get("top_landing_pages") or []:
        path = str(row.get("landing_page_path") or "")
        if "/products/" not in path:
            continue
        handle = path.split("/products/", 1)[1].strip("/")
        if not handle:
            continue
        rows.append({
            "handle": handle,
            "sales": int(row.get("sessions_that_completed_checkout") or 0),
            "sessions": int(row.get("sessions") or 0),
        })
    if not rows:
        return "", "快照里没有能改的商品页（首页与集合页不在写权限内）"

    by_handle = {r["handle"] for r in rows}

    if strategy == "ai_referral":
        # **有 AI 引荐流量但零成交**的商品页
        ai_leaks: dict[str, int] = {}
        for pool in snapshot.get("traffic_pools") or []:
            channel = str(pool.get("agentic_referring_channel") or "")
            path = str(pool.get("landing_page_path") or "")
            if not channel or "/products/" not in path:
                continue
            handle = path.split("/products/", 1)[1].strip("/")
            if handle not in by_handle:
                continue
            if int(pool.get("sessions_that_completed_checkout") or 0) > 0:
                continue
            ai_leaks[handle] = ai_leaks.get(handle, 0) + int(pool.get("sessions") or 0)
        if ai_leaks:
            handle = max(ai_leaks, key=lambda h: ai_leaks[h])
            return handle, (
                f"按策略 ai_referral 选：{handle}"
                f"（AI 引荐 {ai_leaks[handle]} 个 session、零成交）"
            )
        strategy = "conversion_lift"  # 没有这类页面 → 退回

    with_sales = [r for r in rows if r["sales"] > 0]
    pool = with_sales or rows
    best = max(pool, key=lambda r: (r["sales"], r["sessions"]))
    handle, sales, sessions = best["handle"], best["sales"], best["sessions"]

    if with_sales:
        note = f"按策略 {strategy} 选：{handle}（成交 {sales}、sessions {sessions}）"
    else:
        note = f"没有任何商品页有成交，按流量选：{handle}（sessions {sessions}）"
    return handle, note


# ============================================================
# op 1：接住这一轮的快照
# ============================================================


def diagnosis_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：接住这一轮的快照，说清这一跳要查哪几段"""
    snapshot = input_data.get("snapshot_ref")
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError(
            "投料里没有 snapshot_ref——获客诊断盒靠交接通道接上游的快照，"
            "没有它就没有可诊断的事实"
        )

    round_id = str(snapshot.get("round_id") or "").strip()
    if not round_id:
        raise ValueError("上游快照没有 round_id——诊断归不到任何一轮经营")

    return {
        "round_id": round_id,
        "stages_to_check": list(BOTTLENECK_STAGES),
        "snapshot_ref": snapshot,
    }


# ============================================================
# op 2：模型判定
# ============================================================


def diagnosis_work_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：模型判定断点 + **校验它的产出**"""
    snapshot = input_data["snapshot_ref"]
    facts = _facts_for_prompt(snapshot)

    raw: Any = None
    problems: list[str] = []
    for attempt in range(2):
        prompt = (
            _DIAGNOSIS_PROMPT.format(facts=facts)
            if attempt == 0
            else _RETRY_PROMPT.format(facts=facts, problems="; ".join(problems))
        )
        try:
            raw = complete_json(
                prompt,
                thinking="disabled",
                max_tokens=2000,
                timeout=120,
            )
        except LLMError as exc:
            # 模型调不通 = 这轮没有判定——不是「判定为无断点」
            return {
                "round_id": input_data["round_id"],
                "snapshot_ref": snapshot,
                "conclusion": None,
                "reject_reason": f"模型调用失败：{exc}",
            }

        conclusion, problems = _validate_conclusion(raw, snapshot)
        if not problems:
            # **落地页按规则定，不按模型说的定**——
            # 流量大 ≠ 值得改；规则选「有成交的商品页」，可复现、有背书。
            handle, note = choose_landing_page(snapshot)
            model_pick = conclusion.get("target_landing_page") or ""
            if handle and model_pick and model_pick != handle:
                conclusion["unknowns"] = list(conclusion.get("unknowns") or []) + [
                    f"落地页由规则选：{note}；"
                    f"模型挑的是 {model_pick}（它不知道哪个页面在成交）"
                ]
            conclusion["target_landing_page"] = handle
            return {
                "round_id": input_data["round_id"],
                "snapshot_ref": snapshot,
                "conclusion": conclusion,
                # **漏水的池子在 op2 算**（这里才有快照），
                # 往下带——op3 手里只有观察结果。
                "leaking_pools": leaking_pools(snapshot),
                "strategy": str(snapshot.get("strategy") or ""),
                "reject_reason": "",
            }
        if attempt == 0:
            continue  # 带反馈再问一次

    # 两次都不合格 → **没有结论**
    return {
        "round_id": input_data["round_id"],
        "snapshot_ref": snapshot,
        "conclusion": None,
        "reject_reason": "模型产出两次都不合格：" + "; ".join(problems),
    }


def _validate_conclusion(
    raw: Any, snapshot: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    """校验模型产出——**不合格的部分剔掉，剔空了整份作废**"""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return None, ["模型输出不是 JSON 对象"]

    stage = str(raw.get("bottleneck_stage") or "").strip()
    if stage not in BOTTLENECK_STAGES:
        problems.append(
            f"bottleneck_stage={stage!r} 不在合法取值里（{', '.join(BOTTLENECK_STAGES)}）"
        )

    known_metrics = _metric_names(snapshot)
    cited = [
        str(e).strip()
        for e in (raw.get("bottleneck_evidence") or [])
        if str(e).strip()
    ]
    valid_evidence = [e for e in cited if e in known_metrics]
    dropped = [e for e in cited if e not in known_metrics]
    if dropped:
        # **编出来的指标名是硬伤**：它说明模型没在读快照。
        # 这份结论整份作废（带反馈重试一次）——不是「剔掉那一条、其余照收」。
        # 一个会编测量值的模型，它的其余结论也不能当依据。
        problems.append(
            "引用了快照里不存在的指标：" + "、".join(dropped)
        )
    if not valid_evidence:
        problems.append("没有一条证据指得到快照里的真实指标")

    hypothesis = str(raw.get("hypothesis") or "").strip()
    if not hypothesis:
        problems.append("hypothesis 是空的")

    target_customer = str(raw.get("target_customer") or "").strip()
    target_scenario = str(raw.get("target_scenario") or "").strip()
    prioritized_channel = str(raw.get("prioritized_channel") or "").strip()
    for name, value in (
        ("target_customer", target_customer),
        ("target_scenario", target_scenario),
        ("prioritized_channel", prioritized_channel),
    ):
        if not value:
            problems.append(f"{name} 是空的")

    unknowns = [
        str(u).strip() for u in (raw.get("unknowns") or []) if str(u).strip()
    ]
    if stage == "no_single_bottleneck" and not unknowns:
        problems.append("选了 no_single_bottleneck 却没写 unknowns")

    handle = str(raw.get("target_landing_page") or "").strip()
    known_handles = _landing_handles(snapshot)
    if handle and known_handles and handle not in known_handles:
        problems.append(
            f"target_landing_page={handle!r} 不在快照的落地页清单里"
        )

    if problems:
        return None, problems

    return {
        "bottleneck_stage": stage,
        "bottleneck_evidence": valid_evidence,
        "hypothesis": hypothesis,
        "target_customer": target_customer,
        "target_scenario": target_scenario,
        "prioritized_channel": prioritized_channel,
        "target_landing_page": handle,
        "unknowns": unknowns,
    }, []


# ============================================================
# op 3：装成产出
# ============================================================


def diagnosis_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：装成产出

    **没有结论时产出空结论**：结论字段缺席（不是空串充数），
    标志位全 false → 判据全红 → 整轮被拒 → 不进档案。
    """
    conclusion = input_data.get("conclusion")
    round_id = input_data["round_id"]

    if not conclusion:
        reason = str(input_data.get("reject_reason") or "这一轮没有结论")
        return {
            "round_id": round_id,
            "strategy": str(input_data.get("strategy") or ""),
            "observed_at": _now(),
            "unknowns": [reason],
            "snapshot_ref_present": True,
            "hypothesis_present": False,
            "evidence_validated": False,
        }

    return {
        "round_id": round_id,
        "observed_at": _now(),
        "bottleneck_stage": conclusion["bottleneck_stage"],
        "bottleneck_evidence": conclusion["bottleneck_evidence"],
        "hypothesis": conclusion["hypothesis"],
        "target_customer": conclusion["target_customer"],
        "target_scenario": conclusion["target_scenario"],
        "prioritized_channel": conclusion["prioritized_channel"],
        "target_landing_page": conclusion["target_landing_page"],
        "unknowns": conclusion["unknowns"],
        # **规则算的漏水池子**——和模型的判断分开摆，各说各的来路
        "leaking_pools": input_data.get("leaking_pools") or [],
        "strategy": str(input_data.get("strategy") or ""),
        "snapshot_ref_present": True,
        "hypothesis_present": True,
        "evidence_validated": True,
    }


SHOPIFY_TRAFFIC_DIAGNOSIS_HANDLERS: dict[str, Any] = {
    "diagnosis_intake": diagnosis_intake_handler,
    "diagnosis_work": diagnosis_work_handler,
    "diagnosis_packager": diagnosis_packager_handler,
}

RUNNER = HandlerRunner(
    SHOPIFY_TRAFFIC_DIAGNOSIS_HANDLERS, owner="shopify_traffic_diagnosis_box"
)
