"""boxes.learning_english.runner - 英语盒的 operation 实现

op 链：
  1. `english_intake`      取投料 → 这一跳要做什么
  2. `english_work`        真干活 → 算听写得分率
  3. `english_packager`    装配产出（并**复算一遍**得分率）

## 三跳各干什么

- **intake**：把投料核对一遍。缺关键事实就**报错**——
  一条没有出处、没有分数的学习事件，记进去只会污染档案。
- **work**：算听写得分率（得分 ÷ 满分），写成人话。
- **packager**：装出产出，**并拿原始分与满分复算一遍**得分率。
  对不上就判红（`ratio_exact`）——
  这一步防的是「掌握度被写成了一个跟分数无关的数」。

**失败路径必须显式**：每一次失败都要带原因抛出来，
不要静默跳过、不要填默认值。兜底会把投料错伪装成「学得不好」。
"""

from __future__ import annotations

import hashlib
from typing import Any

from runtime.runner import HandlerRunner


SOURCE_KINDS = ("observed", "reported")


def _num(input_data: dict[str, Any], key: str) -> float | None:
    """取一个数；没有就返回 None（**不填默认值**）"""
    value = input_data.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_id(input_data: dict[str, Any]) -> str:
    """这次学习事件的唯一号——**从内容推导**，不是随机数

    随机 id 会让同一件事重记一次就变成两件事；
    而这条事件是**有出处的事实**，它的号必须跟着内容走。
    """
    parts = [
        str(input_data.get("activity") or ""),
        str(input_data.get("observed_at") or ""),
        str(input_data.get("evidence") or ""),
        str(input_data.get('dictation_score') or ""),
        str(input_data.get('dictation_total') or ""),
    ]
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"english-{digest}"


def english_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：核对投料，说清这一跳要做什么"""
    activity = str(input_data.get("activity") or "").strip()
    if not activity:
        raise ValueError(
            "投料里没有 activity——这次做了什么没说清楚，"
            "这样的学习事件记进去只会污染档案"
        )

    source_kind = str(input_data.get("source_kind") or "").strip()
    if source_kind not in SOURCE_KINDS:
        raise ValueError(
            f"source_kind={source_kind!r} 不认得——"
            "只能是 observed（这次真的看到了）或 reported（家里口述的）"
        )

    evidence = str(input_data.get("evidence") or "").strip()
    if not evidence:
        raise ValueError(
            "投料里没有 evidence——出处必须写清楚（作业本 P12 / 单元测卷 / 口述）"
        )

    observed_at = str(input_data.get("observed_at") or "").strip()
    if not observed_at:
        raise ValueError("投料里没有 observed_at——学习发生的时间必须写")

    score = _num(input_data, 'dictation_score')
    total = _num(input_data, 'dictation_total')
    if total is None or total <= 0:
        raise ValueError(
            f"dictation_total（满分）必须是正数，现在拿到 {total!r}——"
            "没有满分就算不出听写得分率"
        )
    if score is None:
        raise ValueError(f"dictation_score（得分）没有投进来")
    if score < 0:
        raise ValueError(f"dictation_score（得分）不能是负数：{score!r}")
    if score > total:
        raise ValueError(
            f"dictation_score（得分）{score:g} 超过了 dictation_total（满分）{total:g}——"
            "投料错了，别让错数字进档案"
        )

    out: dict[str, Any] = {
        "plan": f"记一条英语学习事件：{activity}（{source_kind}）",
    }
    # 原样带下去：work 跳要拿原始分与满分算得分率
    for key, value in input_data.items():
        out[key] = value
    return out


def english_work_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：真干活——算听写得分率，写成人话"""
    score = _num(input_data, 'dictation_score')
    total = _num(input_data, 'dictation_total')
    if score is None or total is None or total <= 0:
        # 正常走不到这里：intake 已经拦过。留着是为了不让这一步静默算出垃圾。
        raise ValueError("算听写得分率需要得分和满分")
    mastery = score / total

    detail = "听写 {dictation_score:g}/{dictation_total:g}".format(
        **{"dictation_score": score, "dictation_total": total},
    )
    extra: list[str] = []
    _v = _num(input_data, 'new_words')
    if _v is not None:
        extra.append('新学 {value:g} 个单词'.format(value=_v))
    if extra:
        detail = detail + "；" + "、".join(extra)

    return {
        "observed": f"{detail}，听写得分率 {mastery:.2f}",
        "detail": detail,
        "mastery": mastery,
        # 原始量一并带下去：装配跳要拿它们复算
        **{k: v for k, v in input_data.items()},
    }


def english_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：装配产出，并**复算一遍**得分率

    复算的意义：工作跳算的 mastery 会不会被写成一个跟分数无关的数？
    装配跳拿原始分与满分再算一遍，对不上就判红（`ratio_exact`）。
    """
    score = _num(input_data, 'dictation_score')
    total = _num(input_data, 'dictation_total')
    mastery = _num(input_data, "mastery")
    if score is None or total is None or mastery is None or total <= 0:
        raise ValueError("装产出需要得分、满分和算出来的掌握度")
    ratio_exact = abs(mastery - score / total) < 1e-9

    errors = input_data.get("errors") or []
    if not isinstance(errors, list):
        errors = [errors]
    notes = str(input_data.get("notes") or "")
    detail = str(input_data.get("detail") or "")
    if errors:
        detail = detail + "；错在：" + "、".join(str(e) for e in errors)

    return {
        "record_id": _record_id(input_data),
        "subject": "english",
        "activity": str(input_data.get("activity") or ""),
        "observed_at": str(input_data.get("observed_at") or ""),
        "mastery": mastery,
        "evidence": str(input_data.get("evidence") or ""),
        "source_kind": str(input_data.get("source_kind") or ""),
        "detail": detail,
        "notes": notes,
        "ratio_exact": ratio_exact,
    }


LEARNING_ENGLISH_HANDLERS: dict[str, Any] = {
    "english_intake": english_intake_handler,
    "english_work": english_work_handler,
    "english_packager": english_packager_handler,
}

RUNNER = HandlerRunner(LEARNING_ENGLISH_HANDLERS, owner="learning_english_box")
