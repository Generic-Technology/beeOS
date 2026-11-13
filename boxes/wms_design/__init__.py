"""boxes.wms_design - 业务设计履约盒

Software Line 第一只盒子——按结构化需求产出可验证的业务设计包。

业务责任：
- 输入：业务需求集（结构化：每条需求带 id / type / priority）
- 输出：业务设计包（含设计元素 + 机械可验证据）
- 不负责：模型的人工业务方确认（独立阶段，由业务方手动 accept）

机械可验 5 项 acceptance：
- requirement_coverage == 100   （每条需求都有 design 元素对应）
- rule_consistency == true       （规则无矛盾）
- reference_integrity == true    （引用一致）
- structural_compliance == true  （结构合规）
- metrics_defined == true        （指标明确）
                                 ——没有指标需求的批次是 n/a，不是 false

业务语义是真的（结构化需求 → 设计元素 + 证据）；
基础设施可以是假的（每个 op 是 mock，下一阶段实现真实 design engine）。

## 这只盒子的零件在哪

| 文件 | 装什么 |
| --- | --- |
| `definition.py` | `DECLARATION`——这只盒子是什么（声明） |
| `beeline.py` | `COMPONENTS`——每一跳的形状（组件链） |
| `runner.py` | 每一跳的实现（handler）+ handler 表 |
| `schemas.py` | 业务 schema（契约） |
| `measure.py` | 实测指标采集 |

**装配在这里发生**（下面这一行）：

```python
ASSEMBLED = assemble(DECLARATION, COMPONENTS, schemas=ALL_SCHEMAS, handlers=MODEL_HANDLERS)
```

声明自相矛盾的话，装配这一步就抛出来——**装配期就报错**，
不用等到履约跑起来才发现判据读不到字段、例外永远不触发。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.wms_design.beeline import COMPONENTS
from boxes.wms_design.definition import DECLARATION
from boxes.wms_design.plugins import register_design_output_check
from boxes.wms_design.runner import MODEL_HANDLERS, RUNNER
from boxes.wms_design.schemas import ALL_SCHEMAS

# **业务插件的登记必须在装配之前**——
# 装配器要认得这个插件类型（I5：类型已登记），否则盒子装不出来。
register_design_output_check()


# **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError，
# 带着「是哪个 op、哪个 schema、现在是什么、应该是什么」。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=MODEL_HANDLERS,
)

WMS_DESIGN_BOX: BeeBoxDefinition = ASSEMBLED.definition
WMS_DESIGN_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    """返回业务设计履约盒 Definition"""
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    """返回业务设计履约 beeline"""
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "WMS_DESIGN_BOX",
    "get_definition",
    "WMS_DESIGN_BEELINE",
    "get_beeline",
    "RUNNER",
    "MODEL_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
