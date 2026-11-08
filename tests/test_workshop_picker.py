"""
tests.test_workshop_picker - 工作台「选一只看一只」的守卫

工作台第一版是**大通铺**：12 只盒子各占一大张卡（结论 + 判据 + 例外 +
指标 + 接线全铺开），十几屏，而且大部分是「已验 · 通过」的绿条——
读者要滚很久才看完，真正要看的（哪只盒子有问题）埋在中间。

现在改成**选一只看一只**：下拉选一只，只渲染那一只。

## 为什么要单独一个文件

页面是**纯客户端渲染**的。上面那些行为（排什么序、默认选谁、
URL 跟不跟着走）在 Python 里断言不了——Python 看不见 JS 跑出来的结果。

所以这里做一件仓里已经有的事：**在 node 里给页面搭一个最小 DOM 桩，
把页面那段 JS 真跑一遍**，然后断言它对 DOM 做了什么。

跟 `tests/test_web_kanban.py` 抠函数出来跑是同一个路子，区别是：
那个抠的是**纯函数**，这个得跑**整个页面**——因为「一次只渲染一只」
是整页的行为，不是某个函数的返回值。

## 桩要够真

桩只实现页面**真正用到**的那些 API（见 `_DOM_STUB` 的注释）。
少实现一个 = 页面在 node 里报错 = 守卫红了，但红的原因跟被测行为无关——
那样的守卫久了就没人信它了。所以每个用到的 API 都在那里列了出来。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from workshop.views import workshop_page


REPO = pathlib.Path(__file__).resolve().parent.parent


# ============================================================
# 1. 页面那段 JS 的抠取
# ============================================================


def _page_script() -> str:
    """抠出页面里那段**可执行**的 JS（不是那段 JSON）

    页面里有两个 `<script>`：一个装数据（`type="application/json"`），
    一个是干活的。抠错就等于没抠。
    """
    page = workshop_page()
    marker = '<script id="workshop-data" type="application/json">'
    assert marker in page, "工作台页面里没有注入数据的那段 script"
    head, _, tail = page.partition(marker)
    _, _, rest = tail.partition("</script>")
    start = rest.index("<script>") + len("<script>")
    end = rest.index("</script>", start)
    return rest[start:end]


# ============================================================
# 2. DOM 桩
# ============================================================
#
# 页面用到的 API，**一个不多一个不少**：
#
#   document.getElementById(id)        → 元素
#   document.createElement(tag)        → 元素
#   document.createTextNode(text)       → 文本节点
#   document.createDocumentFragment()   → 片段
#   history.replaceState / pushState    → 记下 URL 变化
#   location.search                     → 读当前 URL 的查询串
#   window.addEventListener             → 监听 popstate
#
# 元素要支持：textContent（可读可写）、className、appendChild、
# classList.add、value（<select> / <option> 用）、addEventListener。
#
# `classList.add` 之所以要真实现：页面用它给卡片加 `open` 类，
# 而 CSS 里 `.box.open .body { display: block; }` 决定卡片**展不展开**。
# 桩里不实现 = 卡片永远收着 = 页面看起来是坏的。


_DOM_STUB = r"""
// ---- 元素 ----
function El(tag) {
  this.tagName = tag;
  this.children = [];
  this._text = "";
  this._className = "";
  this._listeners = {};
  this.value = "";
}
Object.defineProperty(El.prototype, "textContent", {
  get: function () {
    if (this.children.length) {
      return this.children.map(function (c) { return c.textContent; }).join("");
    }
    return this._text;
  },
  set: function (v) {
    // 浏览器里设 textContent 会**清掉子节点**——页面靠这个清空目录
    this.children = [];
    this._text = (v === undefined || v === null) ? "" : String(v);
  },
});
Object.defineProperty(El.prototype, "className", {
  get: function () { return this._className; },
  set: function (v) { this._className = String(v); },
});
Object.defineProperty(El.prototype, "classList", {
  get: function () {
    var self = this;
    return {
      add: function (cls) {
        var parts = self._className.split(/\s+/).filter(Boolean);
        if (parts.indexOf(cls) === -1) parts.push(cls);
        self._className = parts.join(" ");
      },
      contains: function (cls) {
        return self._className.split(/\s+/).indexOf(cls) !== -1;
      },
    };
  },
});
El.prototype.appendChild = function (child) {
  this.children.push(child);
  return child;
};
El.prototype.addEventListener = function (type, fn) {
  (this._listeners[type] = this._listeners[type] || []).push(fn);
};
// 桩要能**触发**事件——不然验不了「用户换一只盒子」这条路径
El.prototype._fire = function (type, ev) {
  (this._listeners[type] || []).forEach(function (fn) { fn(ev); });
};

function TextNode(text) { this._text = String(text); }
Object.defineProperty(TextNode.prototype, "textContent", {
  get: function () { return this._text; },
  set: function (v) { this._text = String(v); },
});

function Fragment() { this.children = []; }
Fragment.prototype.appendChild = function (child) {
  this.children.push(child);
  return child;
};
Object.defineProperty(Fragment.prototype, "textContent", {
  get: function () {
    return this.children.map(function (c) { return c.textContent; }).join("");
  },
  set: function (v) { this._text = String(v); },
});

// ---- 文档 ----
var _byId = {};
function makeDoc() {
  return {
    getElementById: function (id) {
      if (!_byId[id]) _byId[id] = new El("div");
      return _byId[id];
    },
    createElement: function (tag) { return new El(tag); },
    createTextNode: function (text) { return new TextNode(text); },
    createDocumentFragment: function () { return new Fragment(); },
  };
}

// ---- history / location / window ----
function makeHistory(sink) {
  return {
    replaceState: function (a, b, url) { sink.push({ mode: "replace", url: url }); },
    pushState: function (a, b, url) { sink.push({ mode: "push", url: url }); },
  };
}
function makeWindow() {
  var listeners = {};
  return {
    addEventListener: function (type, fn) {
      (listeners[type] = listeners[type] || []).push(fn);
    },
    _fire: function (type, ev) {
      (listeners[type] || []).forEach(function (fn) { fn(ev); });
    },
  };
}
"""


# ============================================================
# 3. 在 node 里跑页面
# ============================================================


def _run_page(data: dict, search: str = "", change_to: str = "") -> dict:
    """在 node 里把页面跑一遍，返回它对 DOM 做了什么

    返回的 `state` 是「页面上看得见的那些东西」：
    - `options`  —— 下拉里有哪些选项（文字 + 值）
    - `selected` —— 下拉当前选中谁
    - `cards`    —— 正文里渲染了几张卡、每张卡是誰
    - `urlLog`   —— URL 变过几次、变成什么样
    - `note`     —— 选择器旁边写着什么

    给了 `change_to` 就**模拟用户换了一只盒子**（改下拉的值 + 触发 change），
    换完之后的状况放在 `after` 里。
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("这台机器上没有 node，跳过工作台交互守卫")

    script = _page_script()
    harness = f"""
{_DOM_STUB}

var __data = {json.dumps(data, ensure_ascii=False)};
var __urlLog = [];
var __win = makeWindow();

global.document = makeDoc();
global.window = __win;
global.history = makeHistory(__urlLog);
global.location = {{ search: {json.dumps(search)} }};
global.Node = El;

// 页面读数据的方式：document.getElementById("workshop-data").textContent
document.getElementById("workshop-data").textContent = JSON.stringify(__data);

{script}

// ---- 页面跑完了，看看它把 DOM 变成了什么样 ----
function snapshot() {{
  var picker = document.getElementById("box-picker");
  var catalog = document.getElementById("catalog");
  var note = document.getElementById("picker-note");

  var options = picker.children.map(function (o) {{
    return {{ text: o.textContent, value: o.value }};
  }});
  var cards = catalog.children.map(function (c) {{
    // 名字 span 埋在 head 里，不是卡的直接子节点——递归找
    var nameEl = null;
    (function walk(node) {{
      (node.children || []).forEach(function (x) {{
        if (!nameEl && x.className === "name") nameEl = x;
        walk(x);
      }});
    }})(c);
    return {{
      name: nameEl ? nameEl.textContent : "",
      open: c.classList.contains("open"),
      cls: c.className,
    }};
  }});

  return {{
    options: options,
    selected: picker.value,
    cards: cards,
    urlLog: __urlLog,
    note: note.textContent,
    counts: document.getElementById("counts").textContent,
  }};
}}

var __before = snapshot();

// ---- 模拟用户在下拉里换了一只盒子 ----
var __after = null;
if ({json.dumps(change_to)}) {{
  var picker2 = document.getElementById("box-picker");
  picker2.value = {json.dumps(change_to)};
  picker2._fire("change", {{ type: "change" }});
  __after = snapshot();
}}

console.log(JSON.stringify({{ before: __before, after: __after }}));
"""
    proc = subprocess.run(
        [node, "-e", harness], capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"node 跑工作台页面挂了（这是桩或页面自己的问题）：\n{proc.stderr}"
    )
    out = json.loads(proc.stdout)
    # 没模拟换盒子时，「之后」就是「之前」——调用方不用到处判空
    state = dict(out["before"])
    state["after"] = out["after"] or out["before"]
    return state


# ============================================================
# 4. 一份能验「有问题」的假数据
# ============================================================
#
# 真数据现在 12 只全过——那样「有问题排前面」「默认选中有问题的那只」
# 这两条根本验不出来（全都一样）。
#
# 所以这里造一份**假的**：三种状态各一只，外加两只都过的。
# 假数据只用来验**排序和默认选择**这两件事，别的（真盒子的内容）
# 另有守卫管。


def _fake_box(box_id: str, name: str, verdict: str) -> dict:
    return {
        "box_id": box_id,
        "version": "0.0.1",
        "description": "",
        "display_name": name,
        "value_stream": "假流",
        "role": "假角色",
        "fulfillment_scope": "",
        "maturity": "implemented",
        "artifact_view": "",
        "handoffs": [],
        "feedback": [],
        "archive": [],
        "tasks": [],
        "beelines": [],
        "acceptance": [],
        "exceptions": [],
        "metrics": [],
        "health": {
            "box_id": box_id,
            "verdict": verdict,
            "counts": {"pass": 1, "fail": 0, "unknown": 0},
            "checks": [
                {
                    "id": "fake_check",
                    "title": "假的检查项",
                    "why": "假的理由",
                    "status": "pass",
                    "detail": "",
                    "evidence": None,
                }
            ],
        },
    }


def _fake_catalog() -> dict:
    boxes = [
        _fake_box("box_ok_1", "过的一号", "pass"),
        _fake_box("box_bad", "有问题的", "fail"),
        _fake_box("box_unknown", "没验到的", "unknown"),
        _fake_box("box_ok_2", "过的二号", "pass"),
    ]
    return {
        "title": "BeeBox Workshop",
        "subtitle": "盒子体检台 · 只读",
        "counts": {
            "boxes": len(boxes),
            "failed": 1,
            "unknown": 1,
            "passed": 2,
        },
        "boxes": boxes,
    }


# ============================================================
# 5. 守卫
# ============================================================


class TestThePickerPutsTheProblemFirst:
    """体检台是**诊断工具**——最该先看见的是坏的那只

    「有问题排前面」这条不是为了好看：读者来这个页面就是要找问题，
    让他自己翻 12 个选项找，是把工具的活推给用户。
    """

    def test_the_boxes_with_problems_come_first(self):
        state = _run_page(_fake_catalog())
        order = [o["value"] for o in state["options"]]

        assert order.index("box_bad") < order.index("box_unknown"), (
            f"有问题的（fail）该排在没验到的（unknown）前面——"
            f"实际顺序 {order}"
        )
        assert order.index("box_unknown") < order.index("box_ok_1"), (
            f"没验到的该排在全过的前面——实际顺序 {order}"
        )
        assert order[0] == "box_bad", (
            f"下拉第一个该是**有问题**的那只，实际是 {order[0]!r}——"
            f"读者进来第一眼看到的就是它"
        )

    def test_the_order_among_equal_verdicts_is_stable(self):
        """同一种结论的盒子之间，次序不能因为这次排序而变来变去

        排序不稳定 = 读者今天看到 A 在前、明天看到 B 在前，
        他没法把「上次那条」跟「这条」对上。
        """
        state = _run_page(_fake_catalog())
        order = [o["value"] for o in state["options"]]

        assert order.index("box_ok_1") < order.index("box_ok_2"), (
            f"两只都过的盒子之间该保持原来的先后——实际顺序 {order}"
        )


class TestTheInitialChoice:
    """页面打开时得**已经选好了**一只——而且选的是最该看的那只

    「打开就空着，让用户自己去下拉里挑」是把找问题的活推给读者。
    """

    def test_it_opens_on_the_box_with_a_problem(self):
        state = _run_page(_fake_catalog())
        assert state["selected"] == "box_bad", (
            f"打开时该默认选中**有问题**的那只，实际选中 {state['selected']!r}"
        )

    def test_a_box_named_in_the_url_wins(self):
        """URL 指名的那只，优先于「默认选最坏的那只」

        分享出去的链接得打开就是那只——不然「我发给你这只盒子有问题」
        这个动作就白做了。
        """
        state = _run_page(_fake_catalog(), search="?box=box_ok_2")
        assert state["selected"] == "box_ok_2", (
            f"URL 指名的盒子该优先，实际选中 {state['selected']!r}"
        )

    def test_an_unknown_box_in_the_url_falls_back_to_the_worst(self):
        """URL 里写了一只不存在的盒子 → 退回默认，别白屏

        链接可能过期（盒子被删了）。这时该退回默认选择，
        而不是渲染出一张空卡。
        """
        state = _run_page(_fake_catalog(), search="?box=box_does_not_exist")
        assert state["selected"] == "box_bad", (
            f"URL 里的盒子不存在时该退回默认（有问题的那只），"
            f"实际 {state['selected']!r}"
        )
        assert len(state["cards"]) == 1, "该照样渲染一只卡片，别白屏"


class TestOneBoxAtATime:
    """**一次只渲染一只**——这是这一版改版的全部要点

    以前 12 只盒子各占一大张卡，十几屏。
    """

    def test_only_one_card_is_rendered(self):
        state = _run_page(_fake_catalog())
        assert len(state["cards"]) == 1, (
            f"该只渲染一只盒子，实际渲染了 {len(state['cards'])} 只——"
            f"大通铺回来了"
        )

    def test_the_rendered_card_is_the_selected_one(self):
        state = _run_page(_fake_catalog())
        assert state["cards"][0]["name"] == "有问题的", (
            f"渲染的该是**选中**的那只，实际渲染的是 "
            f"{state['cards'][0]['name']!r}，选中的却是 {state['selected']!r}"
        )

    def test_the_card_is_expanded(self):
        """选中的那只得**展开**着——收着的卡片等于没显示

        页面给卡片加 `open` 类，CSS 里 `.box.open .body` 才显示正文。
        """
        state = _run_page(_fake_catalog())
        assert state["cards"][0]["open"], (
            "选中的卡片没展开——正文是收着的，等于什么都没显示"
        )

    def test_every_box_is_still_reachable(self):
        """一只盒子不许因为「只显示一只」而消失

        收起来 ≠ 拿掉：下拉里还得有全部的盒子。
        """
        state = _run_page(_fake_catalog())
        values = [o["value"] for o in state["options"]]
        assert values == ["box_bad", "box_unknown", "box_ok_1", "box_ok_2"], (
            f"下拉里该有全部四只盒子（按有问题排前面），实际 {values}"
        )


class TestTheUrlFollowsTheChoice:
    """URL 得**跟着选中的盒子走**

    不然没法把「这只盒子有问题」发给同事——
    同事点开链接看到的是另一只，那这个动作就白做了。
    """

    def test_the_initial_render_writes_the_box_into_the_url(self):
        state = _run_page(_fake_catalog())
        assert state["urlLog"], "页面加载后没写过一次 URL"
        first = state["urlLog"][0]
        assert first["mode"] == "replace", (
            f"初次渲染该用 replaceState（不堆历史），实际 {first['mode']}"
        )
        assert first["url"] == "?box=box_bad", (
            f"URL 该指到当前这只盒子，实际 {first['url']!r}"
        )

    def test_the_url_uses_the_box_id_not_the_name(self):
        """URL 里放 **box_id**，不是显示名

        显示名会改（改了名字，旧链接就指不到东西了）；
        box_id 才是那只盒子的稳定身份。
        """
        state = _run_page(_fake_catalog())
        url = state["urlLog"][0]["url"]
        assert "有问题的" not in url, (
            f"URL 里不该出现显示名（名字会改），实际 {url!r}"
        )
        assert url.startswith("?box="), f"URL 参数该是 ?box=，实际 {url!r}"

    def test_switching_the_box_writes_the_new_box_into_the_url(self):
        """**用户换了一只盒子** → URL 跟着换成那一只

        不然「我发给你这只盒子有问题」这个动作就白做了：
        同事点开链接看到的是另一只。
        """
        state = _run_page(_fake_catalog(), change_to="box_ok_1")
        after = state["after"]

        assert after["selected"] == "box_ok_1", (
            f"换了下拉就该选中换的那只，实际 {after['selected']!r}"
        )
        assert len(after["cards"]) == 1, (
            f"换完还是只渲染一只，实际 {len(after['cards'])} 只"
        )
        assert after["cards"][0]["name"] == "过的一号", (
            f"换完该渲染换的那只，实际渲染的是 {after['cards'][0]['name']!r}"
        )

        pushes = [u for u in after["urlLog"] if u["mode"] == "push"]
        assert pushes, (
            "用户换了盒子，URL 该被 pushState 改掉——"
            f"实际 URL 变化记录 {after['urlLog']}"
        )
        assert pushes[-1]["url"] == "?box=box_ok_1", (
            f"URL 该指到换过去的那只，实际 {pushes[-1]['url']!r}"
        )

    def test_switching_the_box_pushes_instead_of_replacing(self):
        """用户自己换的盒子得用 **pushState**——后退能回到上一只

        用 replaceState 的话，后退一次就直接离开这个页面了，
        读者没法「退回去看看刚才那只」。
        """
        state = _run_page(_fake_catalog(), change_to="box_ok_2")
        after = state["after"]

        pushes = [u for u in after["urlLog"] if u["mode"] == "push"]
        assert pushes, (
            f"用户换了盒子该 pushState（能后退），实际 URL 变化记录 {after['urlLog']}"
        )


class TestTheOptionsSayTheState:
    """选项文字里得**写着状态**，不只靠颜色

    纯下拉有个洞：「哪只盒子有问题」被折叠了——
    展开 12 个选项才看得见。所以每个选项自己得说清自己什么状态。
    """

    def test_each_option_says_its_state(self):
        state = _run_page(_fake_catalog())
        by_value = {o["value"]: o["text"] for o in state["options"]}

        assert "体检有问题" in by_value["box_bad"], (
            f"有问题的那个选项没写自己有问题：{by_value['box_bad']!r}"
        )
        assert "部分没验到" in by_value["box_unknown"], (
            f"没验到的那个选项没写自己没验到：{by_value['box_unknown']!r}"
        )
        assert "体检通过" in by_value["box_ok_1"], (
            f"通过的那个选项没写自己通过：{by_value['box_ok_1']!r}"
        )

    def test_each_option_says_which_box_it_is(self):
        state = _run_page(_fake_catalog())
        by_value = {o["value"]: o["text"] for o in state["options"]}
        for value, text in by_value.items():
            assert text, f"选项 {value} 的文字是空的"

    def test_the_problem_is_visible_without_expanding_the_dropdown(self):
        """选择器旁边得**一直写着**有问题的盒子是谁

        这是这一版的核心：纯下拉会把「哪只有问题」藏起来。
        """
        state = _run_page(_fake_catalog())
        assert "有问题" in state["note"], (
            f"选择器旁边该写着有问题的盒子是谁，实际 {state['note']!r}"
        )
        assert "有问题的" in state["note"], (
            f"「有问题」那句里该点名是哪只，实际 {state['note']!r}"
        )


class TestTheRealCatalogStillWorks:
    """真目录也得跑得起来——不能只对假数据成立

    桩能跑假数据，不代表跑得了真数据（真数据字段更多）。
    """

    def test_the_real_page_renders_with_the_real_catalog(self):
        from workshop.views import workshop_payload

        state = _run_page(workshop_payload())
        assert len(state["cards"]) == 1, "真目录下该只渲染一只卡片"
        assert state["options"], "真目录下拉该有选项"

    def test_the_real_page_lists_every_box_in_the_dropdown(self):
        from kanban.trigger import BOX_REGISTRY
        from workshop.views import workshop_payload

        state = _run_page(workshop_payload())
        values = {o["value"] for o in state["options"]}
        assert values == set(BOX_REGISTRY), (
            f"下拉里该有目录里的全部盒子，少了 {set(BOX_REGISTRY) - values}"
        )
