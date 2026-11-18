"""fulfillment_scopes.shopify.market_signal —— 市场信号盒的共享词汇

**它抓的是「需求主题」，不是「人」。**

这条边界是这个盒子全部设计的根：一旦产出里有「谁想要」，
它就变成了一台群发机器；只出「外面在找什么」，它才是一只读盒。

## 平台

| 平台 | 拿什么 | 凭证 |
|---|---|---|
| `gsc` | **我们自己店的**搜索词 + 曝光/点击/CTR/位置（第一方官方数据，不是爬虫）| OAuth 客户端，或一条导出的 CSV |
| `reddit` | 公开讨论里的「在找什么」 | Reddit Data API（OAuth） |
| `etsy` | 同类目买家的「我想要但没有」 | Etsy API key + secret |
| `pinterest` | 视觉搜索意图 | Pinterest app token |

**全部走官方 API**，不抓 ToS 禁止的路径。

## 产出里不许有的东西

邮箱、手机号、姓名、账号 ID —— 见 `boxes/shopify_market_signal/pii.py`。

## 平台挂了不许拖垮整轮

一个平台取不到 → 那个平台标 `unverifiable` + 原因，
**其余平台照常**。取不到不是「这轮没有需求信号」。
"""

from __future__ import annotations

#: 支持的平台（**只走官方 API**）
PLATFORMS = ["gsc", "reddit", "etsy", "pinterest"]

#: 平台凭证的环境变量名（**不进投料**——投料里出现凭证，它就会一路进归档）
PLATFORM_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "gsc": ("GSC_ACCESS_TOKEN", "GSC_SITE_URL"),
    "reddit": ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"),
    "etsy": ("ETSY_API_KEY", "ETSY_API_SECRET"),
    "pinterest": ("PINTEREST_ACCESS_TOKEN",),
}

#: 一次最多抓多少条信号（**有上限**——抓太多既慢又像是在扫站）
MAX_SIGNALS_PER_PLATFORM = 25

#: **GSC 自己的数据，上限可以宽一些**
#:
#: 那条上限是给「抓别人的平台」设的（别像在扫站）；
#: 我们自己店的搜索数据不涉及抓取任何人的东西。
#:
#: **而且上限一低就会把关键的那个词切掉**（实测）：
#: `glitter compact mirror`——正是那一页在搜索结果里靠的词——
#: 按展示数排在**第 26 位**，被 25 的上限切了 →
#: 诊断盒于是说「这一轮没有需求词」，
#: 而实验盒只能凭自己的理解改标题 → **把能看见自己的那扇门关上了**。
GSC_MAX_SIGNALS = 100

#: 抓取限速（秒/请求）——**默认慢一点**是礼貌，也是自保
REQUEST_INTERVAL_SECONDS = 1.0
