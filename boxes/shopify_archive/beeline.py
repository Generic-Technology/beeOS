"""boxes.shopify_archive.beeline —— 经营档案盒的作业路线

op 链：
  1. `archive_intake`    接住这一段（三个通道里恰好一个）
  2. `archive_verify`    自己复核：这段标得实不实
  3. `archive_packager`  装成一条档案记录

**为什么复核单独一跳**：复核的结论要**进产出**（`metric_status_honest`），
产出是验收读数的地方。复核藏在打包那一步，读者就看不到它到底查了什么。
"""

from __future__ import annotations

from core.beeline_models import Idempotency
from core.scaffold import Component

#: 同一份产出重复归档，结果相同——**幂等**
IDEMPOTENT_BY_TASK = Idempotency(mode="required", key="round_id")

COMPONENTS: list[Component] = [
    Component(
        op_id="archive_intake",
        type="archive_intake",
        input_from="external",
        input="schema_shopify_archive_input",
        output="schema_shopify_archive_part",
        next=["archive_verify"],
        bee_type="archive_intake_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
    Component(
        op_id="archive_verify",
        type="archive_verify",
        input_from="archive_intake",
        input="schema_shopify_archive_part",
        output="schema_shopify_archive_verdict",
        next=["archive_packager"],
        bee_type="archive_verify_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
    Component(
        op_id="archive_packager",
        type="archive_packager",
        input_from="archive_verify",
        input="schema_shopify_archive_verdict",
        output="schema_shopify_archive_output",
        next=[],
        bee_type="archive_packager_handler",
        idempotency=IDEMPOTENT_BY_TASK,
    ),
]
