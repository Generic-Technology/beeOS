"""tests.test_shopify_round_script —— 编排脚本的守卫

**这个脚本是「一轮经营」的唯一入口**，所以它自己也有几条铁律。

## 这里守的四条

1. **策略要真的投进去**：`--strategy ai_referral` 写在命令行上，
   但诊断盒是从**快照产出**里读策略的——
   不投，策略永远是空字符串，规则一律退回 `conversion_lift`，
   「这一轮打哪种仗」就成了摆设。
2. **市场信号只在它真的交付时才交接**：交接通道会拒收没跑完的上游。
   没配凭证时市场信号盒交不出来，链不能因此断。
3. **链不能因为「站外需求拿不到」而断**：那是去配凭证的事，
   不是「这一轮做不成」。
4. **关键词是词，不是整句假设**：拿一句人话去 GSC / Reddit 搜，搜出来的是零。

## 为什么用假的 trigger_task

真跑一次要打 Shopify、打模型、动营业中的页面——
**测试不许碰那些**。这里只验「编排层怎么接线」，
接线对不对跟盒子跑不跑得动是两件事。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_shopify_round.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("run_shopify_round_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


script = _load_script()


#: 一次假履约的记录形状（脚本只读这几个键）
def _run(
    box_id: str,
    n: int,
    *,
    status: str = "completed",
    acceptance: str = "accepted",
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_run_id": f"tr_{n}",
        "box_id": box_id,
        "status": status,
        "acceptance_status": acceptance,
        "artifact": artifact if artifact is not None else {},
    }


class _FakeTrigger:
    """假 trigger_task——按盒子顺序吐记录，并把每次调用记下来"""

    def __init__(self, market_status: str = "completed",
                 market_acceptance: str = "accepted") -> None:
        self.calls: list[dict[str, Any]] = []
        self.market_status = market_status
        self.market_acceptance = market_acceptance
        self._n = 0

    def __call__(self, box_id: str, task_type: str, payload: dict[str, Any],
                 store: Any, **kwargs: Any) -> dict[str, Any]:
        self._n += 1
        self.calls.append({
            "box_id": box_id,
            "task_type": task_type,
            "payload": payload,
            "handoff": kwargs.get("handoff") or {},
            "chain_ref": kwargs.get("chain_ref"),
        })
        if box_id == "shopify_market_signal_box":
            return _run(
                box_id, self._n,
                status=self.market_status,
                acceptance=self.market_acceptance,
                artifact={
                    "signals": [],
                    "platform_status": [
                        {"platform": "gsc", "status": "unverifiable", "detail": "没配凭证"},
                    ],
                },
            )
        if box_id == "shopify_snapshot_box":
            return _run(box_id, self._n, artifact={
                "qualified_sessions": 141,
                "total_sessions": 163,
                "strategy": payload.get("strategy", ""),
            })
        if box_id == "shopify_traffic_diagnosis_box":
            return _run(box_id, self._n, artifact={
                "bottleneck_stage": "no_product_view",
                "hypothesis": "访客没进商品页",
                "target_scenario": "访客从首页进入店铺，但未继续进入商品详情页了解定制选项。",
                "prioritized_channel": "direct",
            })
        if box_id == "shopify_acquisition_experiment_box":
            return _run(box_id, self._n, artifact={
                "landing_page": "pet-plush-clone",
                "applied": False,
            })
        return _run(box_id, self._n, artifact={"record_kind": "snapshot"})

    def call_for(self, box_id: str) -> dict[str, Any]:
        for call in self.calls:
            if call["box_id"] == box_id:
                return call
        raise AssertionError(f"这次没触发 {box_id}")


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch):
    """凭证只从环境变量读——测试里给假的，别碰真的店"""
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "test-shop.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ADMIN_TOKEN", "shpat_test_not_a_real_token")
    monkeypatch.delenv("SHOPIFY_ALLOW_WRITES", raising=False)
    monkeypatch.setattr(script, "load_env_file", lambda: [])
    monkeypatch.setattr(script, "resolve_token", lambda domain: ("tok", "test"))
    return monkeypatch


def _run_round(monkeypatch: pytest.MonkeyPatch, trigger, argv: list[str]) -> int:
    monkeypatch.setattr(script, "trigger_task", trigger)
    return script.main(argv)


# ============================================================
# 一、策略要真的投进去
# ============================================================


def test_the_strategy_reaches_the_snapshot(env, monkeypatch):
    """**`--strategy` 不投进去 = 命令行上的摆设**

    诊断盒是从快照产出里读策略的（`snapshot.get("strategy")`），
    不投 → 策略是空 → 规则一律退回 `conversion_lift`。
    """
    trigger = _FakeTrigger()
    _run_round(monkeypatch, trigger, ["--strategy", "ai_referral"])

    snapshot = trigger.call_for("shopify_snapshot_box")
    assert snapshot["payload"].get("strategy") == "ai_referral"


def test_the_default_strategy_is_still_declared(env, monkeypatch):
    """不给也要有——默认就是一条策略，不是「没策略」"""
    trigger = _FakeTrigger()
    _run_round(monkeypatch, trigger, [])
    assert trigger.call_for("shopify_snapshot_box")["payload"].get("strategy") == "conversion_lift"


# ============================================================
# 二、市场信号的交接：只在真的交付时接
# ============================================================


def test_a_delivered_market_signal_is_handed_to_the_snapshot(env, monkeypatch):
    trigger = _FakeTrigger(market_status="completed", market_acceptance="accepted")
    _run_round(monkeypatch, trigger, [])

    snapshot = trigger.call_for("shopify_snapshot_box")
    handoff = snapshot["handoff"]
    assert handoff.get("market_signal_ref"), handoff


def test_an_undelivered_market_signal_is_not_handed_over(env, monkeypatch):
    """**没交付的东西不许接上去**

    交接通道会拒收没跑完的上游——脚本就不能替它接。
    """
    trigger = _FakeTrigger(market_status="waiting_external", market_acceptance="rejected")
    _run_round(monkeypatch, trigger, [])

    snapshot = trigger.call_for("shopify_snapshot_box")
    assert not snapshot["handoff"].get("market_signal_ref"), snapshot["handoff"]


# ============================================================
# 三、链不能因为「站外需求拿不到」而断
# ============================================================


def test_the_chain_survives_a_market_signal_that_did_not_deliver(env, monkeypatch):
    """**「没配凭证」不是「这一轮做不成」**

    市场信号是可选上下文：拿不到就只带店里的数，
    诊断把站外需求记成未知项。链照跑。
    """
    trigger = _FakeTrigger(market_status="waiting_external", market_acceptance="rejected")
    code = _run_round(monkeypatch, trigger, [])
    assert code == 0
    # 链走完了：诊断、实验、档案都跑了
    for box_id in (
        "shopify_traffic_diagnosis_box",
        "shopify_acquisition_experiment_box",
        "shopify_archive_box",
    ):
        trigger.call_for(box_id)


def test_the_snapshot_still_gets_the_store_side_facts_without_market_signals(
    env, monkeypatch
):
    trigger = _FakeTrigger(market_status="waiting_external", market_acceptance="rejected")
    _run_round(monkeypatch, trigger, [])

    payload = trigger.call_for("shopify_snapshot_box")["payload"]
    assert payload.get("store_domain")
    assert payload.get("window_start")
    assert payload.get("window_end")


# ============================================================
# 四、关键词是词，不是整句假设
# ============================================================

class _FakeStore:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def list_recent(self, limit: int = 20, box_id: str | None = None) -> list[dict[str, Any]]:
        return self._records[:limit]


def test_a_long_sentence_is_not_used_as_a_search_keyword():
    """**搜的是需求词，不是假设句**

    拿一句人话去 GSC / Reddit 里搜，搜出来的是零。
    """
    records = [{
        "box_id": "shopify_traffic_diagnosis_box",
        "artifact": {
            "target_scenario": "访客从首页进入店铺，但未继续进入商品详情页了解定制选项。",
            "prioritized_channel": "direct",
        },
    }]
    words = script._keywords_from_last_diagnosis(_FakeStore(records))
    assert "direct" in words
    assert all(len(w) <= 40 for w in words), words


def test_the_newest_diagnosis_wins():
    """**最新的那一轮才是「上一轮」**

    `list_recent` 已经倒序（新的在前）——再往前翻就拿到更老的那轮了。
    """
    records = [
        {   # 新
            "box_id": "shopify_traffic_diagnosis_box",
            "artifact": {"prioritized_channel": "new_channel"},
        },
        {   # 旧
            "box_id": "shopify_traffic_diagnosis_box",
            "artifact": {"prioritized_channel": "old_channel"},
        },
    ]
    words = script._keywords_from_last_diagnosis(_FakeStore(records))
    assert words == ["new_channel"], words


# ============================================================
# 五、去哪些平台：自己判断，不让使用者每次挑
# ============================================================


def test_without_credentials_no_platform_is_visited(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GSC_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GSC_SITE_URL", raising=False)
    monkeypatch.setattr(script, "GSC_CSV_DEFAULT", Path("/nonexistent/gsc.csv"))
    platforms, csv_path = script._market_platforms("")
    assert platforms == []
    assert csv_path == ""


def test_an_exported_csv_is_picked_up_without_authorization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """**GSC 的 CSV 路径是免授权的那条路**——导出表丢进来就能跑"""
    monkeypatch.delenv("GSC_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GSC_SITE_URL", raising=False)
    csv = tmp_path / "gsc.csv"
    csv.write_text("Top queries,Clicks,Impressions\n", encoding="utf-8")
    monkeypatch.setattr(script, "GSC_CSV_DEFAULT", csv)

    platforms, csv_path = script._market_platforms("")
    assert platforms == ["gsc"]
    assert csv_path == str(csv)


def test_a_configured_token_turns_on_gsc_too(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GSC_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("GSC_SITE_URL", "sc-domain:giftsjourneyshop.com")
    monkeypatch.setattr(script, "GSC_CSV_DEFAULT", Path("/nonexistent/gsc.csv"))
    platforms, _ = script._market_platforms("")
    assert platforms == ["gsc"]


def test_an_explicit_choice_wins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(script, "GSC_CSV_DEFAULT", Path("/nonexistent/gsc.csv"))
    platforms, _ = script._market_platforms("reddit,etsy")
    assert platforms == ["reddit", "etsy"]


def test_the_market_call_carries_the_platforms_and_the_csv_path(env, monkeypatch):
    trigger = _FakeTrigger()
    _run_round(monkeypatch, trigger, ["--platforms", "gsc", "--gsc-csv", "/tmp/x.csv"])
    payload = trigger.call_for("shopify_market_signal_box")["payload"]
    assert payload.get("platforms") == ["gsc"]
    assert payload.get("gsc_csv_path") == "/tmp/x.csv"
