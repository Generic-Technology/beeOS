"""
tests.test_scope_filter - 「范围」这个轴必须**三块画面一起**换

**这个 bug 是什么**：看板顶部的「范围」（全部域 / 未归域 / Learning / WMS）
是**显示过滤**——它控制「看哪些盒子」。以前它只管中间的地图：
选 Learning 域，地图只剩 Learning 的 4 只盒子，可左边的任务列表和
右边的操作轨迹照旧列**全平台**——于是读者在「Learning 的地图」旁边读
「WMS 的任务」「WMS 的轨迹」，两件事对不上，**而且没有任何东西提示
他口径变了**。

**为什么用 node 真跑一遍**：这段逻辑在 Python 侧没有对应物。
源码里 grep 「出现 scopeBoxIds」只能证明代码写了，
证明不了它**真的按范围过滤了**——过滤写反了、写漏了一个字段，
grep 照样绿。

所以这里把看板那段 JS 抠出来，在 node 里喂一套假 DOM，
喂一份跨两个域的 payload，断言两栏各自的输出。
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import tempfile
import subprocess

import pytest

from kanban.web import HTML_PAGE


# ============================================================
# 假 DOM + 真跑
# ============================================================

#: 一个「什么都能用」的假元素。页面脚本在加载时就会摸 DOM
#: （同步表头高度等），不给假元素它当场就炸。
_FAKE_DOM = """
function fakeEl() {
  const el = {
    style: {}, dataset: {}, classList: {
      add: function(){}, remove: function(){}, toggle: function(){},
      contains: function(){ return false; },
    },
    querySelectorAll: function(){ return []; },
    querySelector: function(){ return null; },
    getAttribute: function(){ return ''; },
    setAttribute: function(){},
    addEventListener: function(){},
    appendChild: function(){},
    removeChild: function(){},
    innerHTML: '', textContent: '', value: '',
    getBoundingClientRect: function(){
      return { top: 0, left: 0, width: 100, height: 20, bottom: 20, right: 100 };
    },
  };
  return el;
}
const EL = fakeEl();
globalThis.document = {
  getElementById: function(){ return EL; },
  querySelectorAll: function(){ return []; },
  querySelector: function(){ return null; },
  createElement: function(){ return fakeEl(); },
  addEventListener: function(){},
  body: EL,
};
globalThis.window = {
  addEventListener: function(){},
  innerWidth: 1200, innerHeight: 800,
  location: { search: SEARCH },
};
globalThis.location = { search: SEARCH };
// 定时器不真跑：页面每 2s 拉一次数据，node 里不需要。
globalThis.setInterval = function(){ return 0; };
globalThis.clearInterval = function(){};
globalThis.requestAnimationFrame = function(){ return 0; };
// fetch 永远不 resolve：`tick()` 停在 await 上，页面不会自己重绘。
globalThis.fetch = function(){ return new Promise(function(){}); };
"""


def _script_source() -> str:
    """看板里那段 JS"""
    scripts = re.findall(r"<script>(.*?)</script>", HTML_PAGE, re.S)
    assert scripts, "看板里没有 <script> 块"
    return "\n".join(scripts)


def _run(search: str, payload: dict) -> dict:
    """在 node 里跑看板的 JS，返回两栏的渲染结果"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("这台机器上没有 node，跳过前端真跑")

    source = _script_source()
    # 把要验的两个函数导出到全局——`eval` 里的 const 出了 eval 就找不着了。
    source += (
        "\nglobalThis.__renderTaskList = renderTaskList;"
        "\nglobalThis.__renderRail = renderRail;\n"
    )
    script = (
        # SEARCH / DATA 先声明：假 DOM 里要用到 SEARCH
        f"const SEARCH = {json.dumps(search)};\n"
        f"const DATA = {json.dumps(payload, ensure_ascii=False)};\n"
        + _FAKE_DOM
        + source
        + "\nprocess.stdout.write(JSON.stringify({"
        + "  tasks: globalThis.__renderTaskList(DATA),"
        + "  rail: globalThis.__renderRail(DATA),"
        + "}));\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        js = pathlib.Path(tmp) / "board.js"
        js.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            [node, str(js)], capture_output=True, text=True, timeout=60,
        )
    assert proc.returncode == 0, (
        f"看板那段 JS 跑不起来：\n{proc.stderr[:1500]}"
    )
    return json.loads(proc.stdout)


# ============================================================
# 跨两个域的 payload
# ============================================================


def _payload() -> dict:
    """Learning 域一只盒子 + WMS 域一只盒子，各带一件任务、一条轨迹"""
    return {
        "box_filter": "",
        "task_filter": None,
        "fulfillment_scopes": {
            "Learning": ["math_box"],
            "WMS": ["wms_box"],
        },
        "tasks": [
            {
                "ref": "task-learning",
                "label": "语文练习这条",
                "kind": "requirement",
                "at": "2026-11-01T10:00",
                "state": "ok",
                "runs": 1,
                "failed": 0,
                "boxes": [{
                    "box_id": "math_box",
                    "display_name": "数学盒",
                    "status": "completed",
                    "acceptance_status": "accepted",
                }],
            },
            {
                "ref": "task-wms",
                "label": "库区管理这条",
                "kind": "requirement",
                "at": "2026-11-01T11:00",
                "state": "ok",
                "runs": 1,
                "failed": 0,
                "boxes": [{
                    "box_id": "wms_box",
                    "display_name": "仓储盒",
                    "status": "completed",
                    "acceptance_status": "accepted",
                }],
            },
        ],
        "op_trails": [
            {
                "box_id": "math_box",
                "display_name": "数学盒",
                "at": "2026-11-01T10:00",
                "ops": [{"op_id": "math_read"}],
                "declared_ops": ["math_read"],
                "has_trace": True,
            },
            {
                "box_id": "wms_box",
                "display_name": "仓储盒",
                "at": "2026-11-01T11:00",
                "ops": [{"op_id": "wms_intake"}],
                "declared_ops": ["wms_intake"],
                "has_trace": True,
            },
        ],
    }


class TestTheScopeFiltersEveryPanel:
    def test_the_learning_scope_drops_the_wms_task_and_trail(self):
        """选 Learning 域：左栏和右栏都不许再出现 WMS 的东西

        这就是线上那个 bug：中间换了域，两边没换。
        """
        out = _run("?scope=Learning", _payload())
        assert "语文练习这条" in out["tasks"], "Learning 的任务不见了"
        assert "库区管理这条" not in out["tasks"], (
            "Learning 域的任务列表里还混着 WMS 的任务——范围没传到左栏"
        )
        assert "数学盒" in out["rail"], "Learning 的轨迹不见了"
        assert "仓储盒" not in out["rail"], (
            "Learning 域的操作轨迹里还混着 WMS 的盒子——范围没传到右栏"
        )

    def test_the_all_scope_still_shows_everything(self):
        """全部域 = 不筛——过滤不能把东西吃掉了"""
        out = _run("", _payload())
        assert "语文练习这条" in out["tasks"]
        assert "库区管理这条" in out["tasks"]
        assert "数学盒" in out["rail"]
        assert "仓储盒" in out["rail"]

    def test_the_wms_scope_drops_the_learning_task_and_trail(self):
        """换个域也要跟着换——不是只对 Learning 生效"""
        out = _run("?scope=WMS", _payload())
        assert "库区管理这条" in out["tasks"]
        assert "语文练习这条" not in out["tasks"]
        assert "仓储盒" in out["rail"]
        assert "数学盒" not in out["rail"]

    def test_an_empty_scope_says_so_instead_of_pretending(self):
        """空范围要说「没有」，不许静悄悄显示全平台

        静默显示全平台 = 读者以为这就是这个域的全部，
        而他其实在看别的东西。
        """
        payload = _payload()
        payload["fulfillment_scopes"] = {"WMS": ["wms_box"]}
        out = _run("?scope=Learning", payload)
        assert "语文练习这条" not in out["tasks"]
        assert "库区管理这条" not in out["tasks"]
        # 明说「这个域里没有」，而不是列别的东西
        assert "Learning" in out["tasks"]
        assert "Learning" in out["rail"]
