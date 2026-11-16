"""boxes.shopify_market_signal.beeline —— 市场信号盒的作业路线

op 链：
  1. `market_signal_intake`   认下这一轮要找什么
  2. `market_signal_work`     各平台取回 → 丢掉带个人信息的
  3. `market_signal_packager` 装成需求主题清单
"""

from __future__ import annotations

from core.beeline_models import Idempotency
from core.scaffold import Component

#: 取数是只读的，但**同一窗口重抓一次，站点内容可能已经变了**——
#: 所以不承诺幂等（重放会拿到更新鲜的结果，不是错的结果）。
IDEMPOTENT_BY_TASK = Idempotency(mode="optional")

COMPONENTS: list[Component] = [
    Component(
        op_id="market_signal_intake",
        type="market_signal_intake",
        input_from="external",
        input="schema_market_signal_input",
        output="schema_market_signal_plan",
        next=["market_signal_work"],
        bee_type="market_signal_intake_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
    Component(
        op_id="market_signal_work",
        type="market_signal_work",
        input_from="market_signal_intake",
        input="schema_market_signal_plan",
        output="schema_market_signal_raw",
        next=["market_signal_packager"],
        bee_type="market_signal_work_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
    Component(
        op_id="market_signal_packager",
        type="market_signal_packager",
        input_from="market_signal_work",
        input="schema_market_signal_raw",
        output="schema_market_signal_output",
        next=[],
        bee_type="market_signal_packager_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
]
