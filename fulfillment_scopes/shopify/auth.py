"""fulfillment_scopes.shopify.auth —— 这个边界怎么拿到一张能用的 token

**两把钥匙，两种拿法**（2026 年的现实）：

| 钥匙 | 怎么来的 | 有效期 |
|---|---|---|
| `shpat_` access token | OAuth 授权码流程（浏览器点一次） | **一直到应用被卸载** |
| client_id + client_secret | Dev Dashboard 的「设置」页 | 换出来的那张 token **24 小时** |

本模块把两把都支持：

1. 环境变量里有 `SHOPIFY_ADMIN_TOKEN` → 直接用（最省事）；
2. 没有 token，但有 client_id + client_secret → **当场换一张**
   （client credentials grant，要求应用已安装在同一家店里）。

第 2 条的意义：24 小时到期这件事**不用人管**——
每次跑盒子都现换一张，用完就扔。

## 为什么凭证只从环境变量读

投料里出现 token，它就会一路进产出、进归档、进日志。
而归档是要长期留存的东西。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fulfillment_scopes.shopify.vocabulary import (
    DOMAIN_ENV,
    TOKEN_ENV,
)

#: Dev Dashboard「设置」页给的两样东西
CLIENT_ID_ENV = "SHOPIFY_CLIENT_ID"
CLIENT_SECRET_ENV = "SHOPIFY_CLIENT_SECRET"


#: 凭证文件（**在仓库外**）：填一次，之后所有脚本与盒子都认它
ENV_FILE_PATH = Path.home() / ".config" / "beeos" / "shopify.env"


class ShopifyAuthError(RuntimeError):
    """拿不到 token"""


def load_env_file() -> list[str]:
    """读 `~/.config/beeos/shopify.env`，把里面缺的变量补进环境

    **环境变量优先**——已经设好的不覆盖（临时改一个值不必改文件）。
    文件读不动就当没有：凭证从哪来是使用者的事，脚本不该因此崩。

    为什么要有这个文件：交互式 shell 的 `export` **进不到工具进程**的环境里，
    于是「用户在自己终端里 export 了，脚本却读不到」——每跑一次都要重新 source。
    文件是双方都能读到的那一份。
    """
    if not ENV_FILE_PATH.exists():
        return []
    loaded: list[str] = []
    try:
        text = ENV_FILE_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        # **去掉 `export ` / `set ` 前缀**——
        # 不剥的话变量名会变成 "export SHOPIFY_CLIENT_ID"，
        # 设进环境也没人认得（踩过）。
        for prefix in ("export ", "set "):
            if name.lower().startswith(prefix):
                name = name[len(prefix):].strip()
                break
        value = value.strip().strip("'\"")
        if not name or name in os.environ:
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def handle_of(store_domain: str) -> str:
    """`giftsjourneyrebirth.myshopify.com` → `giftsjourneyrebirth`"""
    domain = store_domain.strip()
    if domain.endswith(".myshopify.com"):
        return domain[: -len(".myshopify.com")]
    return domain


def mint_client_credentials_token(
    handle: str,
    client_id: str,
    client_secret: str,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """用 client_credentials 换一张 token（24 小时有效期）

    **前提**：应用已经装在这家店里，且应用与店属于**同一个 Shopify 组织**。
    不满足时 Shopify 会拒——那不是 bug，是这条路的边界。
    """
    url = f"https://{handle}.myshopify.com/admin/oauth/access_token"
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ShopifyAuthError(
            f"HTTP {exc.code}：换 token 失败——"
            "client_credentials 要求应用**已安装**在这家店里、"
            "且应用与店属于同一个 Shopify 组织。"
            f"\n  详情：{detail[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ShopifyAuthError(f"连不上店铺：{exc.reason}") from exc


def resolve_token(store_domain: str, *, timeout: float = 30.0) -> tuple[str, str]:
    """拿到一张能用的 token。

    返回 `(token, 来源说明)`。

    **优先用环境变量里的 token**；没有就现换一张——
    这样 24 小时到期不需要人管。
    """
    # **这里不读凭证文件**——只有脚本（probe / round / token）会读。
    # 盒子在测试里跑的时候，测试必须**与环境无关**：
    # 开发者机器上有没有凭证，不该决定单测红不红。
    token = _env(TOKEN_ENV)
    if token:
        return token, f"环境变量 {TOKEN_ENV}"

    client_id = _env(CLIENT_ID_ENV)
    client_secret = _env(CLIENT_SECRET_ENV)
    if client_id and client_secret:
        handle = handle_of(store_domain)
        result = mint_client_credentials_token(
            handle, client_id, client_secret, timeout=timeout
        )
        minted = str(result.get("access_token") or "")
        if not minted:
            raise ShopifyAuthError(
                f"没拿到 access_token：{json.dumps(result, ensure_ascii=False)[:300]}"
            )
        return minted, (
            f"用 {CLIENT_ID_ENV} + {CLIENT_SECRET_ENV} 现换的"
            "（client credentials，24 小时有效期；每次跑现换，用完即弃）"
        )

    raise ShopifyAuthError(
        f"既没有 {TOKEN_ENV}，也没有 {CLIENT_ID_ENV} + {CLIENT_SECRET_ENV}——\n"
        f"  ③ 或者把值填进 {ENV_FILE_PATH}（脚本会自动读它）\n"
        "  ① 有长期 token：export SHOPIFY_ADMIN_TOKEN='shpat_…'\n"
        "  ② 只有应用密钥：export SHOPIFY_CLIENT_ID=… SHOPIFY_CLIENT_SECRET=…\n"
        "     （需要应用已安装在这家店里）\n"
        "  凭证只从环境变量读——不要放进投料，它会一路进归档。"
    )
