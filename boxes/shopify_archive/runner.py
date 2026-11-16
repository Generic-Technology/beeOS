"""boxes.shopify_archive.runner —— 经营档案盒的三跳实现

op 链：
  1. `archive_intake`    接住这一段（三个通道里恰好一个）
  2. `archive_verify`    **自己复核**：这段标得实不实
  3. `archive_packager`  装成一条档案记录

## 为什么要有 `archive_verify` 这一跳

上游会说自己「过了」（`metric_status_complete = true`）。
**它自己说的不算**——这里重新算一遍：

- 快照段：每条指标状态合法吗？observed 有出处吗？unverifiable 有原因吗？
- 诊断段：证据引用非空吗？未知项列了吗？
- 实验段：变更项、停止条件、归因方式齐全吗？

**盒子说「我过了」就过了，没人回头核对它说的对不对**——
那是仓里已经踩过的坑（`boxes/wms_design/plugins.py` 就是为这件事存在的）。

## 三个通道一次只能填一个

一个通道只能接一份产出（`fulfillment_scopes/__init__.py` 的
`handoff_payload` 明写）。所以档案盒跑三次，一次一段。
**三次共享同一个 `round_id`**，归档时按它分组 → 同一条经营记录。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from runtime.runner import HandlerRunner

from fulfillment_scopes.shopify.vocabulary import (
    ATTRIBUTION_METHODS,
    METRIC_STATUS,
)

#: 三个通道 → 这一段叫什么 → 这一段在 op 链里叫什么
CHANNELS = (
    ("snapshot_ref", "snapshot", "snapshot_payload"),
    ("diagnosis_ref", "diagnosis", "diagnosis_payload"),
    ("experiment_ref", "experiment", "experiment_payload"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# op 1：接住这一段
# ============================================================


def archive_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：接住这一段（三个通道里恰好一个）"""
    round_id = str(input_data.get("round_id") or "").strip()
    if not round_id:
        raise ValueError(
            "投料里没有 round_id——三段靠它归成同一条经营记录，"
            "没有它这次归档归不到任何一轮"
        )

    present = [
        (name, kind, field) for name, kind, field in CHANNELS if input_data.get(name)
    ]
    if not present:
        raise ValueError(
            "三个通道（snapshot_ref / diagnosis_ref / experiment_ref）都是空的——"
            "档案盒靠交接通道收产出，没有它就没有东西可归档"
        )
    if len(present) > 1:
        raise ValueError(
            f"一次投了 {len(present)} 段：{[k for _, k, _ in present]}——"
            "一个通道只能接一份产出，三段要分三次投（共享同一个 round_id）"
        )

    channel, kind, field = present[0]
    payload = input_data[channel]
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"{channel} 里的产出是空的——归档不收空记录")

    return {
        "round_id": round_id,
        "record_kind": kind,
        "snapshot_payload": payload if field == "snapshot_payload" else None,
        "diagnosis_payload": payload if field == "diagnosis_payload" else None,
        "experiment_payload": payload if field == "experiment_payload" else None,
    }


# ============================================================
# op 2：自己复核
# ============================================================


def archive_verify_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：复核这一段——**不信上游说「我过了」**"""
    kind = input_data["record_kind"]
    payload = input_data.get(f"{kind}_payload") or {}
    unknowns: list[str] = []

    if kind == "snapshot":
        honest, source_present, unknowns = _verify_snapshot(payload)
        summary = (
            f"快照：合格访问 {payload.get('qualified_sessions')} / "
            f"全部真人 session {payload.get('total_sessions')}；"
            f"转化率 {payload.get('conversion_rate')}"
        )
    elif kind == "diagnosis":
        honest, source_present, unknowns = _verify_diagnosis(payload)
        summary = (
            f"诊断：断点落在 {payload.get('bottleneck_stage')}；"
            f"假设「{payload.get('hypothesis')}」"
        )
    else:
        honest, source_present, unknowns = _verify_experiment(payload)
        summary = (
            f"实验：{payload.get('scenario')} → {payload.get('landing_page')}；"
            f"归因方式 {payload.get('attribution_method')}；"
            f"成本上限 {payload.get('cost_cap')}"
        )

    return {
        "round_id": input_data["round_id"],
        "record_kind": kind,
        "honest": honest,
        "source_present": source_present,
        "unknowns": unknowns,
        "summary": summary,
    }


def _verify_snapshot(payload: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    """复核快照段：每条指标的状态、来源、原因齐不齐"""
    unknowns: list[str] = list(payload.get("unverified") or [])
    metrics = payload.get("metrics") or []
    if not metrics:
        unknowns.append("快照里一条指标都没有——没有指标就没有事实")
        return False, False, unknowns

    honest = True
    for metric in metrics:
        status = str(metric.get("status") or "")
        if status not in METRIC_STATUS:
            honest = False
            unknowns.append(
                f"指标 {metric.get('name')} 的 status={status!r} 不认得"
            )
            continue
        if status == "observed" and not str(metric.get("source") or "").strip():
            honest = False
            unknowns.append(f"指标 {metric.get('name')} 说是 observed，却没有出处")
        if status == "unverifiable" and not str(metric.get("note") or "").strip():
            honest = False
            unknowns.append(f"指标 {metric.get('name')} 说是 unverifiable，却没写为什么")

    if payload.get("session_count_consistent") is False:
        unknowns.append("快照自己说落地页合计与总量对不上——口径有出入")

    source_present = bool(str(payload.get("source") or "").strip())
    if not source_present:
        honest = False
        unknowns.append("快照没有出处")

    return honest, source_present, unknowns


def _verify_diagnosis(payload: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    """复核诊断段：证据引用与未知项齐不齐"""
    unknowns: list[str] = list(payload.get("unknowns") or [])
    stage = str(payload.get("bottleneck_stage") or "")
    evidence = [e for e in (payload.get("bottleneck_evidence") or []) if str(e).strip()]

    honest = True
    if not evidence and stage != "no_single_bottleneck":
        honest = False
        unknowns.append("诊断给了断点结论，却没有任何证据引用")
    if payload.get("evidence_validated") is False:
        honest = False
        unknowns.append("诊断自己说证据没通过校验——它引的指标名在快照里不存在")
    if not str(payload.get("hypothesis") or "").strip():
        honest = False
        unknowns.append("诊断没有假设")

    source_present = bool(evidence) or bool(str(payload.get("hypothesis") or "").strip())
    return honest, source_present, unknowns


def _verify_experiment(payload: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    """复核实验段：变更项、停止条件、归因方式齐不齐"""
    unknowns: list[str] = list(payload.get("unverified") or [])
    honest = True

    if not [c for c in (payload.get("changes") or []) if str(c).strip()]:
        honest = False
        unknowns.append("实验没有写清改了什么——「优化内容」不是变更项")
    if not str(payload.get("stopping_condition") or "").strip():
        honest = False
        unknowns.append("实验没有停止条件——没有停止条件的实验不会结束")
    method = str(payload.get("attribution_method") or "")
    if method not in ATTRIBUTION_METHODS:
        honest = False
        unknowns.append(f"归因方式 {method!r} 不认得")

    source_present = bool(str(payload.get("baseline_ref") or "").strip())
    if not source_present:
        honest = False
        unknowns.append("实验没有写基线——没有基线的结论没法跟谁比")

    return honest, source_present, unknowns


# ============================================================
# op 3：装成一条档案记录
# ============================================================


def archive_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：装成一条档案记录"""
    kind = input_data["record_kind"]
    round_id = input_data["round_id"]
    return {
        "round_id": round_id,
        "observed_at": _now(),
        "record_kind": kind,
        "archived_parts": [kind],
        "summary": input_data["summary"],
        "metric_status_honest": bool(input_data["honest"]),
        "unknowns": list(input_data["unknowns"] or []),
        "source_present": bool(input_data["source_present"]),
        "subject_present": bool(round_id.strip()),
    }


SHOPIFY_ARCHIVE_HANDLERS: dict[str, Any] = {
    "archive_intake": archive_intake_handler,
    "archive_verify": archive_verify_handler,
    "archive_packager": archive_packager_handler,
}

RUNNER = HandlerRunner(SHOPIFY_ARCHIVE_HANDLERS, owner="shopify_archive_box")
