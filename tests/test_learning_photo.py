"""tests.test_learning_photo - 学习盒的「拍照投料」：坏图必拒、读不出必红、手填优先

这组守卫守四件事：

1. **坏照片在投料口就被拒**——mime 不认、超大、压根不是图片，
   都在结构契约那一层就挡住，而不是等模型接口来一句 400。
2. **照片读不出来的必须红**——认不出得分/满分就中止履约并说清
   「哪个字段没读出来，请手工填」。**不猜**：猜一个满分，
   错的是以后每一份档案里的比率。
3. **手填的优先于照片读出来的**——人填的是他当场知道的；
   两边都有时以人填的为准，不悄悄叠成一个说不清是谁说的数。
4. **图片字节不进产出**——产出是要落盘的，
   一张 3 MB 的 base64 跟到落盘会把那条记录挤成「只保留了元信息」。
"""

from __future__ import annotations

import base64
import json
import re

import pytest

from kanban.trigger import (
    BOX_REGISTRY,
    declared_beeline_ops,
    list_task_entries,
    trigger_task,
)
from runtime.contract import validate_payload
from runtime.store import TaskRunStore

from kanban.web import HTML_PAGE as HTML_PAGE_SRC

SUBJECT_BOXES = [
    "learning_math_box",
    "learning_chinese_box",
    "learning_english_box",
]

# 一张 1x1 的 JPEG（只当占位用，模型那腿在测试里是假的）
TINY_JPEG = (
    "data:image/jpeg;base64,"
    + base64.b64encode(
        bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000"
            "ffdb004300ffffffffffffffffffffffffffffffffffff"
            "ffffffffffffffffffffffffffffffffffffffffffff"
            "ffffffffffffffffffffffffffffffffffffffffffffffff"
            "ffffffffffffffffffc00011080001000101011100"
        )
    ).decode()
)


@pytest.fixture()
def store(tmp_path):
    return TaskRunStore(path=tmp_path / "runs.jsonl")


def _definition(box_id: str):
    return BOX_REGISTRY[box_id][0]()


def _schema(box_id: str, schema_id: str):
    return _definition(box_id).get_schema(schema_id)


def _runner_module(box_id: str):
    import importlib

    return importlib.import_module(f"boxes.{box_id.replace('_box', '')}.runner")


def _math_payload(**over):
    base = {
        "activity": "数学作业",
        "observed_at": "2026-11-01T16:30",
        "evidence": "作业本 P12",
        "source_kind": "observed",
        "score": 92,
        "total": 100,
    }
    base.update(over)
    return base


def _fake_photo_facts(monkeypatch, box_id: str, **facts):
    """把「看图那一跳」的模型换成假的，返回它读出来的事实"""
    module = _runner_module(box_id)
    seen = {}

    def fake_complete_json(prompt, **kwargs):
        seen["images"] = kwargs.get("images")
        seen["prompt"] = prompt
        return dict(facts)

    monkeypatch.setattr(module, "complete_json", fake_complete_json)
    return seen


# ------------------------------------------------------------------
# 1. 声明：照片是媒体字段，投料口知道收什么
# ------------------------------------------------------------------
class TestThePhotoIsDeclared:
    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_each_subject_box_declares_a_photo_field(self, box_id):
        schema = _schema(box_id, _definition(box_id).task[0].task_schema)
        photos = [f for f in schema.fields if f.media == "image"]
        assert len(photos) == 1, f"{box_id} 应当恰好声明一个照片字段"
        accepted = photos[0].accepted_media_types()
        assert "image/jpeg" in accepted and "image/png" in accepted
        # iPhone 拍的照片默认是 HEIC——不收它等于把最常见的那种作业照挡在门外
        assert "image/heic" in accepted, (
            f"{box_id} 不收 HEIC：iPhone 拍的作业照片投不进来"
        )

    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_the_intake_lets_the_form_render_the_other_fields(self, box_id):
        """投料口要能渲染「图片之外的字段」——清单归 schema 所有"""
        entries = list_task_entries(box_id)
        entry = entries[0]
        assert entry["payload_kind"] == "media", (
            f"{box_id} 的投料口不是 media 形态，照片投不进去"
        )
        names = {f["name"] for f in entry["fields"]}
        assert "photo" in names
        assert "activity" in names, "表单里没有 activity——这次做了什么没法填"


# ------------------------------------------------------------------
# 2. 坏照片在投料口就被拒
# ------------------------------------------------------------------
class TestBadPhotosAreRefusedAtTheGate:
    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_an_unsupported_mime_is_refused(self, box_id):
        schema = _schema(box_id, _definition(box_id).task[0].task_schema)
        errors = validate_payload(
            {"photo": "data:image/bmp;base64,Qk0eAAAAAAAAABo="},
            schema,
            schemas=_definition(box_id).schemas,
        )
        assert errors, "不认的格式居然放过去了"
        assert "bmp" in str(errors).lower(), str(errors)

    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_an_oversized_photo_is_refused(self, box_id):
        schema = _schema(box_id, _definition(box_id).task[0].task_schema)
        big = "data:image/jpeg;base64," + ("A" * (12 * 1024 * 1024))
        errors = validate_payload(
            {"photo": big}, schema, schemas=_definition(box_id).schemas
        )
        assert errors, "超大的图居然放过去了"
        assert "MB" in str(errors)

    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_a_value_that_is_not_a_picture_is_refused(self, box_id):
        schema = _schema(box_id, _definition(box_id).task[0].task_schema)
        errors = validate_payload(
            {"photo": "我把作业本念给你听：今天做了三道题"},
            schema,
            schemas=_definition(box_id).schemas,
        )
        assert errors, "一段文字居然被当成图片收下了"

    def test_a_bad_photo_produces_no_task_run(self, store):
        """投料被拒 = 这次投料没有产生 TaskRun（看板不留记录）"""
        from kanban.trigger import TriggerError

        with pytest.raises(TriggerError) as exc:
            trigger_task(
                "learning_math_box", "run_math",
                {"photo": "data:application/pdf;base64,AAAA"},
                store,
            )
        assert "photo" in str(exc.value)


# ------------------------------------------------------------------
# 3. 照片读出来的数：手填优先 / 照片补空 / 读不出就红
# ------------------------------------------------------------------
class TestWhatThePhotoContributes:
    def test_the_model_reads_the_score_off_the_photo(self, monkeypatch, store):
        seen = _fake_photo_facts(
            monkeypatch, "learning_math_box",
            score=92, total=100, observed_at="2026-11-01",
            activity="数学作业", errors=["第2题除法算错了"], notes=None,
        )
        run = trigger_task(
            "learning_math_box", "run_math",
            {"photo": TINY_JPEG}, store,
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "completed", record.get("error_reason")
        assert record["acceptance_status"] == "accepted"
        assert seen["images"], "看图那一跳根本没把图发出去"
        artifact = record["artifact"]
        assert artifact["mastery"] == pytest.approx(0.92)

    def test_a_photo_with_no_readable_score_fails_loudly(self, monkeypatch, store):
        """认不出得分就中止，并说清「哪个字段没读出来」"""
        _fake_photo_facts(
            monkeypatch, "learning_math_box",
            score=None, total=None, observed_at=None, activity=None, errors=None,
        )
        run = trigger_task(
            "learning_math_box", "run_math",
            {"photo": TINY_JPEG}, store,
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "failed", "照片里没有分数居然记了一条"
        reason = str(record.get("error_reason"))
        assert "得分" in reason, f"报错要说清是得分没读出来：{reason}"
        assert "score" in reason, f"报错要指名要填哪个字段：{reason}"

    def test_a_photo_with_no_readable_full_score_fails_loudly(self, monkeypatch, store):
        """满分读不出来也不许默认 100——猜的满分会污染以后每一份比率"""
        _fake_photo_facts(
            monkeypatch, "learning_math_box",
            score=92, total=None, observed_at="2026-11-01",
            activity="数学作业", errors=None,
        )
        run = trigger_task(
            "learning_math_box", "run_math",
            {"photo": TINY_JPEG}, store,
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "failed", "猜了个满分就记了一条"
        reason = str(record.get("error_reason"))
        assert "满分" in reason or "total" in reason, reason

    def test_what_the_human_typed_wins_over_what_the_model_read(self, monkeypatch, store):
        """手填优先：人填 80，模型读出 92 → 记 80"""
        _fake_photo_facts(
            monkeypatch, "learning_math_box",
            score=92, total=100, observed_at="2026-11-01",
            activity="模型读到的标题", errors=None,
        )
        run = trigger_task(
            "learning_math_box", "run_math",
            _math_payload(photo=TINY_JPEG, score=80, activity="我写的标题"),
            store,
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "completed", record.get("error_reason")
        artifact = record["artifact"]
        assert artifact["mastery"] == pytest.approx(0.8)
        assert artifact["activity"] == "我写的标题"

    def test_the_photo_fills_the_blanks_the_human_left(self, monkeypatch, store):
        """照片补空：人没填得分，模型读出来了 → 用读出来的"""
        _fake_photo_facts(
            monkeypatch, "learning_math_box",
            score=75, total=100, observed_at="2026-11-01",
            activity="数学作业", errors=None,
        )
        payload = _math_payload()
        payload.pop("score")
        payload.pop("total")
        run = trigger_task(
            "learning_math_box", "run_math", {**payload, "photo": TINY_JPEG}, store
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "completed", record.get("error_reason")
        assert record["artifact"]["mastery"] == pytest.approx(0.75)

    def test_a_photo_makes_the_source_observed(self, monkeypatch, store):
        """照片本身就是「看到了」的证据"""
        _fake_photo_facts(
            monkeypatch, "learning_math_box",
            score=92, total=100, observed_at="2026-11-01",
            activity="数学作业", errors=None,
        )
        payload = _math_payload()
        payload.pop("source_kind")
        run = trigger_task(
            "learning_math_box", "run_math", {**payload, "photo": TINY_JPEG}, store
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "completed", record.get("error_reason")
        assert record["artifact"]["source_kind"] == "observed"

    def test_a_photo_cannot_be_labelled_reported(self, monkeypatch, store):
        """投了照片又标 reported —— 自相矛盾，当场拒"""
        _fake_photo_facts(
            monkeypatch, "learning_math_box",
            score=92, total=100, observed_at="2026-11-01",
            activity="数学作业", errors=None,
        )
        run = trigger_task(
            "learning_math_box", "run_math",
            _math_payload(photo=TINY_JPEG, source_kind="reported"), store,
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "failed", "照片 + reported 居然过了"
        reason = str(record.get("error_reason"))
        assert "reported" in reason, reason

    def test_no_photo_and_no_facts_fails_loudly(self, store):
        """既没照片也没手工事实 → 中止（不产出一条空记录）"""
        run = trigger_task("learning_math_box", "run_math", {}, store)
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "failed"
        assert record.get("error_reason")


# ------------------------------------------------------------------
# 4. 产出：不带图片字节，但带得上照片的出处
# ------------------------------------------------------------------
class TestTheArtifact:
    def test_the_artifact_never_carries_the_image_bytes(self, monkeypatch, store):
        """产出是要落盘的——图片字节跟到落盘，记录会被截成「只保留元信息」"""
        _fake_photo_facts(
            monkeypatch, "learning_math_box",
            score=92, total=100, observed_at="2026-11-01",
            activity="数学作业", errors=["第2题算错了"],
        )
        run = trigger_task(
            "learning_math_box", "run_math",
            {"photo": TINY_JPEG}, store,
        )
        record = store.get_run(run["task_run_id"])
        assert record["status"] == "completed", record.get("error_reason")
        assert not record["artifact_truncated"], "产出被截断了——多半是图片字节跟进去了"
        blob = json.dumps(record["artifact"], ensure_ascii=False)
        assert "data:image" not in blob, "产出里带着图片字节"
        assert "base64" not in blob, "产出里带着 base64"

    def test_the_evidence_names_the_photo(self, monkeypatch, store):
        """出处要能指回那张照片（指纹从图片字节推导）"""
        _fake_photo_facts(
            monkeypatch, "learning_math_box",
            score=92, total=100, observed_at="2026-11-01",
            activity="数学作业", errors=None,
        )
        run = trigger_task(
            "learning_math_box", "run_math",
            {"photo": TINY_JPEG}, store,
        )
        record = store.get_run(run["task_run_id"])
        evidence = record["artifact"]["evidence"]
        assert "photo:" in evidence, f"出处里没有照片的指认：{evidence}"

    def test_the_same_photo_gets_the_same_fingerprint(self):
        """同一张照片重投一次，指纹必须还是同一个

        指纹进了 evidence，evidence 进了 record_id 的推导——
        指纹一漂，「同一件事」就变成「两件事」。
        """
        module = _runner_module("learning_math_box")
        a = module._photo_digest(TINY_JPEG)
        b = module._photo_digest(TINY_JPEG)
        c = module._photo_digest(TINY_JPEG + "x")
        assert a == b
        assert a != c

    @pytest.mark.parametrize("box_id", SUBJECT_BOXES)
    def test_the_beeline_has_a_photo_reading_hop(self, box_id):
        ops = declared_beeline_ops(box_id)
        assert any(op.endswith("_read_photo") for op in ops), (
            f"{box_id} 的作业路线里没有看图那一跳：{ops}"
        )


# ------------------------------------------------------------------
# 5. 投料口那一段 JS：表单 → payload 的组装
# ------------------------------------------------------------------
class TestTheIntakeFormAssemblesThePayload:
    """投料口的表单怎么变成 payload——**用 node 真跑一遍**

    为什么单独跑：这段逻辑在 Python 侧**没有对应物**，
    「字段名写错了」「number 变成了字符串」「数组没按行拆开」
    这三类错在页面上看不出来——投出去被契约拒，
    而那句报错长得像别的错（是 score 不是 score_type？）。

    所以这里把页面里那段 JS 抠出来，在 node 里喂一套假 DOM，
    断言它交出来的 payload 的**键和类型**都对得上 schema。
    """

    @staticmethod
    def _extract_function(source: str, name: str) -> str:
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

    def _collect(self, fields, values) -> dict:
        """在 node 里跑 collectMediaFields()，返回它交出来的 payload"""
        import json
        import re
        import shutil
        import subprocess
        import tempfile
        import pathlib

        from kanban.web import HTML_PAGE

        node = shutil.which("node")
        if not node:
            pytest.skip("没有 node，跳过投料口 JS 的真跑")
        scripts = re.findall(r"<script>(.*?)</script>", HTML_PAGE, re.S)
        assert scripts, "看板里没有 <script> 块"
        source = "\n".join(scripts)
        func = self._extract_function(source, "collectMediaFields")

        elements = []
        for name, kind in fields:
            if kind == "number":
                elements.append(
                    {"id": f"mf-{name}", "tagName": "INPUT", "type": "number",
                     "value": str(values[name])}
                )
            elif kind == "array":
                elements.append(
                    {"id": f"mf-{name}", "tagName": "TEXTAREA", "type": "textarea",
                     "value": values[name]}
                )
            else:
                elements.append(
                    {"id": f"mf-{name}", "tagName": "INPUT", "type": "text",
                     "value": values[name]}
                )

        script = f"""
const ELEMENTS = {json.dumps(elements, ensure_ascii=False)};
const BOX = {{ querySelectorAll: () => ELEMENTS }};
globalThis.document = {{ getElementById: (id) => (id === 'media-fields' ? BOX : null) }};
{func}
process.stdout.write(JSON.stringify(collectMediaFields()));
"""
        with tempfile.TemporaryDirectory() as tmp:
            js = pathlib.Path(tmp) / "intake.js"
            js.write_text(script, encoding="utf-8")
            proc = subprocess.run(
                [node, str(js)], capture_output=True, text=True, timeout=60,
            )
        assert proc.returncode == 0, (
            f"投料口那段 JS 跑不起来：\n{proc.stderr[:1500]}"
        )
        return json.loads(proc.stdout)

    def test_the_form_produces_the_keys_and_types_the_schema_wants(self):
        """字段名、类型都对得上 schema——数字是数字，数组是数组"""
        box_id = "learning_math_box"
        schema = _schema(box_id, _definition(box_id).task[0].task_schema)
        fields = [
            (f.name, f.type)
            for f in schema.fields
            if not f.media and f.type in ("string", "number", "array", "enum")
        ]
        assert fields, "学习盒的投料口没有可填的字段？"

        values = {
            "activity": "数学作业",
            "observed_at": "2026-11-01T16:30",
            "evidence": "作业本 P12",
            "source_kind": "observed",
            "score": 92,
            "total": 100,
            "notes": "",
            "minutes": 30,
            "errors": "第2题除法算错了\n第4题进位忘了",
        }
        payload = self._collect(fields, values)

        assert set(payload) == {name for name, _ in fields} - {"notes"}, (
            f"投料口交出来的字段不对：{sorted(payload)}"
        )
        assert payload["activity"] == "数学作业"
        assert payload["score"] == 92 and isinstance(payload["score"], int), (
            "数字字段必须是数字——字符串会让契约校验拒掉"
        )
        assert payload["total"] == 100
        assert payload["minutes"] == 30
        assert payload["source_kind"] == "observed"
        assert payload["errors"] == ["第2题除法算错了", "第4题进位忘了"], (
            "数组要按行拆开——一整段塞进去会让盒子收到一个长字符串"
        )

    def test_an_empty_field_is_left_out_entirely(self):
        """没填的字段**不要交上去**——交一个空串会被当成「填了但内容是空的」"""
        box_id = "learning_math_box"
        schema = _schema(box_id, _definition(box_id).task[0].task_schema)
        fields = [(f.name, f.type) for f in schema.fields if not f.media]
        payload = self._collect(fields, {name: "" for name, _ in fields})
        assert payload == {}, f"空表单交出了东西：{payload}"

    def test_the_render_reads_the_field_list_from_the_backend(self):
        """表单照着**后端给的字段清单**渲染——前端不自己列字段名"""
        source = "\n".join(
            re.findall(r"<script>(.*?)</script>", HTML_PAGE_SRC, re.S)
        )
        assert "entry.fields" in source or "entry && entry.fields" in source, (
            "表单没有照后端给的字段清单渲染——换个盒子就得改前端"
        )
