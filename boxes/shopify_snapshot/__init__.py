"""boxes.shopify_snapshot - 店铺快照盒

把这一轮窗口里店铺的流量事实取回来，写成一份带出处、带状态的快照。

**它只读。** 不判断、不建议、不改任何商品设置。

**产出里没有一个裸数字**：每条指标都带 status、出处、采集时间。

**合格访问只算一次**：排除只按首个落地页——一个 session
后来访问了购物车或结账页，恰恰说明它是高意向流量。

**取不到写 unverifiable，不写 0**：0 是观测值，「没取到」不是。

## 成熟度：skeleton

腿（ShopifyQL 客户端 + 口径计算）写了，但**还没在真店上跑过**——
`read_reports` + Level 2 受保护客户数据审批还没下来。
按仓里的纪律：静态体检、单元测试通过**不代替**真实履约。
第一次真跑成功之后改成 `live`。

## 数据从哪来

`shopifyqlQuery`（GraphQL Admin API）：

- 需要 `read_reports` scope + **Level 2 受保护客户数据** + API 版本 ≥ 2025-10；
- `FROM sessions` 给流量、漏斗、落地页、来源、地区、真人/机器人过滤；
- 维度 `agentic_referring_channel` 给 AI 引荐渠道（ChatGPT 等）——
  **引荐来源是事实，「商品被 AI 引用」不是**。

## 凭证

`SHOPIFY_ADMIN_TOKEN` / `SHOPIFY_STORE_DOMAIN`（环境变量）。
**凭证不进投料**——投料里出现 token，它就会一路进产出、进归档、进日志。

## 文件

| 文件 | 装什么 |
| --- | --- |
| `__init__.py` | 入口（`get_definition` / `get_beeline`）|
| `schemas.py` | 4 个 schema |
| `definition.py` | `DECLARATION` |
| `beeline.py` | `COMPONENTS`——三跳 |
| `runner.py` | 三跳的实现 |
| `shopifyql_client.py` | 只读 ShopifyQL 客户端（四种失败形态分开处理）|
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.shopify_snapshot.beeline import COMPONENTS
from boxes.shopify_snapshot.definition import DECLARATION
from boxes.shopify_snapshot.runner import RUNNER, SHOPIFY_SNAPSHOT_HANDLERS
from boxes.shopify_snapshot.schemas import ALL_SCHEMAS


#: **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=SHOPIFY_SNAPSHOT_HANDLERS,
)

SHOPIFY_SNAPSHOT_BOX: BeeBoxDefinition = ASSEMBLED.definition
SHOPIFY_SNAPSHOT_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "SHOPIFY_SNAPSHOT_BOX",
    "SHOPIFY_SNAPSHOT_BEELINE",
    "get_definition",
    "get_beeline",
    "RUNNER",
    "SHOPIFY_SNAPSHOT_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
