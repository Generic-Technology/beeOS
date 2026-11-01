"""守卫：Map（BeeBox Map）是从 placement 派生的结构，不是手写的表

为什么要有这一组：
- 「一个域的 Map」是真实存在的东西——它有名字之后，很多话才说得通
  （这件任务在Map上走了哪几步）。但**它不能是一张人手维护的表**：
  各盒的 placement（domain + feeds_into + feedback_into）已经是唯一事实源，
  再写一张域接线表就是第二份事实，必然漂，而且漂了不报错。
- 所以守三件事：
  ① Map的每条边都能追到某只盒子的 placement（派生，不是抄）；
  ② Map画不出来时**说清楚为什么**（跨域 / 没有汇点 / 汇点不唯一 / 成环）；
  ③ Map是一个**结构**：能被 API 读出来，不只是画布上的一张照片。
"""
from __future__ import annotations

from pathlib import Path

from core.field_map import (
    BeeBoxMap,
    PlacementView,
    derive_field_map,
    field_map_of_scope,
)
from kanban.trigger import (
    BOX_REGISTRY,
    domain_field_map,
    placement_views,
    scope_field_map,
    scope_layout,
)


def _p(box_id: str, domain: str = "WMS", feeds=(), feedback=()) -> PlacementView:
    return PlacementView(
        box_id=box_id, domain=domain,
        feeds_into=tuple(feeds), feedback_into=tuple(feedback),
    )


# ---------- 派生，不是抄 ----------

def test_the_edges_come_from_the_placements():
    """改一只盒子的 placement，Map的边跟着变

    这条是「派生」二字的机械证据：Map不是另抄了一份表，
    它读的就是 placement。
    """
    base = [
        _p("a", feeds=["b"]),
        _p("b", feeds=["c"]),
        _p("c", feeds=["d"]),
        _p("d"),
    ]
    m = derive_field_map("WMS", base)
    assert m.center == "d", "唯一汇点是 d"
    # 指向汇点的那条边是**累积**（产出进档案），不是交接——
    # 混成一条就看不出「流水线」和「记账」是两件事。
    assert m.handoffs == (("a", "b"), ("b", "c"))
    assert m.feeds_center == ("c",)

    # 把 a 的下游从 b 改成 c：Map的边必须跟着变
    changed = [
        _p("a", feeds=["c"]),
        _p("b", feeds=["c"]),
        _p("c", feeds=["d"]),
        _p("d"),
    ]
    m2 = derive_field_map("WMS", changed)
    assert m2.handoffs == (("a", "c"), ("b", "c"))
    assert m2.handoffs != m.handoffs


def test_the_feedback_edge_is_kept_out_of_the_handoff_chain():
    """闭环那条边是**反馈**，不进交接链

    混进交接链的话拓扑排序遇到环，会静默按原顺序收尾——
    环序变任意，图照常渲染，只是结构是错的。
    """
    m = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b", feeds=["c"], feedback=["a"]),
        _p("c", feeds=["d"]),
        _p("d"),
    ])
    assert m.feedback == (("b", "a"),)
    assert ("b", "a") not in m.handoffs, "反馈边混进交接链了"
    assert m.center == "d"
    assert m.ring == ("a", "b", "c"), "闭环那条边摘掉后，主干的环序排得出来"


def test_the_field_map_carries_no_hardcoded_box_names():
    """Map模块里不许出现任何一只盒子的名字

    出现 = 有人把业务事实写进了内核。Map只认 placement。
    """
    src = (Path(__file__).resolve().parent.parent / "core" / "field_map.py").read_text()
    for box_id in BOX_REGISTRY:
        assert box_id not in src, f"core/field_map.py 里写死了 {box_id}"
    assert "wms_" not in src, "core/field_map.py 里出现了具体域/盒子的名字"


# ---------- 画不出来时要说清楚 ----------

def test_a_scope_spanning_two_domains_has_no_field_map():
    """跨域 → 没有Map，但要把原因写出来

    硬凑一个汇点等于替业务声明一条没人声明过的拓扑。
    """
    views = {
        "a": _p("a", domain="WMS", feeds=["b"]),
        "b": _p("b", domain="WMS"),
        "c": _p("c", domain="OPS", feeds=["d"]),
        "d": _p("d", domain="OPS"),
    }
    m = field_map_of_scope("全部域", {"WMS": ["a", "b"], "OPS": ["c", "d"]}, views)
    assert m.center is None
    assert not m.drawable
    assert "多个域" in m.reason
    assert m.reason_code == "cross_domain"
    # 盒子还是列出来：「这个范围里有哪几只盒子」是事实，缺的只是 Map
    assert set(m.boxes) == {"a", "b", "c", "d"}


def test_no_sink_and_many_sinks_are_reported_differently():
    """没有汇点和汇点不唯一，是两回事，原因要分开写"""
    no_sink = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b", feeds=["c"]),
        _p("c", feeds=["a"]),
    ])
    assert no_sink.center is None
    assert "没有汇点" in no_sink.reason

    two_sinks = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b"),
        _p("c", feeds=["d"]),
        _p("d"),
    ])
    assert two_sinks.center is None
    assert "汇点" in two_sinks.reason


def test_a_cyclic_handoff_chain_is_reported_not_guessed():
    """交接边成环 → 退回并且点名是哪几只

    不猜：静默按原顺序收尾的话，图上只是一个顺序不对的环，
    看不出哪里声明错了。
    """
    # a↔b 互相交接（闭环），c→d 是主干，d 是汇点
    m = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b", feeds=["a"]),
        _p("c", feeds=["d"]),
        _p("d"),
    ])
    assert not m.drawable
    assert "成环" in m.reason
    assert "feedback_into" in m.reason, "要说清闭环那条边该声明成什么"
    assert "a" in m.reason and "b" in m.reason, "要点名是哪几只成环"


def test_a_branched_domain_is_not_drawn_as_a_ring():
    """**分叉的域画不出环** —— 而且要说清是哪一对没交接

    拓扑排序对分叉的域照样能排出一个顺序（那是个合法的拓扑序），
    但那个顺序里相邻的两只可能根本没有交接边——
    而看板画出来的是「环」，读者会以为相邻就是相连。
    **画出来的是一张撒谎的图**，所以这里不画。
    """
    # a→b→c→d 是主干（d 是汇点）；x 从 b 那里分叉出去（也投给 c）
    m = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b", feeds=["c"]),
        _p("c", feeds=["d"]),
        _p("d"),
        _p("x", feeds=["c"]),
    ])
    assert not m.drawable
    assert "不是一条链" in m.reason
    assert "x" in m.reason or "b" in m.reason, "要点名是哪一对没交接"


def test_a_proper_chain_is_still_drawn():
    """**一条真正的链照旧画得出来**（这条新判断不许误伤）"""
    m = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b", feeds=["c"]),
        _p("c", feeds=["d"]),
        _p("d"),
    ])
    assert m.drawable, m.reason
    assert m.center == "d"
    assert m.reason == ""


def test_the_verdict_is_machine_readable_not_just_a_sentence():
    """`reason` 是给人读的一句话；**判定码**是给程序用的

    为什么要有码：「域还小」和「接线坏了」在 `reason` 里是两句不同的话，
    但程序不能靠认句子来判断——脚手架要区分：
    接一个**新域**的第一只盒子时，域里就一只，画不出环是**正常状态**，
    不该因此拒绝；分叉 / 成环 / 两个汇点才是真的坏了。

    所以判定码必须是**结构化**的：脚手架读它，不读那句人话。
    """
    ok = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b", feeds=["c"]),
        _p("c", feeds=["d"]),
        _p("d"),
    ])
    assert ok.reason_code == "ok"

    # **新域的第一只盒子**：域还小，不是接线坏了
    small = derive_field_map("QA", [_p("only_one")])
    assert not small.drawable
    assert small.reason_code == "too_small", (
        f"一只盒子的域该判 too_small，实际 {small.reason_code}"
    )

    branched = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b", feeds=["c"]),
        _p("c", feeds=["d"]),
        _p("d"),
        _p("x", feeds=["c"]),
    ])
    assert branched.reason_code == "not_a_chain"

    cyclic = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b", feeds=["a"]),
        _p("c", feeds=["d"]),
        _p("d"),
    ])
    assert cyclic.reason_code == "cycle"

    two_sinks = derive_field_map("WMS", [
        _p("a", feeds=["b"]),
        _p("b"),
        _p("c", feeds=["d"]),
        _p("d"),
    ])
    assert two_sinks.reason_code == "multiple_sinks"

    # 判定码和 reason 要**对得上**：不能有码而没话，也不能有话而没码
    for m in (ok, small, branched, cyclic, two_sinks):
        assert bool(m.reason) == (m.reason_code != "ok"), (
            f"{m.domain}: reason={m.reason!r} / code={m.reason_code!r} 对不上"
        )


# ---------- 它是一个结构，不只是一张图 ----------

def test_the_registered_domains_all_have_a_field_map():
    """每个真域都推得出Map，盒子的集合 == 该域的盒子集合"""
    views = placement_views()
    domains = {v.domain for v in views.values()}
    assert domains, "注册表里一个域都没有"
    for domain in domains:
        m = domain_field_map(domain)
        expected = {b for b, v in views.items() if v.domain == domain}
        assert set(m.boxes) == expected
        assert m.domain == domain
        if len(expected) >= 3:
            assert m.drawable, f"{domain} 域推不出Map：{m.reason}"


def test_the_board_layout_is_the_same_map():
    """看板用的布局 == Map（同一份事实，不是两份推导）

    两边各推一次的话，改了 placement 其中一边会忘改——
    而看板上看不出来。
    """
    m = domain_field_map("WMS")
    lay = scope_layout("WMS")
    assert lay["center"] == m.center
    assert list(lay["ring"]) == list(m.ring)
    assert [list(e) for e in lay["handoffs"]] == [list(e) for e in m.handoffs]
    assert lay["layout"] == ("radial" if m.drawable else "flat")


def test_the_map_is_readable_as_a_structure(tmp_path: Path):
    """Map能被程序读出来（不是只能看图）"""
    from kanban.web import field_maps_payload

    payload = field_maps_payload()
    assert "maps" in payload and payload["maps"], "没有各域的Map"
    wms = payload["maps"]["WMS"]
    assert wms["center"]
    assert wms["handoffs"], "WMS 域的交接边不该是空的"
    assert wms["drawable"] is True
    # 跨域范围也有一条结论：没有Map + 原因
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
    """BeeBox Map 的边界（虚线）要画出来，而且跟着卡片走

    边界写死位置的话，环上多一只盒子，它就骑在框线上。
    """
    import kanban.web as W

    page = W.HTML_PAGE
    assert 'class="edge-field"' in page, "BeeBox Map 的边界没画"
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
    """
    import kanban.web as W

    page = W.HTML_PAGE
    assert "' · BeeBox Map'" in page, (
        "边界的标签应该写「<域> · BeeBox Map」"
    )
    assert "' · 场'" not in page, "别再用自造的词「场」当标签"
