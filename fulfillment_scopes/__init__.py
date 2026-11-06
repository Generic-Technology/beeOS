"""domains —— 协作边界声明（盒子之间关系的唯一事实源）

**每个协作边界一个目录**，里面是它的声明（成员 + 边 + 规则）。
业务事实在这里，机制在 `core/`。

## 显式注册，不扫目录

协作边界是**显式声明**的（跟盒子注册表同一条纪律）：
`FULFILLMENT_SCOPE_REGISTRY` 就是全部的协作边界。

扫目录发现协作边界听起来省事，但那样「多了一个目录就多了一个协作边界」——
**没人决定过它是不是一个协作边界**。协作边界是业务判断，不能靠文件系统的形状决定。

## 独立盒子（不属于任何协作边界）

`STANDALONE_BOXES` 是**显式**的「我不参与任何协作边界」。
**它必须是一份清单，不能靠「不在任何协作边界里」来推断**——
否则「忘了加进协作边界」和「有意独立」就分不开了（见 `docs/domain-declaration.md` §6.2）。
"""

from __future__ import annotations

from typing import Mapping

from core.fulfillment_scope import handoff_channels
from core.models import FulfillmentScopeDeclaration, EdgeKind

from fulfillment_scopes.learning.scope import SCOPE as LEARNING_SCOPE
from fulfillment_scopes.wms.scope import SCOPE as WMS_SCOPE

#: 协作边界 id → 协作边界声明
FULFILLMENT_SCOPE_REGISTRY: dict[str, FulfillmentScopeDeclaration] = {
    WMS_SCOPE.id: WMS_SCOPE,
    LEARNING_SCOPE.id: LEARNING_SCOPE,
}

#: 显式声明「不参与任何协作边界」的盒子。
#:
#: `receipt_capture_box`（票据识别）——它的产出是**给人直接读的**
#: （商家、金额、税额、判据），没有盒子接它，**也没人规定它必须有下游**。
#: 它连 `subject_ref` 都没有 → 不进归档分组。
STANDALONE_BOXES: tuple[str, ...] = ("receipt_capture_box",)


def get_fulfillment_scope(scope_id: str) -> FulfillmentScopeDeclaration | None:
    """按 id 取协作边界声明"""
    return FULFILLMENT_SCOPE_REGISTRY.get(scope_id)


def fulfillment_scope_ids() -> tuple[str, ...]:
    """全部协作边界的 id"""
    return tuple(FULFILLMENT_SCOPE_REGISTRY)


def fulfillment_scopes_of(box_id: str) -> list[str]:
    """这只盒子在哪些边界里

    **成员是声明的**（`FulfillmentScopeDeclaration.boxes`），不是从接线推出来的——
    「档案盒属于 WMS 协作边界」是**业务判断**，靠接线推不出来
    （它一条交接边都没有）。
    """
    return [did for did, d in FULFILLMENT_SCOPE_REGISTRY.items() if box_id in d.boxes]


def box_wiring(box_id: str) -> dict[str, list[str]]:
    """这只盒子的接线——**从协作边界声明读**（不是从盒子自己读）

    按 `kind` 分成三组，每组两个方向：

    | 组 | 含义 |
    |---|---|
    | `handoff_from` / `handoffs` | 谁把产出**交给我** / 我把产出**交给谁**（数据交接，下游真的接产出）|
    | `feedback_from` / `feedback` | 谁把结论**反馈给我** / 我把结论**反馈给谁**（闭环，开新一轮）|
    | `archive` | 我的产物**进谁的档案**（归档归属——档案盒不收上游产出，所以没有「来源」方向）|

    **为什么不再从盒子自己读**：接线是**协作边界**的事实。
    盒子自己那份接线声明已经退休——
    照抄它会漏掉真实依赖（8 条真实交接里只声明过 5 条），
    而两份事实并存必然漂，漂了不报错。

    **接线不再混成一个列表**：「交给下游验」和「产物进档案」是两件事，
    混在一起时读者会以为档案盒接了这份产出——它根本不接。
    """
    out: dict[str, list[str]] = {
        "handoff_from": [],
        "handoffs": [],
        "feedback_from": [],
        "feedback": [],
        "archive": [],
    }
    for declaration in FULFILLMENT_SCOPE_REGISTRY.values():
        for edge in declaration.edges:
            if edge.kind == EdgeKind.handoff:
                if edge.from_box == box_id:
                    out["handoffs"].append(edge.to_box)
                if edge.to_box == box_id:
                    out["handoff_from"].append(edge.from_box)
            elif edge.kind == EdgeKind.feedback:
                if edge.from_box == box_id:
                    out["feedback"].append(edge.to_box)
                if edge.to_box == box_id:
                    out["feedback_from"].append(edge.from_box)
            elif edge.kind == EdgeKind.archive and edge.from_box == box_id:
                out["archive"].append(edge.to_box)
    return out


def handoff_payload(
    fulfillment_scope_id: str,
    to_box: str,
    sources: Mapping[str, str],
) -> dict[str, str]:
    """按协作边界声明里的边，把上游产出接进 `to_box` 的投料口

    Args:
        fulfillment_scope_id: 协作边界的 id
        to_box: 下游盒子
        sources: 上游盒子 id → 它的 task_run_id

    Returns:
        `{通道字段名: 上游 task_run_id}`——**通道名来自协作边界声明的 `via`**。

    **编排层不再自己抄一份「来源 + 目标字段」**：抄的那份是第二份事实，
    漂了的后果是运行时才发现接不上（或者更糟：接上了，但接错了对象）。

    **一个通道只能接一份产出**：同一个通道出现两条来源时当场报错——
    那种协作边界要**分多次跑**下游（例：学习协作边界三科都投 `subject_result`，
    成长记录盒就跑三次，一次一科），不能一次全接上。
    """
    declaration = FULFILLMENT_SCOPE_REGISTRY.get(fulfillment_scope_id)
    if declaration is None:
        raise KeyError(f"没有这个协作边界：{fulfillment_scope_id}")
    out: dict[str, str] = {}
    for edge in handoff_channels(declaration, to_box):
        ref = sources.get(edge.from_box)
        if ref is None:
            continue
        if edge.via in out and out[edge.via] != ref:
            raise ValueError(
                f"协作边界 {fulfillment_scope_id!r} 的 {to_box!r}：通道 {edge.via!r} 有两条来源"
                f"（{edge.from_box!r}）——一个通道只能接一份产出。"
                f"这种协作边界要**分多次跑**下游，不能一次全接上"
            )
        out[edge.via] = ref
    return out


__all__ = [
    "FULFILLMENT_SCOPE_REGISTRY",
    "STANDALONE_BOXES",
    "get_fulfillment_scope",
    "fulfillment_scope_ids",
    "handoff_payload",
    "box_wiring",
    "fulfillment_scopes_of",
]
