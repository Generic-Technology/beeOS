"""tests.test_shopify_scope —— giftsjourneyrebirth 店铺经营协作边界的守卫

这组测试盯三件事：

1. **接线没走形**：唯一汇点是档案盒、档案盒没有出边、
   闭环从实验盒回到快照盒；
2. **通道真的接得上**：每条交接边都有通道，通道是下游投料里的 `ref:` 字段；
3. **归档是真归档**：三段共享同一个 `round_id`，
   而档案盒**自己复核**上游的「我过了」标志位。

第 3 条里那条复核最要紧：
上游说「指标都标好了」不算数——档案盒重新算一遍。
没有这条，盒子报「全过」而实际有一半指标没有出处，
看板上没有任何东西提示这件事。
"""

from __future__ import annotations

import pytest

from boxes.shopify_archive.runner import (
    archive_intake_handler,
    archive_verify_handler,
)
from boxes.shopify_archive.schemas import ALL_SCHEMAS as ARCHIVE_SCHEMAS
from core.models import EdgeKind
from fulfillment_scopes import FULFILLMENT_SCOPE_REGISTRY
from fulfillment_scopes.shopify.shapes import (
    DIAGNOSIS_OUTPUT_SCHEMA,
    EXPERIMENT_OUTPUT_SCHEMA,
    SNAPSHOT_OUTPUT_SCHEMA,
)
from fulfillment_scopes.shopify.vocabulary import (
    EXCLUDED_ENTRY_PATH_PREFIXES,
    METRIC_STATUS,
    ROUND_ID_FIELD,
)
from kanban.trigger import BOX_REGISTRY, fulfillment_scope_assemblies

SCOPE_ID = "shopify_store"
SNAPSHOT = "shopify_snapshot_box"
DIAGNOSIS = "shopify_traffic_diagnosis_box"
EXPERIMENT = "shopify_acquisition_experiment_box"
MARKET_SIGNAL = "shopify_market_signal_box"
ARCHIVE = "shopify_archive_box"


def _declaration():
    return FULFILLMENT_SCOPE_REGISTRY[SCOPE_ID]


def _make_snapshot(round_id: str = "giftsjourneyrebirth@2026-W47") -> dict:
    """一份合法的快照产出（跟归档盒声明的形状对齐）"""
    return {
        "round_id": round_id,
        "observed_at": "2026-11-14T12:00:00+00:00",
        "window_start": "2026-10-18",
        "window_end": "2026-11-14",
        "store_domain": "giftsjourneyrebirth.myshopify.com",
        "source": "shopifyql: FROM sessions ... (2026-01)",
        "api_version": "2026-01",
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
        "excluded_entry_paths": list(EXCLUDED_ENTRY_PATH_PREFIXES),
        "metrics": [
            {
                "name": "qualified_sessions",
                "value": 148,
                "status": "observed",
                "source": "shopifyql: FROM sessions",
                "retrieved_at": "2026-11-14T12:00:00+00:00",
                "note": "",
            },
        ],
        "unverified": [],
        "data_present": True,
        "metric_status_complete": True,
        "source_present": True,
        "session_count_consistent": True,
        "top_landing_pages": [],
        "top_sources": [],
        "top_search_terms": [],
    }


# ============================================================
# 接线
# ============================================================


class TestTheScopeIsWellFormed:
    def test_the_scope_assembles(self):
        """这个协作边界装得上——声明自洽、规则全绿"""
        assemblies = fulfillment_scope_assemblies()
        assert SCOPE_ID in assemblies, "shopify_store 没进协作边界注册表"
        assembled = assemblies[SCOPE_ID]
        assert assembled.ok, (
            "shopify_store 装配没过："
            + "; ".join(p.message for p in assembled.problems)
            + " | "
            + "; ".join(f"{r.kind}={r.passed}/{r.total}" for r in assembled.rules_reading)
        )

    def test_the_archive_box_is_the_only_sink(self):
        """唯一汇点是档案盒——出度为 0 的那一只

        汇点判定是「出度 0 且入度大于 0 的唯一一只」（`core/wiring.py`）。
        **档案盒一旦有出边，全链就画不出来**——所以这条钉死。
        """
        declaration = _declaration()
        outgoing = [
            e for e in declaration.edges
            if e.from_box == ARCHIVE
        ]
        assert outgoing == [], (
            f"档案盒有出边：{[(e.from_box, e.to_box, e.kind.value) for e in outgoing]}——"
            "它有出边就不再是唯一汇点，整条链画不出来"
        )

    def test_the_feedback_edge_starts_at_the_experiment_box(self):
        """闭环从**实验盒**回到**链首**

        两条约束：
        - 源必须是链的末位（末位 → 首位，首尾相接）；
        - 目标是入口（回到中段就分不出「下一轮从哪儿起」）。

        链首现在是市场信号盒 → 闭环回到它 → 下一轮重新看市场、重新取数。
        """
        feedback = [e for e in _declaration().edges if e.kind == EdgeKind.feedback]
        assert len(feedback) == 1, f"闭环边应该正好一条：{feedback}"
        edge = feedback[0]
        assert edge.from_box == EXPERIMENT, (
            f"闭环的源是 {edge.from_box}——末位（实验盒）才有资格开下一轮"
        )
        assert edge.to_box == MARKET_SIGNAL, (
            f"闭环的目标是 {edge.to_box}——闭环要回到**链首**"
        )
        assert edge.no_channel is True, "闭环边不是数据交接，不该有通道"

    def test_every_handoff_edge_has_a_channel(self):
        """每条交接边都有通道——没有通道的交接边是画出来的假关系"""
        for edge in _declaration().edges:
            if edge.kind != EdgeKind.handoff:
                continue
            assert edge.via, f"交接边 {edge.from_box} → {edge.to_box} 没有通道"

    def test_the_channel_is_a_ref_field_in_the_downstream_task_schema(self):
        """通道是下游投料里的 `ref:` 字段——不是随便一个名字

        通道名写错一个字母，交接在运行期才断；
        而「下游投料里没有这个字段」在装配期就该断。
        """
        for edge in _declaration().edges:
            if edge.kind != EdgeKind.handoff:
                continue
            downstream = BOX_REGISTRY[edge.to_box][0]()
            task = downstream.task[0]
            schema = downstream.get_schema(task.task_schema)
            field = next((f for f in schema.fields if f.name == edge.via), None)
            assert field is not None, (
                f"{edge.to_box} 的投料里没有 {edge.via} 这个通道字段"
            )
            assert field.type.startswith("ref:"), (
                f"{edge.to_box}.{edge.via} 的类型是 {field.type}，不是 ref:"
            )

    def test_no_archive_edge_rule(self):
        """不声明 `all_archived`——归档盒**收产出**，那是 Learning 范式

        WMS 那条规则是给「不收产出的档案盒」准备的；
        套在会判错（Learning 边界的声明里就是这么写的）。
        """
        kinds = {rule.kind.value for rule in _declaration().rules}
        assert "all_archived" not in kinds, (
            "归档盒收上游产出，不能声明 all_archived"
        )


# ============================================================
# 轮次号：三次投递 → 一条记录
# ============================================================


class TestTheRoundIdGroupsTheArchive:
    @pytest.mark.parametrize(
        "box_id", [SNAPSHOT, DIAGNOSIS, EXPERIMENT, ARCHIVE]
    )
    def test_every_box_carries_the_round_id(self, box_id):
        """四只盒子都认同一个轮次号

        不认同一个号 → 三次投递归成三条互不相干的记录。
        """
        definition = BOX_REGISTRY[box_id][0]()
        task = definition.task[0]
        schema = definition.get_schema(task.task_schema)
        names = {f.name for f in schema.fields}
        subject_field = task.subject_ref_field or ""
        if task.subject_ref_subfield:
            assert subject_field in names, (
                f"{box_id}: subject_ref_field={subject_field!r} 不在投料里"
            )
        else:
            assert subject_field == ROUND_ID_FIELD, (
                f"{box_id}: subject_ref_field={subject_field!r}，"
                f"这条边界约定用 {ROUND_ID_FIELD}"
            )

    def test_the_three_deliveries_share_one_subject(self):
        """四只盒子认领的**业务对象是同一个**——一轮经营

        字段名不必一样：诊断盒拿到的是「快照里的 round_id」，
        实验盒拿到的是「诊断里的 round_id」，档案盒直接拿 round_id。
        **解析到最后的那个字段名必须都是 round_id**——
        否则三次投递归成三条互不相干的记录。
        """
        leaves = set()
        for box_id in (SNAPSHOT, DIAGNOSIS, EXPERIMENT, ARCHIVE):
            definition = BOX_REGISTRY[box_id][0]()
            task = definition.task[0]
            leaves.add(task.subject_ref_subfield or task.subject_ref_field)
        assert leaves == {ROUND_ID_FIELD}, (
            f"四只盒子的业务对象不是同一个：{leaves}——"
            "三次投递归不到同一条经营记录"
        )


# ============================================================
# 档案盒的复核：不信上游说「我过了」
# ============================================================


class TestTheArchiveRecomputesHonesty:
    def test_a_snapshot_that_lies_about_its_status_is_caught(self):
        """上游说「指标都标好了」，但有一条 observed 没有出处 → 复核判红

        这是这组测试最要紧的一条：
        上游的 `metric_status_complete = True` **不算数**，
        档案盒重新算一遍。
        """
        payload = _make_snapshot()
        # 上游自己说「都标好了」——但下面那条 observed 没有出处
        payload["metric_status_complete"] = True
        payload["metrics"] = [
            {
                "name": "qualified_sessions",
                "value": 148,
                "status": "observed",
                "source": "",            # ← 没有出处
                "retrieved_at": "2026-11-14T12:00:00+00:00",
                "note": "",
            },
        ]

        part = archive_intake_handler({
            "round_id": payload["round_id"],
            "snapshot_ref": payload,
        })
        verdict = archive_verify_handler(part)

        assert verdict["honest"] is False, (
            "有一条 observed 指标没有出处，复核却判它诚实——"
            "复核信了上游的标志位"
        )
        assert any("出处" in u for u in verdict["unknowns"]), verdict["unknowns"]

    def test_an_unknown_status_is_caught(self):
        """自造的状态值不认——`not_collected` 这种第四态进不来"""
        payload = _make_snapshot()
        payload["metrics"] = [
            {"name": "sessions", "value": 168, "status": "not_collected",
             "source": "x", "note": ""},
        ]
        part = archive_intake_handler({
            "round_id": payload["round_id"], "snapshot_ref": payload,
        })
        verdict = archive_verify_handler(part)
        assert verdict["honest"] is False
        assert any("not_collected" in u for u in verdict["unknowns"])

    def test_an_honest_snapshot_passes(self):
        """标对了就该过——复核不能只会判红"""
        payload = _make_snapshot()
        part = archive_intake_handler({
            "round_id": payload["round_id"], "snapshot_ref": payload,
        })
        verdict = archive_verify_handler(part)
        assert verdict["honest"] is True, verdict["unknowns"]
        assert verdict["source_present"] is True

    def test_only_one_channel_per_delivery(self):
        """一次只能投一段——一个通道只能接一份产出"""
        with pytest.raises(ValueError, match="只能接一份产出"):
            archive_intake_handler({
                "round_id": "giftsjourneyrebirth@2026-W47",
                "snapshot_ref": _make_snapshot(),
                "diagnosis_ref": {
                    "round_id": "giftsjourneyrebirth@2026-W47",
                    "observed_at": "2026-11-14T12:00:00+00:00",
                    "bottleneck_stage": "no_impression",
                    "bottleneck_evidence": ["qualified_sessions"],
                    "hypothesis": "分发没启动",
                    "target_customer": "个性化节日礼物的购买者",
                    "target_scenario": "个性化节日礼物",
                    "prioritized_channel": "natural_search",
                    "unknowns": [],
                    "snapshot_ref_present": True,
                    "hypothesis_present": True,
                    "evidence_validated": True,
                },
            })

    def test_an_empty_delivery_is_refused(self):
        """三个通道都空 → 报错，不归档空记录"""
        with pytest.raises(ValueError, match="都是空的"):
            archive_intake_handler({"round_id": "giftsjourneyrebirth@2026-W47"})


# ============================================================
# 形状只有一份事实
# ============================================================


class TestThereIsOnlyOneShapePerArtifact:
    @pytest.mark.parametrize(
        "box_id, schema_id",
        [
            (DIAGNOSIS, "schema_shopify_snapshot_output"),
            (EXPERIMENT, "schema_shopify_diagnosis_output"),
            (ARCHIVE, "schema_shopify_snapshot_output"),
            (ARCHIVE, "schema_shopify_diagnosis_output"),
            (ARCHIVE, "schema_shopify_experiment_output"),
        ],
    )
    def test_the_consumer_declares_the_canonical_shape(self, box_id, schema_id):
        """消费者声明的是边界维护的那一份——不是各抄一份

        抄四份的代价是：改一处字段忘了改另一处 →
        交接在装配期判红，或者更糟：判绿但接错了对象。
        """
        definition = BOX_REGISTRY[box_id][0]()
        declared = {s.id: s for s in definition.schemas}
        canonical = {
            "schema_shopify_snapshot_output": SNAPSHOT_OUTPUT_SCHEMA,
            "schema_shopify_diagnosis_output": DIAGNOSIS_OUTPUT_SCHEMA,
            "schema_shopify_experiment_output": EXPERIMENT_OUTPUT_SCHEMA,
        }[schema_id]
        assert schema_id in declared, f"{box_id} 没声明 {schema_id}"
        assert declared[schema_id] is canonical, (
            f"{box_id} 声明的 {schema_id} 不是边界维护的那一份——"
            "两份事实必然漂"
        )

    def test_the_producer_produces_the_canonical_shape(self):
        """生产者产出形状 == 边界维护的那一份"""
        definition = BOX_REGISTRY[SNAPSHOT][0]()
        assert definition.result.result_schema == SNAPSHOT_OUTPUT_SCHEMA.id
        declared = {s.id: s for s in definition.schemas}
        assert declared[SNAPSHOT_OUTPUT_SCHEMA.id] is SNAPSHOT_OUTPUT_SCHEMA

    def test_the_archive_declares_all_three(self):
        """档案盒收三份——三种形状都在它的 schemas 里"""
        ids = {s.id for s in ARCHIVE_SCHEMAS}
        assert {
            "schema_shopify_snapshot_output",
            "schema_shopify_diagnosis_output",
            "schema_shopify_experiment_output",
        } <= ids


# ============================================================
# 口径
# ============================================================


class TestTheMetricVocabulary:
    def test_three_states_only(self):
        """状态只有三态——没有第四态"""
        assert METRIC_STATUS == ["observed", "declared_from_baseline", "unverifiable"]

    def test_the_exclusion_rule_excludes_only_non_acquisition_entries(self):
        """排除只按非获客入口——**购物车和结账不算排除条件**

        按路径把整个 session 删掉会扔掉最有价值的高意向流量。
        """
        assert "/account/" in EXCLUDED_ENTRY_PATH_PREFIXES
        for forbidden in ("/cart", "/checkout"):
            assert not any(
                prefix.startswith(forbidden)
                for prefix in EXCLUDED_ENTRY_PATH_PREFIXES
            ), f"{forbidden} 不许出现在排除清单里"


class TestAMissingValueIsNeverAnObservation:
    """**值是 None = 没取到，不是观测值**

    这条跟「取不到不写 0」是同一条铁律的两个面：
    值不能编（不写 0），**状态也不能伪装**（不标 observed）。
    标错了，下游（模型、诊断盒）就把它当事实用——而它其实没测过。
    """

    def test_a_none_valued_metric_is_unverifiable_not_observed(self):
        from boxes.shopify_snapshot.runner import snapshot_packager_handler

        raw = {
            "round_id": "giftsjourneyrebirth@2026-W47",
            "source": "shopifyql: FROM sessions",
            "api_version": "2026-01",
            "retrieved_at": "2026-11-16T08:30:00+00:00",
            "totals": {
                "sessions": 176,
                "online_store_visitors": 163,
                "pageviews": 312,
                "sessions_with_cart_additions": 7,
                "sessions_that_reached_checkout": 6,
                "sessions_that_completed_checkout": 4,
                "conversion_rate": 0.0227,
                # products_viewed / collections_viewed / searches_submitted 没取到
            },
            "landing_rows": [
                {"landing_page_path": "/products/x", "sessions": 176,
                 "sessions_with_cart_additions": 7, "sessions_that_reached_checkout": 6,
                 "sessions_that_completed_checkout": 4},
            ],
            "view_rows": [],
            "warnings": [],
            "pages_fetched": 1,
            "fetch_ok": True,
            "window_start": "2026-10-18",
            "window_end": "2026-11-15",
            "store_domain": "giftsjourneyrebirth.myshopify.com",
        }
        result = snapshot_packager_handler(raw)
        by_name = {m["name"]: m for m in result["metrics"]}

        for missing in ("products_viewed", "collections_viewed", "searches_submitted"):
            metric = by_name[missing]
            assert metric["value"] is None, f"{missing} 不该有值"
            assert metric["status"] == "unverifiable", (
                f"{missing} 的值是 None，状态却标成 {metric['status']}——"
                "没测到的量不能伪装成测到了"
            )
            assert metric["note"], f"{missing} 标了 unverifiable 就必须写为什么"

        # 取到的量照旧是 observed
        assert by_name["total_sessions"]["status"] == "observed"
