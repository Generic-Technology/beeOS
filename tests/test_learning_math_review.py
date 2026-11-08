"""tests.test_learning_math_review - 数学盒的错题训练卡：卡要有用，也要有出处

这组守卫守**这次改造的核心**——数学盒的产出从「一条学习记录」
变成「**错在哪 + 怎么针对性练**」。守的是：

1. **拍照就是全部输入**——照片里没印得分/满分/日期，照样出卡；
   读不出来的格子是 `n/a`，**不是猜的数**。
2. **卡里的东西不许是模型编的**——题号、题目、孩子的答案以抄下来的为准；
   模型能加的只有「判错因、补答案、出方案」。
3. **方案要盖住每一类错因**——漏了一类就是「练了但没练到点上」，判红。
4. **「看不清」不许说成「都对」**——三态：找到了 / 全对 / 看不清。
"""

from __future__ import annotations

import json

import pytest

from kanban.trigger import (
    BOX_REGISTRY,
    trigger_task,
)
from runtime.store import TaskRunStore

from tests.test_learning_photo import TINY_JPEG, _fake_llm


MATH = "learning_math_box"
GROWTH = "learning_growth_box"

# 一张作业照片上**抄得出来**的东西
PHOTO = {
    "score": None,
    "total": None,
    "observed_at": "2026-11-01",
    "activity": "数学作业",
    "notes": None,
    "wrong_questions_status": "found",
    "wrong_questions": [
        {
            "no": "3",
            "question": "38+27=?",
            "student_answer": "55",
            "correct_answer": None,
        },
        {
            "no": "5",
            "question": "3/4 + 1/8 = ?",
            "student_answer": "",
            "correct_answer": "7/8",
        },
    ],
}

# 老师那一跳的回答：两题、两类错因、两组方案
ANALYSIS = {
    "wrong_questions": [
        {
            "index": 0,
            "topic": "两位数加法进位",
            "correct_answer": "65",
            "answer_source": "模型推算",
            "error_type": "计算失误",
            "error_note": "个位 8+7 满十忘了进一",
        },
        {
            "index": 1,
            "topic": "分数加法",
            "correct_answer": "7/8",
            "answer_source": "照片",
            "error_type": "未作答",
            "error_note": "空着没做",
        },
    ],
    "training_plan": [
        {
            "focus": "两位数加法的进位",
            "error_type": "计算失误",
            "how": "先算个位，满十进一，再加十位",
            "drills": ["38+27", "46+38"],
            "minutes": 10,
        },
        {
            "focus": "分数加法（同分母）",
            "error_type": "未作答",
            "how": "同分母先通分再相加",
            "drills": ["3/4+1/8"],
            "minutes": 5,
        },
    ],
    "training_summary": "这次错在进位加法和分数空着没做。",
}


@pytest.fixture()
def store(tmp_path):
    return TaskRunStore(path=tmp_path / "runs.jsonl")


def _run_photo(monkeypatch, store, *, photo=None, analysis=None):
    _fake_llm(
        monkeypatch, MATH,
        photo=photo if photo is not None else PHOTO,
        analysis=analysis if analysis is not None else ANALYSIS,
    )
    run = trigger_task(MATH, "run_math", {"photo": TINY_JPEG}, store)
    return store.get_run(run["task_run_id"])


# ------------------------------------------------------------------
# 1. 拍照就是全部输入
# ------------------------------------------------------------------
class TestAPhotoIsAllTheInput:
    def test_one_photo_produces_the_wrong_questions_and_the_training(self, monkeypatch, store):
        """拍一张 → 错题清单 + 针对性训练方案"""
        record = _run_photo(monkeypatch, store)
        assert record["status"] == "completed", record.get("error_reason")
        assert record["acceptance_status"] == "accepted", record.get("acceptance_detail")

        artifact = record["artifact"]
        assert artifact["wrong_count"] == 2
        assert artifact["error_types"] == ["计算失误", "未作答"]
        assert artifact["training_covers_errors"] is True
        assert len(artifact["training_plan"]) == 2
        assert artifact["training_summary"], "训练方案没有一句人话总结"

        first = artifact["wrong_questions"][0]
        assert first["no"] == "3"
        assert first["question"] == "38+27=?"
        assert first["student_answer"] == "55"
        assert first["correct_answer"] == "65"
        assert first["error_type"] == "计算失误"
        assert first["error_note"], "错在哪得有一句话"

    def test_the_training_plan_says_what_to_practice(self, monkeypatch, store):
        """方案要能照着做：针对什么、怎么练、练哪几题、多少分钟"""
        record = _run_photo(monkeypatch, store)
        plan = record["artifact"]["training_plan"][0]
        assert plan["focus"]
        assert plan["how"]
        assert plan["drills"], "方案里没有要练的题——等于没给方案"
        assert plan["minutes"]

    def test_a_photo_without_a_score_still_gets_a_card(self, monkeypatch, store):
        """照片里没有得分/满分 → 卡片照样出，掌握度记 `n/a`"""
        record = _run_photo(monkeypatch, store)
        artifact = record["artifact"]
        assert artifact["mastery"] == "n/a"
        assert artifact["ratio_exact"] == "n/a"
        assert "score" in artifact["missing_fields"]
        assert "total" in artifact["missing_fields"]
        assert artifact["wrong_count"] == 2, "没有分数就不给错题了？"

    def test_the_card_says_where_it_came_from(self, monkeypatch, store):
        """卡片也得带出处——半年后要能指回那张照片"""
        record = _run_photo(monkeypatch, store)
        artifact = record["artifact"]
        assert "photo:" in artifact["evidence"]
        assert artifact["source_kind"] == "observed"


# ------------------------------------------------------------------
# 2. 卡里的东西不许是模型编的
# ------------------------------------------------------------------
class TestTheModelCannotInventFacts:
    def test_the_list_under_another_key_is_still_read(self, monkeypatch, store):
        """模型把逐题清单叫 `items` → **照样读**

        实测踩过：模型自己起名叫 `items`，代码只认 `wrong_questions`，
        于是**整份判定结果丢失**——每一道题都成了「其它」，
        家长拿到的是一张看不出所以然的卡。
        """
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["items"] = analysis.pop("wrong_questions")
        record = _run_photo(monkeypatch, store, analysis=analysis)
        artifact = record["artifact"]
        assert artifact["error_types"] == ["计算失误", "未作答"]
        assert artifact["wrong_count"] == 2

    def test_a_shape_without_the_list_fails_loudly(self, monkeypatch, store):
        """返回的形状里没有逐题清单 → **当场报错**，不静默全变「其它」"""
        _fake_llm(
            monkeypatch, MATH,
            photo=PHOTO,
            analysis={"training_plan": [], "training_summary": "说不清"},
        )
        run = trigger_task(MATH, "run_math", {"photo": TINY_JPEG}, store)
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "failed", "形状不对居然出了一张卡"
        reason = str(record.get("error_reason"))
        assert "形状不对" in reason, f"报错要说清是形状的问题：{reason}"

    def test_the_model_cannot_add_questions(self, monkeypatch, store):
        """模型多编一道题 → 加不进来

        多出来的题会让家长照着练一道**不存在**的题。
        """
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["wrong_questions"].append({
            "index": 99,
            "topic": "模型自己加的",
            "correct_answer": "1",
            "answer_source": "模型推算",
            "error_type": "计算失误",
            "error_note": "编的",
        })
        record = _run_photo(monkeypatch, store, analysis=analysis)
        artifact = record["artifact"]
        assert artifact["wrong_count"] == 2, "模型加的题混进来了"
        assert all(q["no"] in ("3", "5") for q in artifact["wrong_questions"])

    def test_the_model_cannot_rewrite_the_transcribed_facts(self, monkeypatch, store):
        """题号、题目、孩子的答案以**抄下来的**为准"""
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["wrong_questions"][0]["no"] = "999"
        analysis["wrong_questions"][0]["question"] = "模型重写的题目"
        analysis["wrong_questions"][0]["student_answer"] = "模型改的答案"
        record = _run_photo(monkeypatch, store, analysis=analysis)
        first = record["artifact"]["wrong_questions"][0]
        assert first["no"] == "3"
        assert first["question"] == "38+27=?"
        assert first["student_answer"] == "55"

    def test_an_error_type_outside_the_table_becomes_other(self, monkeypatch, store):
        """不在固定表里的错因 → 归「其它」，不硬塞进某一类"""
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["wrong_questions"][0]["error_type"] = "心不在焉"
        analysis["training_plan"][0]["error_type"] = "心不在焉"
        record = _run_photo(monkeypatch, store, analysis=analysis)
        artifact = record["artifact"]
        assert artifact["error_types"] == ["其它", "未作答"]
        assert all(
            item["error_type"] in ("其它", "未作答")
            for item in artifact["training_plan"]
        )

    def test_an_empty_answer_is_mechanically_unanswered(self, monkeypatch, store):
        """孩子的答案是空的 → 错因就是「未作答」（不靠模型判）"""
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["wrong_questions"][1]["error_type"] = "计算失误"
        record = _run_photo(monkeypatch, store, analysis=analysis)
        second = record["artifact"]["wrong_questions"][1]
        assert second["student_answer"] == ""
        assert second["error_type"] == "未作答"

    def test_an_answer_mixed_into_the_question_is_split_out(self, monkeypatch, store):
        """模型把「38 + 27 = 55」整句当题干抄了 → **机械地拆出来**

        不拆的话：孩子明明写了 55，答案那格空着 →
        错因被判成「未作答」→ 训练方案整个跑偏
        （练「怎么写答案」而不是练「怎么算」）。
        """
        module = __import__(
            "boxes.learning_math.runner", fromlist=["_clean_wrong_questions"]
        )
        cleaned = module._clean_wrong_questions([{
            "no": "3",
            "question": "38 + 27 = 55",
            "student_answer": None,
            "correct_answer": None,
        }])
        assert cleaned[0]["question"] == "38 + 27"
        assert cleaned[0]["student_answer"] == "55"

    def test_a_plan_for_an_error_that_did_not_happen_is_dropped(self, monkeypatch, store):
        """针对**这次没出现**的错因的方案 → 丢掉

        多出来的方案会让家长以为孩子还有别的毛病。
        """
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["training_plan"].append({
            "focus": "英语",
            "error_type": "概念不清",
            "how": "不用管",
            "drills": ["x"],
            "minutes": 5,
        })
        record = _run_photo(monkeypatch, store, analysis=analysis)
        plan = record["artifact"]["training_plan"]
        assert all(item["error_type"] != "概念不清" for item in plan)


# ------------------------------------------------------------------
# 2b. 家长手填的错题，也走同一条路
# ------------------------------------------------------------------
class TestHandWrittenErrorsAlsoGetAPlan:
    def test_a_typed_error_gets_an_error_type_and_a_plan(self, monkeypatch, store):
        """没照片、家长手填「第2题进位忘了」→ 照样判错因、出方案"""
        _fake_llm(
            monkeypatch, MATH,
            photo={},
            analysis={
                "wrong_questions": [{
                    "index": 0,
                    "topic": "两位数加法进位",
                    "correct_answer": "",
                    "answer_source": "",
                    "error_type": "计算失误",
                    "error_note": "进位忘了",
                }],
                "training_plan": [{
                    "focus": "进位加法",
                    "error_type": "计算失误",
                    "how": "先算个位，满十进一",
                    "drills": ["38+27"],
                    "minutes": 10,
                }],
                "training_summary": "手填的错题也能出方案。",
            },
        )
        run = trigger_task(MATH, "run_math", {
            "activity": "数学作业",
            "observed_at": "2026-11-01T16:30",
            "evidence": "作业本 P12",
            "source_kind": "observed",
            "score": 18,
            "total": 20,
            "errors": ["第2题进位忘了"],
        }, store)
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "completed", record.get("error_reason")
        assert record["acceptance_status"] == "accepted", record.get("acceptance_detail")
        artifact = record["artifact"]
        assert artifact["wrong_count"] == 1
        assert artifact["error_types"] == ["计算失误"]
        assert artifact["training_covers_errors"] is True

    def test_a_typed_error_is_not_mistaken_for_an_unanswered_one(self, monkeypatch, store):
        """家长写的是「哪题错了」，不是「孩子没作答」

        答案空着（`""`）和**没看清**（`None`）是两件事：
        混成一个的话，「第2题进位忘了」会被误判成「未作答」。
        """
        module = __import__(
            "boxes.learning_math.runner", fromlist=["_clean_analysis"]
        )
        judged = module._clean_analysis(
            {
                "wrong_questions": [{
                    "index": 0,
                    "error_type": "计算失误",
                }],
            },
            [{
                "no": "", "question": "第2题进位忘了",
                "student_answer": None, "correct_answer": "",
            }],
        )
        assert judged["wrong_questions"][0]["error_type"] == "计算失误"


# ------------------------------------------------------------------
# 3. 方案要盖住每一类错因
# ------------------------------------------------------------------
class TestThePlanMustCoverTheErrors:
    def test_a_missing_group_is_filled_in_mechanically(self, monkeypatch, store):
        """漏了一类错因的方案 → **兜底补上**，不整条判红

        「练了但没练到点上」是要补的活，不是要把整次履约判红的理由。
        家长拿到一张空卡加一句「未通过」，那是没用，不是诚实。
        """
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["training_plan"] = [analysis["training_plan"][0]]
        record = _run_photo(monkeypatch, store, analysis=analysis)
        artifact = record["artifact"]
        assert artifact["training_covers_errors"] is True
        assert len(artifact["training_plan"]) == 2, "少了一类错因的方案"
        record_status = record.get("acceptance_status")
        assert record_status == "accepted", (
            f"兜底补上了还不通过：{record.get('acceptance_detail')}"
        )

    def test_the_fallback_group_says_it_is_a_fallback(self, monkeypatch, store):
        """兜底的那组要**说清是兜底**——不假装模型给过方法"""
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["training_plan"] = [analysis["training_plan"][0]]
        record = _run_photo(monkeypatch, store, analysis=analysis)
        plan = record["artifact"]["training_plan"]
        fallback = [g for g in plan if g["error_type"] == "未作答"]
        assert fallback, "少的那类没补上"
        assert "没看出具体练法" in fallback[0]["focus"], (
            f"兜底组没说清自己是兜底：{fallback[0]['focus']}"
        )
        assert fallback[0]["drills"], "兜底组也要给出今晚能做的事"

    def test_a_group_with_a_mismatched_label_gets_relabelled(self, monkeypatch, store):
        """模型判「其它」却把方案写在「计算失误」名下 → **把标签改过来**

        方法是对的、标签错了的时候，扔掉方法才是浪费。
        """
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["wrong_questions"][0]["error_type"] = "其它"
        analysis["training_plan"][0]["error_type"] = "计算失误"
        record = _run_photo(monkeypatch, store, analysis=analysis)
        artifact = record["artifact"]
        assert artifact["error_types"] == ["其它", "未作答"]
        plan_types = [g["error_type"] for g in artifact["training_plan"]]
        assert plan_types == ["其它", "未作答"], f"方案没跟错因对上：{plan_types}"
        assert artifact["training_covers_errors"] is True
        # 方法是模型给的那套，只是标签被改过来了
        assert "进位" in artifact["training_plan"][0]["focus"]


# ------------------------------------------------------------------
# 2c. 「错题」是算出来的，不是看出来的
# ------------------------------------------------------------------
class TestTheWrongQuestionsAreVerifiedByCalculating:
    def test_a_question_whose_answer_is_right_gets_dropped(self, monkeypatch, store):
        """孩子答对了的题 → **剔出错题清单**

        照片上看着像错题（红叉看错、批改痕迹看错）很常见。
        算一遍就知道对不对——不剔的话，家长要照着练的是**做对的题**。
        """
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["wrong_questions"][0]["verdict"] = "right"
        analysis["wrong_questions"][0]["correct_answer"] = "55"
        record = _run_photo(monkeypatch, store, analysis=analysis)
        artifact = record["artifact"]
        assert artifact["wrong_count"] == 1, "做对的题还在清单里"
        assert artifact["verified_right_count"] == 1
        assert "55" not in [
            q.get("student_answer") for q in artifact["wrong_questions"]
        ]

    def test_the_model_saying_wrong_does_not_override_the_arithmetic(self, monkeypatch, store):
        """模型说「错了」，但算出来答案一样 → **照样剔掉**"""
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["wrong_questions"][0]["verdict"] = "wrong"
        analysis["wrong_questions"][0]["correct_answer"] = "55"  # 孩子的答案也是 55
        record = _run_photo(monkeypatch, store, analysis=analysis)
        artifact = record["artifact"]
        assert artifact["wrong_count"] == 1
        assert artifact["verified_right_count"] == 1

    def test_the_dropped_count_is_written_in_the_summary(self, monkeypatch, store):
        """剔掉的题数要**说在明面上**——不然家长以为漏了题"""
        analysis = json.loads(json.dumps(ANALYSIS))
        analysis["wrong_questions"][0]["verdict"] = "right"
        analysis["wrong_questions"][0]["correct_answer"] = "55"
        record = _run_photo(monkeypatch, store, analysis=analysis)
        summary = record["artifact"]["training_summary"]
        assert "看着像错题" in summary, f"没说清剔掉了什么：{summary}"


# ------------------------------------------------------------------
# 4. 「看不清」不许说成「都对」
# ------------------------------------------------------------------
class TestUnclearIsNotAllRight:
    def test_a_photo_where_nothing_can_be_read_says_so(self, monkeypatch, store):
        """照片里看不出哪题错了 → 说「没看出」，不谎称「都对」"""
        photo = dict(PHOTO)
        photo["wrong_questions_status"] = "unclear"
        photo["wrong_questions"] = None
        record = _run_photo(monkeypatch, store, photo=photo)
        artifact = record["artifact"]
        assert artifact["wrong_count"] == 0
        assert "没看出" in artifact["training_summary"], (
            f"该说清是没看出来：{artifact['training_summary']}"
        )
        assert "wrong_questions" in artifact["missing_fields"]

    def test_a_page_where_everything_is_right_says_so(self, monkeypatch, store):
        """照片上每题都对 → 说「都对」，别再去编训练方案"""
        photo = dict(PHOTO)
        photo["wrong_questions_status"] = "none"
        photo["wrong_questions"] = []
        record = _run_photo(monkeypatch, store, photo=photo)
        artifact = record["artifact"]
        assert artifact["wrong_count"] == 0
        assert artifact["training_plan"] == []
        assert "都对" in artifact["training_summary"]
        assert artifact["training_covers_errors"] is True


# ------------------------------------------------------------------
# 5. 档案腿：没有得分率也能进档案
# ------------------------------------------------------------------
class TestTheArchiveTakesRecordsWithoutAScore:
    def test_a_record_with_na_mastery_still_lands_in_the_archive(self, monkeypatch, store):
        """掌握度是 `n/a` 的记录 → 照样进成长档案

        档案是**时间线**：这次练习发生过，它就该在。
        缺得分率就写「没有」，不是不收，也不是编一个。
        """
        _fake_llm(monkeypatch, MATH, photo=PHOTO, analysis=ANALYSIS)
        run = trigger_task(MATH, "run_math", {"photo": TINY_JPEG}, store)
        upstream = store.get_run(run["task_run_id"])
        assert upstream["artifact"]["mastery"] == "n/a"

        archived = trigger_task(
            GROWTH, "run_growth", {}, store,
            handoff={"subject_result": run["task_run_id"]},
        )
        record = store.get_run(archived["task_run_id"])
        assert record["status"] == "completed", record.get("error_reason")
        assert record["acceptance_status"] == "accepted", record.get("acceptance_detail")
        assert record["artifact"]["mastery"] == "n/a"
        assert record["artifact"]["record_id"] == upstream["artifact"]["record_id"]


# ------------------------------------------------------------------
# 6. 声明：产出里真的有这些字段
# ------------------------------------------------------------------
class TestTheOutputDeclaresWhatItDelivers:
    def test_the_output_schema_declares_the_training_card_fields(self):
        """产出契约里得**声明**这些字段——判据读不到没声明的字段"""
        definition = BOX_REGISTRY[MATH][0]()
        schema = definition.get_schema(definition.result.result_schema)
        names = {f.name for f in schema.fields}
        for field in (
            "wrong_questions", "wrong_count", "error_types",
            "training_plan", "training_summary", "training_covers_errors",
            "mastery", "ratio_exact", "missing_fields",
        ):
            assert field in names, f"产出契约里没声明 {field}"

    def test_the_mastery_field_admits_the_third_state(self):
        """掌握度得能是 `n/a`——照片里没有得分是常事"""
        definition = BOX_REGISTRY[MATH][0]()
        schema = definition.get_schema(definition.result.result_schema)
        fields = {f.name: f for f in schema.fields}
        assert fields["mastery"].type == "number_or_na"
        assert fields["ratio_exact"].type == "boolean_or_na"

    def test_the_error_types_are_a_closed_table(self):
        """错因是**固定的表**——自由文本就没法按类分组"""
        from boxes.learning_math.schemas import ERROR_TYPES

        assert "其它" in ERROR_TYPES, "说不清的错因得有地方去"
        assert len(ERROR_TYPES) == len(set(ERROR_TYPES))

    def test_the_beeline_has_the_teacher_hop(self):
        """判错因是**独立的一跳**——看图失败和判断失败要分得开"""
        from kanban.trigger import declared_beeline_ops

        ops = declared_beeline_ops(MATH)
        assert "math_analyze" in ops, f"作业路线里没有判错因那一跳：{ops}"
        assert ops.index("math_read_photo") < ops.index("math_analyze")
        assert ops.index("math_analyze") < ops.index("math_packager")


# ------------------------------------------------------------------
# 7. 看板：这张卡得真的画出来
# ------------------------------------------------------------------
class TestTheCardIsDrawnOnTheBoard:
    """产出卡得是**给人看的**，不是一堆 JSON。

    这段逻辑只在页面里（Python 侧没有对应物），
    所以在 node 里真跑一遍——「字段名写错了」「转义漏了」
    这两类错在页面上看不出来。
    """

    @staticmethod
    def _extract(source: str, name: str) -> str:
        """从页面 JS 里抠出一个函数（按大括号配平，不靠猜行数）"""
        start = source.index(f"function {name}(")
        i = source.index("{", start)
        depth = 0
        for j in range(i, len(source)):
            if source[j] == "{":
                depth += 1
            elif source[j] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:j + 1]
        raise AssertionError(f"函数 {name} 没抠完整")

    def _render(self, artifact: dict) -> str:
        import json
        import re
        import shutil
        import subprocess
        import tempfile
        import pathlib

        from kanban.web import HTML_PAGE

        node = shutil.which("node")
        if not node:
            pytest.skip("没有 node，跳过看板渲染的真跑")
        source = "\n".join(re.findall(r"<script>(.*?)</script>", HTML_PAGE, re.S))
        script = f"""
{self._extract(source, "esc")}
{self._extract(source, "renderMathReview")}
const ARTIFACT = {json.dumps(artifact, ensure_ascii=False)};
process.stdout.write(renderMathReview(ARTIFACT));
"""
        with tempfile.TemporaryDirectory() as tmp:
            js = pathlib.Path(tmp) / "card.js"
            js.write_text(script, encoding="utf-8")
            proc = subprocess.run(
                [node, str(js)], capture_output=True, text=True, timeout=60,
            )
        assert proc.returncode == 0, (
            f"产出视图那段 JS 跑不起来：\n{proc.stderr[:1500]}"
        )
        return proc.stdout

    def _artifact(self, **over) -> dict:
        base = {
            "record_id": "math-abc123",
            "subject": "math",
            "activity": "数学作业",
            "observed_at": "2026-11-01",
            "mastery": "n/a",
            "evidence": "作业照片 photo:abc123",
            "source_kind": "observed",
            "detail": "这次没读出得分/满分（掌握度 n/a）；错 1 题（计算失误）",
            "ratio_exact": "n/a",
            "wrong_questions": [{
                "no": "3",
                "topic": "两位数加法进位",
                "question": "38+27=?",
                "student_answer": "55",
                "correct_answer": "65",
                "answer_source": "模型推算",
                "error_type": "计算失误",
                "error_note": "个位满十忘了进一",
            }],
            "wrong_count": 1,
            "error_types": ["计算失误"],
            "training_plan": [{
                "focus": "两位数加法的进位",
                "error_type": "计算失误",
                "how": "先算个位，满十进一，再加十位",
                "drills": ["38+27", "46+38"],
                "minutes": 10,
            }],
            "training_summary": "这次错在进位加法。",
            "training_covers_errors": True,
            "missing_fields": ["score", "total"],
        }
        base.update(over)
        return base

    def test_the_card_shows_the_wrong_questions_and_the_plan(self):
        html = self._render(self._artifact())
        assert "38+27=?" in html, "错题清单没画出来"
        assert "计算失误" in html, "错因没画出来"
        assert "两位数加法的进位" in html, "训练方案没画出来"
        assert "38+27" in html, "要练的题没画出来"

    def test_a_missing_score_is_not_drawn_as_zero(self):
        """`n/a` 画成「没有得分率」——**不画 0.00**

        画成 0.00 等于跟家长说「这次一分没得」。
        """
        html = self._render(self._artifact())
        assert "没有得分率" in html
        assert "0.00" not in html

    def test_the_missing_fields_are_shown(self):
        """缺了哪几个要说出来——空着的格子不说，读者会以为「就是这样」"""
        html = self._render(self._artifact())
        assert "score" in html and "total" in html

    def test_the_model_output_is_escaped(self):
        """卡上的每一格都是模型写的——**不转义就是注入**"""
        html = self._render(self._artifact(
            wrong_questions=[{
                "no": "3",
                "question": "<script>alert(1)</script>",
                "student_answer": "",
                "correct_answer": "",
                "error_type": "其它",
                "error_note": "",
            }],
        ))
        assert "<script>alert(1)</script>" not in html, "模型输出的标签没转义"
        assert "&lt;script&gt;" in html, "转义后的样子也不对"

    def test_the_view_is_registered_and_declared(self):
        from kanban.web import HTML_PAGE

        assert "math_review: renderMathReview" in HTML_PAGE, (
            "看板的视图注册表里没有 math_review"
        )
        definition = BOX_REGISTRY[MATH][0]()
        assert definition.result.view == "math_review", (
            f"数学盒声明的产出形态是 {definition.result.view}"
        )
