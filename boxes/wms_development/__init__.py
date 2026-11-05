"""boxes.wms_development - WMS Development Box

WMS 开发盒——**按设计包改代码**：读方案 → 定位源码 → 改 → 编译 → 制品。

**成熟度：live**。这条腿是真的：它真的起隔离工作区（git worktree）、
真的让模型改代码、真的编译、真的跑仓库自带的测试套件。

判据的答案全部来自**外部神谕**（编译器 / 测试套件 / git），
没有一条来自这只盒子自述——这是「按方案生成代码」这条路能站住的前提。

**工作区必须隔离**：开发盒会真的动文件，动的是 WMS 主仓的话，
一次跑失败的履约就把别人的工作现场弄脏了。所以动的是 worktree，
提交落回主仓的一个分支（`beeos/dev-<sha>`），交付物追得回去。

## 这只盒子的零件在哪

| 文件 | 装什么 |
| --- | --- |
| `definition.py` | `DECLARATION`——这只盒子是什么（声明） |
| `beeline.py` | `COMPONENTS`——每一跳的形状（组件链） |
| `runner.py` | 每一跳的实现（handler）+ handler 表 |
| `schemas.py` | 业务 schema（契约） |

**装配在这里发生**（下面这几行）：

```python
ASSEMBLED = assemble(DECLARATION, COMPONENTS, schemas=ALL_SCHEMAS, handlers=WMS_DEVELOPMENT_HANDLERS)
```

声明自相矛盾的话，装配这一步就抛出来——**装配期就报错**，
不用等到履约跑起来才发现「判据读不到字段」「例外永远不触发」。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.wms_development.beeline import COMPONENTS
from boxes.wms_development.definition import DECLARATION
from boxes.wms_development.runner import RUNNER, WMS_DEVELOPMENT_HANDLERS
from boxes.wms_development.schemas import ALL_SCHEMAS


# **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError，
# 带着「是哪个 op、哪个 schema、现在是什么、应该是什么」。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=WMS_DEVELOPMENT_HANDLERS,
)

WMS_DEVELOPMENT_BOX: BeeBoxDefinition = ASSEMBLED.definition
WMS_DEVELOPMENT_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "WMS_DEVELOPMENT_BOX",
    "get_definition",
    "WMS_DEVELOPMENT_BEELINE",
    "get_beeline",
    "RUNNER",
    "WMS_DEVELOPMENT_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
