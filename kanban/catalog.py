"""
kanban.catalog - 把注册表里的材料攒成「一只盒子」

体检台（`runtime/box_health.py`）接受声明作为入参，**不自己去找盒子**——
runtime 是内核，不许 import 盒子。那「这只盒子有哪些、有哪些作业路线、
示例投料长什么样、有没有执行器」就得由**知道注册表的那一层**准备好。

这一层就是看板：它拥有注册表
（`BOX_REGISTRY` / `RUNNER_REGISTRY` / `METRIC_COMPUTERS`），
所以体检材料由它攒，交给检查器判。

**为什么不把这些攒的动作塞进 `runtime/box_health.py`**：
那样它就得 import 盒子（内核反向依赖产品），
或者自己认 box_id 字面量（把某一只盒子的知识写进内核）。
两头都是那族「内核不该知道的东西」。

## 一处判定，两处引用

守卫（`tests/test_box_standard.py`）和工作台页面都从这里取材料、
都调同一个检查器。所以页面说的和守卫说的**必然一致**——
不是靠两边小心保持一致，是它们读的是同一份判定。
"""

from __future__ import annotations

from typing import Optional

from kanban.trigger import (
    BOX_REGISTRY,
    METRIC_COMPUTERS,
    RUNNER_REGISTRY,
    list_task_entries,
    order_boxes_by_flow,
    registered_box_ids,
)
from runtime.box_health import BoxInspection, CatalogHealth, inspect_catalog


def box_inspection(box_id: str) -> Optional[BoxInspection]:
    """攒一只盒子体检用的全部材料

    没注册这只盒子 → None。
    """
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        return None
    get_definition, beeline_loaders = entry
    definition = get_definition()
    beelines = {bid: loader() for bid, loader in beeline_loaders.items()}
    samples = {
        entry_row["task_type"]: entry_row["sample_payload"]
        for entry_row in list_task_entries(box_id)
    }
    return BoxInspection(
        box_id=box_id,
        definition=definition,
        beelines=beelines,
        sample_payloads=samples,
        has_runner=box_id in RUNNER_REGISTRY,
        # **没有就是没有**：传 None → 那一项判「没验到」，
        # 不传空表 → 会被读成「声明的指标都没人算」，那是另一种话。
        metric_computers=METRIC_COMPUTERS.get(box_id),
    )


def catalog_inspections() -> list[BoxInspection]:
    """目录里所有盒子的体检材料（按价值流顺序）"""
    out: list[BoxInspection] = []
    for box_id in order_boxes_by_flow(registered_box_ids()):
        inspection = box_inspection(box_id)
        if inspection is not None:
            out.append(inspection)
    return out


def catalog_health() -> CatalogHealth:
    """整个目录的体检结论"""
    return inspect_catalog(catalog_inspections())
