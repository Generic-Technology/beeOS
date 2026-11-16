"""tests.test_shopify_page_choice —— 「下一轮改哪一页」的规则

**这是整条链里最要紧的一条规则**：选错页，这一轮的实验就白做。
所以它必须**可复现**（规则算的，不问模型）、**有测试**。

三条策略：

| 策略 | 选谁 |
|---|---|
| `conversion_lift` | 有成交的商品页优先（意图已经在那儿了） |
| `ai_referral` | 有 AI 引荐流量但零成交的商品页 |
| `demand_match` | **买家在站内搜的词最像它、但它零成交**的商品页 |

## 数据是照着真店的实际形状写的

「personalized globe lamp with speaker」那 13 次搜索、
以及那个 2 session / 0 成交的 3D 灯页，都是店里真有的。
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
            # 流量大但零成交
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
        ],
        "traffic_pools": [
            {"referrer_source": "direct", "agentic_referring_channel": "ChatGPT",
             "landing_page_path": "/products/personalized-santa-sack-embroidered-name",
             "sessions": 11, "sessions_that_completed_checkout": 0},
            {"referrer_source": "direct", "agentic_referring_channel": "",
             "landing_page_path": "/products/custom-fair-isle-christmas-stocking",
             "sessions": 13, "sessions_that_completed_checkout": 3},
        ],
        "site_search_terms": [
            {"query": "personalized globe lamp w", "searches": 1},
            {"query": "personalized globe lam", "searches": 1},
            {"query": "santa", "searches": 2},
            {"query": "santa hat", "searches": 1},
            {"query": "stocking", "searches": 1},
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
    """**买家搜的词最像它、但它零成交** → 那一页就是需求没接住的地方"""
    handle, note = diag.choose_landing_page(_snapshot(strategy="demand_match"))
    assert handle == (
        "custom-photo-3d-lamp-with-bluetooth-music-player-personalized-3d-light-"
        "wedding-anniversary-birthday-gifts"
    ), note
    assert "demand_match" in note


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
    # 让那个灯页有成交：它就不该再被选中
    for row in snapshot["landing_funnel"]:
        if "3d-lamp-with-bluetooth" in row["landing_page_path"]:
            row["sessions_that_completed_checkout"] = 2
    handle, _ = diag.choose_landing_page(snapshot)
    assert handle == (
        "custom-3d-moon-lamp-personalized-gift-with-your-pet-photo-desk-light-"
        "3-colors-with-stand-rechargeable-for-pet-lovers"
    )


def test_demand_match_ignores_pages_without_traffic():
    """**没流量的页改了也没人看见**"""
    snapshot = _snapshot(strategy="demand_match")
    for row in snapshot["landing_funnel"]:
        if "3d-lamp-with-bluetooth" in row["landing_page_path"]:
            row["sessions"] = 0
    handle, note = diag.choose_landing_page(snapshot)
    assert "3d-lamp-with-bluetooth" not in handle, note


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
    assert "3d-lamp-with-bluetooth" in handle


# ============================================================
# 这一轮要接住的买家用词
# ============================================================


def test_the_demand_words_come_from_what_buyers_typed():
    """**词从买家搜的词里来**——不是我们自己想出来的"""
    snapshot = _snapshot()
    demand = diag._demand_keywords(snapshot, (
        "custom-photo-3d-lamp-with-bluetooth-music-player-personalized-3d-light-"
        "wedding-anniversary-birthday-gifts"
    ))
    assert demand["query"] == "personalized globe lamp w", demand
    assert set(demand["keywords"]) == {"personalized", "lamp"}, demand


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
