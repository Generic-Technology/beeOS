"""tests.test_shopify_market_signal —— 市场信号盒的守卫

这组测试盯三件事：

1. **个人信息不许进产出**（安全底线）——
   带邮箱/手机号的候选**整条丢掉**，不是洗白；
2. **没出处的主题不算数**——它可能是编的；
3. **一个平台挂了不拖垮整轮**——取不到要标 unverifiable + 原因。

只有 GSC 的 CSV 那条路有夹具（它不需要凭证），
其余平台的客户端按官方文档写、**没跑过真凭证** → 所以盒子是 skeleton。
"""

from __future__ import annotations

import pytest

from boxes.shopify_market_signal import pii
from boxes.shopify_market_signal.clients import (
    FetchResult,
    GscClient,
    PlatformStatus,
)
from boxes.shopify_market_signal.runner import (
    market_signal_intake_handler,
    market_signal_packager_handler,
    market_signal_work_handler,
)


# ============================================================
# 个人信息
# ============================================================


class TestPersonalInfoNeverEntersTheOutput:
    def test_an_email_is_found(self):
        hits = pii.find_personal_info("anyone have a custom pet memorial gift? me@example.com")
        assert any("example.com" in h for h in hits)

    def test_a_phone_is_found_but_a_measurement_is_not(self):
        assert pii.find_personal_info("call +1 (415) 555-0134")
        # 「24.5 inches」「55% wool」「12 designs」**不是**电话
        assert pii.find_personal_info("24.5 inches, 55% wool, 12 designs") == []

    def test_a_signal_with_pii_is_dropped_whole(self):
        """带联系方式的帖子**整条丢掉**——它是有人在私下交易，不是需求信号"""
        kept, dropped = pii.filter_signals([
            {"platform": "reddit", "topic": "where to buy personalized pet memorial gift"},
            {"platform": "reddit", "topic": "selling my stuff, reach me at me@example.com"},
        ])
        assert len(kept) == 1
        assert "pet memorial" in kept[0]["topic"]
        assert len(dropped) == 1
        assert dropped[0]["pii_hits"]

    def test_the_output_has_no_pii_left(self):
        """最后一道路闸：产出里绝不该有一个邮箱"""
        result = market_signal_packager_handler({
            "round_id": "r1",
            "signals": [
                {"platform": "reddit", "topic": "custom pet memorial gift",
                 "source_url": "https://reddit.com/x", "captured_at": "t",
                 "snippet": "anyone know a good seller? ask me at me@example.com"},
            ],
            "platform_status": [{"platform": "reddit", "status": "observed"}],
            "pii_removed": [],
        })
        assert result["pii_remaining_count"] == 0
        assert "example.com" not in json_dump(result)

    def test_a_signal_without_a_source_is_not_counted(self):
        """没出处的主题不算数——它可能是编的"""
        result = market_signal_packager_handler({
            "round_id": "r1",
            "signals": [{"platform": "reddit", "topic": "x", "source_url": "",
                         "captured_at": "t", "snippet": ""}],
            "platform_status": [{"platform": "reddit", "status": "observed"}],
            "pii_removed": [],
        })
        assert result["source_present"] is False
        assert result["signals"] == []


# ============================================================
# GSC 的 CSV 路（唯一用夹具验过的路）
# ============================================================


class TestTheGscCsvPath:
    def test_it_reads_the_exported_csv(self, tmp_path):
        csv_path = tmp_path / "gsc.csv"
        csv_path.write_text(
            "Top queries,Clicks,Impressions,CTR,Position\n"
            "personalized stocking,4,120,0.033,7.5\n"
            "santa hat,2,60,0.033,9.1\n"
            "Total,6,180,0.033,8.0\n",
            encoding="utf-8",
        )
        client = GscClient(csv_path=str(csv_path))
        result = client.fetch(["personalized stocking"], 28)

        assert result.status.status == "observed"
        topics = [s["topic"] for s in result.signals]
        assert topics == ["personalized stocking", "santa hat"], topics
        # 汇总行不算需求
        assert "Total" not in topics
        # **需求大小 = 展示数**（有多少人搜过），不是点击数——
        # 点击数是「我们接住了多少」，不是需求。
        assert result.signals[0]["occurrences"] == 120
        assert result.signals[0]["source_url"]

    def test_it_reads_the_chinese_header(self, tmp_path):
        """**中文界面导出的表头也得读得出**——写死英文 = 用户换个界面就断"""
        csv_path = tmp_path / "gsc_cn.csv"
        csv_path.write_text(
            "热门查询,点击次数,展示,点击率,排名\n"
            "personalized pet gifts,0,59,0%,71.36\n"
            "gifts journey,2,8,25%,26.75\n"
            "总计,2,67,0%,26.75\n",
            encoding="utf-8",
        )
        client = GscClient(csv_path=str(csv_path))
        result = client.fetch(["personalized pet gifts"], 28)

        assert result.status.status == "observed", result.status.detail
        topics = [s["topic"] for s in result.signals]
        assert "总计" not in topics
        assert topics[0] == "personalized pet gifts"
        assert result.signals[0]["occurrences"] == 59

    def test_it_sorts_by_demand_not_by_the_export_order(self, tmp_path):
        """GSC 自己按点击数排——「搜了很多次、一次没点」的需求词全被压在后面

        那些词恰恰是我们最该去接的，所以按**展示数**排。
        """
        csv_path = tmp_path / "gsc_order.csv"
        csv_path.write_text(
            "Top queries,Clicks,Impressions,CTR,Position\n"
            "gifts journey,2,8,0.25,26.75\n"
            "personalized pet gifts,0,59,0,71.36\n"
            "custom pet gifts,0,37,0,69.95\n",
            encoding="utf-8",
        )
        client = GscClient(csv_path=str(csv_path))
        result = client.fetch([], 28)
        topics = [s["topic"] for s in result.signals]
        assert topics[0] == "personalized pet gifts", topics
        assert topics[1] == "custom pet gifts", topics

    def test_it_keeps_position_in_the_snippet(self, tmp_path):
        """**排名是判断「值不值得改」的依据**

        「第一页却 0 点击」= 我们排在前面但没接住——
        这是最该改的一类页面。
        """
        csv_path = tmp_path / "gsc_pos.csv"
        csv_path.write_text(
            "热门查询,点击次数,展示,点击率,排名\n"
            "crystal music box,0,27,0%,25\n",
            encoding="utf-8",
        )
        client = GscClient(csv_path=str(csv_path))
        result = client.fetch([], 28)
        snippet = result.signals[0]["snippet"]
        assert "pos=25" in snippet
        assert "clicks=0" in snippet

    def test_the_pages_table_is_read_too(self, tmp_path):
        """**页面表也要读**——它才是「哪一页有展示却没人点」的证据"""
        csv_path = tmp_path / "gsc_pages.csv"
        csv_path.write_text(
            "排名靠前的网页,点击次数,展示,点击率,排名\n"
            "https://giftsjourneyshop.com/products/mirror,0,219,0%,15.14\n"
            "https://giftsjourneyshop.com/,7,32,21.88%,7.69\n",
            encoding="utf-8",
        )
        client = GscClient(csv_path=str(csv_path))
        result = client.fetch([], 28)

        assert result.status.status == "observed", result.status.detail
        assert len(result.page_signals) == 2
        # **按展示数排序**——展示多的先看
        top = result.page_signals[0]
        assert top["path"] == "/products/mirror", top
        assert top["impressions"] == 219
        assert top["clicks"] == 0
        assert top["position"] == 15.14
        assert top["ctr_percent"] == 0.0

    def test_the_ctr_is_the_same_number_in_both_languages(self, tmp_path):
        """**同一个列，两种单位**——英文导出是比例，中文是百分比

        不统一的话，两个文件读出来的同一个量会差 100 倍
        （而「同一个量只能有一个来源」）。
        """
        en = tmp_path / "en.csv"
        en.write_text(
            "Top pages,Clicks,Impressions,CTR,Position\n"
            "/products/x,3,120,0.025,7.5\n",
            encoding="utf-8",
        )
        cn = tmp_path / "cn.csv"
        cn.write_text(
            "排名靠前的网页,点击次数,展示,点击率,排名\n"
            "/products/x,3,120,2.5%,7.5\n",
            encoding="utf-8",
        )
        a = GscClient(csv_path=str(en)).fetch([], 28).page_signals[0]
        b = GscClient(csv_path=str(cn)).fetch([], 28).page_signals[0]
        assert a["ctr_percent"] == b["ctr_percent"] == 2.5, (a, b)

    def test_a_zip_export_is_read_and_the_tables_are_picked(self, tmp_path):
        """**丢整个导出包进来也行**——盒子按表头自己认表

        Search Console 一次导出好几张表（查询 / 网页 / 日期 / 国家地区…），
        让使用者自己挑一张 = 挑错就白搭。
        """
        import zipfile

        zip_path = tmp_path / "export.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr(
                "csv_0.csv",
                "过滤器,值\n搜索类型,网页（基于文本）\n",
            )
            archive.writestr(
                "csv_1.csv",
                "热门查询,点击次数,展示,点击率,排名\n"
                "crystal music box,0,27,0%,25\n",
            )
            archive.writestr(
                "csv_2.csv",
                "排名靠前的网页,点击次数,展示,点击率,排名\n"
                "https://giftsjourneyshop.com/products/box,0,116,0%,14.99\n",
            )
            archive.writestr(
                "csv_3.csv",
                "日期,点击次数,展示,点击率,排名\n2026-11-01,0,30,0%,28.3\n",
            )

        result = GscClient(csv_path=str(zip_path)).fetch([], 28)
        assert result.status.status == "observed", result.status.detail
        assert [s["topic"] for s in result.signals] == ["crystal music box"]
        assert [p["path"] for p in result.page_signals] == ["/products/box"]

    def test_the_api_path_wins_over_the_export_file(self, tmp_path, monkeypatch):
        """**API 优先，导出文件兜底**——
        API 更新鲜，还能多给一个「词 × 页」维度。
        """
        csv_path = tmp_path / "gsc.csv"
        csv_path.write_text(
            "热门查询,点击次数,展示,点击率,排名\ncrystal music box,0,27,0%,25\n",
            encoding="utf-8",
        )
        calls = {"api": 0, "csv": 0}

        def _fake_api(self, window_days):
            calls["api"] += 1
            return FetchResult(
                signals=[{"platform": "gsc", "topic": "来自 API 的词", "occurrences": 9,
                          "source_url": "gsc://x", "captured_at": "t"}],
                status=PlatformStatus("gsc", "observed", "API 取到 1 条需求词", 1),
            )

        def _fake_csv(self):
            calls["csv"] += 1
            return FetchResult(
                signals=[],
                status=PlatformStatus("gsc", "observed", "CSV 1 条", 1),
            )

        monkeypatch.setattr(GscClient, "_from_api", _fake_api)
        monkeypatch.setattr(GscClient, "_from_csv", _fake_csv)

        client = GscClient(
            csv_path=str(csv_path),
            site_url="https://giftsjourneyshop.com/",
            access_token="tok",
        )
        result = client.fetch([], 28)
        assert calls["api"] == 1 and calls["csv"] == 0, calls
        assert [s["topic"] for s in result.signals] == ["来自 API 的词"]

    def test_the_export_file_is_the_fallback_when_the_api_fails(self, tmp_path, monkeypatch):
        """**API 挂了还有导出文件顶着**——不至于整轮没站外需求"""
        csv_path = tmp_path / "gsc.csv"
        csv_path.write_text(
            "热门查询,点击次数,展示,点击率,排名\ncrystal music box,0,27,0%,25\n",
            encoding="utf-8",
        )

        def _fake_api(self, window_days):
            return FetchResult(
                signals=[],
                status=PlatformStatus("gsc", "unverifiable", "HTTP 429：配额用完了"),
            )

        monkeypatch.setattr(GscClient, "_from_api", _fake_api)
        client = GscClient(
            csv_path=str(csv_path),
            site_url="https://giftsjourneyshop.com/",
            access_token="tok",
        )
        result = client.fetch([], 28)
        assert result.status.status == "observed", result.status.detail
        assert "API 没取到" in result.status.detail, result.status.detail
        assert "导出文件" in result.status.detail, result.status.detail

    def test_a_table_that_is_neither_says_what_it_wanted(self, tmp_path):
        """**认不出的表要说清要哪张**——读者才知道自己是导错了"""
        csv_path = tmp_path / "gsc_dates.csv"
        csv_path.write_text(
            "日期,点击次数,展示,点击率,排名\n2026-11-01,0,30,0%,28.3\n",
            encoding="utf-8",
        )
        client = GscClient(csv_path=str(csv_path))
        result = client.fetch([], 28)
        assert result.status.status == "unverifiable"
        assert "热门查询" in result.status.detail
        assert "排名靠前的网页" in result.status.detail

    def test_it_tolerates_a_different_header(self, tmp_path):
        """GSC 导出表头会变——按名字猜列，不写死"""
        csv_path = tmp_path / "gsc2.csv"
        csv_path.write_text(
            "Query,Clicks,Impressions\n"
            "pet memorial gift,1,10\n",
            encoding="utf-8",
        )
        client = GscClient(csv_path=str(csv_path))
        result = client.fetch(["pet memorial gift"], 28)
        assert [s["topic"] for s in result.signals] == ["pet memorial gift"]

    def test_no_credentials_and_no_csv_is_unverifiable_not_a_failure(self):
        """没凭证 → unverifiable + 原因，**不抛异常**"""
        client = GscClient()
        result = client.fetch(["x"], 28)
        assert result.status.status == "unverifiable"
        assert result.status.detail


# ============================================================
# 盒子本身
# ============================================================


class TestTheBox:
    def test_it_needs_keywords(self):
        with pytest.raises(ValueError, match="keywords"):
            market_signal_intake_handler({"round_id": "r1", "keywords": []})

    def test_it_needs_a_round_id(self):
        with pytest.raises(ValueError, match="round_id"):
            market_signal_intake_handler({"round_id": "", "keywords": ["x"]})

    def test_it_rejects_an_unknown_platform(self):
        with pytest.raises(ValueError, match="不认识的平台"):
            market_signal_intake_handler({
                "round_id": "r1", "keywords": ["x"], "platforms": ["tiktok"],
            })

    def test_one_broken_platform_does_not_stop_the_others(self, tmp_path, monkeypatch):
        """一个平台挂了 → 那个平台 unverifiable，其余照常"""
        csv_path = tmp_path / "gsc.csv"
        csv_path.write_text(
            "Top queries,Clicks,Impressions\nsanta hat,2,60\n", encoding="utf-8")
        monkeypatch.setenv("GSC_CSV_PATH", str(csv_path))

        plan = market_signal_intake_handler({
            "round_id": "r1", "keywords": ["santa hat"],
            "platforms": ["gsc", "reddit"], "gsc_csv_path": str(csv_path),
        })
        raw = market_signal_work_handler(plan)
        statuses = {s["platform"]: s for s in raw["platform_status"]}

        assert statuses["gsc"]["status"] == "observed"
        assert statuses["reddit"]["status"] == "unverifiable"
        assert statuses["reddit"]["detail"]

    def test_the_acceptance_flags_hold(self, tmp_path):
        csv_path = tmp_path / "gsc.csv"
        csv_path.write_text(
            "Top queries,Clicks,Impressions\nsanta hat,2,60\n", encoding="utf-8")
        plan = market_signal_intake_handler({
            "round_id": "r1", "keywords": ["santa hat"],
            "platforms": ["gsc"], "gsc_csv_path": str(csv_path),
        })
        raw = market_signal_work_handler(plan)
        result = market_signal_packager_handler(raw)

        assert result["pii_remaining_count"] == 0
        assert result["signal_status_complete"] is True
        assert result["source_present"] is True
        assert result["signals"]

    def test_it_never_writes_anything(self):
        """它是只读盒——没有写操作代码，也没有写闸"""
        from boxes.shopify_market_signal import runner as r

        source = open(r.__file__, encoding="utf-8").read()
        for forbidden in ("productUpdate", "collectionUpdate", "marketingEvent",
                          "discountCodeBasicCreate"):
            assert forbidden not in source, f"只读盒里不该出现 {forbidden}"


def json_dump(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
