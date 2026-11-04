"""
workshop - BeeBox Workshop：做 BeeBox 的工作台

**和看板的区别**（为什么分成两个目录）：

| | 看板 | 工作台 |
|---|---|---|
| 看什么 | 故障 | 盒子本身 |
| 回答 | 「现在哪儿坏了」 | 「这只盒子声明得对不对」 |
| 权限 | 只读 | 第一版也只读，将来会能改 |

看板那边不放能改东西的入口——看板是盯故障的面，
在它上面放「改声明」的按钮，等于让修机器的人顺手改了机器。

**目录平级，进程不分开**：`workshop/` 和 `kanban/` 平级，
但跑在同一个服务、同一个端口上（`/workshop`）。
什么时候值得拆进程？三条判据同时成立才拆：
权限不同、发布节奏不同、负载特征不同。现在一条都不成立。

第一版只做**体检台**：把一只盒子声明得对不对摊开给人看。
判定在 `runtime/box_health.py`——守卫和工作台读同一份，
所以页面说的和守卫说的必然一致。
"""

from workshop.views import health_summary, workshop_page, workshop_payload

__all__ = ["health_summary", "workshop_page", "workshop_payload"]
