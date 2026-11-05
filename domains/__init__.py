"""domains —— 域声明（盒子之间关系的唯一事实源）

**每个域一个目录**，里面是它的声明（成员 + 边 + 规则）。
业务事实在这里，机制在 `core/`。

## 显式注册，不扫目录

域是**显式声明**的（跟盒子注册表同一条纪律）：
`DOMAIN_REGISTRY` 就是全部的域。

扫目录发现域听起来省事，但那样「多了一个目录就多了一个域」——
**没人决定过它是不是一个域**。域是业务判断，不能靠文件系统的形状决定。

## 独立盒子（不属于任何域）

`STANDALONE_BOXES` 是**显式**的「我不参与任何域」。
**它必须是一份清单，不能靠「不在任何域里」来推断**——
否则「忘了加进域」和「有意独立」就分不开了（见 `docs/domain-declaration.md` §6.2）。
"""

from __future__ import annotations

from typing import Mapping

from core.domain import handoff_channels
from core.models import DomainDeclaration

from domains.learning.domain import DOMAIN as LEARNING_DOMAIN
from domains.wms.domain import DOMAIN as WMS_DOMAIN

#: 域 id → 域声明
DOMAIN_REGISTRY: dict[str, DomainDeclaration] = {
    WMS_DOMAIN.id: WMS_DOMAIN,
    LEARNING_DOMAIN.id: LEARNING_DOMAIN,
}

#: 显式声明「不参与任何域」的盒子。
#:
#: `receipt_capture_box`（票据识别）——它的产出是**给人直接读的**
#: （商家、金额、税额、判据），没有盒子接它，**也没人规定它必须有下游**。
#: 它连 `subject_ref` 都没有 → 不进归档分组。
STANDALONE_BOXES: tuple[str, ...] = ("receipt_capture_box",)


def get_domain(domain_id: str) -> DomainDeclaration | None:
    """按 id 取域声明"""
    return DOMAIN_REGISTRY.get(domain_id)


def domain_ids() -> tuple[str, ...]:
    """全部域 id"""
    return tuple(DOMAIN_REGISTRY)


def handoff_payload(
    domain_id: str,
    to_box: str,
    sources: Mapping[str, str],
) -> dict[str, str]:
    """按域声明里的边，把上游产出接进 `to_box` 的投料口

    Args:
        domain_id: 域 id
        to_box: 下游盒子
        sources: 上游盒子 id → 它的 task_run_id

    Returns:
        `{通道字段名: 上游 task_run_id}`——**通道名来自域声明的 `via`**。

    **编排层不再自己抄一份「来源 + 目标字段」**：抄的那份是第二份事实，
    漂了的后果是运行时才发现接不上（或者更糟：接上了，但接错了对象）。

    **一个通道只能接一份产出**：同一个通道出现两条来源时当场报错——
    那种域要**分多次跑**下游（例：学习域三科都投 `subject_result`，
    成长记录盒就跑三次，一次一科），不能一次全接上。
    """
    declaration = DOMAIN_REGISTRY.get(domain_id)
    if declaration is None:
        raise KeyError(f"没有这个域：{domain_id}")
    out: dict[str, str] = {}
    for edge in handoff_channels(declaration, to_box):
        ref = sources.get(edge.from_box)
        if ref is None:
            continue
        if edge.via in out and out[edge.via] != ref:
            raise ValueError(
                f"域 {domain_id!r} 的 {to_box!r}：通道 {edge.via!r} 有两条来源"
                f"（{edge.from_box!r}）——一个通道只能接一份产出。"
                f"这种域要**分多次跑**下游，不能一次全接上"
            )
        out[edge.via] = ref
    return out


__all__ = [
    "DOMAIN_REGISTRY",
    "STANDALONE_BOXES",
    "get_domain",
    "domain_ids",
    "handoff_payload",
]
