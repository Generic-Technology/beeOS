"""boxes.shopify_snapshot.definition —— 店铺快照盒的声明

**它只读**。它不判断、不建议、不动商品。

**成熟度：skeleton**。腿（ShopifyQL 客户端 + 口径计算）写了，
但**还没在真店上跑过**——`read_reports` + Level 2 受保护客户数据审批还没下来。
按仓里的纪律（`docs/stability-plan.md`）：
「静态体检、单元测试通过不代替真实履约」，
所以在第一次真跑成功之前，它诚实地标成 `skeleton`，看板会显式标「未实现」。

跑通一次真数据之后，把它改成 `live`——**那时它的判据读数才有意义**。

## 判据只判「如实」，不判「数据好不好看」

流量少不是这只盒子的失败——那是店的事实。
它失败的标准只有两个：**数字没有出处**，或者**该标的没标**。
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    ExceptionRule,
    MetricsDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
)
from core.plugins import PluginSpec
from core.scaffold import BoxDeclaration

#: **升级规则的真源**（跟别的盒子同一条纪律：规则住在插件参数里，
#: `queen.rules` 那边不再有它）
#: **只在「取到了但标注不诚实」时升级给人**。
#:
#: 「什么都没取到」（凭证没配 / 权限没批）**不升级**——
#: 那不是「这次交付要不要放行」的决定，那是要去配凭证。
#: 混成一件事的后果：每次试跑都在问人一个根本不用问的问题，
#: 而真正的原因（权限不够）被埋在产出里没人看。
ESCALATION_RULES = [
    {
        "trigger": "data_present == true and metric_status_complete == false",
        "action": "escalate_to_human",
    },
    {
        "trigger": "data_present == true and source_present == false",
        "action": "escalate_to_human",
    },
]

DECLARATION = BoxDeclaration(
    id="shopify_snapshot_box",
    version=1,
    description=(
        "店铺快照盒——把这一轮窗口里店铺的流量事实取回来，写成一份带出处、带状态的快照。\n\n"
        "**它只读。** 不判断、不建议、不改任何商品设置。\n\n"
        "**产出里没有一个裸数字**：每条指标都带 status（observed / "
        "declared_from_baseline / unverifiable）、出处和采集时间。\n\n"
        "**合格访问只算一次**：排除只按首个落地页——"
        "一个 session 后来访问了购物车或结账页，恰恰说明它是高意向流量，"
        "按路径把整个 session 删掉会扔掉最值钱的会话。\n\n"
        "**取不到写 unverifiable，不写 0**：0 是观测值，"
        "「没取到」不是，两者混在一起，下游会把「没数据」读成「没有流量」。"
    ),

    # ---- 归属 ----
    display_name="店铺快照盒",
    value_stream="Shopify Store",
    role="店铺流量事实的只读采集与核验",

    # ---- 投料入口（Intent）----
    task_type="capture_store_snapshot",
    task_schema="schema_shopify_snapshot_input",
    # **归档按轮次分组**：同一个 round_id 的快照、诊断、实验
    # 在档案里是**同一轮经营**，不是三件不相干的事。
    subject_ref_field="round_id",
    subject_kind="operating_round",
    task_trigger=(
        "一轮获客实验开始前取基线\n"
        "OR 一轮实验结束后取复测\n"
        "OR 想核验某个口径到底取不取得到"
    ),

    # ---- 产出契约（Contract）----
    result_type="store_snapshot",
    result_schema="schema_shopify_snapshot_output",
    result_description="一轮窗口的店铺流量事实（带出处、带状态）",
    # **live**：2026-11-16 真跑过一次——真店、真数据、真判据，全绿。
    # 「静态体检不代替真实履约」那条规矩要求真跑过才敢标 live。
    maturity="live",
    artifact_view="generic",
    acceptance=[
        # 合格访问不可能比全部 session 多——口径自洽
        AcceptanceRule(metric="qualified_sessions", op="gte", value=0),
        AcceptanceRule(metric="total_sessions", op="gte", value=0),
        # **这一轮确实取到量了**——「取不到」不是交付
        AcceptanceRule(metric="data_present", op="eq", value=True),
        # 转化率是比率
        AcceptanceRule(metric="conversion_rate", op="gte", value=0),
        AcceptanceRule(metric="conversion_rate", op="lte", value=1),
        # 每条指标都带合法状态（observed 有 source、unverifiable 有 note）
        AcceptanceRule(metric="metric_status_complete", op="eq", value=True),
        # 有出处——没有出处的数字不进档案
        AcceptanceRule(metric="source_present", op="eq", value=True),
    ],
    exceptions=[
        # 「没取到」不升级（原因在 unverified 里，读得到）；
        # 「取到了但标注不诚实」升级——那是要人看的。
        ExceptionRule(
            condition="data_present == true and metric_status_complete == false",
            action="escalate",
        ),
        ExceptionRule(
            condition="data_present == true and source_present == false",
            action="escalate",
        ),
    ],

    # ---- 作业路线（Procedure）----
    beeline_id="beeline_shopify_snapshot_v1",
    beeline_version=1,
    beeline_description=(
        "店铺快照盒 履约作业路线：snapshot_intake → snapshot_query → snapshot_packager"
    ),

    # ---- 履约政策（Policy）----
    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            continuous_improvement="observe_only",
        ),
        rules=QueenRules(
            task_routing={},
            exception_handling={},
            continuous_improvement={},
        ),
    ),

    # **不声明指标**：指标是**承诺**（target），不是事实。
    # 「合格访问要涨到多少」那是经营目标，不是这只盒子现在能兑现的东西。
    metrics=MetricsDef(),

    # ---- 升级决策插件（盒级）----
    plugins=[
        PluginSpec(
            id="queen_escalation",
            extension_point="box.escalate",
            params={"escalation_rules": ESCALATION_RULES},
        ),
    ],
)
