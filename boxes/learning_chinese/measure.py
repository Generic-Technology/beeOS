"""boxes.learning_chinese.measure - 语文盒 的实测指标采集

`definition.metrics` 里写的是 target（目标）。目标是承诺，不是事实。
这一层负责从履约历史里把 actual 算出来，让看板显示的每个数字都有出处。

**骨架盒不声明指标**，所以这里没有采集算法。

**转成 live 时**：在 `definition.metrics` 里声明指标，
在这里给**每一个**指标一个采集算法。声明了却没采集器 =
看板上拿不到那个数（`tests/test_metrics.py` 会红）。
"""

from __future__ import annotations

from core.metrics import Computer


# 骨架盒没有指标 → 没有采集算法。键是指标名，值是算法。
COMPUTERS: dict[str, Computer] = {}
