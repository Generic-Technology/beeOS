"""
runtime.finalize - 产出定案后的统一收口

**产出交出去之前必须过的那两道闸，都在这里**：

1. **形状闸**（通用）：产出必须符合自己声明的 `result_schema`。
   不合 → **履约中止**，而且**不跑验收**
   （验收读的正是产出里的字段，形状都不对了，判据的结论只会是误导人的 n/a）。

2. **盒级核验钩子**（业务）：产出定案之后、验收之前，
   盒子自己的核验插件跑一遍；它说有问题 → **履约中止**。

## 为什么这两道闸住在 runtime，而不是编排层

编排层（`kanban/trigger.py`）只负责**调用**；
判「产出本身合不合格」这件事属于 runtime——产出是 runtime 执行出来的。

**放在编排层的后果**：换个入口跑履约，这两道闸就没了——
而它们恰恰是最不该被绕过的那两道。

## 但要说清：executor 单独跑**不构成一次履约**

`BeelineExecutor.execute()` 是**机制**（跑一条作业路线）。
一次履约 = 投料闸 → 执行 → **收口** → 验收 → 落盘，
落盘与验收都在编排层。

所以「直接调 executor」拿到的是一条**没验收、没落盘**的 task_run——
它不是一次履约，也没有出现在看板上。
**收口在这里，谁宣布交付在编排层**——两件事都在，但不混在一处。

看 [docs/component-plugin-model.md](../../docs/component-plugin-model.md)。
"""

from __future__ import annotations

from typing import Any, Optional

from core.models import BeeBoxDefinition
from core.plugins import PluginContext
from runtime.contract import validate_payload
from runtime.models import TaskRun, TaskRunStatus
from runtime.plugins import (
    EXTENSION_VERIFY,
    PluginFailure,
    abort_on_plugin_failure,
    verify_output,
)


def check_output_contract(
    task_run: TaskRun, definition: BeeBoxDefinition
) -> list[str]:
    """**形状闸**：产出必须符合自己声明的 `result_schema`

    返回问题清单（空 = 通过）。不合时**落一条事件**——
    失败要看得见，不能只在内存里发生。

    **为什么以前没有这道闸**：产出从不过闸 → 缺一个必填字段 →
    判据读不到 → 判「用不上」→ 不进失败也不进通过 → **照样 ACCEPTED 交付**。
    """
    if task_run.status != TaskRunStatus.COMPLETED:
        # 链没走完 → 没有「定案的产出」可判
        return []

    errors: list[str] = []
    result_schema = definition.get_schema(definition.result.result_schema)
    if task_run.result is None:
        errors.append("产出是空的——链走完了却什么都没产出")
    elif result_schema is not None:
        errors = validate_payload(
            task_run.result, result_schema, schemas=definition.schemas
        )

    if errors:
        # **带上上下文**：光说「形状不对」等于让人自己猜是哪个字段、
        # 期望什么、实际是什么。
        errors = [
            f"产出不合产出契约 {definition.result.result_schema!r}：{e}"
            for e in errors
        ]
        task_run.event_log.append(
            event="output_contract_violation",
            detail={
                "schema": definition.result.result_schema,
                "errors": errors,
            },
        )
    return errors


def run_box_verifications(
    task_run: TaskRun,
    definition: BeeBoxDefinition,
    *,
    plugin_context: Optional[PluginContext],
) -> list[str]:
    """**盒级核验钩子**：产出定案后、验收之前

    返回问题清单（空 = 通过）。

    **fail closed**：盒子声明了核验插件，却找不到它的装配快照 →
    **核验等于没跑** → 履约中止。
    静默跳过的话，读者以为它在守着，实际它一次都没跑过——
    那正是「声明了却没人执行」，比报错更坏。
    """
    if task_run.status != TaskRunStatus.COMPLETED:
        # **失败的履约没有「定案的产出」**——核验无从谈起。
        # 链途中挂掉、或只跑了一半（partially_completed），产出都不是最终那一份，
        # 拿它去核验 = 核验一份不存在的东西。
        #
        # **核验是「产出定案之后」的事**（钩子的定义就是定在那个时机的）。
        return []

    bindings = [
        b for b in definition.plugins if b.extension_point == EXTENSION_VERIFY
    ]
    if not bindings:
        return []  # 盒子没挂核验插件 → 不跑

    if plugin_context is None:
        message = (
            f"盒子声明了核验插件（{[b.id for b in bindings]}），"
            f"但找不到它的插件上下文（装配快照）——"
            f"核验不会执行，所以履约中止（不静默跳过）"
        )
        task_run.event_log.append(
            event="verification_missing_context",
            detail={"plugins": [b.id for b in bindings]},
        )
        return [message]

    try:
        problems = verify_output(
            plugin_context,
            bindings,
            extension_point=EXTENSION_VERIFY,
            source={"result": task_run.result or {}},
        )
    except PluginFailure as failure:
        # **插件自己坏了**（不是「核验没通过」）：
        # 履约中止，原因是机制问题——两件事读者要能分开看见。
        abort_on_plugin_failure(task_run, failure)
        return [str(failure)]

    if problems:
        task_run.event_log.append(
            event="verification_failed",
            detail={
                "plugins": [b.id for b in bindings],
                "problems": problems,
            },
        )
    return problems


def finalize_output(
    task_run: TaskRun,
    definition: BeeBoxDefinition,
    *,
    plugin_context: Optional[PluginContext],
) -> list[str]:
    """**产出定案后的统一收口**：形状闸 + 盒级核验

    返回问题清单（空 = 全部通过）。

    调用方（编排层）拿到非空清单就把这次履约**中止**——
    而且**不要跑验收**（形状或内容都不对时，判据的结论只会是误导）。
    """
    problems = check_output_contract(task_run, definition)
    if problems:
        # 形状都不对了，后面的核验只会跟着错 → 不跑
        return problems
    return run_box_verifications(
        task_run, definition, plugin_context=plugin_context
    )
