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

from kanban.catalog import catalog_inspections
from kanban.trigger import box_metrics_readings
from runtime.box_health import BoxHealth, inspect_box


#: 页面里 JSON 数据的占位符。**不可能撞上正文**（正文里没有这个串）。
_DATA_PLACEHOLDER = "__WORKSHOP_DATA__"


def _box_payload(inspection, records: list[dict[str, Any]]) -> dict[str, Any]:
    """一只盒子在工作台页面上的全部内容"""
    d = inspection.definition
    p = d.placement
    return {
        "box_id": inspection.box_id,
        "version": d.version,
        "description": d.description,
        "display_name": p.display_name or inspection.box_id,
        "value_stream": p.value_stream,
        "role": p.role,
        "domain": p.domain,
        "maturity": d.result.maturity,
        "artifact_view": d.result.view,
        "feeds_into": list(p.feeds_into),
        "feedback_into": list(p.feedback_into),
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


_PAGE = """<!DOCTYPE html>
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
  .box {
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
    margin-bottom: 18px; overflow: hidden;
  }
  .box > .head {
    padding: 14px 18px; display: flex; align-items: center; gap: 12px;
    cursor: pointer; user-select: none; background: var(--panel-2);
  }
  .box > .head:hover { background: #232833; }
  .name { font-weight: 600; font-size: 15px; }
  .id { color: var(--dim); font-size: 12px; font-family: ui-monospace, Menlo, monospace; }
  .spacer { flex: 1; }
  .tag {
    font-size: 12px; border: 1px solid var(--line); border-radius: 6px;
    padding: 1px 8px; color: var(--dim);
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
<main id="catalog"></main>
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

  var catalog = document.getElementById("catalog");

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

  data.boxes.forEach(function (box, index) {
    var card = el("div", "box");
    var head = el("div", "head");
    head.appendChild(el("span", "name", box.display_name));
    head.appendChild(el("span", "id", box.box_id));
    head.appendChild(el("span", "spacer"));
    // 归属标签**带前缀**且去重：域和价值流可能是同一个词
    // （学习域的盒子域和价值流都叫 Learning），两个一样的裸标签看着像重复。
    var seen = {};
    [["价值流", box.value_stream], ["域", box.domain]].forEach(function (pair) {
      var value = pair[1];
      if (!value || seen[value]) return;
      seen[value] = true;
      head.appendChild(el("span", "tag", pair[0] + " · " + value));
    });
    head.appendChild(el("span", "tag", box.maturity === "skeleton" ? "未实现" : "已实现"));
    var v = el("span", "verdict " + box.health.verdict, VERDICT_TEXT[box.health.verdict]);
    head.appendChild(v);
    head.addEventListener("click", function () {
      card.classList.toggle("open");
    });
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
    if (box.feeds_into.length) wire.push("交给：" + box.feeds_into.join("、"));
    if (box.feedback_into.length) wire.push("反馈给：" + box.feedback_into.join("、"));
    if (box.tasks.length) {
      wire.push("接：" + box.tasks.map(function (t) { return t.type; }).join("、"));
    }
    body.appendChild(section("接线",
      wire.length ? el("div", "desc", wire.join("　|　")) : el("div", "empty", "没有声明接线")));

    card.appendChild(body);
    catalog.appendChild(card);
    if (index === 0) card.classList.add("open");
  });

  if (!data.boxes.length) {
    catalog.appendChild(el("div", "empty", "目录里一只盒子都没有。"));
  }
})();
</script>
</body>
</html>
"""
