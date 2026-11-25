"""总览页「任务描述输入框」与中栏三段式的守卫

两件事在这里被钉住：

1. **中栏 = 固定头 + 可滚画面 + 固定输入框**，且**整页不滚**
   （左中右三栏各自内部滚）。
2. **输入框按能力找投给谁**——读盒子自己声明的 `accepts_free_text`，
   **不按 box_id 硬编码**；当前范围里没有吃自然语言的盒子时，
   框必须是**禁用的**，并写明原因。

**为什么这两条值得单独守**：它们都是「看起来对、其实没做」的病——
布局塌了截图能看出来，但「按能力找盒子」塌了**看不出**：
写死了 `wms_requirement_box` 的页面在 WMS 范围下照样好用，
换个范围就默默投错盒子。所以判据落在**真源码**上，不落在截图上。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kanban.trigger import list_task_entries
from kanban.web import HTML_PAGE
from tests.test_web_kanban import _extract_js_function, _strip_js_comments

ROOT = Path(__file__).resolve().parent.parent

# 一份最小的看板数据：一只吃自然语言的盒 + 一只不吃的。
# 字段名照 /api/data 的真形状（box_stats[].task_entries[].accepts_free_text）。
_SCOPE_ASSIGNMENTS = {
    "WMS": ["wms_requirement_box", "wms_design_box"],
    "Learning": ["learning_math_box"],
}

_BOX_STATS = [
    {
        "box_id": "wms_requirement_box",
        "meta": {"display_name": "WMS 需求盒"},
        "task_entries": [
            {
                "task_type": "capture_business_requirement",
                "accepts_free_text": True,
            }
        ],
    },
    {
        "box_id": "wms_design_box",
        "meta": {"display_name": "WMS 设计盒"},
        "task_entries": [
            {"task_type": "handle_design_request", "accepts_free_text": False}
        ],
    },
    {
        "box_id": "learning_math_box",
        "meta": {"display_name": "数学盒"},
        "task_entries": [{"task_type": "run_math", "accepts_free_text": False}],
    },
]


def _run_composer_targets(scope_filter: str, box_stats: list | None = None) -> list:
    """在 node 里真跑 composerTargets，返回它找到的投递目标。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("没有 node，跳过输入框守卫")

    src = "\n".join(
        [
            "const ALL_SCOPES = '__all__';",
            "let _scopeFilter = " + json.dumps(scope_filter) + ";",
            _extract_js_function("scopeBoxIds"),
            _extract_js_function("composerTargets"),
            "const data = " + json.dumps(
                {
                    "fulfillment_scopes": _SCOPE_ASSIGNMENTS,
                    "box_stats": box_stats if box_stats is not None else _BOX_STATS,
                },
                ensure_ascii=False,
            )
            + ";",
            "console.log(JSON.stringify(composerTargets(data)));",
        ]
    )
    proc = subprocess.run(
        [node, "-e", src], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"node 跑 composerTargets 失败：\n{proc.stderr}"
    return json.loads(proc.stdout.strip())


# --------------------------------------------------------------------------
# 1. 中栏三段式 + 整页不滚
# --------------------------------------------------------------------------
class TestTheMiddleColumnIsThreeSegments:
    def test_the_page_locks_and_the_three_columns_scroll_on_their_own(self):
        """总览页整页不滚，三栏各自内部滚。

        以前总览页让**整页**滚——读者想盯着右栏某只盒子的 op 轨迹看，
        一滚就没了。现在锁高成三栏各自滚。
        """
        code = _strip_js_comments(HTML_PAGE)
        assert "@media (min-width: 761px)" in code, (
            "总览页的锁高规则不见了——整页又滚起来了"
        )
        assert "body:not(.single-box) {" in code, "锁高只该作用在总览页（单盒页另有一套）"
        assert "overflow: hidden;" in code, "锁高要配 overflow:hidden 才真的不滚"
        assert ".content-scroll" in code, "中栏没有可滚段"
        assert ".sidebar" in code and ".rail" in code, "两侧栏规则不见了"

    def test_the_middle_column_is_head_scroll_and_composer(self):
        """中栏 = 固定头（筛选条）+ 可滚画面 + 固定输入框。"""
        shell = _extract_js_function("ensureContentShell")
        assert 'id="content-head"' in shell, "中栏缺固定头（筛选条住这儿）"
        assert 'id="content-scroll"' in shell, "中栏缺可滚段"
        assert 'id="composer"' in shell, "中栏缺固定输入框"

        # 筛选条必须进**固定头**，不能进可滚段——
        # 它控制的正是下面那块画面，画面滚下去时它也得看得见。
        render = HTML_PAGE[HTML_PAGE.index("function render(data)") :]
        assert 'headEl.innerHTML = renderFilters(data)' in render, (
            "筛选条没写进固定头"
        )

    def test_the_scroll_position_survives_the_two_second_refresh(self):
        """每 2 秒重画一次，不放回滚动位置就会把读者弹回顶部。

        轨迹面板早就有这个毛病（用「结构没变就不重建」治）；
        中栏的画面每轮都在变，那个办法用不上——只能记下位置再放回去。
        """
        code = _strip_js_comments(HTML_PAGE)
        assert "const prevScrollTop = scrollEl.scrollTop" in code, (
            "重画前没记下滚动位置"
        )
        assert "scrollEl.scrollTop = prevScrollTop" in code, (
            "重画后没把滚动位置放回去——读者正翻的那张图会被拉回顶部"
        )

    def test_the_filter_bar_stays_out_of_the_scrolling_part(self):
        """可滚段里不该混进筛选条。"""
        render = HTML_PAGE[HTML_PAGE.index("function render(data)") :]
        assert "scrollEl.innerHTML = html" in render, "画面没写进可滚段"
        assert "html += renderFilters(data)" not in render, (
            "筛选条又被拼进画面里了——它该在固定头里"
        )


# --------------------------------------------------------------------------
# 2. 输入框按能力找盒子
# --------------------------------------------------------------------------
class TestTheComposerFindsItsTargetByCapability:
    def test_task_entries_carry_the_free_text_fact(self):
        """看板数据里要带「这个 task 吃不吃自然语言」。

        这是盒子自己声明的事实（`accepts_free_text`），
        不推、不猜——`payload_kind` 是「投料框长什么样」，
        那个字段是「能不能直接喂人话」，两件事。
        """
        entry = list_task_entries("wms_requirement_box")[0]
        assert entry["accepts_free_text"] is True, (
            "需求盒声明吃自然语言，task_entries 却没报这件事"
        )
        design = list_task_entries("wms_design_box")[0]
        assert design["accepts_free_text"] is False, (
            "设计盒不吃自然语言，报成 true 会让调用方把需求描述投进去"
        )

    def test_it_finds_the_box_that_takes_text_in_the_current_scope(self):
        """WMS 范围下 → 投给需求盒。"""
        targets = _run_composer_targets("WMS")
        assert [t["box_id"] for t in targets] == ["wms_requirement_box"], (
            f"找错了投递目标：{targets}"
        )
        assert targets[0]["task_type"] == "capture_business_requirement"

    def test_it_never_hardcodes_a_box_id(self):
        """按能力找，不按 id 硬编码。

        写死 `wms_requirement_box` 的页面在 WMS 范围下照样好用——
        换个范围就默默投错盒子，而页面上看不出任何异常。
        """
        source = _strip_js_comments(_extract_js_function("composerTargets"))
        for box_id in ("wms_requirement_box", "wms_design_box", "learning_math_box"):
            assert box_id not in source, f"输入框里写死了 {box_id}——那不是按能力找"

    def test_it_finds_nothing_when_the_scope_has_no_text_taking_box(self):
        """Learning 范围里没有吃自然语言的盒子 → 找不到目标。

        找不到时框必须是**禁用的**（前端据此灰掉并写明原因），
        不能悄悄投给别的盒子的 task。
        """
        targets = _run_composer_targets("Learning")
        assert targets == [], f"不该有目标，却找到了：{targets}"

    def test_the_scope_decides_where_it_goes(self):
        """范围是过滤条件：换了范围，目标跟着换。"""
        wms = _run_composer_targets("WMS")
        learning = _run_composer_targets("Learning")
        assert wms and not learning, "范围没起作用"

    def test_the_submit_goes_through_the_text_intake_path(self):
        """投递走 /api/structure（自然语言投料口），不自己拼 box_id。"""
        source = _strip_js_comments(_extract_js_function("composerSubmit"))
        assert "/api/structure" in source, "投递没走自然语言投料口"
        assert "box_id: target.box_id" in source, "投递目标没带上"
        assert "task_type: target.task_type" in source, "投递目标没带上任务类型"
