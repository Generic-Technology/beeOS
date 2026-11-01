"""boxes.learning_growth - 成长记录盒

把每一次学习记成一条带出处的成长记录

**成熟度：skeleton（骨架）**。判据已声明、腿还没实现——
所以这只盒子现在跑出来一定是红的。看板上标着「未实现」，
且不计入验收率。

**为什么宁可红**：一只**零判据**的盒子会被判成「全过」——
那是「看起来成功，其实什么也没验」的谎。
骨架盒反过来：真声明它将来要验的东西，产出**不带**那些量 → 全红。
"""

from __future__ import annotations

from boxes.learning_growth.definition import LEARNING_GROWTH_BOX, get_definition
from boxes.learning_growth.runner import RUNNER, LEARNING_GROWTH_HANDLERS
from boxes.learning_growth.beeline import LEARNING_GROWTH_BEELINE, get_beeline

__all__ = [
    "LEARNING_GROWTH_BOX",
    "get_definition",
    "RUNNER",
    "LEARNING_GROWTH_HANDLERS",
    "LEARNING_GROWTH_BEELINE",
    "get_beeline",
]
