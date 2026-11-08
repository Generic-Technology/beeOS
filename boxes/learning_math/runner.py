"""boxes.learning_math.runner - 数学盒的 operation 实现

op 链：
  1. `math_read_photo` 看照片 → 把图上印着的事实**抄**出来（含错题清单）
  2. `math_intake`      取投料 → 这一跳要做什么（手工的优先，照片补空）
  3. `math_analyze`     当老师 → 判错因、出**针对性训练方案**
  4. `math_work`        真干活 → 算得分率
  5. `math_packager`    装配产出（并**复算一遍**得分率）

## 为什么要多一跳（老师）

**抄图和判错因是两种活，必须分开**：
抄图那一跳是「照着写」，它判不了「这题是进位错还是概念错」；
判错因要看着题目、孩子的答案和正确答案一起想，还要**解一次题**
（图上没印答案时）。混在一跳里，看图失败和判断失败就分不开了。

## 三态：读不出来是 `n/a`，不是猜一个数

照片上常常没有得分、没有满分、没有日期。
这时候那一格是 **`n/a`**（这次这个量用不上），不是 0，也不是猜一个 100。
猜一个满分，错的是以后每一份档案里的比率。

**失败路径必须显式**：每一次失败都要带原因抛出来，
不要静默跳过、不要填默认值。兜底会把投料错伪装成「学得不好」。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from core.models import NOT_APPLICABLE
from runtime import images
from runtime.llm import complete_json
from runtime.runner import HandlerRunner

from boxes.learning_math.schemas import (
    ANSWER_SOURCES,
    ERROR_TYPES,
)


SOURCE_KINDS = ("observed", "reported")

# 照片路径上「这次做了什么」读不出来时的兜底标签。
#
# 它不是**猜**出来的活动类型（作业？单元测？口算卡？——那才叫猜），
# 而是一个中性的兜底：一次数学练习。真的读出来了就用读出来的。
_FALLBACK_ACTIVITY = "数学练习"

# 训练方案里每组最多给几条练习题——多了就成了一本习题册，
# 家长不会照着做，五条以内才可能今晚就练。
_MAX_DRILLS = 5


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
    """这次学习记录的唯一号——**从内容推导**，不是随机数

    随机 id 会让同一件事重记一次就变成两件事；
    而这条记录是**有出处的事实**，它的号必须跟着内容走。
    """
    parts = [
        str(input_data.get("activity") or ""),
        str(input_data.get("observed_at") or ""),
        str(input_data.get("evidence") or ""),
        str(input_data.get('score') or ""),
        str(input_data.get('total') or ""),
    ]
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"math-{digest}"


_PHOTO_PROMPT = """你是一张数学作业照片的抄写员。给你一张数学作业/试卷的照片，
把上面**真实印着**的内容抄成 JSON。

只抄图上印着的。看不清就填 null——**不要猜、不要补全、不要编**。

字段：
- score: 得分。图上可能是「92」「92分」「92/100」里的分子。找不到就 null。
- total: 满分。图上可能是「/100」「满分100」里的分母。找不到就 null。
  **不要默认 100**：图上没印满分就是 null，猜一个满分会把错的比率写进档案。
- observed_at: 图上写的日期（YYYY-MM-DD）。作业本上常有日期；找不到就 null。
- activity: 图上写的这次是什么（作业 / 单元测 / 订正 / 复习 / 第几页）。找不到就 null。
- notes: 其他看得出的信息。没有就 null。

错题（这是这张照片最重要的部分）：
- wrong_questions_status:
  - "found": 你看得出**哪几题错了**（空白、红叉、擦掉重写、明显做错的痕迹）
  - "none": 你看得出整页，而且**每题都对**（每题都有对勾、满分的痕迹）
  - "unclear": 你**看不清**哪些题错了（拿不准就填这个——
    「看不清」和「都对」是两件事，填错了等于跟家长说这次全对）
- wrong_questions: 错题清单。**status="found" 时才填**，一条错题一项：
  - no: 题号（"3" 或 "第3题"）
  - question: 题目原文（**照抄**，别改写；题目太长就抄能认出的关键部分）
  - student_answer: 孩子写的答案（照抄；**空着没写填空串，看不清填 null**——
    「没做」和「没看清」是两件事，别混）
  - correct_answer: 图上印着的正确答案（红笔批注、参考答案区、书上印的）；
    图上没有就 null——**不要自己算**，算答案不是抄写员的事

**不要自己做算术**：score 和 total 各自抄图上印着的那一格。
看到「92/100」就填 92 和 100；只看到「92」就 score=92、total=null
（除非别处印着满分）。

只输出 JSON，不要解释。"""


_ANALYZE_PROMPT = f"""你是一位小学数学老师。下面是一次作业里**错在哪**的材料：
题号、题目原文、孩子写的答案，图上印着的正确答案（可能没有）。

请给出三样东西：

1. **每道错题的判断**（按给的顺序，一题一条，**不要自己加题**）：
   - index: 这道题在下面清单里的序号（**照抄**）
   - topic: 这题考什么知识点/题型
   - correct_answer: 正确答案。图上印着就用印着的；**图上没有就自己算**
   - answer_source: 正确答案的来路——图上印的写「照片」，你自己算的写「模型推算」
   - error_type: 错因，**只能从这张表里选**：
     {' / '.join(ERROR_TYPES)}
   - error_note: 错在哪（人话一句，比如「个位满十忘了进一」）

   判断错因的几条线：
   - 孩子的答案是空的 → 「未作答」
   - 思路对、过程算错（进位、符号、通分、去零头）→ 「计算失误」
   - 公式/概念本身不熟 → 「概念不清」
   - 题意看错、条件看漏（比如漏了「多多少」）→ 「审题不清」
   - 会做但没写完、只写一步 → 「步骤不全」
   - 答案对但单位错 → 「单位换算错」
   - 答案本来对、抄错了 → 「抄写笔误」
   - 真的说不清 → 「其它」（**不要硬塞进别的类**）

2. **针对性训练方案**（training_plan）：**按错因分组**，这次出现的每一类错因
   都要有一组方案，一组一条：
   - focus: 针对什么（人话，比如「两位数加法的进位」）
   - error_type: 这一组针对的错因（从上面那张表里选）
   - how: 怎么练（步骤/口诀/检查习惯，一两句）
   - drills: **具体要练的题或题型**，3 到 5 条，一条一个
     （例："38+27（进位加法）×5"；不要写"多练"这种空话）
   - minutes: 大概练多少分钟

3. **training_summary**：一句人话，说清这次错在哪、下一步怎么练。

**如果给的是手填的错题描述**（不是完整题目）：
照着描述判断错因；topic 写你推断的知识点；correct_answer 留空
（描述里没有题目就算不出答案）；answer_source 留空。

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


def _clean_wrong_questions(raw: Any) -> list[dict[str, str]]:
    """模型抄出来的错题 → 干净的条目（只抄，不判）

    **只留抄得到的东西**：题号、题目、孩子的答案、图上印的答案。
    错因、知识点是下一跳（老师）的事——这一跳不许判，
    判了就没法分清「没看清」和「判错了」。

    **孩子的答案分两种空**：
    - `""` = 图上**空着没写**（这次没做）
    - `None` = **看不清**（那一格没读出来）

    混成一个空串的话，「没做」和「没看清」就分不开了，
    而它们要判的错因完全不一样。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = {
            key: str(item.get(key) or "").strip()
            for key in ("no", "question", "student_answer", "correct_answer")
        }
        if item.get("student_answer") is None:
            # 看不清 ≠ 空着没做
            entry["student_answer"] = None  # type: ignore[assignment]
        # 一条什么都没有的条目（比如模型吐了个空对象）不算一道错题
        if not any(entry.values()):
            continue
        # 图上印着答案 → 来路就是「照片」；图上没有 → 空（下一跳自己算）
        entry["answer_source"] = "照片" if entry["correct_answer"] else ""
        out.append(entry)
    return out


def _clean_photo_facts(raw: dict[str, Any]) -> dict[str, Any]:
    """模型读出来的事实 → 干净的值（认不出的就是 None，不猜）"""
    facts: dict[str, Any] = {}
    for key in ("score", "total"):
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
    notes = raw.get("notes")
    facts["notes"] = (
        str(notes).strip() if isinstance(notes, str) and notes.strip() else None
    )

    # ---- 错题：三态，不能把「看不清」说成「都对」----
    questions = _clean_wrong_questions(raw.get("wrong_questions"))
    said = str(raw.get("wrong_questions_status") or "").strip().lower()
    if questions:
        facts["wrong_questions"] = questions
        facts["wrong_questions_status"] = "found"
    elif said == "none":
        # 模型说「看得全、每题都对」——这是**真的看见了**才算数
        facts["wrong_questions"] = []
        facts["wrong_questions_status"] = "none"
    else:
        # 其余情况一律当「看不清」：说「都对」是要担责任的，
        # 拿不准就别替家长下结论
        facts["wrong_questions"] = None
        facts["wrong_questions_status"] = "unclear"
    return facts


def math_read_photo_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：看图——把照片上印着的事实抄出来（没有照片就什么也不做）

    **只抄，不猜**：读不出来的字段就是 None，后面的核对跳会告诉人
    「哪个字段照片里没读出来」——而不是让模型编一个分数。

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
        max_tokens=2000,
    )
    if not isinstance(raw, dict):
        raise ValueError(
            "看图那一跳没返回对象——这张照片读不出来。"
            "确认拍的是作业/试卷本身（不是桌面、不是书包）"
        )
    facts = _clean_photo_facts(raw)

    # **整张照片什么都没读出来 → 中止**：
    # 拍了一张桌子、书包的照，跑完只会产出一张空卡片——
    # 那不是「这次没问题」，是「这次什么都不知道」。
    if not any(
        facts.get(key) not in (None, "", [])
        for key in ("score", "total", "observed_at", "activity", "notes")
    ) and facts.get("wrong_questions_status") == "unclear":
        raise ValueError(
            "这张照片里没读出任何内容——得分、满分、日期、题目一个都没有。"
            "确认拍的是作业/试卷本身（不是桌面、不是书包），"
            "或者在下面手工填这次练习的事实"
        )
    return {
        **manual,
        "photo_digest": _photo_digest(photo),
        "photo_facts": facts,
    }


def math_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：核对投料，说清这一跳要做什么

    **照片和手工填的合并规则：手工填的优先，照片只补空。**
    人填的是他当场知道的（分数就写在卷子上，直接抄）；
    照片读出的是模型认的（认错了也只有那一处，且能被看见）。
    两边都有时以人填的为准——**不猜**：宁可让人补一个空字段，
    也不让两个来源悄悄叠成一个说不清是谁说的数。

    ## 照片路径**不因为「读不出分数」中止**

    这是「输入就是拍照」的前提：作业本上常常没有分数、没有满分、
    连日期都没有。要是因为这些就把整次履约掐掉，
    「拍一张就行」就成了空话。

    所以照片路径上读不出的字段**留空**（产出里写明缺了哪几个），
    掌握度记 `n/a`——**不是猜一个数**。

    ## 手填路径照旧要齐

    没有照片的时候，事实只能靠人给：这次做了什么、什么时候、在哪、
    得分多少、满分多少。缺了就**一次报齐**，
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
    score = _num(merged, 'score')
    total = _num(merged, 'total')

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
    #
    # **照片路径不等齐**：照片里没有得分、没有满分、没有日期是常事。
    # 那些格子留空（产出里写明这次缺了哪几个），掌握度记 n/a。
    # 要是因为这些就把整次履约掐掉，「拍一张就行」就成了空话。
    #
    # **手填路径照旧要齐**：没有照片，事实只能靠人给。
    where = "照片里没读出来" if digest else "投料里没有"
    missing: list[str] = []
    unread: list[str] = []
    if not activity:
        if digest:
            # 照片里读不出「这次是什么」——**兜一个中性标签**，
            # 不是猜「作业」还是「单元测」（那才叫猜）
            activity = _FALLBACK_ACTIVITY
            unread.append("activity")
        else:
            missing.append(f"{where}这次做了什么（activity：作业 / 单元测 / 订正 / 复习）")
    if not evidence:
        missing.append(f"{where}出处（evidence：作业本 P12 / 单元测卷 / 口述）")
    if not digest:
        # 手填路径：日期、得分、满分照旧必填（下面一次报齐）
        if not observed_at:
            missing.append(f"{where}学习发生的时间（observed_at）")
        if score is None:
            missing.append(f"{where}得分（score）")
        if total is None:
            missing.append(f"{where}满分（total）")
    if missing:
        raise ValueError(
            "；".join(missing)
            + "。在投料口补上就行；照片里确实没有的，手工填一次。"
        )

    # ---- 有数但数不对：矛盾要单独报 ----
    #
    # **手填的数字自相矛盾 → 当场拒**（人要来说清哪个是对的）。
    # **照片里读出来的数字自相矛盾 → 不中止，但这一对数字不用**：
    # 模型可能把题号当成了得分。猜不出就当没这回事，
    # 训练卡照样出——并把这件事写进产出的 missing_fields，
    # 不藏。
    if total is not None and total <= 0:
        raise ValueError(
            f"total（满分）必须是正数，现在拿到 {total!r}——"
            "没有满分就算不出得分率"
        )
    if score is not None and score < 0:
        raise ValueError(f"score（得分）不能是负数：{score!r}")
    if score is not None and total is not None and score > total:
        if digest:
            score = None
            total = None
            unread.extend(["score", "total"])
        else:
            raise ValueError(
                f"score（得分）{score:g} 超过了 total（满分）{total:g}——"
                "投料错了，别让错数字进档案"
            )

    # ---- 这次没拿到哪些事实（产出里要写明，别让人以为没有）----
    if score is None:
        unread.append("score")
    if total is None:
        unread.append("total")
    if not observed_at:
        unread.append("observed_at")
    status = str(merged.get("wrong_questions_status") or "").strip()
    manual_errors = _as_str_list(merged.get("errors"))
    questions = _as_questions(merged.get("wrong_questions"))
    if not questions and not manual_errors and status == "unclear":
        unread.append("wrong_questions")
    unread = list(dict.fromkeys(unread))  # 去重且保序

    merged["activity"] = activity
    merged["source_kind"] = source_kind
    merged["evidence"] = evidence
    merged["observed_at"] = observed_at

    out: dict[str, Any] = {
        "plan": f"记一次数学练习：{activity}（{source_kind}）",
        "photo_digest": digest,
        "missing_fields": unread,
    }
    # 合并后的值带下去：work 跳要拿原始分与满分算得分率。
    # **照片本身不带下去**：产出是要落盘的，图片字节一路跟到落盘，
    # 会把那条记录挤成「只保留了元信息」——看起来像产出没了。
    out.update(merged)
    out["wrong_questions"] = questions
    out["wrong_questions_status"] = status or ("found" if questions else "unclear")
    out["missing_fields"] = unread
    return out


def _as_str_list(value: Any) -> list[str]:
    """一个可能是列表/单值的字段 → 干净的字符串列表"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _as_questions(value: Any) -> list[dict[str, str]]:
    """错题清单 → 干净的条目列表（缺字段补空串，不丢条目）

    **孩子的答案要留得住「空着没写」和「没看清」的区别**：
    前者是 `""`，后者是 `None`。都变成空串的话，
    「这次没做」和「这次没读出来」会被判成同一个错因。
    """
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        entry = {
            key: str(item.get(key) or "").strip()
            for key in (
                "no", "topic", "question", "student_answer",
                "correct_answer", "answer_source", "error_type", "error_note",
            )
        }
        if item.get("student_answer") is None:
            entry["student_answer"] = None  # type: ignore[assignment]
        out.append(entry)
    return out


def _clean_analysis(raw: dict[str, Any], questions: list[dict[str, str]]) -> dict[str, Any]:
    """模型判出来的错因/训练方案 → 干净的值

    **模型的输出只能「填」这些题，不能「加」题**：
    题号、题目、孩子的答案**一律以抄下来的为准**，
    模型返回的对应位置按序号对齐，多出来的条目丢掉。
    不然模型一发挥，多出一道题，家长照着练一道不存在的题。
    """
    by_index: dict[int, dict[str, Any]] = {}
    for item in raw.get("wrong_questions") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        by_index[index] = item

    judged: list[dict[str, str]] = []
    for i, question in enumerate(questions):
        said = by_index.get(i, {})
        topic = str(said.get("topic") or "").strip()
        correct = str(said.get("correct_answer") or "").strip()
        source = str(said.get("answer_source") or "").strip()
        if not correct:
            correct = question.get("correct_answer", "")
        if not correct:
            source = ""
        elif question.get("correct_answer") and correct == question["correct_answer"]:
            # 图上本来就有答案 → 来路就是照片（模型说什么不算）
            source = "照片"
        elif source not in ANSWER_SOURCES:
            source = "模型推算"
        error_type = str(said.get("error_type") or "").strip()
        if error_type not in ERROR_TYPES:
            # 说不出个正经错因 → 归「其它」，不硬塞进某一类
            error_type = "其它"
        if not question.get("student_answer"):
            # **空串** = 图上空着没做 → 错因就是「未作答」（不用模型判）。
            # `None` = 那一格没看清 → 不强制判，交给模型按看到的来。
            if question.get("student_answer") == "":
                error_type = "未作答"
        judged.append({
            "no": question.get("no", ""),
            "topic": topic,
            "question": question.get("question", ""),
            "student_answer": question.get("student_answer", ""),
            "correct_answer": correct,
            "answer_source": source,
            "error_type": error_type,
            "error_note": str(said.get("error_note") or "").strip(),
        })

    # 错因清单：**从错题里算出来**，不信模型自己报的那份
    error_types: list[str] = []
    for item in judged:
        if item["error_type"] not in error_types:
            error_types.append(item["error_type"])

    # 训练方案：只留**这次真出现的错因**的方案，一类一组
    plan: list[dict[str, Any]] = []
    covered: set[str] = set()
    for item in raw.get("training_plan") or []:
        if not isinstance(item, dict):
            continue
        error_type = str(item.get("error_type") or "").strip()
        if error_type not in ERROR_TYPES or error_type not in error_types:
            continue
        if error_type in covered:
            continue
        drills = _as_str_list(item.get("drills"))[:_MAX_DRILLS]
        minutes = item.get("minutes")
        try:
            minutes = float(minutes) if minutes not in (None, "") else None
        except (TypeError, ValueError):
            minutes = None
        if minutes is not None and minutes == int(minutes):
            minutes = int(minutes)
        plan.append({
            "focus": str(item.get("focus") or "").strip() or error_type,
            "error_type": error_type,
            "drills": drills,
            "how": str(item.get("how") or "").strip(),
            "minutes": minutes,
        })
        covered.add(error_type)

    summary = str(raw.get("training_summary") or "").strip()
    if not summary:
        summary = _fallback_summary(judged, error_types, plan)
    return {
        "wrong_questions": judged,
        "error_types": error_types,
        "training_plan": plan,
        "training_summary": summary,
    }


def _fallback_summary(
    questions: list[dict[str, str]],
    error_types: list[str],
    plan: list[dict[str, Any]],
) -> str:
    """模型没给总结时的兜底（**用算出来的数**，不编）"""
    if not questions:
        return "这次没有错题信息。"
    causes = "、".join(error_types)
    tail = f"按上面 {len(plan)} 组方案练。" if plan else "（还没有训练方案。）"
    return f"这次错了 {len(questions)} 题，错因是{causes}。{tail}"


def math_analyze_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：当老师——判错因，出针对性训练方案

    **没有错题就不调模型**：没有可判断的东西，模型只会编一段
    「建议多练习」——那种话对谁都没用。
    """
    questions = _as_questions(input_data.get("wrong_questions"))
    manual_errors = _as_str_list(input_data.get("errors"))
    status = str(input_data.get("wrong_questions_status") or "").strip()

    if not questions and manual_errors:
        # 照片里没读出错题，但家长手填了错题描述 → 按描述来
        #
        # **孩子的答案记成 `None`（没看清），不是 `""`（空着没做）**：
        # 家长写的是「第2题进位忘了」，不是「孩子这题没作答」。
        # 记成空串的话，「空着没做」这条机械规则会把它误判成「未作答」。
        questions = [
            {
                "no": "", "topic": "", "question": text,
                "student_answer": None, "correct_answer": "", "answer_source": "",
                "error_type": "", "error_note": "",
            }
            for text in manual_errors
        ]

    if not questions:
        # **没有错题 → 没有训练方案**（不调模型）
        summary = {
            "none": "这次照片上每题都对，没看出要补的错题。",
            "unclear": (
                "照片里没看出哪题错了——重拍一张能看清批改痕迹的，"
                "或者在下面手工填错题。"
            ),
        }.get(status, "这次没有错题信息。")
        return {
            **{k: v for k, v in input_data.items()},
            "wrong_questions": [],
            "error_types": [],
            "training_plan": [],
            "training_summary": summary,
        }

    context = [
        {
            "index": i,
            "no": q.get("no", ""),
            "question": q.get("question", ""),
            "student_answer": q.get("student_answer", ""),
            "correct_answer": q.get("correct_answer", ""),
        }
        for i, q in enumerate(questions)
    ]
    raw = complete_json(
        _ANALYZE_PROMPT
        + "\n\n这次的错题（index 就是它的序号，照抄回去）：\n"
        + json.dumps(context, ensure_ascii=False, indent=2),
        thinking="disabled",
        max_tokens=2000,
    )
    if not isinstance(raw, dict):
        raise ValueError(
            "判错因那一跳没返回对象——这次的训练方案出不来。"
            "重投一次，或检查模型这一跳是不是能通"
        )
    analysis = _clean_analysis(raw, questions)
    return {
        **{k: v for k, v in input_data.items()},
        **analysis,
    }


def math_work_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 4：真干活——算得分率，写成人话

    **没有得分/满分时掌握度是 `n/a`**（这次这个量用不上），
    不是 0，也不是猜一个满分。
    """
    score = _num(input_data, 'score')
    total = _num(input_data, 'total')
    if score is not None and total is not None and total > 0:
        mastery: Any = score / total
    else:
        mastery = NOT_APPLICABLE

    questions = _as_questions(input_data.get("wrong_questions"))
    error_types = _as_str_list(input_data.get("error_types"))

    parts: list[str] = []
    if mastery == NOT_APPLICABLE:
        parts.append("这次没读出得分/满分（掌握度 n/a）")
    else:
        parts.append(f"得分 {score:g}/{total:g}")
    if questions:
        causes = "、".join(error_types) if error_types else "未分类"
        parts.append(f"错 {len(questions)} 题（{causes}）")
    minutes = _num(input_data, 'minutes')
    if minutes is not None:
        parts.append(f"用时 {minutes:g} 分钟")
    detail = "；".join(parts)

    ratio = "n/a" if mastery == NOT_APPLICABLE else f"{mastery:.2f}"
    return {
        **{k: v for k, v in input_data.items()},
        "observed": f"{detail}，得分率 {ratio}",
        "detail": detail,
        "mastery": mastery,
    }


def math_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 5：装配产出，并**复算一遍**得分率

    复算的意义：工作跳算的 mastery 会不会被写成一个跟分数无关的数？
    装配跳拿原始分与满分再算一遍，对不上就判红（`ratio_exact`）。

    **训练方案覆没覆盖住错题，也在这一步判**：
    漏了一类错因 = 练了但没练到点上 = 这张卡不成立。
    """
    score = _num(input_data, 'score')
    total = _num(input_data, 'total')
    mastery = input_data.get("mastery")
    if mastery == NOT_APPLICABLE:
        ratio_exact: Any = NOT_APPLICABLE
    else:
        mastery_num = _num(input_data, "mastery")
        if score is None or total is None or mastery_num is None or total <= 0:
            raise ValueError("装产出需要得分、满分和算出来的掌握度")
        ratio_exact = abs(mastery_num - score / total) < 1e-9

    questions = _as_questions(input_data.get("wrong_questions"))
    # 错因清单：**再算一遍**（不信上游报上来的那份）
    error_types: list[str] = []
    for item in questions:
        et = item.get("error_type") or ""
        if et and et not in error_types:
            error_types.append(et)

    plan = input_data.get("training_plan") or []
    if not isinstance(plan, list):
        plan = []
    covered = {
        str(item.get("error_type") or "")
        for item in plan
        if isinstance(item, dict)
    }
    # 这次出现的每一类错因，都要有一组训练方案
    training_covers_errors = set(error_types) <= covered

    notes = str(input_data.get("notes") or "")
    detail = str(input_data.get("detail") or "")
    unread = _as_str_list(input_data.get("missing_fields"))
    if unread:
        detail = detail + "；这次没拿到：" + "、".join(unread)

    return {
        "record_id": _record_id(input_data),
        "subject": "math",
        "activity": str(input_data.get("activity") or ""),
        "observed_at": str(input_data.get("observed_at") or ""),
        "mastery": input_data.get("mastery", NOT_APPLICABLE),
        "evidence": str(input_data.get("evidence") or ""),
        "source_kind": str(input_data.get("source_kind") or ""),
        "detail": detail,
        "notes": notes,
        "ratio_exact": ratio_exact,
        "wrong_questions": questions,
        "wrong_count": len(questions),
        "error_types": error_types,
        "training_plan": plan,
        "training_summary": str(input_data.get("training_summary") or ""),
        "training_covers_errors": training_covers_errors,
        "missing_fields": unread,
    }


LEARNING_MATH_HANDLERS: dict[str, Any] = {
    "math_read_photo": math_read_photo_handler,
    "math_intake": math_intake_handler,
    "math_analyze": math_analyze_handler,
    "math_work": math_work_handler,
    "math_packager": math_packager_handler,
}

RUNNER = HandlerRunner(LEARNING_MATH_HANDLERS, owner="learning_math_box")
