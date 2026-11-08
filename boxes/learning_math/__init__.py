"""boxes.learning_math - 数学盒

把一次数学练习记成带出处的学习记录

**成熟度：skeleton（骨架）**。判据已声明、腿还没实现——
所以这只盒子现在跑出来一定是红的。看板上标着「未实现」，
且不计入验收率。

**为什么宁可红**：一只**零判据**的盒子会被判成「全过」——
那是「看起来成功，其实什么也没验」的谎。
骨架盒反过来：真声明它将来要验的东西，产出**不带**那些量 → 全红。

## 这只盒子的零件在哪

| 文件 | 装什么 |
| --- | --- |
| `definition.py` | `DECLARATION`——这只盒子是什么（声明） |
| `beeline.py` | `COMPONENTS`——每一跳的形状（组件链） |
| `runner.py` | 每一跳的实现（handler）+ handler 表 |
| `schemas.py` | 业务 schema（契约） |

**装配在这里发生**（下面这几行）：

```python
ASSEMBLED = assemble(DECLARATION, COMPONENTS, schemas=ALL_SCHEMAS, handlers=LEARNING_MATH_HANDLERS)
```

声明自相矛盾的话，装配这一步就抛出来——**装配期就报错**，
不用等到履约跑起来才发现「判据读不到字段」「例外永远不触发」。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.learning_math.beeline import COMPONENTS
from boxes.learning_math.definition import DECLARATION
from boxes.learning_math.runner import RUNNER, LEARNING_MATH_HANDLERS
from boxes.learning_math.schemas import ALL_SCHEMAS


# **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError，
# 带着「是哪个 op、哪个 schema、现在是什么、应该是什么」。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=LEARNING_MATH_HANDLERS,
)

LEARNING_MATH_BOX: BeeBoxDefinition = ASSEMBLED.definition
LEARNING_MATH_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "LEARNING_MATH_BOX",
    "get_definition",
    "RUNNER",
    "LEARNING_MATH_HANDLERS",
    "LEARNING_MATH_BEELINE",
    "get_beeline",
    "DECLARATION",
    "COMPONENTS",
]
