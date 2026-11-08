"""boxes.learning_english - 英语盒

把一次英语练习记成一条带出处的学习记录

**成熟度：live**。腿是真的：投料 → 算听写得分率 → 装出带出处的一条学习记录。

**这一盒只干一件事**：把「这次练习的事实」变成一条**能被追溯**的学习记录。
它不判断孩子学得好不好——那是家里和老师的事；
它只保证记下来的那条**有出处、算数、装得进档案**。

## 判据为什么是这三条

- `mastery >= 0` / `mastery <= 1` —— 掌握度是个**比率**，越界就是投料错了
- `ratio_exact` —— 掌握度**确实是本盒口径的得分率**：
  装配跳拿原始分与满分复算一遍，跟工作跳算出来的那份对不上就判红。
  这一条防的是「掌握度被写成了一个跟分数无关的数」（比如写死 0.8）。

## 例外为什么是这两条

- `mastery > 1` —— 得分超过满分：投料错了，升级人工
- `ratio_exact == false` —— 两跳算出来的数对不上：升级人工
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
ASSEMBLED = assemble(DECLARATION, COMPONENTS, schemas=ALL_SCHEMAS, handlers=LEARNING_ENGLISH_HANDLERS)
```

声明自相矛盾的话，装配这一步就抛出来——**装配期就报错**，
不用等到履约跑起来才发现「判据读不到字段」「例外永远不触发」。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.learning_english.beeline import COMPONENTS
from boxes.learning_english.definition import DECLARATION
from boxes.learning_english.runner import RUNNER, LEARNING_ENGLISH_HANDLERS
from boxes.learning_english.schemas import ALL_SCHEMAS


# **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError，
# 带着「是哪个 op、哪个 schema、现在是什么、应该是什么」。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=LEARNING_ENGLISH_HANDLERS,
)

LEARNING_ENGLISH_BOX: BeeBoxDefinition = ASSEMBLED.definition
LEARNING_ENGLISH_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "LEARNING_ENGLISH_BOX",
    "get_definition",
    "LEARNING_ENGLISH_BEELINE",
    "get_beeline",
    "RUNNER",
    "LEARNING_ENGLISH_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
