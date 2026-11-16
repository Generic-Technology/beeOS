"""boxes.shopify_acquisition_experiment.shopify_writer —— 店铺内容写入客户端

**它能改真实的店。** 所以这一份代码里钉了四道闸：

| 闸 | 做什么 | 为什么 |
|---|---|---|
| 写闸 | `SHOPIFY_ALLOW_WRITES` 不是 "1" 就**拒绝写** | 改营业中的页面不报错，误改要人工才发现 |
| 字段白名单 | 只写 `title` / `descriptionHtml` / `seo.*` / `tags` | 价格、库存、变体、发布状态**误改要赔钱** |
| 一次一个落地页 | 一次调用只改一个商品 | 一轮只改一个变量——同时改两处，结果不可归因 |
| 改前基线 | 写之前先读一遍旧值，存进产出 | 没有基线就没法回滚、没法对照 |

## 凭证只从环境变量读

`SHOPIFY_ADMIN_TOKEN` / `SHOPIFY_STORE_DOMAIN`，
**不进投料、不进产出、不进错误消息**。

## 为什么这个盒子自己有一份客户端，而不是用快照盒那份

**盒子之间不许互相 import**（仓里的分层守卫：`tests/test_layering.py`）。
读一份、写一份是两份代码——但它们**碰的不是同一件事**：
读只读会话数据，写只写商品字段。
真要合并，得先解决「跨盒共享业务类型受 import 顺序限制」那个已知缺口。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fulfillment_scopes.shopify.auth import ShopifyAuthError, resolve_token
from fulfillment_scopes.shopify.vocabulary import (
    ALLOW_WRITES_ENV,
    DOMAIN_ENV,
    WRITABLE_FIELDS,
)


class ShopifyWriteError(RuntimeError):
    """写入失败"""


class WriteNotAllowed(ShopifyWriteError):
    """**写闸关着**——这次只出方案，不落笔"""


#: 读商品现状（改前基线用）
_READ_PRODUCT = """
query ReadProduct($handle: String!) {
  products(first: 1, query: $handle) {
    nodes {
      id
      title
      handle
      descriptionHtml
      seo { title description }
      tags
    }
  }
}
"""

#: 写商品（白名单字段）
_UPDATE_PRODUCT = """
mutation UpdateProduct($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product {
      id
      title
      descriptionHtml
      seo { title description }
      tags
    }
    userErrors { field message }
  }
}
"""


@dataclass
class ShopifyWriter:
    """只改商品页内容的写入客户端"""

    store_domain: str
    token: str
    api_version: str = "2026-10"
    allow_writes: bool = False
    timeout: float = 30.0
    opener: Optional[Callable[[urllib.request.Request, float], Any]] = None

    # ---- 构造 ----

    @classmethod
    def from_env(cls, **kwargs: Any) -> "ShopifyWriter":
        """从环境变量读凭证与**写闸**

        写闸跟凭证放在一起读，是故意的：
        「配了写权限 token」和「允许写」是**两件不同的事**，
        后者必须显式开。
        """
        domain = os.environ.get(DOMAIN_ENV, "").strip()
        if not domain:
            raise ShopifyWriteError(f"没配 {DOMAIN_ENV}——店铺域名从环境变量读")
        try:
            token, _source = resolve_token(domain)
        except ShopifyAuthError as exc:
            raise ShopifyWriteError(str(exc)) from exc
        allow = os.environ.get(ALLOW_WRITES_ENV, "").strip() == "1"
        return cls(
            store_domain=domain,
            token=token,
            allow_writes=allow,
            **{"opener": None, **kwargs},
        )

    # ---- 读 ----

    def read_product(self, handle: str) -> Optional[dict[str, Any]]:
        """读一个商品的现状（改前基线）"""
        payload = self._post(_READ_PRODUCT, {"handle": f"handle:{handle}"})
        nodes = (
            ((payload.get("data") or {}).get("products") or {}).get("nodes") or []
        )
        if not nodes:
            return None
        return dict(nodes[0])

    # ---- 写 ----

    def update_product(
        self, product_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """改一个商品的**白名单字段**

        返回 `{"applied_fields": {...}, "user_errors": [...]}`。
        `user_errors` 非空 = Shopify 拒了某一项，**如实带回去**，不吞。
        """
        if not self.allow_writes:
            raise WriteNotAllowed(
                f"写闸关着（{ALLOW_WRITES_ENV} != 1）——这次只出方案，不落笔。"
                "改营业中的页面不会报错，所以这一道必须有人显式开。"
            )

        allowed = _filter_whitelisted(changes)
        if not allowed:
            raise ShopifyWriteError(
                "要改的字段一个都不在白名单里——"
                f"白名单是 {', '.join(WRITABLE_FIELDS)}"
            )

        product: dict[str, Any] = {"id": product_id}
        for key, value in allowed.items():
            if key.startswith("seo."):
                product.setdefault("seo", {})[key.split(".", 1)[1]] = value
            else:
                product[key] = value

        payload = self._post(_UPDATE_PRODUCT, {"product": product})
        result = (payload.get("data") or {}).get("productUpdate") or {}
        errors = [
            f"{e.get('field')}: {e.get('message')}"
            for e in (result.get("userErrors") or [])
        ]
        applied = dict(result.get("product") or {})
        stored = _flatten(applied)
        # **没报错 ≠ 写进去了**——
        # 实测：seo.title 跟商品标题一模一样时，Shopify 把它**静默存成 null**，
        # 不报 userErrors。所以「写成功」要看**存下来的值**，不是看有没有报错。
        #
        # **只判「写了非空、存成空」**——不判「值不一样」：
        # Shopify 会给标签排序、给正文补 HTML 属性，
        # 值不一样不代表没写进去；**存成空才是被吞掉的signature**。
        not_stuck = [
            key for key, value in allowed.items()
            if value not in (None, "") and stored.get(key) in (None, "")
        ]
        return {
            "applied_fields": stored,
            "user_errors": errors,
            "not_stuck": not_stuck,
        }

    # ---- HTTP ----

    def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        url = f"https://{self.store_domain}/admin/api/{self.api_version}/graphql.json"
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": self.token,
                "User-Agent": "beeOS-shopify-experiment/0.1",
            },
        )
        try:
            if self.opener is not None:
                raw = self.opener(request, self.timeout).read()
            else:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    raw = resp.read()
        except urllib.error.HTTPError as exc:  # pragma: no cover - 网络异常
            raise ShopifyWriteError(f"HTTP {exc.code}：Shopify 请求失败") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - 网络异常
            raise ShopifyWriteError(f"连不上店铺：{exc.reason}") from exc

        payload = json.loads(raw.decode("utf-8"))
        errors = payload.get("errors") or []
        if errors:
            messages = "; ".join(str(e.get("message") or e) for e in errors)
            raise ShopifyWriteError(f"GraphQL 报错：{messages}")
        return payload


def _filter_whitelisted(changes: dict[str, Any]) -> dict[str, Any]:
    """只留白名单里的字段——**不在白名单的一律不写**"""
    return {
        key: value
        for key, value in (changes or {}).items()
        if key in WRITABLE_FIELDS and value not in (None, "")
    }


def _flatten(product: dict[str, Any]) -> dict[str, Any]:
    """把商品压成可对照的扁平字段"""
    seo = product.get("seo") or {}
    return {
        "title": product.get("title"),
        "descriptionHtml": product.get("descriptionHtml"),
        "seo.title": seo.get("title"),
        "seo.description": seo.get("description"),
        "tags": product.get("tags") or [],
    }
