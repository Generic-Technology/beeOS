"""守卫：接线投影（Wiring View）是从**协作边界声明**派生的结构，不是手写的表

为什么要有这一组：
- 「一个协作边界的接线」是真实存在的东西——它有名字之后，很多话才说得通
  （这件任务在范围上走了哪几步）。但**它不能是一张人手维护的表**：
  协作边界声明（成员 + 边）已经是唯一事实源，再写一张协作边界接线表就是第二份事实，
  必然漂，而且漂了不报错。
- 所以守三件事：
  ① 接线的每条边都能追到协作边界声明里的某条边（派生，不是抄）；
  ② 画不出来时**说清楚为什么**（跨边界 / 没有汇点 / 汇点不唯一 / 成环）；
  ③ 它是一个**结构**：能被 API 读出来，不只是画布上的一张照片。

**边按 `kind` 分三类**（这是 2026-11-05 的实测结论）：
`handoff` 进链、`archive` 决定汇点但不是交接、`feedback` 是闭环反馈。
"""
from __future__ import annotations

from pathlib import Path

from core.wiring import (
    ARCHIVE,
    FEEDBACK,
    HANDOFF,
    WiringView,
    derive_wiring,
    wiring_of_scope,
)
from kanban.trigger import (
    BOX_REGISTRY,
    fulfillment_scope_wiring,
    scope_wiring,
    scope_layout,
)
from fulfillment_scopes import FULFILLMENT_SCOPE_REGISTRY


def _e(src: str, dst: str, kind: str = HANDOFF) -> tuple[str, str, str]:
    return (src, dst, kind)


def _derive(boxes, edges, domain: str = "WMS") -> WiringView:
    return derive_wiring(domain, boxes, edges)


# ---------- 派生，不是抄 ----------

def test_the_edges_come_from_the_declaration():
    """改一条边，接线的边跟着变

    这条是「派生」二字的机械证据：接线不是另抄了一份表，
    它读的就是协作边界声明。
    """
    base_boxes = ["a", "b", "c", "d"]
    base = [_e("a", "b"), _e("b", "c"), _e("c", "d", ARCHIVE)]
    m = _derive(base_boxes, base)
    assert m.center == "d", "唯一汇点是 d"
    # 指向汇点的那条边是**归档归属**，不是交接——
    # 混成一条就看不出「流水线」和「记账」是两件事。
    assert m.handoffs == (("a", "b"), ("b", "c"))
    assert m.feeds_center == ("c",)

    # 把 a 的下游从 b 改成 c：接线的边必须跟着变
    changed = [_e("a", "c"), _e("b", "c"), _e("c", "d", ARCHIVE)]
    m2 = _derive(base_boxes, changed)
    assert m2.handoffs == (("a", "c"), ("b", "c"))
    assert m2.handoffs != m.handoffs


def test_the_archive_edge_decides_the_center_but_is_not_a_handoff():
    """**归档边决定谁是汇点，但它不是交接**

    这是 2026-11-05 实测逼出来的区分：
    档案盒（`wms_system_box`）**一条交接边都没有**——
    它只被 5 条归档边连着。
    如果归档边不计入度数，汇点会从「档案盒」变成「链条末端那只」，整张图就错了。
    但它**不是交接**——把它算进链，就是在暗示「这两只有交接关系」。
    """
    boxes = ["a", "b", "c", "sink"]
    m = _derive(boxes, [
        _e("a", "b"),
        _e("b", "c"),
        _e("c", "sink", ARCHIVE),
    ])
    assert m.center == "sink", "汇点是被归档的那只"
    assert m.handoffs == (("a", "b"), ("b", "c")), "归档边不是交接"
    assert m.feeds_center == ("c",)


def test_the_feedback_edge_is_kept_out_of_the_handoff_chain():
    """闭环那条边是**反馈**，不进交接链

    混进交接链的话拓扑排序遇到环，会静默按原顺序收尾——
    环序变任意，图照常渲染，只是结构是错的。
    """
    m = _derive(
        ["a", "b", "c", "d"],
        [
            _e("a", "b"),
            _e("b", "c"),
            _e("b", "a", FEEDBACK),
            _e("c", "d", ARCHIVE),
        ],
    )
    assert m.feedback == (("b", "a"),)
    assert ("b", "a") not in m.handoffs, "反馈边混进交接链了"
    assert m.center == "d"
    assert m.ring == ("a", "b", "c"), "闭环那条边摘掉后，主干的环序排得出来"


def test_the_wiring_module_carries_no_hardcoded_box_names():
    """接线模块里不许出现任何一只盒子的名字

    出现 = 有人把业务事实写进了内核。接线只认协议。
    """
    src = (Path(__file__).resolve().parent.parent / "core" / "wiring.py").read_text()
    for box_id in BOX_REGISTRY:
        assert box_id not in src, f"core/wiring.py 里写死了 {box_id}"
    assert "wms_" not in src, "core/wiring.py 里出现了具体协作边界/盒子的名字"


# ---------- 画不出来时要说清楚 ----------

def test_a_scope_spanning_two_domains_has_no_wiring():
    """跨边界 → 没有接线图，但要把原因写出来

    硬凑一个汇点等于替业务声明一条没人声明过的拓扑。
    """
    wirings = {
        "WMS": _derive(["a", "b"], [_e("a", "b")]),
        "OPS": _derive(["c", "d"], [_e("c", "d")]),
    }
    m = wiring_of_scope("全部范围", {"WMS": ["a", "b"], "OPS": ["c", "d"]}, wirings)
    assert m.center is None
    assert not m.drawable
    assert "多个协作边界" in m.reason
    assert m.reason_code == "cross_scope"
    # 盒子还是列出来：「这个范围里有哪几只盒子」是事实，缺的只是图
    assert set(m.boxes) == {"a", "b", "c", "d"}


def test_no_sink_and_many_sinks_are_reported_differently():
    """没有汇点和汇点不唯一，是两回事，原因要分开写"""
    no_sink = _derive(
        ["a", "b", "c"],
        [_e("a", "b"), _e("b", "c"), _e("c", "a")],
    )
    assert no_sink.center is None
    assert "没有汇点" in no_sink.reason

    two_sinks = _derive(
        ["a", "b", "c", "d"],
        [_e("a", "b"), _e("c", "d")],
    )
    assert two_sinks.center is None
    assert "汇点" in two_sinks.reason


def test_a_cyclic_handoff_chain_is_reported_not_guessed():
    """交接边成环 → 退回并且点名是哪几只

    不猜：静默按原顺序收尾的话，图上只是一个顺序不对的环，
    看不出哪里声明错了。
    """
    m = _derive(
        ["a", "b", "c", "d"],
        [_e("a", "b"), _e("b", "a"), _e("c", "d", ARCHIVE)],
    )
    assert not m.drawable
    assert "成环" in m.reason
    assert "feedback" in m.reason, "要说清闭环那条边该声明成什么"
    assert "a" in m.reason and "b" in m.reason, "要点名是哪几只成环"


def test_a_branched_domain_is_not_drawn_as_a_ring():
    """**分叉的协作边界画不出环** —— 而且要说清是哪一对没交接

    拓扑排序对分叉的协作边界照样能排出一个顺序（那是个合法的拓扑序），
    但那个顺序里相邻的两只可能根本没有交接边——
    而看板画出来的是「环」，读者会以为相邻就是相连。
    **画出来的是一张撒谎的图**，所以这里不画。
    """
    m = _derive(
        ["a", "b", "c", "d", "x"],
        [
            _e("a", "b"),
            _e("b", "c"),
            _e("c", "d", ARCHIVE),
            _e("x", "c"),
        ],
    )
    assert not m.drawable
    assert "不是一条链" in m.reason
    assert "x" in m.reason or "b" in m.reason, "要点名是哪一对没交接"


def test_a_proper_chain_is_still_drawn():
    """**一条真正的链照旧画得出来**（这条新判断不许误伤）"""
    m = _derive(
        ["a", "b", "c", "d"],
        [_e("a", "b"), _e("b", "c"), _e("c", "d", ARCHIVE)],
    )
    assert m.drawable, m.reason
    assert m.center == "d"
    assert m.reason == ""


def test_the_verdict_is_machine_readable_not_just_a_sentence():
    """`reason` 是给人读的一句话；**判定码**是给程序用的

    为什么要有码：「边界还小」和「接线坏了」在 `reason` 里是两句不同的话，
    但程序不能靠认句子来判断——脚手架要区分：
    接一个**新协作边界**的第一只盒子时，边界里就一只，画不出环是**正常状态**，
    不该因此拒绝；分叉 / 成环 / 两个汇点才是真的坏了。

    所以判定码必须是**结构化**的：脚手架读它，不读那句人话。
    """
    ok = _derive(
        ["a", "b", "c", "d"],
        [_e("a", "b"), _e("b", "c"), _e("c", "d", ARCHIVE)],
    )
    assert ok.reason_code == "ok"

    # **新协作边界的第一只盒子**：边界还小，不是接线坏了
    small = _derive(["only_one"], [], domain="QA")
    assert not small.drawable
    assert small.reason_code == "too_small", (
        f"一只盒子的范围该判 too_small，实际 {small.reason_code}"
    )

    branched = _derive(
        ["a", "b", "c", "d", "x"],
        [_e("a", "b"), _e("b", "c"), _e("c", "d", ARCHIVE), _e("x", "c")],
    )
    assert branched.reason_code == "not_a_chain"

    cyclic = _derive(
        ["a", "b", "c", "d"],
        [_e("a", "b"), _e("b", "a"), _e("c", "d", ARCHIVE)],
    )
    assert cyclic.reason_code == "cycle"

    two_sinks = _derive(
        ["a", "b", "c", "d"],
        [_e("a", "b"), _e("c", "d")],
    )
    assert two_sinks.reason_code == "multiple_sinks"

    # 判定码和 reason 要**对得上**：有话才需要码。
    # 「放射」是**另一种成立的形状**，不是「画不出来」——所以它没有 reason。
    for m in (ok, small, branched, cyclic, two_sinks):
        assert bool(m.reason) == (m.reason_code not in ("ok", "star")), (
            f"{m.scope}: reason={m.reason!r} / code={m.reason_code!r} 对不上"
        )


# ---------- 多源单汇（放射形状） ----------


def test_a_star_domain_is_drawn_as_a_fan_not_as_a_ring():
    """**多源单汇**（放射）：数学 / 语文 / 英语 → 一份成长记录

    这是**另一种成立的形状**，不是「接线坏了」：
    三只学科盒之间**没有交接**（数学的产出不是语文的输入），
    各自直投汇点。以前这里会判成「不是一条链」→ 协作边界退回平铺 →
    「接一个新协作边界」永远接不进来。

    放射里源盒**不能摆在环上相邻的位置**——环上「相邻 = 有交接」
    是环的前提，在放射里摆成环就是在暗示不存在的接线关系。
    """
    m = _derive(
        ["math_box", "chinese_box", "english_box", "growth_box"],
        [
            _e("math_box", "growth_box"),
            _e("chinese_box", "growth_box"),
            _e("english_box", "growth_box"),
        ],
        domain="Learning",
    )
    assert m.center == "growth_box"
    assert m.shape == "star"
    assert m.reason == "", f"放射形状不该有 reason：{m.reason}"
    assert m.reason_code == "star"
    assert m.drawable, "放射是成立的形状，画得出来"
    # 源盒之间**没有交接**：交接边必须为空
    assert m.handoffs == ()
    assert set(m.feeds_center) == {"math_box", "chinese_box", "english_box"}


def test_a_star_domain_with_an_isolated_box_is_refused():
    """放射形状里混进一只**哪都不接**的盒子 → 判红

    它既不投汇点、也不交接给谁 —— 那不是放射，是接错了。
    放过它 = 它的产出落在虚空里，而图上看不出来。
    """
    m = _derive(
        ["math_box", "growth_box", "lost_box"],
        [_e("math_box", "growth_box")],
        domain="Learning",
    )
    assert not m.drawable
    assert m.reason_code == "isolated_box"
    assert "lost_box" in m.reason, "要点名是哪一只接空了"


def test_two_independent_chains_are_still_refused():
    """**两条独立的链**在一个协作边界里 → 仍然判「不是一条链」

    这条是「放射别把真分叉一起放过」的守卫：
    放射的前提是「边界内**一条交接边都没有**」。
    只要有交接边，环序相邻就必须真的有交接——否则画出来的是撒谎的环。
    """
    m = _derive(
        ["a", "b", "x", "y", "hub"],
        [
            _e("a", "b"),
            _e("b", "hub"),
            _e("x", "y"),
            _e("y", "hub"),
        ],
    )
    assert m.center == "hub"
    assert m.shape == "", "有交接边就不该判成放射"
    assert m.reason_code == "not_a_chain"


def test_the_layout_tells_the_frontend_which_shape_it_is():
    """看板布局里必须带 `shape` —— 前端要靠它选画法

    画错形状的后果不是「不好看」，是**暗示了不存在的接线关系**：
    把放射的源盒摆成环，读者就会以为它们两两相邻即相连。
    """
    from kanban.trigger import fulfillment_scope_layout

    lay = fulfillment_scope_layout("Learning")
    assert lay["shape"] in ("ring", "star", "")
    # 环形状的协作边界（WMS）仍然是环
    assert fulfillment_scope_layout("WMS")["shape"] == "ring"


def test_the_star_layout_does_not_put_the_sources_on_a_ring():
    """放射形状的源盒**不许**摆成圆 —— 那是在暗示不存在的交接

    环上「相邻 = 有交接」是环的前提；放射里源盒两两之间都没有交接，
    把它们摆在环上相邻的位置 = 画出一张撒谎的图。

    放射的源盒排成**一行**（按卡宽 + 间隙），汇点在下方的中间。
    """
    from tests.test_web_kanban import _extract_js_function

    src = _extract_js_function("renderRadial")
    assert "layout.shape === 'star'" in src, (
        "前端没读 shape —— 它不知道这个协作边界是放射形状"
    )
    star_branch = src[src.index("if (isStar) {"): src.index("} else {")]
    assert "polar(" not in star_branch, (
        "放射的源盒不许摆在圆上：环上相邻 = 有交接，放射里没有交接"
    )
    assert "pitch" in star_branch, "放射的源盒排成一行（按卡宽 + 间隙）"
    ring_branch = src[src.index("} else {"):]
    assert "polar(" in ring_branch, "环形状还是摆在圆上"


# ---------- 它是一个结构，不只是一张图 ----------

def test_the_registered_domains_all_have_a_wiring():
    """每个协作边界都推得出接线，盒子的集合 == 协作边界声明的盒子集合"""
    assert FULFILLMENT_SCOPE_REGISTRY, "一个协作边界都没有声明"
    for domain_id, declaration in FULFILLMENT_SCOPE_REGISTRY.items():
        m = fulfillment_scope_wiring(domain_id)
        assert set(m.boxes) == set(declaration.boxes)
        assert m.scope == domain_id
        if len(declaration.boxes) >= 3:
            assert m.drawable, f"{domain_id} 协作边界推不出接线：{m.reason}"


def test_the_board_layout_is_the_same_wiring():
    """看板用的布局 == 接线（同一份事实，不是两份推导）

    两边各推一次的话，改了协作边界声明其中一边会忘改——
    而看板上看不出来。
    """
    m = fulfillment_scope_wiring("WMS")
    lay = scope_layout("WMS")
    assert lay["center"] == m.center
    assert list(lay["ring"]) == list(m.ring)
    assert [list(e) for e in lay["handoffs"]] == [list(e) for e in m.handoffs]
    assert lay["layout"] == ("radial" if m.drawable else "flat")


def test_the_wiring_is_readable_as_a_structure(tmp_path: Path):
    """接线能被程序读出来（不是只能看图）"""
    from kanban.web import wirings_payload

    payload = wirings_payload()
    assert "maps" in payload and payload["maps"], "没有各协作边界的接线"
    wms = payload["maps"]["WMS"]
    assert wms["center"]
    assert wms["handoffs"], "WMS 协作边界的交接边不该是空的"
    assert wms["drawable"] is True
    # 跨边界范围也有一条结论：没有图 + 原因
    assert payload["scope_all"]["drawable"] is False
    assert payload["scope_all"]["reason"]


# ---------- 画布几何：纵向中心不能用宽度的一半 ----------

def test_the_radial_canvas_uses_the_vertical_centre():
    """环形视图里卡片的 top 必须用**高**的一半，不是宽的一半

    以前写的是 `cx`（W/2）：整张环竖着偏移了 (W-H)/2，
    顶部那只盒子的立方体探出画布被裁掉——看上去就是「被遮住了一些」，
    而画布本身是正常的，最难查。

    这条守卫钉住的是**几何算对了**，不是「画得像不像」。
    """
    import kanban.web as W

    page = W.HTML_PAGE
    # 卡片的 top 必须是 cy + p.y，cy = H / 2
    assert "top:' + (cy + p.y - RADIAL_CARD_H / 2)" in page, (
        "卡片的 top 还在用 cx —— 整张环会竖着偏移，顶部被裁"
    )
    assert "const cy = H / 2;" in page, "纵向中心 cy 没算出来"
    # 连线的 y 坐标同样用 cy（否则箭头跟卡片脱开）
    assert "(cy + l.a.y)" in page and "(cy + l.b.y)" in page
    assert "(cx + l.a.y)" not in page and "(cx + l.b.y)" not in page, (
        "连线的 y 坐标还在用 cx"
    )


def test_the_field_boundary_is_drawn_and_follows_the_cards():
    """范围边界的虚线要画出来，而且跟着卡片走

    边界写死位置的话，环上多一只盒子，它就骑在框线上。
    """
    import kanban.web as W

    page = W.HTML_PAGE
    assert 'class="edge-field"' in page, "范围边界没画"
    assert "fieldHalfX" in page and "fieldHalfY" in page, (
        "边界必须由卡片位置算出来，不能写死"
    )
    assert "const FIELD_PAD" in page and "const CUBE_ALLOW" in page, (
        "边界余量 / 立方体投影余量得是常量（要跟着布局改）"
    )


def test_the_boundary_is_labelled_with_the_products_own_name():
    """边界上写的是 **BeeBox Map**——产品自己的名字，不自造词

    「活儿」「场」这类自造词，业务方看不懂，还得再解释一遍；
    而产品名（BeeBox）本来就在标题里出现过，是现成的词。

    **标签里不该再写范围名**：框上方那行标题已经说了
    「<范围> 协作范围的 BeeBox Map」，框角上再来一遍是同一句话说两遍，
    而且框上那份更弱——少了「协作范围」这个限定，
    「Learning · BeeBox Map」读起来像产品名的一部分。
    """
    import kanban.web as W

    page = W.HTML_PAGE
    label_lines = [ln for ln in page.splitlines() if "const fieldLabel" in ln]
    assert label_lines, "边界标签那一行不见了——框角上的字是怎么来的？"
    label = label_lines[0]
    assert "BeeBox Map" in label, "边界的标签应该用产品自己的名字"
    assert "scope" not in label, (
        "边界的标签不该再写范围名——框上方标题已经写了"
        "「<范围> 协作范围的 BeeBox Map」，框角再说一遍是同一句话说两遍"
    )
    assert "' · 场'" not in page, "别再用自造的词「场」当标签"
