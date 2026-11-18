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

from fulfillment_scopes.shopify.vocabulary import (
    BOTTLENECK_STAGES,
    SEARCH_CTR_MAX_POSITION,
)


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


def _int(value: Any) -> int:
    """**排序用的数字**——取不到就当 0

    这个数只用来**排序**（哪个需求词更值得追），
    不作为观测值报进产出——所以 0 在这里是「排最后」，
    不是「测到了 0」。
    """
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
    # **全部落地页**（`landing_funnel`），不只是前 25 名——
    # 模型选中的那一页未必在流量前 25 里
    # （「买家搜了却没搜到」的那一页，流量往往很小）。
    page_rows = list(snapshot.get("landing_funnel") or []) or list(
        snapshot.get("top_landing_pages") or []
    )
    for row in page_rows:
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


#: **虚词**——不算「像」，也不该塞进标题
#:
#: 「personalized globe lamp **with** speaker」里的 with，
#: 写进标题没人会搜；而它出现在 handle 里会让匹配分虚高。
_STOPWORDS = frozenset({
    "with", "and", "for", "the", "your", "from", "of", "in", "on", "to",
    "my", "me", "by", "at", "is", "it", "this", "that", "or", "a", "an",
    "be", "are", "was", "so", "if", "as", "no", "not", "do", "does",
})


def _tokens(text: str) -> set[str]:
    """把一段文字切成可比的小写词

    **只用字母数字分词**——中文/标点连在一起，
    「globe lamp」和「globe-lamp」得算同一个词。
    **虚词剔掉**——「with」出现在两边不算「像」。
    """
    raw = str(text or "").lower()
    out: set[str] = set()
    buf: list[str] = []
    for ch in raw:
        if ch.isalnum():
            buf.append(ch)
        else:
            if len(buf) >= 2:
                out.add("".join(buf))
            buf = []
    if len(buf) >= 2:
        out.add("".join(buf))
    return out - _STOPWORDS


#: **通用词**——它们几乎出现在每个 handle、每条搜索词里
#: （personalized / custom / gift / photo / mom …）。
#:
#: 它们**不算「像」**：拿它们凑够门槛就是在制造巧合。
#:
#: 实测踩到的坑：「personalized globe lamp w」命中「月亮灯页」，
#: 靠的就是 {personalized, lamp}——`personalized` 谁都有，
#: 那个页面跟买家搜的「globe lamp」根本不是一回事。
#: 把它剔掉之后，命中才算真命中。
_GENERIC_TOKENS = frozenset({
    # 定制形式
    "personalized", "personalisation", "personalization", "personalise",
    "personalize", "custom", "customized", "customised", "customize",
    "customise", "gift", "gifts", "present", "presents", "photo", "photos",
    "picture", "pictures", "diy", "made", "make", "unique", "special",
    "beautiful", "best", "top", "new", "online", "shop", "wholesale", "bulk",
    # 收礼的人（不是产品特征）
    "mom", "mum", "dad", "him", "her", "they", "them", "kids", "kid",
    "baby", "babies", "wife", "husband", "boyfriend", "girlfriend", "parent",
    "parents", "family", "employee", "employees", "colleague", "colleagues",
    "teacher", "boss", "friend", "friends", "customer", "customers",
    "everyone", "anyone", "someone", "lover", "lovers",
})

#: **匹配门槛**：命中的内容词至少这么多个
MATCH_MIN_WORDS = 2

#: **匹配门槛**：查询里的内容词，至少被页面覆盖这么高
#:
#: 为什么还要覆盖率：查询比页面更具体时不该硬套。
#: 「personalized globe lamp with speaker」里的 {globe, lamp, speaker}，
#: 一个只卖 lamp 的页面覆盖 1/3 = 33% → 那是**别的东西**。
MATCH_MIN_COVERAGE = 0.75


def _stem(word: str) -> str:
    """**词干**——只去尾部的 s

    买家搜「crystal music **boxes**」，页面写的是「…music **box**」——
    那是同一个东西。

    **只比较时用词干，输出时保留原词**：
    把词干塞进词集里会造出「christma」这种幽灵词
    （christmas → christma）——它能跟任何带 christma 的东西对上，
    还会被写进标题，那不是词。
    """
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _demand_match(query: str, handle: str) -> tuple[int, float, set[str]]:
    """**这个词有多像这一页**——(命中数, 覆盖率, 命中的词)

    规则（**只看字面，不猜语义**）：
    - 取查询与页面各自的**内容词**（通用词不算）；
    - 按**词干**比对（单复数算同一个）；
    - 要求命中 ≥ 2 个内容词，**且**覆盖查询内容词的 75% 以上。

    **为什么要有覆盖率**：查询比页面更具体时不能硬套——
    「globe lamp with speaker」不该落到一个只卖 lamp 的页面上。
    """
    q_words = _tokens(query) - _GENERIC_TOKENS
    if not q_words:
        return 0, 0.0, set()
    p_stems = {_stem(w) for w in (_tokens(handle) - _GENERIC_TOKENS)}
    hit = {w for w in q_words if _stem(w) in p_stems}
    if not hit:
        return 0, 0.0, set()
    return len(hit), len(hit) / len(q_words), hit


#: 站外平台的**人话名字**——产出里写「gsc」谁也看不懂
_PLATFORM_LABELS = {
    "gsc": "Google 搜索",
    "reddit": "Reddit",
    "etsy": "Etsy",
    "pinterest": "Pinterest",
}


def _demand_terms(snapshot: dict[str, Any]) -> list[tuple[str, str, int]]:
    """**买家打过的字**——站内 + 站外，两边一起

    | 来源 | 是什么 | 说明 |
    |---|---|---|
    | 站内 | `site_search_terms` | **已经进了店**的买家打的字（搜了、没买成） |
    | 站外 | `market_signals` | **还没进店**的人在 Google 等地方搜的字 |

    **为什么要合起来看**：两边的字是同一种东西——「买家想要什么」。
    区别只在**阶段**：
    站内的字说明**需求已经到门口了但没接住**（改页面立刻能接）；
    站外的字说明**门外的需求**（改标题让它能被搜到 → 未来的访客）。

    **站内的优先级更高**（离成交更近），排序时排在前面。

    返回 `(词, 来源, 需求大小)`——
    **需求大小**是「有多少人搜过这个词」：
    站内是搜索次数，站外是展示数（搜了多少次）。
    同样命中两个词时，**需求大的那个词**才是值得追的。
    """
    out: list[tuple[str, str, int]] = []
    for term in snapshot.get("site_search_terms") or []:
        query = str(term.get("query") or "").strip()
        if query:
            out.append((query, "站内搜索", _int(term.get("searches"))))
    for signal in snapshot.get("market_signals") or []:
        topic = str(signal.get("topic") or "").strip()
        if topic:
            platform = str(signal.get("platform") or "").strip()
            label = _PLATFORM_LABELS.get(platform, platform or "站外")
            out.append((topic, f"站外（{label}）", _int(signal.get("occurrences"))))
    return out


def _demand_matched_page(
    snapshot: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[str, int, str, str] | None:
    """**买家搜的词最像它、但它零成交**的那一页

    **用买家打过的字**（站内的 + 站外的，见 `_demand_terms`）——
    那是**真实需求**，比任何爬来的线索都强。

    **怎么算「像」**：买家搜的词 ∩ 这一页的 handle（标题的代理）
    命中的词越多，说明这一页就是他搜的那个东西。
    **只看字面，不猜语义**——「crystal ball」是不是「globe」，
    规则不猜（猜错了就会改错页）。

    **门槛**：至少命中 2 个**内容词**，且覆盖查询内容词的 75%。
    通用词（personalized / custom / gift / mom）**不算命中**——
    拿它们凑数就是在制造巧合（见 `_GENERIC_TOKENS`）。

    **排序**：先看**没改过的**（改过的要留观察期），
    再看零成交（需求在那儿却没接住），再看**站内词优先**（离成交更近），
    再看**命中数**，再看**需求大小**（这个词有多少人搜过），
    最后看 session 数。

    返回 `(handle, 命中词数, 买家的那个词, 那个词的来源)`。
    """
    terms = _demand_terms(snapshot)
    if not terms:
        return None

    # 候选页：**有流量**的（没流量的页改了也没人看见）
    candidates = [r for r in rows if r["sessions"] > 0]
    if not candidates:
        return None

    best: tuple[str, int, str, str] | None = None
    best_key: tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0)
    for row in candidates:
        for query, source, demand in terms:
            score, coverage, _hit = _demand_match(query, row["handle"])
            if score < MATCH_MIN_WORDS or coverage < MATCH_MIN_COVERAGE:
                continue
            # **没改过的优先**（改过的要留观察期）
            # **零成交优先**：有成交的页面说明它接住了需求
            fresh = 0 if row.get("recently_changed") else 1
            zero_sales = 1 if row["sales"] == 0 else 0
            # **站内词优先**（离成交更近）；站外词一样能选，只是排后
            in_store = 1 if source == "站内搜索" else 0
            key = (fresh, zero_sales, in_store, score, demand, row["sessions"])
            if key > best_key:
                best_key = key
                best = (row["handle"], score, query, source)
    return best


def _proven_queries(snapshot: dict[str, Any], handle: str) -> list[str]:
    """**Google 说这一页是为哪些词排的**——不用我们猜

    「词 × 页」是 **Google 自己的语义匹配**：
    买家搜「globe lamp」、Google 知道是那个「3d lamp」页在排。
    我们的词面规则接不住这种**同物异名**，**但 Google 接得住**。

    所以：页面信号里带着的词，**直接采信**（不再过词面门槛）。
    """
    if not handle:
        return []
    path = f"/products/{handle}"
    for signal in snapshot.get("market_page_signals") or []:
        signal_path = str(signal.get("path") or "").strip().rstrip("/")
        if signal_path != path:
            continue
        words = [str(q).strip() for q in (signal.get("queries") or []) if str(q).strip()]
        if not words:
            single = str(signal.get("query") or "").strip()
            words = [single] if single else []
        return words
    return []


def _content_tokens(text: str) -> set[str]:
    """**内容词**——去掉通用修饰词的那些"""
    return _tokens(text) - _GENERIC_TOKENS


def _demand_keywords(snapshot: dict[str, Any], handle: str) -> dict[str, Any]:
    """**这一轮要接住的买家用词**

    **从买家搜的词里来**——不是我们自己想出来的词。
    命中目标页的那几个词，就是买家打字时用的词；
    页面照着这些词写，站内搜索、Google 和 AI 引荐才接得上。

    - `query`：买家打的那一句（原样）
    - `keywords`：那句话里的词（去掉 1 个字母的碎片）
    - `source`：这个词来自哪儿（站内搜索 / 站外 GSC 等）

    **站内 + 站外一起看**（见 `_demand_terms`）——
    站外的词（GSC）正是「还没进店的人怎么搜的」，
    把它们写进标题，就是给未来的访客铺路。

    找不到就返回空——**没有需求词就说没有**，
    不许拿店里的品类词来充数（那是另一回事）。
    """
    if not handle:
        return {}
    # **Google 自己排的那个词**——采信它，不再过词面门槛
    # （同物异名只能靠它：买家搜 globe lamp，页面写 3d lamp）
    proven = _proven_queries(snapshot, handle)
    if proven:
        query = proven[0]
        page_words = _content_tokens(handle)
        keep = sorted(_content_tokens(query) & page_words)
        return {
            "query": query,
            "keywords": keep or sorted(_content_tokens(query)),
            "source": "站外（Google 把你排在这儿的）",
        }
    best: tuple[int, str, set[str], str] | None = None
    best_key: tuple[int, int, int] = (-1, -1, -1)
    for query, source, demand in _demand_terms(snapshot):
        score, coverage, hit = _demand_match(query, handle)
        if score < MATCH_MIN_WORDS or coverage < MATCH_MIN_COVERAGE:
            continue
        # **与选页用同一套排序**（站内优先 → 命中数 → 需求大小），
        # 这样「为什么选它」和「往标题里写什么词」永远说的是同一件事。
        key = (1 if source == "站内搜索" else 0, score, demand)
        if key > best_key:
            best_key = key
            best = (score, query, hit, source)
    if best is None:
        return {}
    _score, query, hit, source = best
    return {"query": query, "keywords": sorted(hit), "source": source}


def _page_fact(snapshot: dict[str, Any], handle: str) -> str:
    """**这一页在站外是什么样子**——一句话，给下游照着改

    例：「站外 219 次展示、0 点击、平均排名 15.14」
    """
    if not handle:
        return ""
    path = f"/products/{handle}"
    best: dict[str, Any] | None = None
    for signal in snapshot.get("market_page_signals") or []:
        signal_path = str(signal.get("path") or "").strip().rstrip("/")
        if signal_path != path:
            continue
        if best is None or _int(signal.get("impressions")) > _int(best.get("impressions")):
            best = signal
    if best is None:
        return ""
    parts = []
    impressions = _int(best.get("impressions"))
    clicks = _int(best.get("clicks"))
    if impressions:
        parts.append(f"{impressions} 次展示")
    if clicks:
        parts.append(f"{clicks} 次点击")
    else:
        parts.append("0 点击")
    position = best.get("position")
    if position is not None:
        parts.append(f"平均排名 {position}")
    query = str(best.get("query") or "").strip()
    if query:
        parts.append(f"排在它前面的词是「{query}」")
    return "站外 " + "、".join(parts) if parts else ""


def _search_ctr_page(
    snapshot: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[str, str] | None:
    """**站外有展示、却没人点**的那一页

    **为什么它要单独立一条**：这类页面的证据不是「买家搜了它」，
    而是「Google 已经把它摆给人看了，只是没人点进来」——
    **药方也不同**：不是加需求词，是**把标题/描述写成买家会点的那句话**
    （说清是什么、送谁、多少钱、什么时候到）。

    **门槛**：有展示 + **0 点击** + 排名在**推得动**的范围内。
    排太靠后是**排名问题**（词不够相关），改标题救不回来——
    两种药方不同，别拿改文案的力气去打排名。

    **排序**：展示多的先（空白最大的先），再比排名近的。
    """
    facts: dict[str, dict[str, Any]] = {}
    for signal in snapshot.get("market_page_signals") or []:
        path = str(signal.get("path") or "").strip().rstrip("/")
        if not path or "/products/" not in path:
            continue
        impressions = _int(signal.get("impressions"))
        clicks = _int(signal.get("clicks"))
        if impressions <= 0 or clicks > 0:
            continue
        raw_position = signal.get("position")
        position = float(raw_position) if raw_position is not None else None
        if position is not None and position > SEARCH_CTR_MAX_POSITION:
            continue
        current = facts.get(path)
        if current is None or impressions > current["impressions"]:
            facts[path] = {
                "impressions": impressions,
                "position": position,
                "query": str(signal.get("query") or "").strip(),
            }
    if not facts:
        return None

    candidates: list[tuple[int, float, str, dict[str, Any]]] = []
    for row in rows:
        path = f"/products/{row['handle']}"
        fact = facts.get(path)
        if fact is None:
            continue
        # **展示多的先，再比排名近的**
        candidates.append((-fact["impressions"], fact["position"] or 999.0,
                           row["handle"], fact))
    if not candidates:
        return None
    candidates.sort()
    _neg, position, handle, fact = candidates[0]
    return handle, (
        f"按策略 search_ctr 选：{handle}"
        f"（站外 {fact['impressions']} 次展示、0 点击、平均排名 {position}"
        f"——Google 把它摆在那儿了，只是没人点）"
    )


def choose_landing_page(
    snapshot: dict[str, Any],
    exclude_handles: list[str] | None = None,
) -> tuple[str, str]:
    """**下一轮该改哪个落地页——规则说了算，不交给模型**

    **三种策略**（这一轮打哪种仗由投料声明，见 `TARGET_STRATEGIES`）：

    - `conversion_lift`（默认）：**有成交的商品页**优先。
      那些页面已经在成交了，改承接的边际收益最大，也最不容易白改。
    - `ai_referral`：**有 AI 引荐流量但零成交**的商品页优先——
      用来验证「AI 引荐来的流量能不能变成成交」：
      那些页面已经在被 AI 引荐了，只是没接住。
    - `demand_match`：**买家搜的词最像它、但它零成交**的商品页优先。
      站内的搜索词是**真实买家**打出来的：
      搜了 → 点了 → 没买成 = 需求在那儿，页面没接住。
    - `search_ctr`：**站外有展示、却 0 点击**的商品页优先。
      Google 已经把它摆给人看了，只是没人点进来
      → 标题/描述没接住（药方：写成买家会点的那句话）。

    **为什么不交给模型**：实测它挑了「11 sessions / 0 成交」那个页面，
    而「11 sessions / 1 成交」的圣诞袜页才是销冠——
    **流量大 ≠ 值得改**。有成交的页面说明意图已经在那儿了，
    承接一改，转化提升的边际收益更大。

    **为什么策略要显式**：换了假设，规则不会自己跟着变。
    藏在代码里的规则会继续按上一个假设选页。

    **`exclude_handles`（最近改过的页）**：它们要留观察期——
    改完立刻再改一次，等于没让效果显出来，实验就白做了。
    **是「排后」不是「排除」**：全排除了这一轮就什么都不做，
    那还不如改一个刚改过的页。

    返回 `(handle, 说明)`。
    """
    strategy = str(snapshot.get("strategy") or "conversion_lift")
    excluded = {str(h) for h in (exclude_handles or []) if str(h)}
    rows = []
    # **`landing_funnel` 优先**：它有**全部**落地页，
    # 而 `top_landing_pages` 只有流量前 25 名——
    # 「买家搜了却没搜到」的那一页，流量往往很小，排不进前 25。
    page_rows = list(snapshot.get("landing_funnel") or []) or list(
        snapshot.get("top_landing_pages") or []
    )
    for row in page_rows:
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
            # **最近改过的页排后**（不是排除——全排除了这一轮就什么都不做）
            "recently_changed": handle in excluded,
        })
    if not rows:
        return "", "快照里没有能改的商品页（首页与集合页不在写权限内）"

    by_handle = {r["handle"] for r in rows}

    if strategy == "search_ctr":
        # **站外有曝光、但店里一个 session 都没有的页**也要进候选——
        # 「有展示、0 点击」的页正是那种页（它压根不会被 Shopify 的
        # 落地页漏斗收录，因为没人点进来）。
        # 只从漏斗里挑的话，**最该改的那一页压根看不见**。
        for signal in snapshot.get("market_page_signals") or []:
            path = str(signal.get("path") or "").strip().rstrip("/")
            if "/products/" not in path:
                continue
            handle = path.split("/products/", 1)[1].strip("/")
            if not handle or handle in by_handle:
                continue
            by_handle.add(handle)
            rows.append({
                "handle": handle,
                "sales": 0,
                "sessions": 0,
                "recently_changed": handle in excluded,
            })

    # **观察期压过策略偏好**：先在**没改过的页**里选。
    # 理由：改过的页要留时间看效果；
    # 而且规则是确定性的，数据没变就还会选中同一页——
    # 不让观察期生效，这个循环就永远卡在一页上。
    # 全都改过 → 退回全部（宁可改一个刚改过的页，也不要什么都不做）。
    fresh = [r for r in rows if not r["recently_changed"]]
    pool_all = fresh or rows

    if strategy == "search_ctr":
        picked = _search_ctr_page(snapshot, pool_all)
        if picked:
            return picked
        strategy = "conversion_lift"   # 没有站外可见却没人点的页 → 退回
    if strategy == "demand_match":
        picked = _demand_matched_page(snapshot, pool_all)
        if picked:
            handle, score, query, source = picked
            return handle, (
                f"按策略 demand_match 选：{handle}"
                f"（买家{source}的「{query}」最像它，命中 {score} 个词，"
                f"但它零成交）"
            )
        strategy = "conversion_lift"  # 没有匹配上的页 → 退回
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
        # **只在这个策略自己的候选里挑**（有 AI 引荐流量、零成交），
        # 且**只挑没改过的**；都没有 → 退回 conversion_lift（在没改过的页里）
        ai_leaks = {h: s for h, s in ai_leaks.items() if h not in excluded}
        if ai_leaks:
            handle = max(ai_leaks, key=lambda h: ai_leaks[h])
            return handle, (
                f"按策略 ai_referral 选：{handle}"
                f"（AI 引荐 {ai_leaks[handle]} 个 session、零成交）"
            )
        strategy = "conversion_lift"  # 没有这类页面 → 退回

    with_sales = [r for r in pool_all if r["sales"] > 0]
    pool = with_sales or pool_all
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
        # **最近改过的页**（编排层从履约历史里数出来的）——
        # 它们要留观察期，这一轮优先改别的。
        "exclude_landing_pages": [
            str(h) for h in (input_data.get("exclude_landing_pages") or []) if str(h)
        ],
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
            handle, note = choose_landing_page(
                snapshot,
                exclude_handles=list(input_data.get("exclude_landing_pages") or []),
            )
            model_pick = conclusion.get("target_landing_page") or ""
            if handle and model_pick and model_pick != handle:
                conclusion["unknowns"] = list(conclusion.get("unknowns") or []) + [
                    f"落地页由规则选：{note}；"
                    f"模型挑的是 {model_pick}（它不知道哪个页面在成交）"
                ]
            conclusion["target_landing_page"] = handle
            # **这一轮要接住的买家用词**——从买家搜的词里来，
            # 下游照着它写页面（不是我们自己想出来的词）。
            demand = _demand_keywords(snapshot, handle)
            return {
                "round_id": input_data["round_id"],
                "snapshot_ref": snapshot,
                "conclusion": conclusion,
                # **漏水的池子在 op2 算**（这里才有快照），
                # 往下带——op3 手里只有观察结果。
                "leaking_pools": leaking_pools(snapshot),
                "strategy": str(snapshot.get("strategy") or ""),
                "target_demand_query": demand.get("query") or "",
                "target_keywords": demand.get("keywords") or [],
                # **这个词是从哪儿来的**——站内搜索（需求到门口了）
                # 还是站外 GSC（门外的需求）——读的人要知道差在哪
                "target_demand_source": demand.get("source") or "",
                # **这一页在站外是什么样子**——
                # 下游要照着它改（search_ctr 时：「有展示、0 点击」= 标题没接住）
                "target_page_fact": _page_fact(snapshot, handle),
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
        # **买家搜的那句话 + 这一轮要接住的词**（demand_match 时才有）
        "target_demand_query": str(input_data.get("target_demand_query") or ""),
        "target_keywords": list(input_data.get("target_keywords") or []),
        "target_demand_source": str(input_data.get("target_demand_source") or ""),
        # **这一页在站外的样子**（站外有展示、0 点击 → 标题没接住）
        "target_page_fact": str(input_data.get("target_page_fact") or ""),
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
