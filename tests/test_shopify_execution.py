"""tests.test_shopify_execution - 两条有副作用的腿的守卫

这一组盯的是**两件会出事的事**：

1. **模型会编**。诊断盒让模型判断点，模型能编指标名、编结论。
   → 产出必须被机械校验：编出来的指标名剔掉，剔空了整份作废。
2. **写入会误伤**。实验盒能改用户真实的店。
   → 写闸、字段白名单、一次一个落地页、改前基线，四道闸都要在。

**「模型不可信」不是免责声明，是实现要求**：
仓里已有同族教训（`boxes/wms_design/plugins.py` 就是为「盒子说自己过了」
这件事存在的；learning 数学盒把「模型能写的取值表」和「产出能出现的取值表」
分成两份）。

## 写入相关的测试一个都不打真网络

假 opener 回放固定响应，**不会碰真实店铺**。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from boxes.shopify_acquisition_experiment import runner as exp_runner
from boxes.shopify_acquisition_experiment.shopify_writer import (
    ShopifyWriteError,
    ShopifyWriter,
    WriteNotAllowed,
)
from boxes.shopify_traffic_diagnosis import runner as diag_runner
from core.models import NOT_APPLICABLE  # noqa: F401 - 占位：确认 core 不进业务词
from fulfillment_scopes.shopify.vocabulary import (
    ALLOW_WRITES_ENV,
    WRITABLE_FIELDS,
)


# ============================================================
# 诊断盒：模型判定
# ============================================================


def _snapshot() -> dict[str, Any]:
    return {
        "round_id": "giftsjourneyrebirth@2026-W47",
        "observed_at": "2026-11-14T12:00:00+00:00",
        "window_start": "2026-10-18",
        "window_end": "2026-11-14",
        "store_domain": "giftsjourneyrebirth.myshopify.com",
        "api_version": "2026-01",
        "source": "shopifyql: FROM sessions",
        "total_sessions": 168,
        "qualified_sessions": 148,
        "excluded_entry_sessions": 20,
        "online_store_visitors": 151,
        "pageviews": 302,
        "sessions_with_cart_additions": 6,
        "sessions_that_reached_checkout": 5,
        "sessions_that_completed_checkout": 3,
        "conversion_rate": 0.0178,
        "qualified_session_rule": "真人 session 且首个落地页不在排除清单里",
        "excluded_entry_paths": ["/account/"],
        "metrics": [
            {"name": "qualified_sessions", "value": 148, "status": "observed",
             "source": "shopifyql", "note": ""},
            {"name": "total_sessions", "value": 168, "status": "observed",
             "source": "shopifyql", "note": ""},
            {"name": "conversion_rate", "value": 0.0178, "status": "observed",
             "source": "shopifyql", "note": ""},
        ],
        "unverified": [],
        "data_present": True,
        "metric_status_complete": True,
        "source_present": True,
        "session_count_consistent": True,
        "top_landing_pages": [
            {"landing_page_path": "/products/personalized-santa-hat",
             "sessions": 40, "sessions_that_completed_checkout": 2},
            {"landing_page_path": "/", "sessions": 30,
             "sessions_that_completed_checkout": 0},
        ],
        "top_sources": [],
        "top_search_terms": [],
    }


def _good_conclusion() -> dict[str, Any]:
    return {
        "bottleneck_stage": "no_single_bottleneck",
        "bottleneck_evidence": ["qualified_sessions", "conversion_rate"],
        "hypothesis": "分发没启动：自然渠道里只有 ChatGPT 与 Google 带来过访问",
        "target_customer": "买个性化节日礼物的人",
        "target_scenario": "个性化节日礼物",
        "prioritized_channel": "natural_search",
        "target_landing_page": "personalized-santa-hat",
        "unknowns": ["曝光与点击没有数据源，断点只能定位到 session 侧"],
    }


def _patch_model(monkeypatch, responses: list[Any]) -> list[str]:
    """把模型换成一串预设回答，并记录每次收到的提示词"""
    prompts: list[str] = []
    queue = list(responses)

    def fake_complete_json(prompt: str, **kwargs: Any) -> Any:
        prompts.append(prompt)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(diag_runner, "complete_json", fake_complete_json)
    return prompts


class TestTheModelJudgement:
    def test_a_valid_conclusion_reaches_the_result(self, monkeypatch):
        """合格的回答 → 结论落进产出，标志位全 true"""
        _patch_model(monkeypatch, [_good_conclusion()])
        part = diag_runner.diagnosis_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "snapshot_ref": _snapshot(),
        })
        assert part["conclusion"] is not None
        assert part["reject_reason"] == ""

        result = diag_runner.diagnosis_packager_handler(part)
        assert result["bottleneck_stage"] == "no_single_bottleneck"
        assert result["hypothesis_present"] is True
        assert result["evidence_validated"] is True

    def test_a_made_up_metric_name_voids_the_whole_conclusion(self, monkeypatch):
        """模型编了一个不存在的指标名 → **整份结论作废**，并带反馈重试

        编出来的证据比没有证据更坏——它会让「证据引用」这件事失去意义。
        而一个会编测量值的模型，它的其余结论也不能当依据。
        """
        conclusion = _good_conclusion()
        conclusion["bottleneck_evidence"] = ["qualified_sessions", "sessions_per_day"]
        prompts = _patch_model(monkeypatch, [conclusion, _good_conclusion()])

        part = diag_runner.diagnosis_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "snapshot_ref": _snapshot(),
        })
        # 第一次作废 → 带反馈重问 → 第二次合格
        assert part["conclusion"] is not None
        assert part["conclusion"]["bottleneck_evidence"] == [
            "qualified_sessions", "conversion_rate",
        ]
        assert len(prompts) == 2
        assert "sessions_per_day" in prompts[1]

    def test_all_made_up_evidence_voids_the_whole_conclusion(self, monkeypatch):
        """证据全是编的 → 整份结论作废 → 空结论（不是「勉强收下」）"""
        conclusion = _good_conclusion()
        conclusion["bottleneck_evidence"] = ["sessions_per_day", "impressions"]
        _patch_model(monkeypatch, [conclusion, conclusion])

        part = diag_runner.diagnosis_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "snapshot_ref": _snapshot(),
        })
        assert part["conclusion"] is None
        assert "不存在的指标" in part["reject_reason"]

        result = diag_runner.diagnosis_packager_handler(part)
        assert result["hypothesis_present"] is False
        assert result["evidence_validated"] is False
        # **结论字段缺席**，不是空串充数
        assert "bottleneck_stage" not in result

    def test_an_unknown_stage_voids_the_conclusion(self, monkeypatch):
        """模型编了一个不存在的断点 → 作废"""
        conclusion = _good_conclusion()
        conclusion["bottleneck_stage"] = "no_vibes"
        _patch_model(monkeypatch, [conclusion, conclusion])

        part = diag_runner.diagnosis_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "snapshot_ref": _snapshot(),
        })
        assert part["conclusion"] is None
        assert "no_vibes" in part["reject_reason"]

    def test_a_model_failure_is_not_a_verdict(self, monkeypatch):
        """模型调不通 → 没有结论——**不是「判定为无断点」**"""
        from runtime.llm import LLMError

        _patch_model(monkeypatch, [LLMError("端点不通")])

        part = diag_runner.diagnosis_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "snapshot_ref": _snapshot(),
        })
        assert part["conclusion"] is None
        result = diag_runner.diagnosis_packager_handler(part)
        assert result["hypothesis_present"] is False
        assert result["unknowns"]

    def test_it_retries_once_with_the_problems_stated(self, monkeypatch):
        """第一次不合格 → 把问题写回去再问一次

        只重试一次：再错就是这件事这轮做不成，不该无限问下去。
        """
        bad = _good_conclusion()
        bad["bottleneck_evidence"] = ["made_up_metric"]
        prompts = _patch_model(monkeypatch, [bad, _good_conclusion()])

        part = diag_runner.diagnosis_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "snapshot_ref": _snapshot(),
        })
        assert part["conclusion"] is not None
        assert len(prompts) == 2
        assert "made_up_metric" in prompts[1], "第二次的提示词要带上上次的问题"

    def test_the_conclusion_shape_has_no_numbers(self):
        """**模型的结论里没有任何数字字段**

        数字由快照盒算。模型报了数字也不作数——
        同一个量只能有一个来源，两处都会打架。
        """
        from boxes.shopify_traffic_diagnosis.schemas import (
            DIAGNOSIS_OBSERVATION_SCHEMA,
        )

        conclusion = next(
            f for f in DIAGNOSIS_OBSERVATION_SCHEMA.fields
            if f.name == "conclusion"
        )
        numeric = [
            f.name for f in (conclusion.properties or [])
            if f.type in ("number", "integer")
        ]
        assert numeric == [], f"模型的结论里不该有数字字段：{numeric}"


# ============================================================
# 实验盒：写入
# ============================================================


def _diagnosis() -> dict[str, Any]:
    return {
        "round_id": "giftsjourneyrebirth@2026-W47",
        "observed_at": "2026-11-14T12:00:00+00:00",
        "bottleneck_stage": "no_single_bottleneck",
        "bottleneck_evidence": ["qualified_sessions"],
        "hypothesis": "分发没启动",
        "target_customer": "买个性化节日礼物的人",
        "target_scenario": "个性化节日礼物",
        "prioritized_channel": "natural_search",
        "target_landing_page": "personalized-santa-hat",
        "unknowns": [],
        "snapshot_ref_present": True,
        "hypothesis_present": True,
        "evidence_validated": True,
    }


def _draft() -> dict[str, Any]:
    return {
        "title": "Personalized Santa Hat — 名字绣上去了",
        "descriptionHtml": "<p>这是你的圣诞帽：名字绣上去，送给谁都不会错。</p>",
        "seoTitle": "Personalized Santa Hat | 绣名字的圣诞帽",
        "seoDescription": "绣上名字的个性化圣诞帽，送礼不重样。",
        "tags": ["christmas", "personalized", "gift"],
        # 模型多写的字段：**必须被丢掉**
        "price": "1.00",
        "status": "DRAFT",
    }


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw


def _patch_writer(monkeypatch, payloads: list[dict[str, Any]], *, allow: bool):
    """假写入客户端：回放响应，并记录发出去的请求"""
    sent: list[dict[str, Any]] = []
    queue = list(payloads)

    def opener(request: Any, timeout: float) -> _FakeResponse:
        body = json.loads(request.data.decode("utf-8"))
        sent.append(body)
        return _FakeResponse(queue.pop(0))

    def fake_from_env(**kwargs: Any) -> ShopifyWriter:
        return ShopifyWriter(
            store_domain="giftsjourneyrebirth.myshopify.com",
            token="shpat_secret",
            api_version="2026-01",
            allow_writes=allow,
            opener=opener,
        )

    monkeypatch.setattr(exp_runner.ShopifyWriter, "from_env", staticmethod(fake_from_env))
    return sent


def _patch_draft(monkeypatch, draft: dict[str, Any] | None = None) -> None:
    def fake_complete_json(prompt: str, **kwargs: Any) -> Any:
        return draft if draft is not None else _draft()

    monkeypatch.setattr(exp_runner, "complete_json", fake_complete_json)


_READ_OK = {
    "data": {
        "products": {
            "nodes": [{
                "id": "gid://shopify/Product/123",
                "title": "旧标题",
                "handle": "personalized-santa-hat",
                "descriptionHtml": "<p>旧正文</p>",
                "seo": {"title": "旧 SEO 标题", "description": "旧 SEO 描述"},
                "tags": ["christmas"],
            }]
        }
    }
}

_UPDATE_OK = {
    "data": {
        "productUpdate": {
            "product": {
                "id": "gid://shopify/Product/123",
                "title": "新标题",
                "descriptionHtml": "<p>新正文</p>",
                "seo": {"title": "新 SEO 标题", "description": "新 SEO 描述"},
                "tags": ["christmas", "personalized"],
            },
            "userErrors": [],
        }
    }
}


class TestTheWriteGate:
    def test_the_write_gate_closed_means_nothing_is_written(self, monkeypatch):
        """**写闸关着 → 一次写请求都不发**"""
        sent = _patch_writer(monkeypatch, [_READ_OK, _UPDATE_OK], allow=False)
        _patch_draft(monkeypatch)

        part = exp_runner.experiment_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "target_handle": "personalized-santa-hat",
            "apply": True,
            "diagnosis_ref": _diagnosis(),
        })

        assert part["applied"] is False
        assert ALLOW_WRITES_ENV in part["reject_reason"]
        # 只发了读请求（读基线），**没有写请求**
        mutations = [
            b for b in sent if "productUpdate" in (b.get("query") or "")
        ]
        assert mutations == [], "写闸关着却发了写请求"

    def test_the_gate_is_read_from_the_environment(self, monkeypatch):
        """写闸是**进程级**开关——不在投料里，所以交接不会误触写入"""
        import os

        from fulfillment_scopes.shopify.vocabulary import DOMAIN_ENV, TOKEN_ENV

        monkeypatch.setenv(TOKEN_ENV, "shpat_test")
        monkeypatch.setenv(DOMAIN_ENV, "giftsjourneyrebirth.myshopify.com")
        monkeypatch.setenv(ALLOW_WRITES_ENV, "1")
        writer = ShopifyWriter.from_env.__func__(  # type: ignore[attr-defined]
            ShopifyWriter
        )
        assert writer.allow_writes is True

        monkeypatch.setenv(ALLOW_WRITES_ENV, "0")
        writer = ShopifyWriter.from_env.__func__(ShopifyWriter)  # type: ignore[attr-defined]
        assert writer.allow_writes is False

        monkeypatch.delenv(ALLOW_WRITES_ENV, raising=False)
        writer = ShopifyWriter.from_env.__func__(ShopifyWriter)  # type: ignore[attr-defined]
        assert writer.allow_writes is False
        assert os.environ.get(ALLOW_WRITES_ENV) is None


class TestWhatGetsWritten:
    def test_only_whitelisted_fields_are_sent(self, monkeypatch):
        """模型多写的字段（价格、发布状态）**不进写入载荷**"""
        sent = _patch_writer(monkeypatch, [_READ_OK, _UPDATE_OK], allow=True)
        _patch_draft(monkeypatch)

        exp_runner.experiment_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "target_handle": "personalized-santa-hat",
            "apply": True,
            "diagnosis_ref": _diagnosis(),
        })

        mutation = next(
            b for b in sent if "productUpdate" in (b.get("query") or "")
        )
        product = mutation["variables"]["product"]
        assert set(product) <= {"id", "title", "descriptionHtml", "seo", "tags"}
        assert "price" not in product
        assert "status" not in product
        assert product["id"] == "gid://shopify/Product/123"

    def test_exactly_one_product_is_touched(self, monkeypatch):
        """**一次只改一个落地页**——一轮只改一个变量"""
        sent = _patch_writer(monkeypatch, [_READ_OK, _UPDATE_OK], allow=True)
        _patch_draft(monkeypatch)

        exp_runner.experiment_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "target_handle": "personalized-santa-hat",
            "apply": True,
            "diagnosis_ref": _diagnosis(),
        })

        mutations = [
            b for b in sent if "productUpdate" in (b.get("query") or "")
        ]
        assert len(mutations) == 1

    def test_the_baseline_is_captured_before_the_write(self, monkeypatch):
        """**改前基线要留下来**——没有它就没法回滚、没法对照"""
        _patch_writer(monkeypatch, [_READ_OK, _UPDATE_OK], allow=True)
        _patch_draft(monkeypatch)

        part = exp_runner.experiment_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "target_handle": "personalized-santa-hat",
            "apply": True,
            "diagnosis_ref": _diagnosis(),
        })

        assert part["applied"] is True
        assert part["baseline_fields"]["title"] == "旧标题"
        assert part["baseline_fields"]["seo.title"] == "旧 SEO 标题"
        assert part["applied_fields"]["title"] == "新标题"

    def test_shopify_errors_are_carried_back_not_swallowed(self, monkeypatch):
        """Shopify 拒了某一项 → 如实带回去，applied=False"""
        _patch_writer(monkeypatch, [
            _READ_OK,
            {
                "data": {
                    "productUpdate": {
                        "product": None,
                        "userErrors": [
                            {"field": ["title"], "message": "标题不合法"},
                        ],
                    }
                }
            },
        ], allow=True)
        _patch_draft(monkeypatch)

        part = exp_runner.experiment_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "target_handle": "personalized-santa-hat",
            "apply": True,
            "diagnosis_ref": _diagnosis(),
        })
        assert part["applied"] is False
        assert any("标题不合法" in e for e in part["user_errors"])


class TestTheDraftGate:
    def test_a_draft_over_the_limits_is_refused(self, monkeypatch):
        """超长的 SEO 标题 → 草稿作废 → 不写"""
        sent = _patch_writer(monkeypatch, [_READ_OK, _UPDATE_OK], allow=True)
        draft = _draft()
        draft["seoTitle"] = "长" * 100
        _patch_draft(monkeypatch, draft)

        part = exp_runner.experiment_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "target_handle": "personalized-santa-hat",
            "apply": True,
            "diagnosis_ref": _diagnosis(),
        })
        assert part["applied"] is False
        assert "70" in part["reject_reason"]
        mutations = [
            b for b in sent if "productUpdate" in (b.get("query") or "")
        ]
        assert mutations == [], "草稿不合格却写了"

    def test_no_landing_page_means_no_action(self, monkeypatch):
        """诊断没指定落地页 → **不动手**，也不中止

        「这一轮没指定改哪里」不是「投料坏了」——
        抛错会让履约中止，而这里应该是「没有东西可做 → 判据红 → 被拒」。
        """
        sent = _patch_writer(monkeypatch, [_READ_OK, _UPDATE_OK], allow=True)
        _patch_draft(monkeypatch)

        plan = exp_runner.experiment_intake_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "apply": True,
            "diagnosis_ref": {
                "round_id": "giftsjourneyrebirth@2026-W47",
                "observed_at": "2026-11-14T12:00:00+00:00",
                "snapshot_ref_present": True,
                "hypothesis_present": True,
                "evidence_validated": True,
            },
        })
        assert plan["target_handle"] == ""

        part = exp_runner.experiment_work_handler(plan)
        assert part["applied"] is False
        assert "target_landing_page" in part["reject_reason"]
        assert sent == [], "不知道改哪个就不该发任何请求"

    def test_without_apply_intent_nothing_is_written(self, monkeypatch):
        """**没有逐次意图就不写**——哪怕写闸开着

        看板的示例投料、交接链路发起的履约，都带着环境变量跑；
        只靠环境变量那一道，一次点错就是改在营业中的页面上。

        **读可以，写不行**：干跑本来就要读基线、出施工单——
        「没有 apply 就不发任何请求」是错的，那等于连方案都不出。
        """
        sent = _patch_writer(monkeypatch, [_READ_OK, _UPDATE_OK], allow=True)
        _patch_draft(monkeypatch)

        part = exp_runner.experiment_work_handler({
            "round_id": "giftsjourneyrebirth@2026-W47",
            "target_handle": "personalized-santa-hat",
            "apply": False,
            "diagnosis_ref": _diagnosis(),
        })
        assert part["applied"] is False
        assert "apply" in part["reject_reason"]
        # 读基线可以，**写请求一次都不发**
        mutations = [
            b for b in sent if "productUpdate" in (b.get("query") or "")
        ]
        assert mutations == [], "没有逐次意图却发了写请求"


class TestTheWriterItself:
    def test_the_whitelist_is_the_only_thing_that_can_be_written(self):
        """白名单是**唯一**的准入名单——不在里面的字段连载荷都进不去"""
        assert "price" not in WRITABLE_FIELDS
        assert "variants" not in WRITABLE_FIELDS
        assert "status" not in WRITABLE_FIELDS
        assert "title" in WRITABLE_FIELDS

    def test_the_writer_refuses_when_the_gate_is_closed(self):
        writer = ShopifyWriter(
            store_domain="x.myshopify.com", token="t", allow_writes=False
        )
        with pytest.raises(WriteNotAllowed):
            writer.update_product("gid://shopify/Product/1", {"title": "新"})

    def test_the_writer_refuses_a_non_whitelisted_change(self):
        writer = ShopifyWriter(
            store_domain="x.myshopify.com", token="t", allow_writes=True
        )
        with pytest.raises(ShopifyWriteError):
            writer.update_product("gid://shopify/Product/1", {"price": "1.00"})

    def test_the_write_op_is_not_replayable(self):
        """有副作用的那一跳**不许重放**"""
        from boxes.shopify_acquisition_experiment.beeline import COMPONENTS

        work = next(c for c in COMPONENTS if c.op_id == "experiment_work")
        assert work.idempotency is not None
        assert work.idempotency.mode == "forbidden", (
            "写操作声明成可重放 = 重试会再写一次"
        )


class TestTheDraftFollowsThePageLanguage:
    """**文案要用商品页现在的语言写**

    这家店的商品页是英文的。模型看中文提示词就写中文——
    顾客看的是商品页，不是我们的提问。
    """

    def test_the_prompt_says_to_follow_the_page_language(self):
        from boxes.shopify_acquisition_experiment.runner import _DRAFT_PROMPT

        assert "商品页现在的语言" in _DRAFT_PROMPT, (
            "提示词里必须写明「用商品页现在的语言写」——"
            "否则模型会跟着提问者的语言写"
        )
        assert "current_title" in _DRAFT_PROMPT, (
            "提示词要把当前标题喂给模型——它是判断原语言的依据"
        )


class TestTheTargetPageSurvivesTheChain:
    """**目标页要一路带到产出**——不然方案是「无头」的

    这条漏过一次：op2 的 `not_applied` 分支没带 `target_handle`，
    产出的 `landing_page` 就是空的——读者不知道这份文案要写到哪里去。
    """

    def test_the_dry_run_plan_names_its_page(self, monkeypatch):
        from boxes.shopify_acquisition_experiment import runner as exp_runner

        class _W:
            def read_product(self, handle):
                return {"id": "gid://shopify/Product/1", "title": "T", "handle": handle,
                        "descriptionHtml": "<p>x</p>",
                        "seo": {"title": "", "description": ""}, "tags": []}

            def update_product(self, *_a, **_k):
                raise AssertionError("干跑不许写")

        monkeypatch.setattr(exp_runner.ShopifyWriter, "from_env",
                            staticmethod(lambda **kw: _W()))
        monkeypatch.setattr(exp_runner, "complete_json",
                            lambda prompt, **kw: {
                                "title": "T", "descriptionHtml": "<p>x</p>",
                                "seoTitle": "S", "seoDescription": "D", "tags": ["a"]})

        diagnosis = _diagnosis()
        diagnosis["target_landing_page"] = "fair-isle-stocking"
        plan = exp_runner.experiment_intake_handler({
            "round_id": "mkwagn-5d@2026-W46", "apply": False, "diagnosis_ref": diagnosis,
        })
        observation = exp_runner.experiment_work_handler(plan)
        result = exp_runner.experiment_packager_handler(observation)

        assert observation.get("target_handle") == "fair-isle-stocking", (
            "op2 把目标页丢了——"
            "op2→op3 之间会丢掉没在 observation schema 里声明的字段"
        )
        assert result.get("landing_page") == "fair-isle-stocking", (
            "产出的落地页是空的——这份方案看不出要写到哪里"
        )


class TestTheWriteDoesNotDamageWhatItDidNotMeanToTouch:
    """**只改你要改的，别把没打算改的抹了**

    两条都是实测踩出来的：
    - 写了一次 seo.description，**SEO 标题变空了**（Shopify 的 seo 是整体替换语义）；
    - 标签被整体替换，**分类标签（For Baby + Kids 之类）没了**——
      分类标签是用来把商品挂到分类页上的，删了商品可能就从分类里消失。
    """

    def test_seo_is_written_whole(self):
        from boxes.shopify_acquisition_experiment.runner import _merge_with_baseline

        baseline = {"seo.title": "旧 SEO 标题", "seo.description": "旧 SEO 描述"}
        merged = _merge_with_baseline({"seo.description": "新描述"}, baseline)
        assert merged["seo.title"] == "旧 SEO 标题", (
            "只写 seo.description 会把 seo.title 抹成空——必须一起写"
        )
        assert merged["seo.description"] == "新描述"

    def test_tags_are_merged_not_replaced(self):
        from boxes.shopify_acquisition_experiment.runner import _merge_with_baseline

        baseline = {"tags": ["Christmas", "For Baby + Kids", "Personalization"]}
        merged = _merge_with_baseline({"tags": ["christmas gift", "fair isle stocking"]},
                                      baseline)
        assert "For Baby + Kids" in merged["tags"], (
            "分类标签被换掉了——商品可能从分类页里消失"
        )
        assert "christmas gift" in merged["tags"], "新标签没进去"

    def test_the_landed_result_keeps_the_draft(self, monkeypatch):
        """**产出里必须看得见自己写了什么**——草稿要一路带到结果"""
        from boxes.shopify_acquisition_experiment import runner as exp_runner

        class _W:
            def read_product(self, handle):
                return {"id": "gid://shopify/Product/1", "title": "旧标题",
                        "handle": handle, "descriptionHtml": "<p>旧</p>",
                        "seo": {"title": "旧 SEO", "description": "旧描述"},
                        "tags": ["Christmas"]}

            def update_product(self, _id, changes):
                self.changes = changes
                return {"applied_fields": {"title": changes.get("title")},
                        "user_errors": []}

        writer = _W()
        monkeypatch.setattr(exp_runner.ShopifyWriter, "from_env",
                            staticmethod(lambda **kw: writer))
        monkeypatch.setattr(exp_runner, "complete_json",
                            lambda prompt, **kw: {
                                "title": "新标题", "descriptionHtml": "<p>新</p>",
                                "seoTitle": "新 SEO", "seoDescription": "新描述",
                                "tags": ["新标签"]})

        diagnosis = _diagnosis()
        diagnosis["target_landing_page"] = "fair-isle-stocking"
        plan = exp_runner.experiment_intake_handler({
            "round_id": "mkwagn-5d@2026-W46", "apply": True, "diagnosis_ref": diagnosis,
        })
        observation = exp_runner.experiment_work_handler(plan)
        result = exp_runner.experiment_packager_handler(observation)

        assert result.get("draft"), "产出里没有草稿——事后没人说得清写了什么"
        assert result["draft"]["title"] == "新标题"
        assert "For Baby + Kids" not in result["draft"]["tags"], (
            "草稿里的标签是模型起草的，合并发生在写入载荷上"
        )
