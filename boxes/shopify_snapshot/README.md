# 店铺快照盒（shopify_snapshot_box）

> 把这一轮窗口里店铺的流量事实取回来，写成一份带出处、带状态的快照。

**它只读。** 不判断、不建议、不改任何商品设置。

## 产出里没有一个裸数字

每条指标都带三样：

| 字段 | 为什么 |
| --- | --- |
| `status` | `observed` / `declared_from_baseline` / `unverifiable`（三态） |
| `source` | 取自哪张 schema、哪个 API 版本（`observed` 必填） |
| `note` | 为什么取不到（`unverifiable` 必填） |

**取不到写 `unverifiable`，不写 0。** 0 是观测值，「没取到」不是——
两者混在一起，下游会把「没数据」读成「没有流量」。

## 合格访问只算一次

`qualified_sessions` 是**在产出里一次算出来的**，不是「查两次相减」。
规则文本同时写进 `qualified_session_rule`，读者不用回头猜口径。

排除**只按首个落地页**：

```
/account/  /password/  /policies/  /apps/
```

一个 session 后来访问了购物车或结账页，恰恰说明它是高意向流量——
按路径把整个 session 删掉，会把最值钱的那几个会话扔掉。

## 数据从哪来

`shopifyqlQuery`（GraphQL Admin API）：

- 需要 `read_reports` scope + **Level 2 受保护客户数据** + API 版本 ≥ 2025-10；
- `FROM sessions` 给流量、漏斗、落地页、来源、地区、真人/机器人过滤；
- 维度 `agentic_referring_channel` 给 AI 引荐渠道（ChatGPT 等）——
  **引荐来源是事实，「商品被 AI 引用」不是**。

## 四种失败形态要分开

Shopify 的失败**都可能是 HTTP 200**：语法错在 `parseErrors`，
权限不足是 `ACCESS_DENIED`，响应过大是 `RESPONSE_TOO_LARGE`，
被限流是 `THROTTLED`。只看状态码的客户端会把「语法错了」
当成「查到了空数据」，然后写下一片 0。

## 「没取到」和「取到了但标注不诚实」是两件事

产出里的 `data_present` 就是分开这两件事的标志：

| 情况 | 状态 | 为什么 |
|---|---|---|
| 什么都没取到（凭证没配 / 权限没批） | 判据全红 → **被拒**，原因写在 `unverified` 里 | 那不是「这次交付要不要放行」的决定，那是要去配凭证 |
| 取到了，但某条 `observed` 没有出处 | **升级给人判定** | 这是要人看的：标注不诚实 |

混成一件事的后果：每次试跑都在问人一个根本不用问的问题，
而真正的原因（权限不够）被埋在产出里没人看。

## 凭证

`SHOPIFY_ADMIN_TOKEN` / `SHOPIFY_STORE_DOMAIN`（环境变量）。
**凭证不进投料**——投料里出现 token，它就会一路进产出、进归档、进日志。

## 成熟度：skeleton

腿（ShopifyQL 客户端 + 口径计算）写了，但**还没在真店上跑过**——
权限审批还没下来。按仓里的纪律：静态体检、单元测试通过**不代替**真实履约。
第一次真跑成功之后改成 `live`。

**没跑过时的样子**：产出一份**什么量都没有**的快照（量字段缺席，不是 0），
判据因此全红 → 整轮被拒 → 不进档案。

## 文件

| 文件 | 装什么 |
| --- | --- |
| `__init__.py` | 入口（`get_definition` / `get_beeline`） |
| `schemas.py` | 5 个 schema |
| `definition.py` | `DECLARATION` |
| `beeline.py` | `COMPONENTS`——3 跳 |
| `runner.py` | 3 跳的实现 |
| `shopifyql_client.py` | 只读 ShopifyQL 客户端（四种失败形态分开处理） |
