"""core.wiring.py —— 接线投影（Wiring View）

**一个域的接线长什么样**：域里有哪些盒子、谁交给谁、谁的产出汇进档案（汇点）、
哪条边是上一轮结论变成下一轮输入（闭环反馈）。

## 为什么它是**派生**的，不是一张手写的表

接线事实已经有一份了——**域声明**（`domains/<域>/`）。
再手写一张「WMS 接线表」就是**第二份事实**，两份必然漂，而且漂了
**没有任何东西会报错**——图照常渲染，只是有一只盒子接错了对象。
所以投影只能是**从域声明推出来的视图**：改一条边，接线自己跟着变。

## 边按 `kind` 分三类，只有一类进链

| kind | 进度数计算 | 进链（环序）| 含义 |
|---|---|---|---|
| `handoff` | ✅ | ✅ | **数据交接**：产出交出去给下游验 |
| `archive` | ✅ | ❌ | **归档归属**：这一环的产物进档案（档案盒是去线上读的，不收上游产出）|
| `feedback` | ✅ | ❌ | **闭环反馈**：上一轮结论回到起点变成新一轮输入 |

**为什么 `archive` 进度数、不进链**：
汇点（圆心）就是**被归档的那只盒子**——它之所以是圆心，正因为大家把产物投给它。
所以归档边**必须**计入度数，否则圆心会变成链条末端那只（实测：会从「档案盒」变成
「分析盒」），整张图就错了。
但它**不是交接**：链上相邻 = 有交接是环的前提，把归档边算进链，
就是在暗示「这两只有交接关系」——**那是一张撒谎的图**。

**闭环反馈边为什么不进拓扑排序**：闭环那条边一旦混进主干，
排序就遇到环，于是按原顺序静默收尾——环序变任意，图照常渲染，只是结构是错的。
摘出来，主干就永远无环。

## 跨域没有接线图

「全部域」不是一个域的图：各域的流水线互不相干，硬凑一个汇点等于
替业务声明一条没人声明过的拓扑。所以跨域时**不给图**，给的是
一句能读懂的原因（`reason`），而不是一张勉强画出来的图。

## 它是**结构**，不只是图

接线是可以被程序读的东西：哪些盒子在这个域的图里、它们之间的边是什么类型、
谁是汇点。画布上的虚线边界只是它的一张照片。

**本模块不含任何业务知识**：它只认协议（边的三元组），不认识任何一只盒子。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# 边的类别（字符串常量，避免与业务协议耦合）
HANDOFF = "handoff"
ARCHIVE = "archive"
FEEDBACK = "feedback"


@dataclass(frozen=True)
class WiringView:
    """一个域的接线投影

    `reason` 为空 = 图成立；非空 = **画不出来**，原因写在这里。
    跨域、没有汇点、汇点不唯一、交接成环——四种情况各有各的原因，
    不合并成一个「画不出来」。

    ## `shape`：这个域的接线是哪种形状

    - `"ring"` —— **环**：域内是一条链（每环交给下一环），产出同时直投汇点
    - `"star"` —— **放射**：域内**没有交接**，多只盒子各自直投汇点
      （例如「数学 / 语文 / 英语」三只学科盒 → 一份成长记录）

    为什么要分形状：环上**相邻 = 有交接边**是环的前提；
    放射形状里源盒之间根本没有交接，把它们摆在环上相邻的位置
    就是在暗示「这两只有关系」——那是一张撒谎的图。
    所以放射的源盒**不摆成环**，摆成一把扇子。

    ## `reason_code`：`reason` 的机器可读版本

    `reason` 是**给人读的一句话**。但程序要判断的是另一件事：
    「这个域画不出图」到底是**接线坏了**，还是**这个域还太小**？

    - 接线坏了（`no_sink` / `multiple_sinks` / `cycle` / `not_a_chain`）
      → 谁接谁真的不对，必须有人来改
    - 域还小（`too_small` / `empty`）→ 接线没错，只是盒子还不够多
    """

    domain: str
    boxes: tuple[str, ...] = ()
    center: str | None = None
    ring: tuple[str, ...] = ()
    handoffs: tuple[tuple[str, str], ...] = ()
    feeds_center: tuple[str, ...] = ()
    feedback: tuple[tuple[str, str], ...] = ()
    reason: str = ""
    # 机器可读的判定码：见类文档。空 reason ⇔ "ok"
    reason_code: str = "ok"
    # 这个域的接线形状："ring" / "star" / ""（画不出来）
    shape: str = ""

    @property
    def drawable(self) -> bool:
        """图画不画得出来（有没有汇点、环序排不排得出来）"""
        return self.center is not None and not self.reason

    def to_dict(self) -> dict:
        """给 API / 看板用的形状"""
        return {
            "domain": self.domain,
            "boxes": list(self.boxes),
            "center": self.center,
            "ring": list(self.ring),
            "handoffs": [list(e) for e in self.handoffs],
            "feeds_center": list(self.feeds_center),
            "feedback": [list(e) for e in self.feedback],
            "reason": self.reason,
            "reason_code": self.reason_code,
            "shape": self.shape,
            "drawable": self.drawable,
        }


def _broken_adjacency(
    ring: Sequence[str], handoffs: Sequence[tuple[str, str]],
) -> tuple[str, str] | None:
    """环上第一对「相邻却没有交接边」的盒子（没有就返回 None）

    **为什么要有这个判断**：拓扑排序对**分叉**的域照样能排出一个顺序
    （那是个合法的拓扑序），但顺序里相邻的两只可能根本没有交接边。
    看板画出来的是环，读者会以为相邻就是相连——**那是撒谎的**。
    """
    edges = set(handoffs)
    for i in range(len(ring) - 1):
        pair = (ring[i], ring[i + 1])
        if pair not in edges:
            return pair
    return None


def _topo_within(
    ids: Sequence[str], edges: Sequence[tuple[str, str]],
) -> tuple[list[str], list[str]]:
    """ids 内部的拓扑排序；返回 (有序结果, 卡在环上的节点)

    遇到环**必须被看见**：成环只有一个原因，就是有人把闭环的边声明成了
    handoff，而那会让环序变成任意的。这里返回卡住的那几个节点，
    交给调用方把原因写进 reason——不静默按原顺序收尾。
    """
    inside = set(ids)
    preds: dict[str, set[str]] = {b: set() for b in ids}
    for src, dst in edges:
        if src in inside and dst in inside:
            preds[dst].add(src)
    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(ids)
    while remaining:
        ready = [b for b in remaining if preds[b] <= placed]
        if not ready:
            return ordered, remaining
        ordered.extend(ready)
        placed.update(ready)
        remaining = [b for b in remaining if b not in placed]
    return ordered, []


def derive_wiring(
    domain: str,
    boxes: Sequence[str],
    edges: Sequence[tuple[str, str, str]],
) -> WiringView:
    """从域声明推导出这个域的接线投影

    **纯函数**：只吃（域名、盒子清单、边清单），不认识注册表、不认识任何一只盒子。
    这样它能被看板、API、将来产出图的盒子共用同一份推导。

    Args:
        domain: 域 id
        boxes: 本域的盒子清单
        edges: `(from, to, kind)` 三元组；kind 是 `handoff` / `archive` / `feedback`
    """
    ids = list(boxes)
    idset = set(ids)

    # **悬空边在这里被丢弃**——指向不在本域的盒子（除非显式声明为外部边）。
    # 装配期的校验会先把这事挑出来（见 `core/decl_checks.py`），
    # 所以这里的「丢弃」不会静默：**装配期就拒了**。
    flow = [
        (b, t, kind)
        for (b, t, kind) in edges
        if t in idset
    ]

    # 交接边：进链的那部分
    chain_edges = [(b, t) for (b, t, k) in flow if k == HANDOFF]
    # 闭环反馈边：**不进**拓扑排序（见模块文档）
    feedback = [(b, t) for (b, t, k) in flow if k == FEEDBACK]

    indeg: dict[str, int] = {b: 0 for b in ids}
    outdeg: dict[str, int] = {b: 0 for b in ids}
    for src, dst, _kind in flow:
        outdeg[src] += 1
        indeg[dst] += 1

    sinks = [b for b in ids if outdeg[b] == 0 and indeg[b] > 0]
    center = sinks[0] if len(sinks) == 1 else None

    ring = [b for b in ids if b != center] if center else list(ids)
    # **链上相邻 = 有交接**：只有 handoff 边算交接。
    # 归档边虽然计入度数（它决定谁是圆心），但它**不是交接**，
    # 把它算进链，就是在暗示「这两只有交接关系」——撒谎的图。
    handoffs = tuple((s, d) for (s, d) in chain_edges if d != center)
    # 直投汇点的：handoff 也算（Learning 域三只学科盒 → 成长记录就是这么接的）
    feeds_center = tuple(s for (s, d, k) in flow if d == center and k in (HANDOFF, ARCHIVE))

    # 环序 = 交接边的拓扑序。中心摘掉——它是汇点，让它参与排序
    # 会把整环的深度都压平。
    ordered, cyclic = _topo_within(ring, chain_edges)

    reason = ""
    reason_code = "ok"
    shape = ""
    if center is None:
        if len(ids) < 3:
            reason = f"域内只有 {len(ids)} 只盒子，撑不起环"
            # **域还小 ≠ 接线坏了**：新域的第一只盒子就在这儿。
            reason_code = "too_small"
        elif not sinks:
            reason = "域内没有汇点（每只盒子都还有下游）"
            reason_code = "no_sink"
        else:
            reason = "域内有 " + str(len(sinks)) + " 个汇点，中心不唯一：" + "、".join(sinks)
            reason_code = "multiple_sinks"
    elif cyclic:
        # 闭环的边声明成 handoff 了。主干因此成环，环序排不出来。
        # 不猜：把是哪几只说清楚——不然图上就只是一个顺序不对的环，
        # 看不出哪里声明错了。
        reason = (
            "域内交接边成环（" + "、".join(cyclic) + "）——"
            "闭环那条边应该声明成 feedback，不是 handoff"
        )
        reason_code = "cycle"
    elif not handoffs:
        # **域内一条交接边都没有** → 多源单汇（放射形状）。
        #
        # 例如「数学 / 语文 / 英语」三只学科盒，各自直投「成长记录」：
        # 它们之间**没有交接**（数学的产出不是语文的输入），
        # 所以这不是「一条链」，是一把扇子。
        #
        # 以前这里会判成「不是一条链」→ 域退回平铺 → 域画不出来。
        # 但接线其实**没错**，错的是「域必须是链」这个前提。
        #
        # 孤岛要单独抓出来：有盒子既不投汇点、也不交接给任何人，
        # 它在这个域里没有任何接线——那不是放射，是接错了。
        orphans = [b for b in ids if b != center and b not in feeds_center]
        if orphans:
            reason = (
                f"域内有盒子既不投给汇点、也不交接给谁：{'、'.join(orphans)} ——"
                "它在这个域里没有任何接线"
            )
            reason_code = "isolated_box"
        else:
            shape = "star"
            # 「放射」不是「坏」：它是**另一种成立的形状**，没有 reason。
            reason_code = "star"
    else:
        broken = _broken_adjacency(ordered, handoffs)
        if broken:
            # **环上相邻必须有交接边**。
            #
            # 拓扑排序对**分叉**的域照样能排出一个顺序（那是合法的拓扑序），
            # 但那个顺序里相邻的两只可能根本没有交接边——
            # 而看板画出来的是「环」，读者会以为相邻就是相连。
            # **画出来的是一张撒谎的图**，所以这里不画，并把哪一对说清楚。
            #
            # 域里应该是一条**线性链**：新盒子要么插在链上，要么自己开一条链。
            first, second = broken
            reason = (
                f"域内不是一条链（{'、'.join(broken)} 之间没有交接边，"
                "却会画成环上相邻）——"
                "新盒子要么**插在链上**（接手上一只的下游），"
                "要么**自己开一条链**"
            )
            reason_code = "not_a_chain"
        else:
            shape = "ring"

    return WiringView(
        domain=domain,
        boxes=tuple(ids),
        center=center,
        ring=tuple(ordered),
        handoffs=handoffs,
        feeds_center=feeds_center,
        feedback=tuple(feedback),
        reason=reason,
        reason_code=reason_code,
        shape=shape,
    )


def wiring_of_scope(
    scope: str,
    scopes: dict[str, list[str]],
    wirings: dict[str, WiringView],
) -> WiringView:
    """一个**范围**（可能横跨多个域）的接线投影

    横跨多个域时**没有图**：各域流水线互不相干，硬凑一个中心等于
    替业务声明一条不存在的拓扑。给的是一句能读懂的原因。
    """
    domains = [d for d in scopes if d in scopes and scopes[d]]
    if len(domains) > 1:
        every: list[str] = []
        for d in domains:
            every.extend(scopes[d])
        return WiringView(
            domain=scope,
            # 盒子还是列出来——「这个范围里有哪几只盒子」是事实，
            # 缺的只是**图**（共同汇点 + 接线）。
            boxes=tuple(every),
            reason=(
                "范围横跨多个域，各域流水线互不相干，没有共同汇点——"
                "硬凑一个中心等于替业务声明一条不存在的拓扑"
            ),
            reason_code="cross_domain",
        )
    if not domains:
        return WiringView(
            domain=scope, reason="这个范围里没有盒子", reason_code="empty",
        )
    domain = domains[0]
    return wirings.get(domain) or WiringView(
        domain=domain, reason="这个域还没装配", reason_code="empty",
    )
