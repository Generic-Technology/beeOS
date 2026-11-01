"""boxes.learning_math - 数学盒

把一次数学练习记成带出处的学习事件

**成熟度：skeleton（骨架）**。判据已声明、腿还没实现——
所以这只盒子现在跑出来一定是红的。看板上标着「未实现」，
且不计入验收率。

**为什么宁可红**：一只**零判据**的盒子会被判成「全过」——
那是「看起来成功，其实什么也没验」的谎。
骨架盒反过来：真声明它将来要验的东西，产出**不带**那些量 → 全红。
"""

from __future__ import annotations

from boxes.learning_math.definition import LEARNING_MATH_BOX, get_definition
from boxes.learning_math.runner import RUNNER, LEARNING_MATH_HANDLERS
from boxes.learning_math.beeline import LEARNING_MATH_BEELINE, get_beeline

__all__ = [
    "LEARNING_MATH_BOX",
    "get_definition",
    "RUNNER",
    "LEARNING_MATH_HANDLERS",
    "LEARNING_MATH_BEELINE",
    "get_beeline",
]
