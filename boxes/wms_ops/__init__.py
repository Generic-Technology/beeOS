"""boxes.wms_ops - WMS Ops Box

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、
以及把线上真实状态回写进系统档案。

**成熟度：live**。这条腿是真的：
- 它**接上上游**：测试盒的验收报告（验过没有）+ 开发盒的制品（放哪一版）
- 它**读生产**（只读 GET）——前面几只盒子都在说「应该是什么样」，
  它说「现在实际是什么样」
- 5 条判据的答案没有一条来自自述：审批看上游验收报告、回滚看 git/制品库、
  处置看告警条数对不对得上、线上状态看生产实例答不答话

**没读到不许算读到了**：生产连不上 → `runtime_state_recorded` 判红，
理由写清是连不上还是没配地址——不是产出一份空档案说「已回写」。

**「用不上」和「通过」分开**：没有变更就没有审批率、没有告警就没有处置
记录——那些判据的取值是 "n/a"，看板单独列，不混进通过率。

## 这只盒子的零件在哪

| 文件 | 装什么 |
| --- | --- |
| `definition.py` | `DECLARATION`——这只盒子是什么（声明） |
| `beeline.py` | `COMPONENTS`——每一跳的形状（组件链） |
| `runner.py` | 每一跳的实现（handler）+ handler 表 |
| `schemas.py` | 业务 schema（契约） |

**装配在这里发生**（下面这几行）：

```python
ASSEMBLED = assemble(DECLARATION, COMPONENTS, schemas=ALL_SCHEMAS, handlers=WMS_OPS_HANDLERS)
```

声明自相矛盾的话，装配这一步就抛出来——**装配期就报错**，
不用等到履约跑起来才发现「判据读不到字段」「例外永远不触发」。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.wms_ops.beeline import COMPONENTS
from boxes.wms_ops.definition import DECLARATION
from boxes.wms_ops.runner import RUNNER, WMS_OPS_HANDLERS
from boxes.wms_ops.schemas import ALL_SCHEMAS


# **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError，
# 带着「是哪个 op、哪个 schema、现在是什么、应该是什么」。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=WMS_OPS_HANDLERS,
)

WMS_OPS_BOX: BeeBoxDefinition = ASSEMBLED.definition
WMS_OPS_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "WMS_OPS_BOX",
    "get_definition",
    "WMS_OPS_BEELINE",
    "get_beeline",
    "RUNNER",
    "WMS_OPS_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
