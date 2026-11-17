"""tests.test_shopify_page_choice —— 「下一轮改哪一页」的规则

**这是整条链里最要紧的一条规则**：选错页，这一轮的实验就白做。
所以它必须**可复现**（规则算的，不问模型）、**有测试**。

三条策略：

| 策略 | 选谁 |
|---|---|
| `conversion_lift` | 有成交的商品页优先（意图已经在那儿了） |
| `ai_referral` | 有 AI 引荐流量但零成交的商品页 |
| `demand_match` | **买家搜的词最像它、但它零成交**的商品页 |

## 数据是照着真店的实际形状写的

站内的搜索词（真店 2026-10-21~11-17 导出的原话）：
`santa sack`、`santa hat`、`personalized globe lamp with speaker`……
站外的词（真店 GSC 导出的原话）：
`clone my pet stuffed animal wholesale`、`crystal music box`……

**规则的两条硬门槛**（都是被真数据教出来的）：

1. **通用词不算命中**——`personalized / custom / gift / mom` 几乎每个页面都有，
   拿它们凑够 2 个就是在制造巧合；
2. **覆盖率 ≥ 75%**——查询比页面具体时不能硬套
   （买家搜 `globe lamp with speaker`，页面只有 `lamp` → 那不是它）。
"""
from __future__ import annotations

from typing import Any

from boxes.shopify_traffic_diagnosis import runner as diag


def _snapshot(**over: Any) -> dict[str, Any]:
    """一份照着真店形状编的快照"""
    base: dict[str, Any] = {
        "strategy": "conversion_lift",
        "landing_funnel": [
            # 销冠：13 session / 3 成交
            {"landing_page_path": "/products/custom-fair-isle-christmas-stocking",
             "sessions": 13, "sessions_that_completed_checkout": 3},
            # 流量大但零成交——买家在站内搜过「santa sack」
            {"landing_page_path": "/products/personalized-santa-sack-embroidered-name",
             "sessions": 11, "sessions_that_completed_checkout": 0},
            # **买家反复搜的那一页**：2 session / 0 成交
            {"landing_page_path": (
                "/products/custom-photo-3d-lamp-with-bluetooth-music-player-"
                "personalized-3d-light-wedding-anniversary-birthday-gifts"
            ),
             "sessions": 2, "sessions_that_completed_checkout": 0},
            # 另一个也沾边的灯页：1 session / 0 成交
            {"landing_page_path": (
                "/products/custom-3d-moon-lamp-personalized-gift-with-your-pet-photo-"
                "desk-light-3-colors-with-stand-rechargeable-for-pet-lovers"
            ),
             "sessions": 1, "sessions_that_completed_checkout": 0},
            # 另一只圣诞袜：4 session / 0 成交（搜过「stocking christmas」）
            {"landing_page_path": "/products/personalized-christmas-stocking-classic",
             "sessions": 4, "sessions_that_completed_checkout": 0},
            # 圣诞老人帽：1 session / 1 成交
            {"landing_page_path": "/products/personalized-santa-hat-embroidered-name",
             "sessions": 1, "sessions_that_completed_checkout": 1},
        ],
        "traffic_pools": [
            {"referrer_source": "direct", "agentic_referring_channel": "ChatGPT",
             "landing_page_path": "/products/personalized-santa-sack-embroidered-name",
             "sessions": 11, "sessions_that_completed_checkout": 0},
            {"referrer_source": "direct", "agentic_referring_channel": "",
             "landing_page_path": "/products/custom-fair-isle-christmas-stocking",
             "sessions": 13, "sessions_that_completed_checkout": 3},
        ],
        # **真店导出的原话**（含完整短语，不只是被截断的前缀）
        "site_search_terms": [
            {"query": "santa sack", "searches": 1},
            {"query": "santa hat", "searches": 1},
            {"query": "santa", "searches": 2},
            {"query": "stocking", "searches": 1},
            {"query": "stocking christmas", "searches": 1},
            {"query": "personalized globe lamp with speaker", "searches": 1},
        ],
    }
    base.update(over)
    return base


# ============================================================
# conversion_lift
# ============================================================


def test_conversion_lift_picks_the_page_that_already_sells():
    """**流量大 ≠ 值得改**——有成交的页面说明意图已经在那儿了"""
    handle, note = diag.choose_landing_page(_snapshot())
    assert handle == "custom-fair-isle-christmas-stocking", note
    assert "conversion_lift" in note


def test_conversion_lift_uses_the_top_landing_pages_when_there_is_no_funnel():
    """老快照没有 `landing_funnel` → 退回前 N 名那一版"""
    snapshot = _snapshot()
    snapshot["top_landing_pages"] = snapshot.pop("landing_funnel")
    handle, _ = diag.choose_landing_page(snapshot)
    assert handle == "custom-fair-isle-christmas-stocking"


# ============================================================
# ai_referral
# ============================================================


def test_ai_referral_picks_the_page_with_ai_traffic_and_no_sales():
    handle, note = diag.choose_landing_page(_snapshot(strategy="ai_referral"))
    assert handle == "personalized-santa-sack-embroidered-name", note
    assert "ai_referral" in note


# ============================================================
# demand_match —— 这一轮打的是「站内的真实需求」
# ============================================================


def test_demand_match_picks_the_page_the_buyers_searched_for():
    """**买家搜的词最像它、但它零成交** → 那一页就是需求没接住的地方

    买家在站内搜了「santa sack」→ 圣诞老人袜袋页（11 session / 0 成交）。
    """
    handle, note = diag.choose_landing_page(_snapshot(strategy="demand_match"))
    assert handle == "personalized-santa-sack-embroidered-name", note
    assert "demand_match" in note


def test_the_store_wording_does_not_match_the_buyers_wording():
    """**词面匹配接不住「同物异名」**——这是**已知**的边界，不是 bug

    买家搜「personalized globe lamp with speaker」，
    我们写的是「custom photo 3d lamp with bluetooth music player」——
    `globe`/`speaker` 两个词对不上，只剩 `lamp` 一个内容词。

    **所以词面规则选不出这一页**（门槛：≥2 个内容词 + 覆盖率 ≥75%）。
    这也正是这一轮实验要干的事：**把买家的词写进标题**，
    让词面对上——下一轮它就能被选中了。
    """
    snapshot = _snapshot(strategy="demand_match")
    # 只留那条同物异名的搜索词
    snapshot["site_search_terms"] = [
        {"query": "personalized globe lamp with speaker", "searches": 1},
    ]
    handle, note = diag.choose_landing_page(snapshot)
    assert "3d-lamp-with-bluetooth" not in handle, note
    assert "demand_match" not in note      # 没有匹配上 → 退回默认打法


def test_the_generic_words_do_not_count_as_a_match():
    """**通用词凑数 = 巧合**——`personalized custom gift` 谁都像"""
    snapshot = _snapshot(strategy="demand_match")
    snapshot["site_search_terms"] = [
        {"query": "personalized custom gift", "searches": 5},
    ]
    handle, note = diag.choose_landing_page(snapshot)
    assert "demand_match" not in note, note


def test_a_more_specific_query_does_not_land_on_a_looser_page():
    """**查询比页面具体时不能硬套**——覆盖率不够就不算「像」"""
    snapshot = _snapshot(strategy="demand_match")
    snapshot["site_search_terms"] = [
        # 圣诞老人袜袋页有 {santa, sack, embroidered, name}，但这条查询要 5 个词
        {"query": "santa sack embroidered burlap rustic", "searches": 3},
    ]
    handle, note = diag.choose_landing_page(snapshot)
    assert "demand_match" not in note, note


def test_demand_match_needs_at_least_two_matching_words():
    """**命中 1 个词多半是巧合**——很多 handle 都带 personalized"""
    snapshot = _snapshot(strategy="demand_match")
    # 只剩一个共同词（personalized）→ 不算「像」
    snapshot["site_search_terms"] = [{"query": "personalized", "searches": 3}]
    handle, note = diag.choose_landing_page(snapshot)
    # 退回 conversion_lift（有成交的那页）
    assert handle == "custom-fair-isle-christmas-stocking", note
    assert "demand_match" not in note


def test_demand_match_prefers_a_page_with_no_sales():
    """**零成交优先**——有成交说明它接住了需求"""
    snapshot = _snapshot(strategy="demand_match")
    # 让圣诞老人袜袋页有成交：它就不该再被选中
    for row in snapshot["landing_funnel"]:
        if "santa-sack" in row["landing_page_path"]:
            row["sessions_that_completed_checkout"] = 2
    handle, _ = diag.choose_landing_page(snapshot)
    assert handle == "personalized-christmas-stocking-classic"


def test_demand_match_ignores_pages_without_traffic():
    """**没流量的页改了也没人看见**"""
    snapshot = _snapshot(strategy="demand_match")
    for row in snapshot["landing_funnel"]:
        if "santa-sack" in row["landing_page_path"]:
            row["sessions"] = 0
    handle, note = diag.choose_landing_page(snapshot)
    assert "santa-sack" not in handle, note


def test_demand_match_without_search_terms_falls_back():
    """没有站内搜索词 → 退回默认打法（**不假装有需求**）"""
    snapshot = _snapshot(strategy="demand_match", site_search_terms=[])
    handle, note = diag.choose_landing_page(snapshot)
    assert handle == "custom-fair-isle-christmas-stocking", note


def test_demand_match_sees_pages_beyond_the_top_25():
    """**「买家搜了却没搜到」的那一页，流量往往很小**——排不进前 25 名"""
    snapshot = _snapshot(strategy="demand_match")
    # 只把前两页放进 top_landing_pages（模拟前 N 名截断）
    snapshot["top_landing_pages"] = snapshot["landing_funnel"][:2]
    handle, _ = diag.choose_landing_page(snapshot)
    assert "santa-sack" in handle


# ============================================================
# 站外需求（GSC）：门外的买家搜的词
# ============================================================
#
# **站内的词说明「需求到门口了」，站外的词说明「门外的需求」**
# ——两者是同一种东西（买家想要什么），只是阶段不同。
# 站外的词进了闭环，改标题就能被搜到 → 那是**未来的访客**。

PET_HANDLE = (
    "custom-stuffed-animal-of-your-dog-cat-handmade-pet-plush-clone-from-photo"
)
CRYSTAL_HANDLE = (
    "spinning-luminous-crystal-ball-music-box-girl-birthday-gift-photos-diy-"
    "custom-christmas-decoration"
)


def _offsite_snapshot(**over: Any) -> dict[str, Any]:
    """一份带**站外需求词**的快照（词与数字都取自真店的 GSC 导出）"""
    snapshot = _snapshot(strategy="demand_match", **over)
    snapshot["landing_funnel"] = [
        {"landing_page_path": "/products/custom-fair-isle-christmas-stocking",
         "sessions": 13, "sessions_that_completed_checkout": 3},
        {"landing_page_path": f"/products/{PET_HANDLE}",
         "sessions": 5, "sessions_that_completed_checkout": 0},
        {"landing_page_path": f"/products/{CRYSTAL_HANDLE}",
         "sessions": 2, "sessions_that_completed_checkout": 0},
    ]
    snapshot["site_search_terms"] = []
    snapshot["market_signals"] = [
        {"platform": "gsc", "topic": "personalized pet gifts", "occurrences": 59},
        {"platform": "gsc", "topic": "custom pet gifts", "occurrences": 37},
        {"platform": "gsc", "topic": "crystal music box", "occurrences": 27},
        {"platform": "gsc", "topic": "clone my pet stuffed animal wholesale",
         "occurrences": 25},
    ]
    return snapshot


def test_an_offsite_term_can_pick_the_page():
    """**门外的需求也能选页**——搜「clone my pet stuffed animal」的人要找的就是它"""
    handle, note = diag.choose_landing_page(_offsite_snapshot())
    assert handle == PET_HANDLE, note
    assert "站外" in note
    assert "Google" in note, note          # 别写「gsc」——没人看得懂


def test_the_offsite_term_needs_two_matching_words_too():
    """门槛一视同仁——1 个内容词多半是巧合"""
    snapshot = _offsite_snapshot()
    # 「pet」只有一个内容词——它对得上 6 个宠物页面，说不清是哪一个
    snapshot["market_signals"] = [
        {"platform": "gsc", "topic": "pet", "occurrences": 59},
    ]
    handle, note = diag.choose_landing_page(snapshot)
    assert handle == "custom-fair-isle-christmas-stocking", note
    assert "demand_match" not in note


def test_the_inside_term_wins_over_the_offsite_one():
    """**站内优先**——站内的需求已经到门口了，离成交更近"""
    snapshot = _offsite_snapshot()
    # 站内搜的那个词，命中另一个页
    snapshot["site_search_terms"] = [
        {"query": "fair isle christmas stocking", "searches": 3},
    ]
    snapshot["landing_funnel"][0]["sessions_that_completed_checkout"] = 0
    handle, note = diag.choose_landing_page(snapshot)
    assert handle == "custom-fair-isle-christmas-stocking", note
    assert "站内" in note


def test_the_bigger_demand_wins_between_two_offsite_terms():
    """**需求大的词才是值得追的**——59 次搜索比 10 次搜索值钱"""
    snapshot = _offsite_snapshot()
    snapshot["market_signals"] = [
        {"platform": "gsc", "topic": "clone my pet stuffed animal wholesale",
         "occurrences": 25},
        {"platform": "gsc", "topic": "crystal music box", "occurrences": 10},
    ]
    handle, note = diag.choose_landing_page(snapshot)
    assert handle == PET_HANDLE, note


def test_the_source_travels_with_the_demand_words():
    """**读的人要知道这个词是从哪儿来的**——站内还是站外，差很远"""
    snapshot = _offsite_snapshot()
    demand = diag._demand_keywords(snapshot, PET_HANDLE)
    assert demand["source"] == "站外（Google 搜索）", demand
    assert demand["query"] == "clone my pet stuffed animal wholesale", demand
    assert set(demand["keywords"]) == {"clone", "pet", "stuffed", "animal"}, demand


def test_an_offsite_term_that_matches_nothing_falls_back():
    """**没有匹配就退回**——不许硬把一个词塞给不相干的页"""
    snapshot = _offsite_snapshot()
    snapshot["market_signals"] = [
        {"platform": "gsc", "topic": "secret santa generator", "occurrences": 32},
    ]
    handle, note = diag.choose_landing_page(snapshot)
    assert handle == "custom-fair-isle-christmas-stocking", note
    assert "demand_match" not in note


def test_the_exact_product_phrase_beats_a_bigger_but_looser_demand():
    """**「crystal music box」这样的整名匹配，比 59 次的泛需求更该动手**

    「personalized pet gifts」有 59 次展示，但那是**品类词**——
    它对得上 6 个宠物页面，说不清是哪一个；
    「crystal music box」只对得上那一个页面，而且那正是页面的整名。
    """
    snapshot = _offsite_snapshot()
    snapshot["market_signals"] = [
        {"platform": "gsc", "topic": "personalized pet gifts", "occurrences": 59},
        {"platform": "gsc", "topic": "crystal music box", "occurrences": 27},
    ]
    handle, note = diag.choose_landing_page(snapshot)
    assert handle == CRYSTAL_HANDLE, note


# ============================================================
# 这一轮要接住的买家用词
# ============================================================

def test_the_demand_words_come_from_what_buyers_typed():
    """**词从买家搜的词里来**——不是我们自己想出来的"""
    snapshot = _snapshot()
    demand = diag._demand_keywords(snapshot, "personalized-santa-sack-embroidered-name")
    assert demand["query"] == "santa sack", demand
    assert set(demand["keywords"]) == {"santa", "sack"}, demand


def test_the_demand_words_are_the_content_words_only():
    """**写进标题的词要是内容词**——`personalized` 写进去没人会搜"""
    snapshot = _snapshot()
    demand = diag._demand_keywords(snapshot, "personalized-santa-sack-embroidered-name")
    assert "personalized" not in demand["keywords"], demand


def test_no_demand_words_means_no_demand_words():
    """**没有就返回空**——不许拿店里的品类词来充数"""
    snapshot = _snapshot()
    demand = diag._demand_keywords(snapshot, "some-page-nobody-searched-for")
    assert demand == {}


def test_the_tokens_survive_punctuation_and_case():
    """「Globe-Lamp」和「globe lamp」得算同一个词"""
    assert diag._tokens("Personalized Globe-Lamp, with Speaker!") == {
        "personalized", "globe", "lamp", "speaker",
    }


def test_the_filler_words_are_dropped():
    """**虚词不算「像」**——「with」出现在两边不算命中"""
    assert "with" not in diag._tokens("a lamp with speaker")
    assert "for" not in diag._tokens("gift for her")


# ============================================================
# 观察期：改过的页要留时间看效果
# ============================================================


def test_a_recently_changed_page_is_not_picked_again():
    """**改完立刻再改一次，等于没让效果显出来**

    规则是确定性的，数据没变就还会选中同一页——
    所以「最近改过」必须参与排序。
    """
    snapshot = _snapshot(strategy="demand_match")
    handle, note = diag.choose_landing_page(
        snapshot,
        exclude_handles=[
            "custom-photo-3d-lamp-with-bluetooth-music-player-"
            "personalized-3d-light-wedding-anniversary-birthday-gifts",
        ],
    )
    assert "3d-lamp-with-bluetooth" not in handle, note


def test_the_observation_window_is_a_preference_not_a_veto():
    """**全排除了就什么都不做**——那还不如改一个刚改过的页"""
    snapshot = _snapshot(strategy="demand_match")
    every_page = [
        "custom-fair-isle-christmas-stocking",
        "personalized-santa-sack-embroidered-name",
        "custom-photo-3d-lamp-with-bluetooth-music-player-"
        "personalized-3d-light-wedding-anniversary-birthday-gifts",
        "custom-3d-moon-lamp-personalized-gift-with-your-pet-photo-"
        "desk-light-3-colors-with-stand-rechargeable-for-pet-lovers",
    ]
    handle, note = diag.choose_landing_page(snapshot, exclude_handles=every_page)
    assert handle, "全都排除时也得选一个——否则这一轮什么都不做"
    assert note


def test_conversion_lift_also_prefers_an_unchanged_page():
    """**三条策略都要守观察期**"""
    snapshot = _snapshot(strategy="conversion_lift")
    handle, _ = diag.choose_landing_page(
        snapshot, exclude_handles=["custom-fair-isle-christmas-stocking"],
    )
    assert handle != "custom-fair-isle-christmas-stocking"


def test_ai_referral_also_prefers_an_unchanged_page():
    snapshot = _snapshot(strategy="ai_referral")
    handle, note = diag.choose_landing_page(
        snapshot, exclude_handles=["personalized-santa-sack-embroidered-name"],
    )
    assert handle != "personalized-santa-sack-embroidered-name", note
