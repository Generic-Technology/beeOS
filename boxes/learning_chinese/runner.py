"""boxes.learning_chinese.runner - 语文盒的 operation 实现

op 链：
  1. `chinese_read_photo` 看照片 → 把图上印着的事实抄出来
  2. `chinese_intake`      取投料 → 这一跳要做什么
  3. `chinese_work`        真干活 → 算阅读得分率
  4. `chinese_packager`    装配产出（并**复算一遍**得分率）

## 三跳各干什么

- **intake**：把投料核对一遍。缺关键事实就**报错**——
  一条没有出处、没有分数的学习事件，记进去只会污染档案。
- **work**：算阅读得分率（得分 ÷ 满分），写成人话。
- **packager**：装出产出，**并拿原始分与满分复算一遍**得分率。
  对不上就判红（`ratio_exact`）——
  这一步防的是「掌握度被写成了一个跟分数无关的数」。

**失败路径必须显式**：每一次失败都要带原因抛出来，
不要静默跳过、不要填默认值。兜底会把投料错伪装成「学得不好」。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from runtime import images
from runtime.llm import complete_json
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
        str(input_data.get('reading_score') or ""),
        str(input_data.get('reading_total') or ""),
    ]
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"chinese-{digest}"


_PHOTO_PROMPT = """你是一张语文作业照片的抄写员。给你一张语文作业/试卷的照片，
把上面**真实印着**的内容抄成 JSON。

只抄图上印着的。看不清就填 null——**不要猜、不要补全、不要编**。

字段：
- reading_score: 阅读理解部分的得分。图上阅读那一部分的得分（可能是「18」「18/20」里的分子）。找不到就 null。
- reading_total: 阅读理解部分的满分。图上阅读那一部分的满分（可能是「/20」「满分20」里的分母）。找不到就 null。
  **不要默认 100**：图上没印满分就是 null，猜一个满分会把错的比率写进档案。
- observed_at: 图上写的日期（YYYY-MM-DD）。作业本上常有日期；找不到就 null。
- activity: 图上写的这次是什么（作业 / 单元测 / 订正 / 复习 / 第几页）。找不到就 null。
- errors: 错题。看得出错在哪就一条一个（人话，如「第2题除法算错了」）；
  看得见全对就填 []；看不出来就填 null。
- notes: 其他看得出的信息。作文的字数、新学的字这些看得出的也一并写进 notes。没有就 null。

**不要自己做算术**：reading_score 和 reading_total 各自抄图上印着的那一格。
看到「92/100」就填 92 和 100；只看到「92」就 reading_score=92、reading_total=null
（除非别处印着满分）。

只输出 JSON，不要解释。"""


def _filled(value: Any) -> bool:
    """「投料方真的给了这个值」的判法

    空串 / None / 空数组都算**没给**——空数组是「没填错题」，
    不是「填了「错题：一个都没有」」。
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _photo_digest(photo: str) -> str:
    """照片指纹——**从图片字节推导**，不是随机数

    同一张照片重投一次，指纹必须还是同一个：
    指纹进了 evidence，而 evidence 进了 record_id 的推导，
    所以指纹一漂，「同一件事」就变成「两件事」。
    """
    payload = photo.partition(",")[2] or photo
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:12]


def _photo_date(value: Any) -> str | None:
    """照片上的日期 → YYYY-MM-DD；认不出写法就 None

    认写法 ≠ 认可内容：拼不成 YYYY-MM-DD 的**直接不给**，
    宁可让核对跳报错让人填，也不要塞一个猜出来的日子。
    """
    text = str(value or "").strip()
    if not text:
        return None
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _clean_photo_facts(raw: dict[str, Any]) -> dict[str, Any]:
    """模型读出来的事实 → 干净的值（认不出的就是 None，不猜）"""
    facts: dict[str, Any] = {}
    for key in ("reading_score", "reading_total"):
        value = raw.get(key)
        try:
            facts[key] = float(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            facts[key] = None
    facts["observed_at"] = _photo_date(raw.get("observed_at"))
    activity = raw.get("activity")
    facts["activity"] = (
        str(activity).strip() if isinstance(activity, str) and activity.strip() else None
    )
    errors = raw.get("errors")
    if isinstance(errors, list):
        facts["errors"] = [str(e).strip() for e in errors if str(e).strip()]
    elif isinstance(errors, str) and errors.strip():
        facts["errors"] = [errors.strip()]
    else:
        facts["errors"] = None
    notes = raw.get("notes")
    facts["notes"] = (
        str(notes).strip() if isinstance(notes, str) and notes.strip() else None
    )
    return facts


def chinese_read_photo_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：看图——把照片上印着的事实抄出来（没有照片就什么也不做）

    **只抄，不猜**：读不出来的字段就是 None，后面的核对跳会告诉人
    「哪个字段照片里没读出来，请手工填」——而不是让模型编一个分数。

    **图片字节不进产出**：这一跳只把「读出来的事实」和「照片指纹」带下去。
    图片本身留在投料里，不跟着产出走——产出是要落盘的，
    一张 3 MB 的 base64 跟着一路走到落盘，那条记录会被截断成
    「这里只保留了元信息」，看起来像产出没了。
    """
    photo = input_data.get("photo")
    manual = {k: v for k, v in input_data.items() if k != "photo"}
    if not isinstance(photo, str) or not photo.strip():
        return {**manual, "photo_digest": "", "photo_facts": {}}

    image = images.as_viewable_image(photo)
    raw = complete_json(
        _PHOTO_PROMPT,
        images=[image],
        thinking="disabled",
        max_tokens=1500,
    )
    if not isinstance(raw, dict):
        raise ValueError(
            "看图那一跳没返回对象——这张照片读不出来。"
            "确认拍的是作业/试卷本身（不是桌面、不是书包）"
        )
    return {
        **manual,
        "photo_digest": _photo_digest(photo),
        "photo_facts": _clean_photo_facts(raw),
    }


def chinese_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：核对投料，说清这一跳要做什么

    **照片和手工填的合并规则：手工填的优先，照片只补空。**
    人填的是他当场知道的（分数就写在卷子上，直接抄）；
    照片读出的是模型认的（认错了也只有那一处，且能被看见）。
    两边都有时以人填的为准——**不猜**：宁可让人补一个空字段，
    也不让两个来源悄悄叠成一个说不清是谁说的数。

    **缺什么就报什么，一次报齐**：
    让人补一个字段报一次（补四个报四次），等于把「填表」变成「猜题」。
    """
    manual = {
        k: v for k, v in input_data.items()
        if k not in ("photo", "photo_facts", "photo_digest")
    }
    facts = input_data.get("photo_facts") or {}
    digest = str(input_data.get("photo_digest") or "")
    merged = dict(manual)
    for key, value in facts.items():
        if not _filled(merged.get(key)):
            merged[key] = value

    activity = str(merged.get("activity") or "").strip()
    source_kind = str(merged.get("source_kind") or "").strip()
    evidence = str(merged.get("evidence") or "").strip()
    observed_at = str(merged.get("observed_at") or "").strip()
    score = _num(merged, 'reading_score')
    total = _num(merged, 'reading_total')

    # 照片和口述是两种**互相矛盾**的出处，不能同时成立
    if digest and source_kind == "reported":
        raise ValueError(
            "投了照片就不能标 reported——照片是「看到了」的证据。"
            "要么去掉照片（改成口述），要么把 source_kind 改成 observed"
        )
    if not source_kind and digest:
        source_kind = "observed"  # 照片本身就是「看到了」
    if source_kind not in SOURCE_KINDS:
        raise ValueError(
            f"source_kind={source_kind!r} 不认得——"
            "只能是 observed（这次真的看到了）或 reported（家里口述的）"
        )

    # 出处：照片来源就用「照片 + 指纹」当出处（能指回那张照片）
    if not evidence and digest:
        evidence = f"作业照片 photo:{digest}"

    # ---- 缺什么就报什么：一次报齐 ----
    where = "照片里没读出来" if digest else "投料里没有"
    missing: list[str] = []
    if not activity:
        missing.append(f"{where}这次做了什么（activity：作业 / 单元测 / 订正 / 复习）")
    if not observed_at:
        missing.append(f"{where}学习发生的时间（observed_at）")
    if not evidence:
        missing.append(f"{where}出处（evidence：作业本 P12 / 单元测卷 / 口述）")
    if score is None:
        missing.append(f"{where}得分（reading_score）")
    if total is None:
        missing.append(f"{where}满分（reading_total）")
    if missing:
        raise ValueError(
            "；".join(missing)
            + "。在投料口补上就行；照片里确实没有的，手工填一次。"
        )

    # ---- 有数但数不对：矛盾要单独报 ----
    if total <= 0:
        raise ValueError(
            f"reading_total（满分）必须是正数，现在拿到 {total!r}——"
            "没有满分就算不出阅读得分率"
        )
    if score < 0:
        raise ValueError(f"reading_score（得分）不能是负数：{score!r}")
    if score > total:
        raise ValueError(
            f"reading_score（得分）{score:g} 超过了 reading_total（满分）{total:g}——"
            "投料错了，别让错数字进档案"
        )

    merged["activity"] = activity
    merged["source_kind"] = source_kind
    merged["evidence"] = evidence
    merged["observed_at"] = observed_at

    out: dict[str, Any] = {
        "plan": f"记一条语文学习事件：{activity}（{source_kind}）",
        "photo_digest": digest,
    }
    # 合并后的值带下去：work 跳要拿原始分与满分算阅读得分率。
    # **照片本身不带下去**：产出是要落盘的，图片字节一路跟到落盘，
    # 会把那条记录挤成「只保留了元信息」——看起来像产出没了。
    out.update(merged)
    return out


def chinese_work_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：真干活——算阅读得分率，写成人话"""
    score = _num(input_data, 'reading_score')
    total = _num(input_data, 'reading_total')
    if score is None or total is None or total <= 0:
        # 正常走不到这里：intake 已经拦过。留着是为了不让这一步静默算出垃圾。
        raise ValueError("算阅读得分率需要得分和满分")
    mastery = score / total

    detail = "阅读 {reading_score:g}/{reading_total:g}".format(
        **{"reading_score": score, "reading_total": total},
    )
    extra: list[str] = []
    _v = _num(input_data, 'writing_words')
    if _v is not None:
        extra.append('作文 {value:g} 字'.format(value=_v))
    _v = _num(input_data, 'new_chars')
    if _v is not None:
        extra.append('新学会 {value:g} 字'.format(value=_v))
    if extra:
        detail = detail + "；" + "、".join(extra)

    return {
        "observed": f"{detail}，阅读得分率 {mastery:.2f}",
        "detail": detail,
        "mastery": mastery,
        # 原始量一并带下去：装配跳要拿它们复算
        **{k: v for k, v in input_data.items()},
    }


def chinese_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：装配产出，并**复算一遍**得分率

    复算的意义：工作跳算的 mastery 会不会被写成一个跟分数无关的数？
    装配跳拿原始分与满分再算一遍，对不上就判红（`ratio_exact`）。
    """
    score = _num(input_data, 'reading_score')
    total = _num(input_data, 'reading_total')
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
        "subject": "chinese",
        "activity": str(input_data.get("activity") or ""),
        "observed_at": str(input_data.get("observed_at") or ""),
        "mastery": mastery,
        "evidence": str(input_data.get("evidence") or ""),
        "source_kind": str(input_data.get("source_kind") or ""),
        "detail": detail,
        "notes": notes,
        "ratio_exact": ratio_exact,
    }


LEARNING_CHINESE_HANDLERS: dict[str, Any] = {
    "chinese_read_photo": chinese_read_photo_handler,
    "chinese_intake": chinese_intake_handler,
    "chinese_work": chinese_work_handler,
    "chinese_packager": chinese_packager_handler,
}

RUNNER = HandlerRunner(LEARNING_CHINESE_HANDLERS, owner="learning_chinese_box")
