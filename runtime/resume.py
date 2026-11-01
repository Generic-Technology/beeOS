"""
runtime.resume - 「等外部」的履约怎么回来

**为什么需要这个模块**（2026-11-01 实测出来的）：
queen 的 escalate 决策（`human_review` / `auto_resolve` / `observe_only`）
以前只被**记成一个字符串**，然后不管判成什么，验收一律 REJECTED——
**决策没有牙齿**：queen 说「要人看」，系统还是直接拒了。

现在 `human_review` 会让履约**停在 `waiting_external`**，
等人给个说法。这一步之后，就得有人能把这次履约**接回去**——
不然就是「停在半路」，比直接拒绝还糟（读者看到的是个永远不动的任务）。

**接回去的语义**（这一条是产品决定，写在这里以免被当成实现细节）：

| 人的判定 | 履约层 | 验收层 | 为什么 |
|---|---|---|---|
| `approve`（放行） | `completed` | `accepted` | 人判了就算——但**必须留痕**：放行了哪条没过、谁放的、为什么 |
| `reject`（不放行） | `rejected` | `rejected` | 人判不放行 |

**放行不许抹掉没过的那条判据**：`acceptance_detail` 里那条 failed 还在，
另加一段 `human_decision` 说明「这条是人工放行的」。
「人放行了」和「判据过了」是两件事，混成一件就是造假信号。

**记录怎么落**：store 是**只追加**的 JSONL 审计日志，所以这次判定
**再追加一条**同 `task_run_id` 的记录（带上最终状态）。
`get_run` 取**最新**那条——追加式日志里，「最新」才是「当前状态」。
历史那条（waiting_external）留在日志里，正好是「它当时在等」的事实。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from runtime.store import TaskRunStore


#: 人能给的判定
HUMAN_DECISIONS = ("approve", "reject")


class ResumeError(Exception):
    """接不回去（状态不对 / 判定不认识）"""


class ResumeNotFound(ResumeError):
    """这次履约**根本不存在**

    跟「存在但不在等人判定」分开：前者是 **404**（没这回事），
    后者是 **409**（有，但此刻不接受回答）。
    两种情况混成一个码，读者分不清「我 id 写错了」还是「时机不对」。
    """


def resume_waiting_run(
    store: TaskRunStore,
    task_run_id: str,
    decision: str,
    note: str = "",
    decided_by: str = "human",
) -> dict[str, Any]:
    """把一条停在 `waiting_external` 的履约接回去

    Returns: 追加的那条新记录（同 task_run_id）

    Raises:
        ResumeError: 找不到 / 状态不对 / 判定不认识
    """
    decision = (decision or "").strip().lower()
    if decision not in HUMAN_DECISIONS:
        raise ResumeError(
            f"判定不认识：{decision!r}（只认 {'、'.join(HUMAN_DECISIONS)}）"
        )

    record = store.get_run(task_run_id)
    if record is None:
        raise ResumeNotFound(f"找不到这次履约：{task_run_id}")
    if record.get("status") != "waiting_external":
        raise ResumeError(
            f"这次履约不在等人判定（status={record.get('status')!r}）"
        )

    # **人工放行 ≠ 判据过了**：没过的那条判据原样留着，
    # 另加一段 human_decision 说明这是人放行的。
    detail = list(record.get("acceptance_detail") or [])
    failed = [
        row.get("metric") for row in detail
        if row.get("outcome") == "failed" or row.get("passed") is False
    ]
    approved = decision == "approve"
    new_record = dict(record)
    new_record["status"] = "completed" if approved else "rejected"
    new_record["acceptance_status"] = "accepted" if approved else "rejected"
    new_record["human_decision"] = {
        "decision": decision,
        "decided_by": decided_by,
        "note": note,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        # **放行了哪些没过的东西**——不写这一条就成了「凭空通过」
        "overridden_rules": failed,
        "was_waiting_for": record.get("pending_external") or {},
    }
    new_record["finished_at"] = new_record["human_decision"]["decided_at"]
    if not approved:
        # 人判不放行 → 归因仍然是当初那条
        new_record["rejection_class"] = record.get("rejection_class")
    store.append_record(new_record)
    return new_record
