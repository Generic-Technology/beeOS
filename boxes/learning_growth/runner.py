"""boxes.learning_growth.runner - 成长记录盒的 operation 实现

op 链：
  1. `growth_intake`      接住上游那条学习记录 → 这一跳要做什么
  2. `growth_work`        核对这条记录 → 真的看到了什么
  3. `growth_packager`    装配成档案里的一条

## 它怎么「追加」

**档案就是这只盒子的历次履约产出**——每次履约一条，按
`observed_at`（学习发生的时间）排。落盘是运行时的事：
产出进了履约记录，就是进了档案。

**为什么不给档案单独建一个文件**：那就是**第二份事实**——
它跟履约记录会漂，而且漂了没有任何东西会报错。
履约记录本来就是这个事实的所在处。

**为什么时间线按 `observed_at` 排，不按归档时间**：档案是
「学习什么时候发生的」，不是「什么时候补录的」。
补录三天前的一次练习，不该让这条记录排到今天后面去。

## 失败路径必须显式

接不住上游那条（缺字段 / 出处类型不认识）就**报错**，
不降级成「先记个空的」——空记录会让人以为那天学过。
"""

from __future__ import annotations

from typing import Any

from runtime.runner import HandlerRunner


SOURCE_KINDS = ("observed", "reported")

# 这只盒子接受的那份形状里有哪些字段——**缺一个都不入档**
REQUIRED_FIELDS = (
    "record_id",
    "subject",
    "activity",
    "observed_at",
    "mastery",
    "evidence",
    "source_kind",
    "detail",
)


def growth_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：接住上游那条学习记录，说清这一跳要做什么"""
    result = input_data.get("subject_result")
    if not isinstance(result, dict) or not result:
        raise ValueError(
            "投料里没有 subject_result——成长记录盒靠交接通道接上游的产出，"
            "没有它就没有东西可归档"
        )

    missing = [f for f in REQUIRED_FIELDS if result.get(f) in (None, "")]
    if missing:
        raise ValueError(
            f"上游那条学习记录缺字段：{missing}——缺字段的记录不入档，"
            "补齐了再投"
        )

    source_kind = str(result.get("source_kind") or "")
    if source_kind not in SOURCE_KINDS:
        raise ValueError(
            f"source_kind={source_kind!r} 不认得——"
            "档案只收 observed（真的看到了）或 reported（家里口述的）"
        )

    return {
        "plan": (
            f"把一条{result.get('subject')}的学习记录"
            f"（{result.get('activity')}）归档"
        ),
        "subject_result": result,
    }


def growth_work_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：核对这条记录 —— 它真的能进档案吗"""
    result = input_data["subject_result"]

    try:
        mastery = float(result["mastery"])
    except (TypeError, ValueError):
        raise ValueError(
            f"mastery 不是数：{result.get('mastery')!r}——档案不收算不出来的数"
        )
    if mastery < 0 or mastery > 1:
        raise ValueError(
            f"mastery={mastery:g} 越界（0..1）——上游算错了，这条不入档"
        )

    detail = str(result.get("detail") or "")
    notes = str(result.get("notes") or "")
    if notes:
        detail = detail + "；备注：" + notes

    return {
        "observed": (
            f"{result.get('subject')} · {result.get('activity')} · "
            f"掌握度 {mastery:.2f} · {result.get('source_kind')}"
        ),
        "detail": detail,
        "subject_result": result,
    }


def growth_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：装配成档案里的一条"""
    result = input_data["subject_result"]

    return {
        # **沿用上游的号**：同一个事实一个号，
        # 档案里出现两条同一个号 = 同一件事记了两遍，看板上要能看出来。
        "record_id": str(result.get("record_id") or ""),
        "subject": str(result.get("subject") or ""),
        "activity": str(result.get("activity") or ""),
        "observed_at": str(result.get("observed_at") or ""),
        "mastery": float(result["mastery"]),
        "evidence": str(result.get("evidence") or ""),
        "source_kind": str(result.get("source_kind") or ""),
        "detail": str(input_data.get("detail") or result.get("detail") or ""),
        "notes": str(result.get("notes") or ""),
        "source_record_present": bool(str(result.get("record_id") or "").strip()),
    }


LEARNING_GROWTH_HANDLERS: dict[str, Any] = {
    "growth_intake": growth_intake_handler,
    "growth_work": growth_work_handler,
    "growth_packager": growth_packager_handler,
}

RUNNER = HandlerRunner(LEARNING_GROWTH_HANDLERS, owner="learning_growth_box")
