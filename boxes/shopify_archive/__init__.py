"""boxes.shopify_archive - 经营档案盒

把一轮经营的快照、诊断、实验沉淀成**一条**带出处的记录。

**它存结果，不存过程。** 三只上游盒子各自做自己那一段，
过程产物留在它们自己那里。

**它自己复核，不信上游说「我过了」**：每条指标有没有状态、
observed 有没有出处、unverifiable 有没有原因——这里重新算一遍。

**三次投递归一条记录**：同一个 `round_id` 的三段，
在归档里是同一轮经营，不是三件不相干的事。

## 成熟度：live

这只盒子不碰网络——它只接住上游产出、复核、归档。
所以它现在就能真跑、真测。

**但它「活」不代表整条链活了**：上游三只还没在真店上跑过
（`read_reports` + Level 2 受保护客户数据审批还没下来）。

## 文件

| 文件 | 装什么 |
| --- | --- |
| `__init__.py` | 入口（`get_definition` / `get_beeline`）|
| `schemas.py` | 4 个 schema（含三份「我收什么」的形状副本）|
| `definition.py` | `DECLARATION`——这只盒子是什么 |
| `beeline.py` | `COMPONENTS`——三跳的形状 |
| `runner.py` | 三跳的实现（复核在这里）|
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.shopify_archive.beeline import COMPONENTS
from boxes.shopify_archive.definition import DECLARATION
from boxes.shopify_archive.runner import RUNNER, SHOPIFY_ARCHIVE_HANDLERS
from boxes.shopify_archive.schemas import ALL_SCHEMAS


#: **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=SHOPIFY_ARCHIVE_HANDLERS,
)

SHOPIFY_ARCHIVE_BOX: BeeBoxDefinition = ASSEMBLED.definition
SHOPIFY_ARCHIVE_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "SHOPIFY_ARCHIVE_BOX",
    "SHOPIFY_ARCHIVE_BEELINE",
    "get_definition",
    "get_beeline",
    "RUNNER",
    "SHOPIFY_ARCHIVE_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
