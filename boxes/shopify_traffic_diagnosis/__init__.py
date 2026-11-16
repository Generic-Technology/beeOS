"""boxes.shopify_traffic_diagnosis - 获客诊断盒

判断这一轮流量**断在哪一段**，给出一个主问题假设、一个目标客群、一个优先渠道。

**断点分四段**：没有曝光 / 有曝光没点击 / 有点击没 session / 有 session 没商品浏览。

**结论必须带证据引用**——「流量少」不是证据，「qualified_sessions = 148」才是。

**未知项必须写出来**：分不出来的段就说是分不出来，不能装作全知。

## 成熟度：skeleton

判据和形状都声明好了，**腿还没实现**（断点判定走规则还是走模型，没拍板）。
第 2 跳现在直接报错——一只永远返回空结果的盒子，
读者会以为它在守着。

## 文件

| 文件 | 装什么 |
| --- | --- |
| `__init__.py` | 入口（`get_definition` / `get_beeline`）|
| `schemas.py` | 4 个 schema |
| `definition.py` | `DECLARATION` |
| `beeline.py` | `COMPONENTS`——三跳 |
| `runner.py` | 三跳的实现（第 2 跳是显式的未实现）|
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.shopify_traffic_diagnosis.beeline import COMPONENTS
from boxes.shopify_traffic_diagnosis.definition import DECLARATION
from boxes.shopify_traffic_diagnosis.runner import (
    RUNNER,
    SHOPIFY_TRAFFIC_DIAGNOSIS_HANDLERS,
)
from boxes.shopify_traffic_diagnosis.schemas import ALL_SCHEMAS


#: **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=SHOPIFY_TRAFFIC_DIAGNOSIS_HANDLERS,
)

SHOPIFY_TRAFFIC_DIAGNOSIS_BOX: BeeBoxDefinition = ASSEMBLED.definition
SHOPIFY_TRAFFIC_DIAGNOSIS_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "SHOPIFY_TRAFFIC_DIAGNOSIS_BOX",
    "SHOPIFY_TRAFFIC_DIAGNOSIS_BEELINE",
    "get_definition",
    "get_beeline",
    "RUNNER",
    "SHOPIFY_TRAFFIC_DIAGNOSIS_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
