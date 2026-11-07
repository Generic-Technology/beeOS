"""
tests.test_workshop - BeeBox Workshop（工作台）的守卫

工作台第一版只做**体检台**：把一只盒子声明得对不对摊开给人看。

这组守卫盯三件事：

1. **页面里不许出现某一只盒子的名字**——加盒子不用改页面；
2. **页面不许把业务事实的值重新敲一遍**——归属、价值流、协作边界的值都住在盒子里；
3. **页面和守卫说的是同一件事**——它们读的是同一份判定
   （`runtime/box_health.py`），不是各说各话。

第 3 条最要紧：以前「盒子合不合规」只住在 pytest 断言里。
工作台要给人看同一批结论，照抄一遍就会出现两套口径——
守卫说绿、页面说红，而且没人知道该信哪个。
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import shutil
import socket
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from kanban.trigger import BOX_REGISTRY
from kanban.web import KanbanRequestHandler
from runtime.store import TaskRunStore


REPO = pathlib.Path(__file__).resolve().parent.parent
WORKSHOP_DIR = REPO / "workshop"


# ============================================================
# 1. 页面不许认识某一只盒子
# ============================================================


class TestTheWorkshopNamesNoBox:
    def test_no_box_id_appears_in_the_workshop_source(self):
        """工作台的源码里不许出现任何一只盒子的 id

        页面里写死一只盒子 = 页面抄了一份目录。那份目录必然漂：
        加了盒子页面不更新、删了盒子页面还留着。
        """
        offenders = []
        for path in sorted(WORKSHOP_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    text = ast.get_docstring(node, clean=False)
                    if text is not None:
                        docstrings.add(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in docstrings:
                        continue
                    for box_id in BOX_REGISTRY:
                        if box_id in node.value:
                            offenders.append(f"{path.name}: {node.value[:60]!r}")
        assert not offenders, (
            "工作台里写死了盒子 id：\n  " + "\n  ".join(sorted(set(offenders)))
        )

    def test_the_page_renders_every_box_from_data(self):
        """页面上的盒子清单来自数据，不是源码

        反过来验：加一只盒子，页面上的清单跟着长——
        不改页面一个字。
        """
        from workshop.views import workshop_payload

        payload = workshop_payload()
        assert {b["box_id"] for b in payload["boxes"]} == set(BOX_REGISTRY)


# ============================================================
# 2. 工作台不许把业务事实的值重新敲一遍
# ============================================================


class TestTheWorkshopRetypesNoBusinessFact:
    def test_no_business_fact_is_written_into_the_workshop(self):
        """归属、价值流、协作边界的值都住在盒子里（或协作边界声明里），工作台只读

        跟看板那条守卫同一条规矩：值重敲一遍 = 第二份真相。
        """
        from fulfillment_scopes import fulfillment_scopes_of

        facts = set()
        for box_id, (get_def, _bl) in BOX_REGISTRY.items():
            d = get_def()
            facts |= {d.display_name, d.value_stream, d.role}
            facts |= set(fulfillment_scopes_of(box_id))
        facts = {f for f in facts if f}

        offenders = []
        for path in sorted(WORKSHOP_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    text = ast.get_docstring(node, clean=False)
                    if text is not None:
                        docstrings.add(text)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value not in docstrings
                        and node.value in facts):
                    offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
        assert not offenders, (
            "工作台里又敲了一遍业务事实的值：\n  " + "\n  ".join(offenders)
            + "\n它们应该只住在各盒 definition（或协作边界声明）里"
        )


# ============================================================
# 3. 页面说的和守卫说的是同一件事
# ============================================================


class TestThePageShowsTheSameVerdict:
    def test_the_page_carries_the_checker_verdict_for_every_box(self):
        """页面上的结论 = 检查器的结论（同一个函数，不是各算一遍）"""
        from runtime.box_health import inspect_box
        from workshop.views import workshop_payload

        payload = workshop_payload()
        for box in payload["boxes"]:
            assert box["health"]["verdict"] in ("pass", "fail", "unknown")
            assert box["health"]["checks"], "一只盒子一条检查都没有 = 体检台没在体检"
            for check in box["health"]["checks"]:
                assert check["why"], (
                    f"{box['box_id']} 的检查 {check['id']} 没写「为什么重要」——"
                    f"看的人只知道红，不知道值不值得修"
                )

    def test_the_three_states_look_different_on_the_page(self):
        """三态在页面上必须长得不一样

        「没验到」混成「通过」= 给没验过的事发合格证。
        要是三种状态在页面上长得一样，那三态就等于没分。
        """
        from workshop.views import workshop_page

        page = workshop_page()
        for status in ("pass", "fail", "unknown"):
            assert f".{status} {{" in page, f"页面里没有 {status} 的样式"

        # 三态的文案表：**键必须正好是这三个**，不多不少。
        # 多一个值说明判定那边加了第四种状态而页面没跟上；
        # 少一个值，那一种状态在页面上落进 fallback——看起来跟别的没区别。
        block = re.search(r"var STATUS_TEXT = \{(.*?)\n  \};", page, re.S)
        assert block, "页面里没有 STATUS_TEXT 文案表"
        keys = set(re.findall(r"^\s{4}(\w+):", block.group(1), re.M))
        assert keys == {"pass", "fail", "unknown"}, (
            f"页面的三态文案表是 {sorted(keys)}，判定那边是三种——"
            f"对不上就说明两边各说各话"
        )


# ============================================================
# 4. 页面本身不许坏
# ============================================================


class TestThePageIsNotBroken:
    def test_the_javascript_is_syntactically_valid(self):
        """页面里的 JS 必须能过语法检查

        页面是**纯客户端渲染**：JS 错 = 白屏，
        而且服务端照样返回 200——不验的话，坏掉的页面会被当成正常的。
        """
        node = shutil.which("node")
        if node is None:
            pytest.skip("这台机器上没有 node，跳过 JS 语法检查")

        from workshop.views import workshop_page

        page = workshop_page()
        scripts = re.findall(
            r"<script(?![^>]*type=\"application/json\")[^>]*>(.*?)</script>",
            page, re.S,
        )
        assert scripts, "页面里没有可执行的 JS——它靠什么渲染？"

        tmp = REPO / ".workshop_js_check.js"
        try:
            tmp.write_text("\n".join(scripts), encoding="utf-8")
            proc = subprocess.run(
                [node, "--check", str(tmp)], capture_output=True, text=True,
            )
            assert proc.returncode == 0, (
                f"页面里的 JS 语法不过：\n{proc.stderr}"
            )
        finally:
            tmp.unlink(missing_ok=True)

    def test_the_injected_data_is_valid_json(self):
        """注入页面的那段必须是合法 JSON

        解析失败 = 整页白屏，而服务端返回 200。
        """
        from workshop.views import workshop_page

        page = workshop_page()
        match = re.search(
            r'<script id="workshop-data" type="application/json">(.*?)</script>',
            page, re.S,
        )
        assert match, "页面里没有注入数据的 script 块"
        data = json.loads(match.group(1))
        assert data["boxes"]
        assert data["counts"]["boxes"] == len(data["boxes"])


# ============================================================
# 5. HTTP 层的两个接口
# ============================================================


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server_url(tmp_path: pathlib.Path):
    store = TaskRunStore(path=tmp_path / "task_runs.jsonl")
    port = _find_free_port()
    KanbanRequestHandler.store = store
    server = ThreadingHTTPServer(("127.0.0.1", port), KanbanRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


class TestHealthEndpoints:
    def test_the_catalog_endpoint_answers_with_every_box(self, server_url):
        import urllib.request

        with urllib.request.urlopen(f"{server_url}/api/boxes/health") as resp:
            data = json.loads(resp.read())
        assert data["counts"]["boxes"] == len(BOX_REGISTRY)
        assert {b["box_id"] for b in data["boxes"]} == set(BOX_REGISTRY)

    def test_a_single_box_endpoint_answers_with_that_box(self, server_url):
        import urllib.request

        box_id = sorted(BOX_REGISTRY)[0]
        with urllib.request.urlopen(f"{server_url}/api/boxes/{box_id}/health") as resp:
            data = json.loads(resp.read())
        assert data["box_id"] == box_id
        assert data["checks"]

    def test_an_unknown_box_is_refused_with_a_reason(self, server_url):
        import urllib.error
        import urllib.request

        try:
            urllib.request.urlopen(f"{server_url}/api/boxes/no_such_box/health")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            body = json.loads(exc.read())
            assert "no_such_box" in body["error"]
            assert body["registered"], "404 得说清目录里有哪些盒子"
        else:
            pytest.fail("不存在的盒子居然答了 200")

    def test_the_workshop_page_is_served(self, server_url):
        import urllib.request

        with urllib.request.urlopen(f"{server_url}/workshop") as resp:
            body = resp.read().decode("utf-8")
        assert "BeeBox Workshop" in body
        assert "workshop-data" in body

    def test_the_workshop_is_read_only(self, server_url):
        """工作台第一版不许能改东西

        看板那边不放能改东西的入口，工作台第一版也只读。
        哪天要能改，这里得跟着改——而且要有对应的授权设计。
        """
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{server_url}/workshop", data=b"{}", method="POST",
        )
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            pytest.fail("工作台居然能 POST——第一版是只读的")


# ============================================================
# 4. 协作边界摆的是名字，不是 id
# ============================================================


class TestTheWorkshopShowsTheScopeName:
    """工作台上的协作边界得是**名字**，不是 id

    同一个事实，看板和工作台不能两种说法：
    看板摆「学习成长」、工作台摆「Learning」，读者会以为是两回事。

    id 是机器用的（URL / API / 接线）；摆到界面上的是声明里的
    `display_name`——`Learning` 这样的 id 摆出来，读者分不出它是
    协作边界还是价值流（学习协作边界那 4 只盒子的 `value_stream`
    也正好叫 `Learning`）。
    """

    def test_the_scope_is_shown_by_name_not_by_id(self):
        from fulfillment_scopes import FULFILLMENT_SCOPE_REGISTRY
        from workshop.views import workshop_payload

        names = {
            d.display_name
            for d in FULFILLMENT_SCOPE_REGISTRY.values()
            if d.display_name
        }
        ids = set(FULFILLMENT_SCOPE_REGISTRY)

        payload = workshop_payload()
        assert payload["boxes"], "工作台一只盒子都没有——那这条守卫是空的"
        for box in payload["boxes"]:
            shown = box["fulfillment_scope"]
            assert shown not in ids, (
                f"{box['box_id']} 摆的是协作边界的 **id**（{shown!r}）——"
                f"界面上该摆名字（{sorted(names)}）"
            )
            assert shown == "" or shown in names, (
                f"{box['box_id']} 摆的 {shown!r} 不是任何协作边界声明的名字——"
                f"该是 {sorted(names)} 里的一个，或者空（没归范围）"
            )
