"""tests.fixtures.sample_box.definition - 夹具盒的 Definition

**它是一只夹具，不是产品**：
- 不注册进 `BOX_REGISTRY`（看板不显示、trigger 不接收）
- 不进 `pyproject.toml` 的打包清单
- 没有任何业务语义（没有订单、没有库存、没有票据）

存在的唯一理由：**让运行时的机制有一只可控的盒子可跑**。
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricDef,
    MetricsDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    TaskDef,
)
from core.plugins import PluginBinding
from tests.fixtures.sample_box.schemas import ALL_SCHEMAS


SAMPLE_BOX = BeeBoxDefinition(
    id="sample_box",
    version=1,
    description="夹具盒——只为运行时机制测试提供一只最小可跑的盒子，不是产品",

    schemas=ALL_SCHEMAS,

    # **展示位摊平在顶层**（原来住在 placement 里，退休后搬上来）。
    # **这里没有接线**——夹具盒不属于任何协作边界。
    display_name="Sample Fixture Box",
    value_stream="Testing",
    role="Sample Fixture Fulfillment",

    task=[
        TaskDef(
            type="handle_sample_request",
            task_schema="schema_sample_request",
            beeline_id="beeline_sample_v1",
            beeline_version=1,
            trigger="测试直接投料",
        ),
    ],

    result=ResultDef(
        type="sample_resolved",
        result_schema="schema_sample_result",
        acceptance=[
            AcceptanceRule(metric="sample_state", op="eq", value="resolved"),
            AcceptanceRule(metric="notified", op="eq", value=True),
        ],
        exceptions=[
            ExceptionRule(condition="sample_state == escalated", action="escalate"),
        ],
    ),

    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            continuous_improvement="observe_only",
        ),
        rules=QueenRules(
            task_routing={"default_route": "sample_resolve"},
            exception_handling={},
            continuous_improvement={"monitor_metrics": ["sample_pass_rate"]},
        ),
    ),

    # **升级决策插件（盒级）**（2026-11-12）
    #
    # 升级决策已收编成盒级插件，规则的唯一真源是**插件参数**。
    # 夹具盒手写 `BeeBoxDefinition`（不走装配器），所以这里直接写**绑定**
    # （`PluginBinding` 是装配后的形状）。
    plugins=[
        PluginBinding(
            id="queen_escalation",
            extension_point="box.escalate",
            params={
                "escalation_rules": [
                    {"trigger": "sample_state == escalated", "action": "escalate_to_human"},
                ],
            },
            implementation_key="queen.escalation",
        ),
    ],

    metrics=MetricsDef(
        quality=[MetricDef(name="sample_pass_rate", definition="夹具盒通过率", target=1.0)],
        latency=[MetricDef(name="sample_duration_ms", definition="单次耗时", target=100)],
        cost=[MetricDef(name="sample_cost", definition="单次成本", target=0.01)],
    ),
)


def get_definition() -> BeeBoxDefinition:
    """返回夹具盒 Definition"""
    return SAMPLE_BOX
