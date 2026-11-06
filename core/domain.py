"""core.domain —— 域装配器：把域声明装成产物，**装配期就报错**

域声明是**盒子之间关系的唯一事实源**（住在 `domains/<域>/`）。
本模块把它装成 `AssembledDomain`：

```
DomainDeclaration  ──assemble_domain──▶  AssembledDomain
                                          ├─ declaration   装好的声明
                                          ├─ wiring        接线投影（派生）
                                          ├─ rules_reading 域级规则的读数
                                          └─ problems      装配期结论
```

## 为什么要「装配期就报错」

域声明是**手写**的（成员清单 + 边 + 规则），手写就会写错。
而域声明错了的代价特别大：**看板照常渲染**，图看起来完全正常，
只是有一只盒子接错了对象——**没有任何东西会报错**。

所以跟盒子协议一样：**装配期就拒**。
参照 `core/scaffold.py` 的做法：一次报齐所有问题，不改一个报一次。

## 判定分三层（别混）

| 层 | 判什么 | 住在 |
|---|---|---|
| **① 声明合法性** | 这份声明本身对不对（D1–D15）| `core/decl_checks.py` |
| **② 域业务规则** | **这个域**自己额外要求的（`rules`）| 本模块 |
| **③ 展示适用性** | 能不能画成环/放射 | `core/wiring.py` |

**「汇点唯一」属于 ③，不属于 ①**——那是画图的要求，不是声明合法性的要求。

## 规则读数是「声明层」的

`rules_reading` 说的是**声明完整不完整**，**不是**「事实发生了没有」。
比如 `all_archived` 读数 5/5，证明的是**五只盒子都写了归档归属声明**，
**不证明**产物进了档案——真归档由 `runtime/store.py` 的 `subject_ref` 机制负责
（见 `docs/domain-declaration.md` §1.7）。

**声明层与事实层必须分开看。**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from core.decl_checks import (
    check_domain_boxes,
    check_domain_channels,
    check_domain_edges,
    check_domain_rules,
)
from core.models import (
    BoxFacts,
    DomainDeclaration,
    Edge,
    EdgeKind,
    Rule,
    RuleKind,
)
from core.wiring import WiringView, derive_wiring


@dataclass(frozen=True)
class DomainProblem:
    """装配期挑出的一条问题"""

    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - 只是好用
        return f"{self.code} {self.message}"


@dataclass(frozen=True)
class RuleReading:
    """一条域级规则的读数

    `passed` / `total` 是**声明层**的数；`exempt` 是被点名豁免的盒子。
    """

    kind: str
    passed: int
    total: int
    exempt: tuple[str, ...] = ()
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.passed == self.total

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "passed": self.passed,
            "total": self.total,
            "exempt": list(self.exempt),
            "detail": self.detail,
            "ok": self.ok,
        }

    def __str__(self) -> str:
        tail = f"  (豁免 {len(self.exempt)}: {', '.join(self.exempt)})" if self.exempt else ""
        return f"{self.kind:<26} {self.passed}/{self.total}{tail}"


@dataclass(frozen=True)
class AssembledDomain:
    """装好的域——与 ``AssembledBox`` 对称"""

    declaration: DomainDeclaration
    wiring: WiringView
    rules_reading: tuple[RuleReading, ...] = ()
    problems: tuple[DomainProblem, ...] = ()

    @property
    def ok(self) -> bool:
        """声明检查与域业务规则全部通过。"""
        return not self.problems and all(r.ok for r in self.rules_reading)

    def to_dict(self) -> dict:
        return {
            "id": self.declaration.id,
            "display_name": self.declaration.display_name,
            "boxes": list(self.declaration.boxes),
            "wiring": self.wiring.to_dict(),
            "rules_reading": [r.to_dict() for r in self.rules_reading],
            "problems": [{"code": p.code, "message": p.message} for p in self.problems],
            "ok": self.ok,
        }


# ============================================================
# 规则读数
# ============================================================

def _exempted(rule: Rule) -> set[str]:
    return {ex.box_id for ex in rule.exemptions}


def _evaluate_rule(
    rule: Rule,
    declaration: DomainDeclaration,
    wiring: WiringView,
) -> RuleReading:
    """算一条域级规则的读数"""
    exempt = _exempted(rule)
    members = [b for b in declaration.boxes if b not in exempt]

    if rule.kind == RuleKind.all_archived:
        # 除豁免外，每只**非汇点**成员都要有一条 archive 边指向汇点
        center = wiring.center
        if center is None:
            return RuleReading(
                kind=rule.kind.value,
                passed=0,
                total=len(members),
                exempt=tuple(sorted(exempt)),
                detail="域里没有汇点——归档归属指向谁？",
            )
        non_center = [b for b in members if b != center]
        archive_to_center = {
            e.from_box for e in declaration.edges
            if e.kind == EdgeKind.archive and e.to_box == center
        }
        passed = [b for b in non_center if b in archive_to_center]
        return RuleReading(
            kind=rule.kind.value,
            passed=len(passed),
            total=len(non_center),
            exempt=tuple(sorted(exempt)),
            detail=(
                f"{len(non_center)} 只非汇点成员，{len(passed)} 只声明了归档到 {center}"
                + (f"；缺：{'、'.join(sorted(set(non_center) - set(passed)))}"
                   if set(non_center) - set(passed) else "")
            ),
        )

    if rule.kind == RuleKind.all_handoff:
        # 每只非汇点成员都要**在交接链上**——即在本域至少有一条 handoff 边
        #（作为上游**或**下游）。
        #
        # **为什么不是「都要有出边」**：链的末端那只（分析盒）没有交接出边——
        # 它的出边是归档和闭环反馈。**要求出边会把合法结构判成错。**
        # 而「入边或出边至少有一条」既抓得住「新加的盒子忘了接进链」
        # （它一条边都没有），又不挑链的末端。
        center = wiring.center
        non_center = [b for b in members if b != center]
        chain_nodes: set[str] = set()
        for e in declaration.edges:
            if e.kind != EdgeKind.handoff:
                continue
            if e.from_box in declaration.boxes:
                chain_nodes.add(e.from_box)
            if e.to_box in declaration.boxes:
                chain_nodes.add(e.to_box)
        passed = [b for b in non_center if b in chain_nodes]
        return RuleReading(
            kind=rule.kind.value,
            passed=len(passed),
            total=len(non_center),
            exempt=tuple(sorted(exempt)),
            detail=(
                f"{len(non_center)} 只非汇点成员，{len(passed)} 只在交接链上"
                + (f"；不在链上：{'、'.join(sorted(set(non_center) - set(passed)))}"
                   if set(non_center) - set(passed) else "")
            ),
        )

    if rule.kind == RuleKind.each_handoff_has_channel:
        # 每条 handoff 边都要有通道
        handoffs = [e for e in declaration.edges if e.kind == EdgeKind.handoff]
        with_channel = [e for e in handoffs if e.via]
        return RuleReading(
            kind=rule.kind.value,
            passed=len(with_channel),
            total=len(handoffs),
            exempt=(),
            detail=f"{len(handoffs)} 条交接边，{len(with_channel)} 条声明了通道",
        )

    return RuleReading(
        kind=rule.kind.value, passed=0, total=0, detail="没有这个规则"
    )


# ============================================================
# 装配
# ============================================================

def assemble_domain(
    declaration: DomainDeclaration,
    boxes: Mapping[str, BoxFacts],
) -> AssembledDomain:
    """把一份域声明装成 `AssembledDomain`

    Args:
        declaration: 域声明（业务事实）
        boxes: 所有盒子的事实（由调用方从注册表收集）——
            `core` 不认识注册表，也不认识任何一只盒子

    Returns:
        装好的域。**`problems` 非空 = 装配期就拒**，
        里面有它就等于「这份声明不能用」。
    """
    problems: list[DomainProblem] = []

    for message in check_domain_boxes(declaration, boxes):
        problems.append(DomainProblem("D1", message))
    for message in check_domain_edges(declaration, boxes):
        problems.append(DomainProblem("D7", message))
    for message in check_domain_channels(declaration, boxes):
        problems.append(DomainProblem("D3", message))
    for message in check_domain_rules(declaration, boxes):
        problems.append(DomainProblem("D14", message))

    # 接线投影（派生层）：按 kind 分类拓扑
    wiring = derive_wiring(
        declaration.id,
        declaration.boxes,
        [(e.from_box, e.to_box, e.kind.value) for e in declaration.edges],
    )

    # 域级规则的读数（声明层）
    readings = tuple(
        _evaluate_rule(rule, declaration, wiring) for rule in declaration.rules
    )
    for reading in readings:
        if not reading.ok:
            problems.append(DomainProblem(
                "R1",
                f"域规则 {reading.kind} 未通过（{reading.passed}/{reading.total}）："
                f"{reading.detail}",
            ))

    # 展示适用性（③）：画不成图**不是**声明不合法，
    # 但它得被看见——否则看板上一片空白，没人知道为什么。
    if wiring.reason and wiring.reason_code not in ("star",):
        problems.append(
            DomainProblem("W1", f"域 {declaration.id!r} 画不成场图：{wiring.reason}")
        )

    return AssembledDomain(
        declaration=declaration,
        wiring=wiring,
        rules_reading=readings,
        problems=tuple(problems),
    )


# ============================================================
# 编排层读边
# ============================================================

def handoff_channels(
    declaration: DomainDeclaration,
    to_box: str,
) -> tuple[Edge, ...]:
    """接进 `to_box` 的那些交接边（按声明顺序）

    **这就是编排层要填的那几个通道**——`via` 就是下游投料里的字段名。
    编排层不再自己抄一份「来源 + 目标字段」：
    抄的那份会跟域声明漂，而漂了的后果是**运行时才发现接不上**
    （投料被拒，或者更糟：接上了但接错了对象）。
    """
    return tuple(
        e for e in declaration.edges
        if e.kind == EdgeKind.handoff and e.to_box == to_box and e.via
    )
