"""boxes.wms_analysis - WMS Analysis Box

WMS 线上效果分析盒——**上线之后回头验需求**：拿真实运行数据，跟当初那条
需求声明的预期值逐项比。落差直接变成下一轮需求的种子。

**成熟度：skeleton（骨架）**。判据已声明、腿还没实现——所以这只盒子
现在跑出来一定是红的。看板上标着「未实现」，且不计入验收率。

骨架盒的做法是**反过来**的：真声明判据（它将来要验的东西），
产出**不带**这些 metric → `evaluate_acceptance` 判 `None != True`
→ 全红 → REJECTED。

**为什么不能空声明判据**：`evaluate_acceptance` 里是
`if not failed: ACCEPTED`。零条判据的盒子会被判成「全过」——
那就是「看起来成功，其实什么也没验」的谎。

**它跟别的 WMS 盒不一样的地方**：闭环口。ops ─交接→ 本盒 ─反馈→ 需求盒。
反馈边声明在 `placement.feedback_into`（不是 `feeds_into`），
因为需求盒不会去验分析结论——分析结论是拿去**开新一轮需求**的。
分开声明还有个副作用是好的：反馈边不进拓扑排序，
所以「需求 → … → 运维 → 分析 → 需求」这个环不会让主干排序成环。

## 这只盒子的零件在哪

| 文件 | 装什么 |
| --- | --- |
| `definition.py` | `DECLARATION`——这只盒子是什么（声明） |
| `beeline.py` | `COMPONENTS`——每一跳的形状（组件链） |
| `runner.py` | 每一跳的实现（handler）+ handler 表 |
| `schemas.py` | 业务 schema（契约） |

**装配在这里发生**（下面这几行）：

```python
ASSEMBLED = assemble(DECLARATION, COMPONENTS, schemas=ALL_SCHEMAS, handlers=WMS_ANALYSIS_HANDLERS)
```

声明自相矛盾的话，装配这一步就抛出来——**装配期就报错**，
不用等到履约跑起来才发现「判据读不到字段」「例外永远不触发」。
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.wms_analysis.beeline import COMPONENTS
from boxes.wms_analysis.definition import DECLARATION
from boxes.wms_analysis.runner import RUNNER, WMS_ANALYSIS_HANDLERS
from boxes.wms_analysis.schemas import ALL_SCHEMAS


# **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError，
# 带着「是哪个 op、哪个 schema、现在是什么、应该是什么」。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=WMS_ANALYSIS_HANDLERS,
)

WMS_ANALYSIS_BOX: BeeBoxDefinition = ASSEMBLED.definition
WMS_ANALYSIS_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "WMS_ANALYSIS_BOX",
    "get_definition",
    "WMS_ANALYSIS_BEELINE",
    "get_beeline",
    "RUNNER",
    "WMS_ANALYSIS_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
