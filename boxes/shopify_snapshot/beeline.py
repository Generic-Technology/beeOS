"""boxes.shopify_snapshot.beeline —— 店铺快照盒的作业路线

op 链：
  1. `snapshot_intake`    认下这一轮的口径（窗口 + 合格访问规则）
  2. `snapshot_query`     真去 Shopify 取数（只读）
  3. `snapshot_packager`  算合格访问 + 装出带状态的快照

**为什么分三跳而不是一跳**：取数会失败（权限没批、响应过大、被限流）。
把「取数」和「算数 + 装产出」分开，
失败的时候能说清是**没取到**还是**取到了但口径算不出来**——
后者是逻辑错，前者不是。
"""

from __future__ import annotations

from core.beeline_models import Idempotency
from core.scaffold import Component

#: 同一窗口重复取，结果会随新订单变化——**不承诺幂等**。
#: 声明 required 等于骗 runtime 说「重放安全」。
IDEMPOTENT_BY_TASK = Idempotency(mode="optional")

COMPONENTS: list[Component] = [
    Component(
        op_id="snapshot_intake",
        type="snapshot_intake",
        input_from="external",
        input="schema_shopify_snapshot_input",
        output="schema_shopify_snapshot_plan",
        next=["snapshot_query"],
        bee_type="snapshot_intake_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
    Component(
        op_id="snapshot_query",
        type="snapshot_query",
        input_from="snapshot_intake",
        input="schema_shopify_snapshot_plan",
        output="schema_shopify_snapshot_raw",
        next=["snapshot_packager"],
        bee_type="snapshot_query_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
    Component(
        op_id="snapshot_packager",
        type="snapshot_packager",
        input_from="snapshot_query",
        input="schema_shopify_snapshot_raw",
        output="schema_shopify_snapshot_output",
        next=[],
        bee_type="snapshot_packager_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
]
