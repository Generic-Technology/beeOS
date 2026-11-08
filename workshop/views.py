"""
workshop.views - BeeBox Workshop 的只读页面

**工作台是做 BeeBox 的地方，看板是盯故障的面。**
看板那边不放能改东西的入口，所以工作台单开一个目录。

第一版只做一件事：**体检台**——把一只盒子声明得对不对，摊开给人看。
只读。现在不提供「改声明」「发版」「装别人的盒子」。

## 为什么页面不自己判

判定全在 `runtime/box_health.py`。页面只负责把结论**摊开**。

页面自己判一遍 = 两套口径 = 守卫说绿、页面说红，而且没人知道该信谁。
守卫（`tests/test_box_standard.py`）和工作台读的是同一份判定。

## 为什么数据是注入的，页面源码里一个盒子名都没有

前端一个 box_id 字面量都不许出现（守卫盯着，`tests/test_workshop.py`）。
页面里写死一只盒子，就等于页面抄了一份目录——那份目录必然漂。

所以这里注入 JSON，前端照着渲染。加盒子不用碰页面。

## 为什么不用 f-string 拼 HTML

看板那边的 `HTML_PAGE` 是 f-string，JS 的花括号得双写 `{{ }}`——
一处写错就是整页白屏，而且服务端照样返回 200。
这里用**占位符替换**：页面源码里没有花括号转义的活，
JSON 也不会被 f-string 解释（`{` 在数据里不是转义问题，是安全问题——
f-string 会把数据里的花括号当格式符）。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fulfillment_scopes import (
    box_wiring,
    fulfillment_scope_display_name,
    fulfillment_scopes_of,
)
from kanban.catalog import catalog_inspections
from kanban.trigger import box_metrics_readings
from runtime.box_health import BoxHealth, inspect_box


#: 页面里 JSON 数据的占位符。**不可能撞上正文**（正文里没有这个串）。
_DATA_PLACEHOLDER = "__WORKSHOP_DATA__"


def _box_payload(inspection, records: list[dict[str, Any]]) -> dict[str, Any]:
    """一只盒子在工作台页面上的全部内容"""
    d = inspection.definition
    # **接线从协作边界声明读**（`fulfillment_scopes/<边界>/`）——不再从盒子自己的字段读。
    # 那份字段已经退休：照抄它会漏掉真实依赖，而两份事实并存必然漂。
    wiring = box_wiring(inspection.box_id)
    box_fulfillment_scopes = fulfillment_scopes_of(inspection.box_id)
    return {
        "box_id": inspection.box_id,
        "version": d.version,
        "description": d.description,
        "display_name": d.display_name or inspection.box_id,
        "value_stream": d.value_stream,
        "role": d.role,
        "fulfillment_scope": fulfillment_scope_display_name(
            box_fulfillment_scopes[0] if box_fulfillment_scopes else ""
        ),
        "maturity": d.result.maturity,
        "artifact_view": d.result.view,
        "handoffs": list(wiring["handoffs"]),
        "feedback": list(wiring["feedback"]),
        "archive": list(wiring["archive"]),
        "tasks": [
            {
                "type": t.type,
                "schema": t.task_schema,
                "trigger": t.trigger or "",
                "beeline_id": t.beeline_id,
                "beeline_version": t.beeline_version,
            }
            for t in d.task
        ],
        "beelines": [
            {
                "id": bid,
                "version": b.version,
                "description": b.description,
                "ops": [
                    {"id": op.op_id, "type": op.type, "leaves_artifact": op.leaves_artifact}
                    for op in b.operations
                ],
            }
            for bid, b in inspection.beelines.items()
        ],
        "acceptance": [
            {"metric": r.metric, "op": r.op, "expected": r.value}
            for r in d.result.acceptance
        ],
        "exceptions": [
            {"condition": e.condition, "action": e.action, "reason": e.reason}
            for e in d.result.exceptions
        ],
        "metrics": box_metrics_readings(inspection.box_id, records),
        "health": inspect_box(inspection).to_dict(),
    }


def workshop_payload(store: Any = None) -> dict[str, Any]:
    """整个目录的体检数据（注入页面用）"""
    inspections = catalog_inspections()
    boxes = []
    for inspection in inspections:
        records = (
            store.list_recent(limit=20, box_id=inspection.box_id)
            if store is not None else []
        )
        boxes.append(_box_payload(inspection, records))

    healths = [inspect_box(i) for i in inspections]
    counts = {
        "boxes": len(boxes),
        "failed": sum(1 for h in healths if h.verdict == "fail"),
        "unknown": sum(1 for h in healths if h.verdict == "unknown"),
        "passed": sum(1 for h in healths if h.verdict == "pass"),
    }
    return {
        "title": "BeeBox Workshop",
        "subtitle": "盒子体检台 · 只读",
        "counts": counts,
        "boxes": boxes,
    }


def workshop_page(store: Any = None) -> str:
    """渲染工作台页面"""
    payload = workshop_payload(store)
    return _PAGE.replace(
        _DATA_PLACEHOLDER,
        json.dumps(payload, ensure_ascii=False),
    )


def health_summary(box_id: str) -> Optional[dict[str, Any]]:
    """单只盒子的体检结论（给 API 用）"""
    for inspection in catalog_inspections():
        if inspection.box_id == box_id:
            health: BoxHealth = inspect_box(inspection)
            return health.to_dict()
    return None


#: 页面源码。**raw string**——里面有 JS 的正则（`\*\*`），
#: 非 raw 字符串会把 `\*` 当成 Python 的转义序列报警告
#: （将来的 Python 会直接报错）。raw 才是这里正确的语义。
_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BeeBox Workshop</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #171a21;
    --panel-2: #1e222b;
    --line: #2a2f3a;
    --fg: #e6e8ec;
    --dim: #9aa3b2;
    --ok: #3fb950;
    --bad: #f85149;
    --unknown: #d29922;
    --accent: #58a6ff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.6 -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
  }
  header {
    padding: 22px 28px 16px; border-bottom: 1px solid var(--line);
    position: sticky; top: 0; background: var(--bg); z-index: 5;
  }
  h1 { margin: 0; font-size: 20px; letter-spacing: .5px; }
  .sub { color: var(--dim); font-size: 13px; margin-top: 4px; }
  .counts { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
  .pill {
    border: 1px solid var(--line); border-radius: 999px; padding: 2px 12px;
    font-size: 12px; color: var(--dim); background: var(--panel);
  }
  .pill b { color: var(--fg); font-weight: 600; }
  main { padding: 20px 28px 60px; max-width: 1180px; margin: 0 auto; }
  /* ===== 选哪只盒子 =====
     以前 12 只盒子各占一大张卡（结论 + 判据 + 例外 + 指标 + 接线全铺开），
     十几屏，而且大部分是「已验 · 通过」的绿条。
     现在**选一只看一只**：选择器 + 一张卡。 */
  .picker {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    margin-bottom: 18px;
  }
  .picker-label { color: var(--dim); font-size: 13px; }
  .picker-select {
    background: var(--panel); color: var(--fg); border: 1px solid var(--line);
    border-radius: 8px; padding: 6px 10px; font: inherit; min-width: 280px;
  }
  .picker-select:focus { outline: 1px solid var(--accent); }
  .picker-note { color: var(--dim); font-size: 12px; }
  .box {
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
    margin-bottom: 18px; overflow: hidden;
  }
  .box > .head {
    padding: 14px 18px; display: flex; align-items: center; gap: 12px;
    /* 窄屏时标签**整块**换到下一行**，不要把一个标签里的字从中间折断
       （"协作边界 · 学习成长" 折成两行，读起来像两个字是两个词）。 */
    flex-wrap: wrap;
    user-select: none; background: var(--panel-2);
  }
  .box > .head:hover { background: #232833; }
  .name { font-weight: 600; font-size: 15px; white-space: nowrap; }
  .id { color: var(--dim); font-size: 12px; font-family: ui-monospace, Menlo, monospace;
        white-space: nowrap; }
  .spacer { flex: 1; }
  .tag {
    font-size: 12px; border: 1px solid var(--line); border-radius: 6px;
    padding: 1px 8px; color: var(--dim);
    /* 标签是一块，不该从中间折断 */
    white-space: nowrap;
  }
  .verdict { font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 6px; }
  .verdict.pass { color: var(--ok); border: 1px solid var(--ok); }
  .verdict.fail { color: var(--bad); border: 1px solid var(--bad); }
  .verdict.unknown { color: var(--unknown); border: 1px solid var(--unknown); }
  .body { padding: 4px 18px 18px; display: none; }
  .box.open .body { display: block; }
  .desc { color: var(--dim); margin: 12px 0 6px; }
  h3 {
    font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
    color: var(--dim); margin: 20px 0 8px; font-weight: 600;
  }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td {
    text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line);
    vertical-align: top;
  }
  th { color: var(--dim); font-weight: 500; font-size: 12px; }
  code {
    font-family: ui-monospace, Menlo, monospace; font-size: 12px;
    background: #0c0e13; padding: 1px 5px; border-radius: 4px;
  }
  .check { border-bottom: 1px solid var(--line); padding: 10px 0; }
  .check:last-child { border-bottom: none; }
  .check-head { display: flex; gap: 10px; align-items: baseline; }
  .check-title { font-weight: 600; }
  .check-why { color: var(--dim); font-size: 12px; margin-top: 3px; }
  .st { font-size: 11px; padding: 1px 8px; border-radius: 5px; white-space: nowrap; }
  .st.pass { color: var(--ok); border: 1px solid var(--ok); }
  .st.fail { color: var(--bad); border: 1px solid var(--bad); }
  .st.unknown { color: var(--unknown); border: 1px solid var(--unknown); }
  .detail {
    margin-top: 6px; font-size: 12px; color: #ffb4ad; white-space: pre-wrap;
    font-family: ui-monospace, Menlo, monospace;
  }
  .empty { color: var(--dim); font-size: 13px; }
  .foot { color: var(--dim); font-size: 12px; margin-top: 30px; }
</style>
</head>
<body>
<header>
  <h1>BeeBox Workshop</h1>
  <div class="sub">盒子体检台 · 只读——把每只盒子声明得对不对摊开给人看。判定在 runtime/box_health.py，守卫和这个页面读同一份。</div>
  <div class="counts" id="counts"></div>
</header>
<main>
  <div class="picker">
    <label class="picker-label" for="box-picker">看哪只盒子</label>
    <select class="picker-select" id="box-picker"></select>
    <span class="picker-note" id="picker-note"></span>
  </div>
  <div id="catalog"></div>
</main>
<script id="workshop-data" type="application/json">__WORKSHOP_DATA__</script>
<script>
(function () {
  var raw = document.getElementById("workshop-data").textContent;
  var data = JSON.parse(raw);

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  // **盒子的说明是用 markdown 写的**（`**粗体**` / `` `代码` ``）。
  // 直接塞进去会把星号原样显示出来。
  //
  // 这里自己渲染，不用 innerHTML：盒子的说明是**数据**，
  // 拿数据拼 HTML 就是开门放注入。逐段建节点，只认两种标记。
  function mdInline(text) {
    var frag = document.createDocumentFragment();
    var re = /\*\*([^*]+)\*\*|`([^`]+)`/g;
    var last = 0, m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      if (m[1] !== undefined) frag.appendChild(el("strong", null, m[1]));
      else frag.appendChild(el("code", null, m[2]));
      last = re.lastIndex;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    return frag;
  }

  function mdCell(text) {
    var td = el("td");
    td.appendChild(mdInline(text));
    return td;
  }

  function mdDiv(cls, text) {
    var d = el("div", cls);
    d.appendChild(mdInline(text));
    return d;
  }

  var STATUS_TEXT = {
    pass: "已验 · 通过",
    fail: "已验 · 有问题",
    unknown: "没验到",
  };
  var VERDICT_TEXT = {
    pass: "体检通过",
    fail: "体检有问题",
    unknown: "部分没验到",
  };

  var counts = document.getElementById("counts");
  [
    ["盒子", data.counts.boxes],
    ["有问题", data.counts.failed],
    ["没验到", data.counts.unknown],
    ["全过", data.counts.passed],
  ].forEach(function (pair) {
    var p = el("span", "pill");
    p.appendChild(document.createTextNode(pair[0] + " "));
    p.appendChild(el("b", null, pair[1]));
    counts.appendChild(p);
  });

  // ===== 选哪只盒子 =====
  //
  // 以前 12 只盒子各占一大张卡（体检结论 + 判据 + 例外 + 指标 + 接线全铺开），
  // 十几屏，而且**大部分是「已验 · 通过」的绿条**——
  // 读者要滚很久才能看完，而真正要看的（哪只盒子有问题）埋在中间。
  //
  // 现在**选一只看一只**。但纯下拉有个洞：**「哪只盒子有问题」被折叠了**——
  // 展开 12 个选项才看得见。所以：
  // 1. 选项文字**带状态**（`体检有问题` / `部分没验到` / `体检通过`），
  //    不靠颜色单独表达；
  // 2. 选项**有问题排前面**（体检台是诊断工具，最该先看见的是坏的那只），
  //    默认就选中它；
  // 3. 选择器旁边**永远写着有问题的盒子是谁**——一眼可见，不靠展开。
  var VERDICT_ORDER = { fail: 0, unknown: 1, pass: 2 };

  function orderForPicker(boxes) {
    // **稳定排序**：同一种结论的盒子保持原来的先后
    //（Array.prototype.sort 自 ES2019 起是稳定的），
    // 所以「有问题的那几只」之间的次序不会因为这次排序而变来变去。
    //
    // **用 `??` 不用 `||`**：`fail` 的序号是 **0**，而 `0 || 9` = 9——
    // falsy 的 0 会被当成「这个键不存在」，
    // 于是**有问题的盒子被排到了最后**（正好和这个函数的目的相反）。
    return boxes.slice().sort(function (a, b) {
      return (VERDICT_ORDER[a.health.verdict] ?? 9)
        - (VERDICT_ORDER[b.health.verdict] ?? 9);
    });
  }

  function pickInitialBox(boxes, requestedId) {
    // URL 指名的优先——分享出去的链接得打开就是那只。
    if (requestedId) {
      for (var i = 0; i < boxes.length; i++) {
        if (boxes[i].box_id === requestedId) return boxes[i];
      }
    }
    // 否则选最坏的那只：有问题 → 没验到 → 全过。
    var ordered = orderForPicker(boxes);
    return ordered.length ? ordered[0] : null;
  }

  function optionText(box) {
    // 状态写进**文字**，不只靠颜色——
    // 「没验到」和「通过」必须长得不一样，这是三态在选择器上的落点。
    return box.display_name + " · " + VERDICT_TEXT[box.health.verdict];
  }

  function boxIdFromUrl() {
    var m = /[?&]box=([^&]*)/.exec(location.search);
    return m ? decodeURIComponent(m[1]) : "";
  }

  var catalog = document.getElementById("catalog");
  var picker = document.getElementById("box-picker");
  var note = document.getElementById("picker-note");

  orderForPicker(data.boxes).forEach(function (box) {
    var opt = el("option", null, optionText(box));
    opt.value = box.box_id;
    picker.appendChild(opt);
  });

  // 有问题的盒子是谁——**一眼可见**，不用展开下拉。
  var sick = data.boxes.filter(function (b) { return b.health.verdict !== "pass"; });
  if (sick.length) {
    note.appendChild(document.createTextNode("有问题：" + sick.map(function (b) {
      return b.display_name + "（" + VERDICT_TEXT[b.health.verdict] + "）";
    }).join("、")));
  } else {
    note.appendChild(document.createTextNode("全部盒子体检通过。"));
  }

  function table(headers, rows) {
    var t = el("table");
    var thead = el("thead");
    var tr = el("tr");
    headers.forEach(function (h) { tr.appendChild(el("th", null, h)); });
    thead.appendChild(tr);
    t.appendChild(thead);
    var tbody = el("tbody");
    rows.forEach(function (cells) {
      var row = el("tr");
      cells.forEach(function (c) {
        if (c instanceof Node) { row.appendChild(el("td")).appendChild(c); }
        else { row.appendChild(mdCell(String(c))); }
      });
      tbody.appendChild(row);
    });
    t.appendChild(tbody);
    return t;
  }

  function section(title, node) {
    var wrap = document.createDocumentFragment();
    wrap.appendChild(el("h3", null, title));
    wrap.appendChild(node);
    return wrap;
  }

  // 一只盒子的**完整体检**：结论 + 判据 + 例外 + 指标 + 接线，一段都不少。
  function renderBox(box) {
    var card = el("div", "box");
    var head = el("div", "head");
    head.appendChild(el("span", "name", box.display_name));
    head.appendChild(el("span", "id", box.box_id));
    head.appendChild(el("span", "spacer"));
    // 归属标签**带前缀**且去重：两个维度可能摆出同一个词
    // （协作边界和价值流撞名时），两个一样的裸标签看着像重复。
    //
    // 协作边界摆的是**名字**（学习成长 / WMS 仓储），不是 id（Learning / WMS）——
    // id 摆出来读者分不出它是协作边界还是价值流
    // （学习协作边界那 4 只盒子的 value_stream 也正好叫 Learning）。
    // 看板那边也是这么摆的：同一个事实，两个界面不能两种说法。
    var seen = {};
    [["价值流", box.value_stream], ["协作边界", box.fulfillment_scope]].forEach(function (pair) {
      var value = pair[1];
      if (!value || seen[value]) return;
      seen[value] = true;
      head.appendChild(el("span", "tag", pair[0] + " · " + value));
    });
    head.appendChild(el("span", "tag", box.maturity === "skeleton" ? "未实现" : "已实现"));
    var v = el("span", "verdict " + box.health.verdict, VERDICT_TEXT[box.health.verdict]);
    head.appendChild(v);
    card.appendChild(head);

    var body = el("div", "body");
    if (box.description) body.appendChild(mdDiv("desc", box.description));

    var checks = el("div");
    box.health.checks.forEach(function (c) {
      var item = el("div", "check");
      var line = el("div", "check-head");
      line.appendChild(el("span", "check-title", c.title));
      line.appendChild(el("span", "spacer"));
      line.appendChild(el("span", "st " + c.status, STATUS_TEXT[c.status]));
      item.appendChild(line);
      item.appendChild(mdDiv("check-why", c.why));
      if (c.detail) item.appendChild(mdDiv("detail", c.detail));
      checks.appendChild(item);
    });
    body.appendChild(section("体检结论", checks));

    var acceptRows = box.acceptance.map(function (a) {
      return [a.metric, a.op, JSON.stringify(a.expected)];
    });
    body.appendChild(section("判据（合同）",
      acceptRows.length ? table(["量", "比较", "期望"], acceptRows) : el("div", "empty", "没有声明判据")));

    var excRows = box.exceptions.map(function (e) {
      return [e.condition, e.action, e.reason || "—"];
    });
    body.appendChild(section("例外条款",
      excRows.length ? table(["条件", "动作", "为什么"], excRows) : el("div", "empty", "没有声明例外")));

    var metricRows = box.metrics.map(function (m) {
      var actual = m.actual === null || m.actual === undefined
        ? "没测到"
        : m.actual + " / 目标 " + m.target;
      return [m.name, m.definition, actual, m.sample_size + " 次样本"];
    });
    body.appendChild(section("指标",
      metricRows.length ? table(["指标", "定义", "实测 / 目标", "样本"], metricRows) : el("div", "empty", "没有声明指标")));

    var wire = [];
    if (box.handoffs.length) wire.push("交给：" + box.handoffs.join("、"));
    if (box.feedback.length) wire.push("反馈给：" + box.feedback.join("、"));
    if (box.archive.length) wire.push("产物进档案：" + box.archive.join("、"));
    if (box.tasks.length) {
      wire.push("接：" + box.tasks.map(function (t) { return t.type; }).join("、"));
    }
    body.appendChild(section("接线",
      wire.length ? el("div", "desc", wire.join("　|　")) : el("div", "empty", "没有声明接线")));

    card.classList.add("open");
    card.appendChild(body);
    return card;
  }

  function show(box) {
    catalog.textContent = "";
    if (!box) {
      catalog.appendChild(el("div", "empty", "目录里一只盒子都没有。"));
      return;
    }
    catalog.appendChild(renderBox(box));
  }

  var current = pickInitialBox(data.boxes, boxIdFromUrl());
  if (current) picker.value = current.box_id;
  show(current);
  // URL 跟着选中的盒子走——不然没法把「这只盒子有问题」发给同事。
  // 初次渲染用 replaceState（不堆历史），用户自己换盒子才 pushState
  //（后退能回到上一只，而不是直接离开这个页面）。
  if (current) {
    history.replaceState(null, "", "?box=" + encodeURIComponent(current.box_id));
  }

  picker.addEventListener("change", function () {
    var box = data.boxes.filter(function (b) { return b.box_id === picker.value; })[0];
    if (!box) return;
    show(box);
    history.pushState(null, "", "?box=" + encodeURIComponent(box.box_id));
  });

  window.addEventListener("popstate", function () {
    var box = pickInitialBox(data.boxes, boxIdFromUrl());
    if (box) picker.value = box.box_id;
    show(box);
  });
})();
</script>
</body>
</html>
"""
