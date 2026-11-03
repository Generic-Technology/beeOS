"""boxes.learning_growth - 成长记录盒

把每一次学习记成一条带出处的成长记录。

**它存结果，不存过程**：数学 / 语文 / 英语三只学科盒各自做自己那一段的
过程（看这次练习、算得分率），过程产物留在它们自己那里；
本盒只沉淀**一条条记录**——这就是车宥安的成长档案。

**档案里没有一个裸数字。** 每条记录都带着：
学的是哪一科、这次做了什么、掌握度是多少、
**这次是基于什么记的**（`evidence`）、
**是看到的还是听说的**（`source_kind`）。

**为什么分 `observed` 和 `reported`**：
「批改过的作业上 92 分」和「今天练了半小时」是两种事实，
混在一起的话，半年后翻这份档案就分不清哪些是**看见的**、哪些是**听说的**。
分不清的档案 = 看着完整，其实不能当依据。

**它是 Learning 域的唯一汇点**：三只学科盒的产出最终都跟这份档案对上。
**成熟度：live**。腿是真的：投料 → 看照片/取投料 → 真干活 → 装出带出处的一条记录。

## 这只盒子的零件在哪

| 文件 | 装什么 |
| --- | --- |
| `definition.py` | `DECLARATION`——这只盒子是什么（声明） |
| `beeline.py` | `COMPONENTS`——每一跳的形状（组件链） |
| `runner.py` | 每一跳的实现（handler）+ handler 表 |
| `schemas.py` | 业务 schema（契约） |

**装配在这里发生**（下面这几行）：

```python
ASSEMBLED = assemble(DECLARATION, COMPONENTS, schemas=ALL_SCHEMAS, handlers=LEARNING_GROWTH_HANDLERS)
```

声明自相矛盾的话，装配这一步就抛出来——**装配期就报错**，
不用等到履约跑起来才发现「判据读不到字段」「例外永远不触发」。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.learning_growth.beeline import COMPONENTS
from boxes.learning_growth.definition import DECLARATION
from boxes.learning_growth.runner import RUNNER, LEARNING_GROWTH_HANDLERS
from boxes.learning_growth.schemas import ALL_SCHEMAS


# **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError，
# 带着「是哪个 op、哪个 schema、现在是什么、应该是什么」。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=LEARNING_GROWTH_HANDLERS,
)

LEARNING_GROWTH_BOX: BeeBoxDefinition = ASSEMBLED.definition
LEARNING_GROWTH_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "LEARNING_GROWTH_BOX",
    "get_definition",
    "LEARNING_GROWTH_BEELINE",
    "get_beeline",
    "RUNNER",
    "LEARNING_GROWTH_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
