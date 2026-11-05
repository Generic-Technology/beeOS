"""boxes.wms_requirement - 需求捕获履约盒

自然语言 → 结构化业务需求集（设计盒的输入）。

为什么要独立成盒（而不是设计盒的一个 op）：
- 设计盒的 task in 已经是结构化的 BusinessRequirementSet，它不做自然语言理解
- 这一步的验收判据与设计盒**零重叠**（判据不同 = 值得独立）
- 原先它是 kanban/trigger.py 里的一个影子函数：没有 task_run、失败静默
- 抽出的需求是设计盒的输入，所以设计盒的 schema 归设计盒所有（消费者定契约）

机械可验 4 项 acceptance：
- contract_compliance == true   （需求集过 schema 契约）
- source_coverage      >= 100   （原文每句都被覆盖）
- trace_integrity     == true   （每条需求溯源到真实句子）
- no_hallucination    == true   （需求描述是原文片段，不是编造）

**已知盲区**：type_boundary（rule / metric / process 的边界）无法机械验证——
LLM 实测仍有边界错判。这条走人工确认，不写进 acceptance 假装机器能判。

## 这只盒子的零件在哪

| 文件 | 装什么 |
| --- | --- |
| `definition.py` | `DECLARATION`——这只盒子是什么（声明） |
| `beeline.py` | `COMPONENTS`——每一跳的形状（组件链） |
| `runner.py` | 每一跳的实现（handler）+ handler 表 |
| `schemas.py` | 业务 schema（契约） |

**装配在这里发生**（下面这几行）：

```python
ASSEMBLED = assemble(DECLARATION, COMPONENTS, schemas=ALL_SCHEMAS, handlers=CAPTURE_HANDLERS)
```

声明自相矛盾的话，装配这一步就抛出来——**装配期就报错**，
不用等到履约跑起来才发现「判据读不到字段」「例外永远不触发」。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.wms_requirement.beeline import COMPONENTS
from boxes.wms_requirement.definition import DECLARATION
from boxes.wms_requirement.runner import RUNNER, CAPTURE_HANDLERS
from boxes.wms_requirement.schemas import ALL_SCHEMAS


# **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError，
# 带着「是哪个 op、哪个 schema、现在是什么、应该是什么」。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=CAPTURE_HANDLERS,
)

WMS_REQUIREMENT_BOX: BeeBoxDefinition = ASSEMBLED.definition
WMS_REQUIREMENT_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "WMS_REQUIREMENT_BOX",
    "get_definition",
    "WMS_REQUIREMENT_BEELINE",
    "get_beeline",
    "RUNNER",
    "CAPTURE_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
