"""giftsjourneyrebirth 店铺经营协作边界：共享业务词汇

**这个边界里四只盒子说的是同一门话**：

- 同一个经营轮次号（`round_id`）——一次「取数 → 诊断 → 实验」是一轮；
- 同一套数据状态三态（`observed` / `declared_from_baseline` / `unverifiable`）；
- 同一条「合格访问」的排除规则。

## 为什么这些词住在边界里，不住在盒子里

放进某一只盒子的话，下游要 `import` 上游的模块——
而「跨盒共享的业务类型受 import 顺序限制」是仓里已知的缺口
（`docs/component-plugin-model.md` §12.7）。
边界是这四只盒子**共同的**归属，词表放这儿没有归属争议。

## 为什么状态是三态，不是「有值 / 没值」

`observed` = 这次真的取到了；
`declared_from_baseline` = 只有基线里的声明值，这次没验证（必须写出处）；
`unverifiable` = 拿不到（必须写为什么）。

**没有第四态**：「没去采」是 `unverifiable` 的一种，
note 里写清是「没去采」还是「采了取不到」。
另起一套词表（`not_collected`）只会在仓里多出一份口径——
同一个意思两种写法，半年后没人说得清哪份是真的。

**取不到的量不许写 0**：0 是一个观测值，「取不到」不是。
"""

from __future__ import annotations


#: 数据状态三态（沿用 `boxes/wms_system/schemas.py` 的 canonical 词表）
METRIC_STATUS = ["observed", "declared_from_baseline", "unverifiable"]

#: 经营轮次号——跨四只盒子识别「同一轮经营」
ROUND_ID_FIELD = "round_id"

#: **合格访问**的落地页排除规则（只按**首个落地页**排除）
#:
#: 为什么只按首个落地页：一次 session 里**后来**访问了购物车或结账页，
#: 恰恰说明它是高意向流量——按路径把整个 session 删掉，
#: 等于把最值钱的那几个会话扔了。
#:
#: `/account/` 开头的是退订、账户设置这类**非获客**入口
#: （2026-10-16 ~ 2026-11-14 的审计里，约 20 次访问来自 `/account/unsubscribe`）。
EXCLUDED_ENTRY_PATH_PREFIXES = [
    "/account/",
    "/password/",
    "/policies/",
    "/apps/",
]

#: 合格访问的规则文本——**写进产出**，不是只写在这里。
#:
#: 同一个量在产物里出现两次，两处必须来自同一次计算：
#: 规则写在盒子里、结论写在产出里，读者才不用回头猜口径。
QUALIFIED_SESSION_RULE = (
    "合格访问 = 真人 session（human_or_bot_session = 'human'）"
    " 且首个落地页不在排除清单里"
    f"（{', '.join(EXCLUDED_ENTRY_PATH_PREFIXES)}）"
    " 且来源或落地页可识别；"
    "购物车/结账页是漏斗阶段，不作为排除条件"
)

#: 归因方式——**一等字段**，不是塞进 UTM 里的将就
#:
#: 自然搜索没有 UTM：把 UTM 当唯一归因方式，
#: 等于让 SEO / AI 引荐这一类渠道**永远归因不到**。
ATTRIBUTION_METHODS = ["utm", "landing_page_time_window", "none"]

# ============================================================
# 凭证与写闸
# ============================================================

#: 凭证从环境变量读，**不进投料、不进产出、不进日志**。
#:
#: 投料里出现 token，它就会一路进产出、进归档、进日志——
#: 而档案是要长期留存的东西。
TOKEN_ENV = "SHOPIFY_ADMIN_TOKEN"
DOMAIN_ENV = "SHOPIFY_STORE_DOMAIN"

#: **写操作总闸**：没设成 "1"，写接口一律拒绝执行。
#:
#: 为什么要有这一道：改商品页是**改用户真实的店**——
#: 一次误改就改在营业中的页面上，而且不报错。
#: 闸门的作用不是「安全洁癖」，是让「写」这件事必须**有人明确开过**。
#:
#: 它是**进程级**的，不在投料里：这样交接通道自动发起的履约
#: **不可能**触发写入——它只会跑在默认关着的进程里。
ALLOW_WRITES_ENV = "SHOPIFY_ALLOW_WRITES"

#: 允许写入的字段白名单
#:
#: **只改这几个**：标题、描述正文、SEO 标题与描述、标签。
#: 价格、库存、变体、发布状态一律不在白名单里——
#: 那些是「改错了会赔钱」的字段，而首轮实验一个都用不上。
WRITABLE_FIELDS = (
    "title",
    "descriptionHtml",
    "seo.title",
    "seo.description",
    "tags",
)

#: 获客断点的四段（外加「分不出单一断点」）
#:
#: 四段来自「访问 → 点击 → session → 商品浏览」这条链：
#: 断在哪一段，决定了下一轮该做什么。
BOTTLENECK_STAGES = [
    "no_impression",       # 没有曝光：分发 / SEO / 内容 / 广告没启动
    "no_click",            # 有曝光没点击：素材 / 受众 / 卖点不匹配
    "no_session",          # 有点击没 session：链接 / 追踪 / 落地页有问题
    "no_product_view",     # 有 session 没商品浏览：落地页与商品承接有问题
    "no_single_bottleneck",  # 分不出单一断点（数据量不够就是这种）
]
