"""boxes.shopify_acquisition_experiment - 获客实验盒

设计并记录一轮获客实验：场景、落地页、变更项、成本上限、起止时间、停止条件、归因方式。

**一轮只改一个变量、一个渠道。**

**首轮是自然渠道内容实验**：这家店 100% 的销售来自自然搜索，
自然渠道已被验证；付费渠道连基线都还没有。

**它不执行任何写操作**：只出方案。改价 / 改库存 / 发优惠 / 投广告
都不在它的职责里。

## 成熟度：skeleton

形状与判据声明好了，**腿还没实现**（实验设计走规则还是走模型，没拍板）。
第 2 跳现在直接报错——一只永远返回空方案的盒子，
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

from boxes.shopify_acquisition_experiment.beeline import COMPONENTS
from boxes.shopify_acquisition_experiment.definition import DECLARATION
from boxes.shopify_acquisition_experiment.runner import (
    RUNNER,
    SHOPIFY_ACQUISITION_EXPERIMENT_HANDLERS,
)
from boxes.shopify_acquisition_experiment.schemas import ALL_SCHEMAS


#: **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=SHOPIFY_ACQUISITION_EXPERIMENT_HANDLERS,
)

SHOPIFY_ACQUISITION_EXPERIMENT_BOX: BeeBoxDefinition = ASSEMBLED.definition
SHOPIFY_ACQUISITION_EXPERIMENT_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "SHOPIFY_ACQUISITION_EXPERIMENT_BOX",
    "SHOPIFY_ACQUISITION_EXPERIMENT_BEELINE",
    "get_definition",
    "get_beeline",
    "RUNNER",
    "SHOPIFY_ACQUISITION_EXPERIMENT_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
