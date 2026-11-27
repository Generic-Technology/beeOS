"""boxes.shopify_market_signal - 市场信号盒

去外部平台看**公开的需求**：谁在公开地找什么、抱怨什么、想要什么。

## 它抓的是「需求主题」，不是「人」

一旦产出里有「谁想要」，它就变成了一台群发机器。
只出「外面有一类人在想 X」，它才是一只读盒。

**带个人信息的候选整条丢掉**（不是洗白）：带联系方式的帖子
不是需求信号，是有人在私下交易。

## 四个平台，全走官方 API

| 平台 | 拿什么 | 凭证 |
|---|---|---|
| `gsc` | 我们自己店的搜索词 + 曝光/点击/CTR/位置 | `GSC_ACCESS_TOKEN` + `GSC_SITE_URL`，或投料给一条导出的 CSV |
| `reddit` | 公开讨论里的「在找什么」 | `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` |
| `etsy` | 同类目买家的「我想要但没有」 | `ETSY_API_KEY`（+ `ETSY_API_SECRET`） |
| `pinterest` | 视觉搜索意图 | `PINTEREST_ACCESS_TOKEN` |

**凭证只从环境变量读**，不进投料——投料里出现凭证，它会一路进归档。

## 它在链上的位置

```
market_signal → snapshot → diagnosis → experiment → archive
```

**为什么在链首而不是挂在诊断上**：诊断要挂两个上游，环图就画不出来
（BeeOS 的环要求相邻有交接边）。所以市场信号从链首进，
「这一轮的事实」= 店里的数据 + 市场的需求。

## 成熟度：skeleton

腿实现了，但**只有 GSC 的 CSV 那条路用夹具验过**；
其余平台要等凭证到位。第一次真跑过之后标 `live`。

## 文件

| 文件 | 装什么 |
| --- | --- |
| `__init__.py` | 入口（`get_definition` / `get_beeline`） |
| `schemas.py` | 4 个 schema |
| `definition.py` | `DECLARATION` |
| `beeline.py` | `COMPONENTS`——3 跳 |
| `runner.py` | 3 跳的实现 |
| `pii.py` | 个人信息扫描与剔除（**安全底线**） |
| `clients.py` | 四个平台的只读客户端 |
"""

from __future__ import annotations

from core.beeline_models import Beeline
from core.models import BeeBoxDefinition
from core.scaffold import assemble

from boxes.shopify_market_signal.beeline import COMPONENTS
from boxes.shopify_market_signal.definition import DECLARATION
from boxes.shopify_market_signal.runner import (
    RUNNER,
    SHOPIFY_MARKET_SIGNAL_HANDLERS,
)
from boxes.shopify_market_signal.schemas import ALL_SCHEMAS


#: **装配在这一步发生。** 声明自相矛盾 → 这里抛 AssemblyError。
ASSEMBLED = assemble(
    DECLARATION,
    COMPONENTS,
    schemas=ALL_SCHEMAS,
    handlers=SHOPIFY_MARKET_SIGNAL_HANDLERS,
)

SHOPIFY_MARKET_SIGNAL_BOX: BeeBoxDefinition = ASSEMBLED.definition
SHOPIFY_MARKET_SIGNAL_BEELINE: Beeline = ASSEMBLED.beeline


def get_definition() -> BeeBoxDefinition:
    return ASSEMBLED.definition


def get_beeline() -> Beeline:
    return ASSEMBLED.beeline


__all__ = [
    "ASSEMBLED",
    "SHOPIFY_MARKET_SIGNAL_BOX",
    "SHOPIFY_MARKET_SIGNAL_BEELINE",
    "get_definition",
    "get_beeline",
    "RUNNER",
    "SHOPIFY_MARKET_SIGNAL_HANDLERS",
    "DECLARATION",
    "COMPONENTS",
]
