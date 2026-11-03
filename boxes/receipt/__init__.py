"""boxes.receipt - 小票识别履约盒

输入一张收银小票照片，输出结构化小票 + 6 项机械校验证据。
模型只负责看图抄字，算术一律由盒子自己算——
「模型把数字凑平」这条路是堵死的。

## 这只盒子的零件在哪

| 文件 | 装什么 |
| --- | --- |
| `definition.py` | `DECLARATION`——这只盒子是什么（声明） |
| `beeline.py` | `COMPONENTS`——每一跳的形状（组件链） |
| `runner.py` | 每一跳的实现（handler）+ handler 表 |
| `schemas.py` | 业务 schema（契约） |

**装配在这里发生**（下面这几行）：

```python
ASSEMBLED = assemble(DECLARATION, COMPONENTS, schemas=ALL_SCHEMAS, handlers=RECEIPT_HANDLERS)
```

声明自相矛盾的话，装配这一步就抛出来——**装配期就报错**，
不用等到履约跑起来才发现「判据读不到字段」「例外永远不触发」。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.receipt.beeline import COMPONENTS
from boxes.receipt.definition import DECLARATION
from boxes.receipt.runner import RUNNER, RECEIPT_HANDLERS
from boxes.receipt.schemas import ALL_SCHEMAS


# **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError，
# 带着「是哪个 op、哪个 schema、现在是什么、应该是什么」。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=RECEIPT_HANDLERS,
)

RECEIPT_CAPTURE_BOX: BeeBoxDefinition = ASSEMBLED.definition
RECEIPT_CAPTURE_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "RECEIPT_CAPTURE_BOX",
    "get_definition",
    "RECEIPT_CAPTURE_BEELINE",
    "get_beeline",
    "RUNNER",
    "RECEIPT_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
