"""boxes.shopify_acquisition_experiment.runner —— 获客实验盒的三跳实现

op 链：
  1. `experiment_intake`    接住这一轮的诊断
  2. `experiment_work`      模型起草内容 → **真写上去**（受写闸管）
  3. `experiment_packager`  装成方案 + 落笔结果

## 「直接投放」到底指什么

首轮实验是**自然渠道内容实验**：改的是店铺自己的落地页内容
（标题 / 正文 / SEO 标题与描述 / 标签）。
自然渠道没有「投放 API」——**唯一能被自动化的投放，就是改自己的页面**。

所以本盒的职责是：**自己把内容改上去**，不再交一份施工单等人手动改。

## 四道闸（都在 `shopify_writer.py` 里）

写闸、字段白名单、一次一个落地页、改前基线——见那个模块的说明。
**写闸关着的时候**：本盒只出方案，`applied = False`，
判据 `applied eq True` 因此判红 → 整轮被拒 → 不进档案。

**「出方案但没落笔」不算交付**——这是「直接投放」的判据。

## 模型起草的内容也要校验

模型起草的标题/正文进白名单之前要过三道：

1. 标题非空、不过长（Shopify 自己会拒，但先拦一道更省事）；
2. 正文非空；
3. **只留白名单字段**——模型多写的字段直接丢掉。

草稿不合格 → 没有草稿 → 不写 → 判据判红。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from runtime.llm import LLMError, complete_json
from runtime.runner import HandlerRunner

from boxes.shopify_acquisition_experiment.shopify_writer import (
    ShopifyWriteError,
    ShopifyWriter,
    WriteNotAllowed,
)
from fulfillment_scopes.shopify.vocabulary import ATTRIBUTION_METHODS


#: 标题 / 正文的长度上限（Shopify 自己的限制——先拦一道省一次往返）
MAX_TITLE = 255
MAX_DESCRIPTION = 65535

#: 模型起草用的提示词
_DRAFT_PROMPT = """\
你在给一家卖个性化定制礼物的 Shopify 店改**一个商品页**的内容。

【这一轮要改的】
目标客群：{customer}
购买场景：{scenario}
优先渠道：{channel}
断点判断：{bottleneck}
主问题假设：{hypothesis}
当前标题：{current_title}
当前正文（前 800 字）：{current_description}
买家搜的词（站内搜索 / Google 搜索）：{demand_query}
这一轮要接住的词：{demand_keywords}

【硬要求】
0. **用商品页现在的语言写**——照着「当前标题 / 当前正文」是什么语言，
   就用什么语言（这家店的商品页是英文的，就写英文）。
   **不要用提问者的语言写**，顾客看的是商品页，不是我们。
1. 只改**这一个商品页**。
2. 产出 5 个字段：
   - title：标题。写清楚「个性化 + 礼物 + 场合」，别堆关键词。
   - descriptionHtml：正文（HTML 片段）。要包含：这是什么、怎么个性化、
     送谁合适、什么时候要下单（圣诞有交付期）、怎么下单。
     正文里**不许出现未经证实的承诺**（比如「24 小时送达」「100% 满意」）。
   - seoTitle：SEO 标题，**不超过 70 个字符**，
     **而且不能和 title 一字不差**——
     Google 里标题与 SEO 标题挨着显示，写成一样就白白浪费了一条位置；
     换个说法，把「这一轮要接住的词」自然地放进去。
   - seoDescription：SEO 描述，**不超过 160 个字符**。
     **它就是 Google 搜索结果里给买家看的那句话**——
     所以原来描述里的**硬事实要留着**（价格、尺寸/规格、适用人数、
     款数字体数、交付截止日）：那是买家点进来的理由。
     在原描述上**加需求词**，别重写成一句没有信息量的广告语。
   - tags：3 到 6 个标签。
3. **不许编造**：没给的信息不要写（材质、尺寸、退货政策、价格一律不要编）。
4. 只用 JSON 回答：
{{"title": "...", "descriptionHtml": "...", "seoTitle": "...",
  "seoDescription": "...", "tags": ["..."]}}

【买家搜的词怎么用】
5. 「这一轮要接住的词」是**真实买家打出来的字**（店里的站内搜索，
   或 Google 搜索里他们搜的词）——
   标题和正文要**自然地包含这些词**，
   这样买家搜同样的词时才搜得到我们（站内搜索、搜索引擎、AI 引荐都靠这个）。
6. **但不许为了塞词而写假话**：只有当那个词说的确实是这个商品时才能用。
   买家搜「globe lamp」而我们卖的是「月亮灯」——
   就写「lamp with speaker」「round base」这些**真的成立的**词，
   **不要写 globe**。宁可少一个词，不许骗人。
7. **不要把原来正文里的事实弄丢**：尺寸、制作时间、运费与时效、
   FAQ 里的问答——**要改的是措辞和结构，不是把信息删了**。
   「当前正文」里写了的事实，**照抄进去**（数字必须一模一样）。

【上一版的问题】
{feedback}
"""

#: 写完之后产出里要记的字段
_APPLIED_FIELDS = ("title", "descriptionHtml", "seo.title", "seo.description", "tags")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# op 1：接住这一轮的诊断
# ============================================================


def experiment_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：接住这一轮的诊断，说清这一跳要动哪个落地页"""
    diagnosis = input_data.get("diagnosis_ref")
    if not isinstance(diagnosis, dict) or not diagnosis:
        raise ValueError(
            "投料里没有 diagnosis_ref——获客实验盒靠交接通道接上游的诊断，"
            "没有它就没有可实验的假设"
        )

    round_id = str(diagnosis.get("round_id") or "").strip()
    if not round_id:
        raise ValueError("上游诊断没有 round_id——实验归不到任何一轮经营")

    handle = str(diagnosis.get("target_landing_page") or "").strip()
    # **没有落地页不抛错**：它不是「投料坏了」，是「这一轮没指定改哪里」——
    # 抛错会让履约中止，而这里应该是「没有东西可做 → 判据红 → 被拒」。

    return {
        "round_id": round_id,
        "target_handle": handle,
        "apply": bool(input_data.get("apply")),
        "diagnosis_ref": diagnosis,
    }


# ============================================================
# op 2：起草 → 写
# ============================================================


def experiment_work_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：模型起草内容，然后**真写上去**（写闸关着就只出方案）"""
    diagnosis = input_data["diagnosis_ref"]
    handle = input_data["target_handle"]
    round_id = input_data["round_id"]
    apply_requested = bool(input_data.get("apply"))

    def not_applied(reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "round_id": round_id,
            # **目标页要带下去**——不然这份方案是「无头」的，
            # 读者不知道它要写到哪里去。
            "target_handle": handle,
            "diagnosis_ref": diagnosis,
            "applied": False,
            "apply_requested": apply_requested,
            "reject_reason": reason,
            **extra,
        }

    # ---- 0. 有没有页面可改 ----
    if not handle:
        return not_applied(
            "诊断没有指定 target_landing_page——"
            "首轮实验只改**一个**落地页，不知道改哪个就不能动手"
        )

    # **apply 不为真 ≠ 什么都不做**：照样读基线、照样起草，
    # 只是**不落笔**——「先看施工单再决定写不写」正是干跑要看的东西。
    # 真正拦住写入的是下面第 3 步（两道闸都开才写）。

    # ---- 1. 找商品 + 读改前基线 ----
    try:
        writer = ShopifyWriter.from_env()
    except ShopifyWriteError as exc:
        return not_applied(str(exc))

    try:
        baseline = writer.read_product(handle)
    except ShopifyWriteError as exc:
        return not_applied(f"读不到商品现状：{exc}")
    if not baseline:
        return not_applied(f"按 handle={handle!r} 没找到商品——没有基线就不敢改")

    baseline_fields = {
        "title": baseline.get("title"),
        "descriptionHtml": baseline.get("descriptionHtml"),
        "seo.title": (baseline.get("seo") or {}).get("title"),
        "seo.description": (baseline.get("seo") or {}).get("description"),
        "tags": baseline.get("tags") or [],
    }

    # ---- 2. 模型起草 ----
    demand_keywords = list(diagnosis.get("target_keywords") or [])
    prompt_base = dict(
        customer=diagnosis.get("target_customer") or "（诊断没给出客群）",
        scenario=diagnosis.get("target_scenario") or "（诊断没给场景）",
        channel=diagnosis.get("prioritized_channel") or "（诊断没给渠道）",
        bottleneck=diagnosis.get("bottleneck_stage") or "（诊断没给断点）",
        hypothesis=diagnosis.get("hypothesis") or "（诊断没给假设）",
        current_title=baseline_fields["title"] or "（空）",
        # **给完整正文**——只给前 800 字的话，模型看不见 Details / FAQ，
        # 写出来自然就把那些事实丢了（实测：尺寸、制作时间、运费全没了）。
        current_description=(baseline_fields["descriptionHtml"] or "（空）")[:4000],
        # **买家搜的那句话 + 这一轮要接住的词**——
        # 从诊断盒传下来（那边是从真实搜索词里算的）。
        demand_query=diagnosis.get("target_demand_query") or "（这轮没有对应的买家搜索词）",
        demand_keywords=", ".join(demand_keywords) if demand_keywords
        else "（这轮没有对应的买家搜索词——按商品页本身写，别硬塞词）",
    )

    # **起草两次**：第一次不合格就把问题写回提示词再问一次
    # （诊断盒也是这个做法——提示词不是闸，但反馈是）。
    draft: dict[str, Any] = {}
    problems: list[str] = []
    feedback = "（无——这是第一版）"
    for _attempt in range(2):
        prompt = _DRAFT_PROMPT.format(**prompt_base, feedback=feedback)
        try:
            raw = complete_json(prompt, thinking="disabled", max_tokens=4000, timeout=180)
        except LLMError as exc:
            return not_applied(f"模型起草失败：{exc}", baseline_fields=baseline_fields)

        draft, problems = _validate_draft(raw)
        if not problems:
            # **事实不能丢**——丢了也算不合格
            missing = _missing_facts(
                baseline_fields["descriptionHtml"] or "",
                str(draft.get("descriptionHtml") or ""),
            )
            if missing:
                problems = ["把原正文里的事实弄丢了：" + ", ".join(missing)]
        if not problems:
            # **SEO 描述里的硬事实也不能丢**——
            # 那是 Google 摘要里给买家看的理由（价格 / 尺寸 / 交付截止日）。
            # 正文守住了、描述没守住，等于把「点进来的理由」删了。
            missing_seo = _missing_seo_facts(baseline_fields, draft)
            if missing_seo:
                problems = ["SEO 描述里弄丢了这些事实：" + ", ".join(missing_seo)]
        if not problems:
            break
        # **把问题写回提示词**——再说一次，别原地作废
        feedback = (
            "上一版有这些问题，请改好再给一版：\n- "
            + "\n- ".join(problems)
            + "\n数字必须原样保留；长度必须真的在上限内（别差一点）。"
        )
    if problems:
        return not_applied(
            "模型起草不合格：" + "; ".join(problems),
            baseline_fields=baseline_fields,
            draft=draft if draft else None,
        )

    # ---- 3. 写：两道闸都开才写 ----
    #
    # **逐次意图**（投料里的 apply）+ **进程级写闸**（环境变量）。
    # 只靠环境变量那一道不够：看板的示例投料、交接链路发起的履约，
    # 都会带着环境变量跑——那样一次误触就是改在营业中的页面上。
    if not apply_requested:
        return not_applied(
            "这次没有要求落笔（apply 不为真）——方案已出，店里的页面没动。"
            "要真的写，投料里显式带 apply=true。",
            baseline_fields=baseline_fields,
            draft=draft,
        )

    try:
        result = writer.update_product(baseline["id"], _merge_with_baseline(draft, baseline_fields))
    except WriteNotAllowed as exc:
        # **写闸关着**——只出方案，不落笔。applied=False → 判据判红。
        return not_applied(
            str(exc),
            baseline_fields=baseline_fields,
            draft=draft,
        )
    except ShopifyWriteError as exc:
        return not_applied(
            f"写入失败：{exc}",
            baseline_fields=baseline_fields,
            draft=draft,
        )

    user_errors = result.get("user_errors") or []
    # **没报错 ≠ 写进去了**——
    # 实测：seo.title 跟商品标题一模一样时，Shopify 静默存成 null，
    # userErrors 是空的。所以「写成功」要看**存下来的值**。
    not_stuck = list(result.get("not_stuck") or [])
    applied_fields = {
        key: value
        for key, value in (result.get("applied_fields") or {}).items()
        if key in _APPLIED_FIELDS
    }

    reasons = list(user_errors)
    if not_stuck:
        reasons.append(
            "写了但没存进去：" + ", ".join(not_stuck)
            + "（Shopify 没报错，但存下来的值不是我们写的那个）"
        )

    return {
        "round_id": round_id,
        "target_handle": handle,
        "diagnosis_ref": diagnosis,
        # **交付 = 写进去了**（不是「没报错」）
        "applied": not reasons,
        "baseline_fields": baseline_fields,
        "applied_fields": applied_fields,
        "draft": draft,
        "user_errors": user_errors,
        "not_stuck": not_stuck,
        "reject_reason": "; ".join(reasons),
        "write_scope": "write_products",
    }


def _merge_with_baseline(draft: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """**只改你要改的，别把没打算改的抹了**

    两条踩过的坑：

    1. **seo 是整体替换语义**：只写 `seo.description`，Shopify 会把
       `seo.title` 变成空（实测：写了一次 seo，标题的 SEO 就没了）。
       → 写 seo 的一个字段，另一个必须带着（基线里的原值）。
    2. **标签是合并不是替换**：基线里有 Shopify 的分类标签
       （`For Baby + Kids` 之类）——被模型换掉，商品可能就从分类里消失了。
       → 旧标签保留，新标签追加。
    3. **seo.title 不能跟商品标题一模一样**：实测那样写，
       Shopify 会把 seo.title **静默存成 null**（还不报 userErrors）。
       → 跟标题一样时，改用基线里那个（它跟标题不一样），
         基线也没有就整个 seo 都不写（宁可不改，别写成空的）。
    """
    out = dict(draft or {})

    if "seo.title" in out and "seo.description" not in out:
        out["seo.description"] = (baseline or {}).get("seo.description") or ""
    if "seo.description" in out and "seo.title" not in out:
        out["seo.title"] = (baseline or {}).get("seo.title") or ""

    base_tags = [str(t).strip() for t in ((baseline or {}).get("tags") or []) if str(t).strip()]
    new_tags = [str(t).strip() for t in (out.get("tags") or []) if str(t).strip()]
    if new_tags:
        merged = list(base_tags)
        for tag in new_tags:
            if tag not in merged:
                merged.append(tag)
        out["tags"] = merged

    # **seo.title 跟 title 一模一样 → Shopify 会把它存成空**
    title = str(out.get("title") or "").strip()
    seo_title = str(out.get("seo.title") or "").strip()
    if title and seo_title and seo_title == title:
        fallback = str((baseline or {}).get("seo.title") or "").strip()
        if fallback and fallback != title:
            out["seo.title"] = fallback
        else:
            # **基线里也没有能用的** → 整个 seo 都不写
            # （seo 是整体替换的：只写一半会把另一边也抹掉）
            out.pop("seo.title", None)
            out.pop("seo.description", None)

    return out


def _fact_numbers(html: str) -> set[str]:
    """**正文里的事实数字**——尺寸、天数、金额、件数

    「别把没打算改的抹了」的另一面：
    重写正文的时候，**原来的事实不能跟着消失**。
    事实以数字的形式最硬（9.8 英寸、3–5 个工作日、$7.99）——
    文字可以改，数字不能凭空消失。
    """
    out: set[str] = set()
    for raw in re.findall(r"\d+(?:[.,]\d+)*(?:\s*[–—-]\s*\d+(?:[.,]\d+)*)?", str(html or "")):
        text = raw.replace(",", "")
        text = re.sub(r"\s*[–—]\s*", "-", text)
        text = text.replace("—", "-").replace("–", "-")
        if text:
            out.add(text)
    return out


def _missing_facts(baseline_html: str, draft_html: str) -> list[str]:
    """**草稿里丢了原正文哪些事实**（返回缺失的数字）"""
    before = _fact_numbers(baseline_html)
    after = _fact_numbers(draft_html)
    missing = before - after
    # 「1」这种太常见的数字不单独判（它可能是列表序号，不一定是事实）
    return sorted(missing, key=lambda t: (len(t), t))


#: **带单位的词**——它们是「事实短语」的一部分
_FACT_UNITS = (
    "in|inch|inches|people|designs|fonts|styles|sizes|business days|"
    "days|hours|colors|colours|pieces|pcs|reviews|orders"
)

#: 月份（用于「交付截止日」这种事实）
_MONTHS = (
    "January|February|March|April|May|June|July|August|September|"
    "October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)


def _fact_phrases(text: str) -> set[str]:
    """**带单位的事实短语**——价格、尺寸、人数、款数、交付截止日

    **为什么要单独一套**：SEO 描述（`seo.description`）是
    **Google 摘要里给买家看的那句话**——
    里面掉了「$41.90」「Order by Dec 1」，买家就少了一个点进来的理由。
    而「1」「3」这种裸数字在描述里到处都是，用**带单位的短语**才判得准。
    """
    raw = str(text or "")
    out: set[str] = set()
    for m in re.finditer(r"\$\s*[\d,]+(?:\.\d{1,2})?", raw):
        out.add(re.sub(r"\s+", "", m.group(0)))
    for m in re.finditer(rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}\b", raw, re.I):
        out.add(re.sub(r"\s+", " ", m.group(0)).strip())
    for m in re.finditer(rf"\b(\d+(?:\.\d+)?)\s*({_FACT_UNITS})\b", raw, re.I):
        out.add(f"{m.group(1)} {m.group(2).lower()}")
    return out


def _missing_seo_facts(baseline: dict[str, Any], draft: dict[str, Any]) -> list[str]:
    """**SEO 描述里丢了哪些硬事实**（价格 / 尺寸 / 人数 / 截止日）

    正文的事实我们已经在守；**SEO 描述的事实没人管**——
    而它才是 Google 摘要里给买家看的理由。
    """
    before: set[str] = set()
    after: set[str] = set()
    for key in ("seo.title", "seo.description"):
        before |= _fact_phrases(str(baseline.get(key) or ""))
        after |= _fact_phrases(str(draft.get(key) or ""))
    return sorted(before - after)


def _validate_draft(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """校验模型起草的内容——**不合格就整份作废**"""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["模型输出不是 JSON 对象"]

    title = str(raw.get("title") or "").strip()
    if not title:
        problems.append("标题是空的")
    elif len(title) > MAX_TITLE:
        problems.append(f"标题 {len(title)} 字，超过 Shopify 的 {MAX_TITLE} 上限")

    description = str(raw.get("descriptionHtml") or "").strip()
    if not description:
        problems.append("正文是空的")
    elif len(description) > MAX_DESCRIPTION:
        problems.append(f"正文 {len(description)} 字，超过上限")

    seo_title = str(raw.get("seoTitle") or "").strip()
    if not seo_title:
        problems.append("SEO 标题是空的")
    elif len(seo_title) > 70:
        problems.append(f"SEO 标题 {len(seo_title)} 字符，超过 70（搜索结果会截断）")

    seo_description = str(raw.get("seoDescription") or "").strip()
    if not seo_description:
        problems.append("SEO 描述是空的")
    elif len(seo_description) > 160:
        problems.append(f"SEO 描述 {len(seo_description)} 字符，超过 160")

    tags = [str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()]
    if not tags:
        problems.append("标签是空的")

    if problems:
        return {}, problems

    # **只留白名单字段**——模型多写的字段直接丢掉，不进写入载荷
    return {
        "title": title,
        "descriptionHtml": description,
        "seo.title": seo_title,
        "seo.description": seo_description,
        "tags": tags,
    }, []


# ============================================================
# op 3：装成产出
# ============================================================


def experiment_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：装成产出

    **没落笔就没有交付**：写闸关着 / 草稿不合格 / 写入失败，
    产出的标志位是 false → 判据全红 → 整轮被拒 → 不进档案。
    """
    round_id = input_data["round_id"]
    diagnosis = input_data["diagnosis_ref"]
    applied = bool(input_data.get("applied"))
    apply_requested = bool(input_data.get("apply_requested"))
    reason = str(input_data.get("reject_reason") or "")

    changes = [
        f"{key} 已更新"
        for key, value in (input_data.get("applied_fields") or {}).items()
        if value not in (None, "", [])
    ]

    if not applied:
        # **这一轮没做出实验**：方案字段**缺席**（不是空串充数），
        # 标志位全 false → 判据全红 → 整轮被拒 → 不进档案。
        # 起草的内容照样留在记录里——它就是那份施工单。
        draft = input_data.get("draft") or {}
        return {
            "round_id": round_id,
            "observed_at": _now(),
            # **方案是「无头」的就等于没有方案**——
            # 读者得知道这份文案是给哪个页面的、属于哪个场景。
            "landing_page": input_data.get("target_handle") or "",
            "scenario": diagnosis.get("target_scenario") or "",
            "changes": [
                f"{key} 待写" for key, value in draft.items() if value not in (None, "", [])
            ],
            "unverified": [reason] if reason else [],
            "diagnosis_ref_present": True,
            "cost_cap_present": False,
            "stopping_condition_present": False,
            "applied": False,
            "apply_requested": apply_requested,
            "draft": draft,
            "baseline_fields": input_data.get("baseline_fields") or {},
            "applied_fields": {},
        }

    return {
        "round_id": round_id,
        "observed_at": _now(),
        "scenario": diagnosis.get("target_scenario") or "",
        "landing_page": input_data.get("target_handle") or "",
        "changes": changes,
        "attribution_method": "landing_page_time_window",
        "cost_cap": 0.0,
        "start_at": _now(),
        "end_at": "",
        "stopping_condition": (
            "复测窗口结束后，合格访问与加购没有比基线高 → 停手，不追加变更"
        ),
        "baseline_ref": diagnosis.get("round_id") or "",
        "result_metrics": [],
        "unverified": (
            [str(input_data["reject_reason"])]
            if input_data.get("reject_reason")
            else []
        ),
        "diagnosis_ref_present": True,
        "cost_cap_present": True,
        "stopping_condition_present": True,
        "applied": applied,
        "apply_requested": apply_requested,
        # **草稿要进产出**——不然事后没人说得清我们到底写了什么
        "draft": input_data.get("draft") or {},
        "baseline_fields": input_data.get("baseline_fields") or {},
        "applied_fields": input_data.get("applied_fields") or {},
        "write_scope": input_data.get("write_scope") or "",
    }


SHOPIFY_ACQUISITION_EXPERIMENT_HANDLERS: dict[str, Any] = {
    "experiment_intake": experiment_intake_handler,
    "experiment_work": experiment_work_handler,
    "experiment_packager": experiment_packager_handler,
}

RUNNER = HandlerRunner(
    SHOPIFY_ACQUISITION_EXPERIMENT_HANDLERS,
    owner="shopify_acquisition_experiment_box",
)
