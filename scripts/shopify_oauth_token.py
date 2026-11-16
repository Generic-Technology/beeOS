#!/usr/bin/env python3
"""scripts/shopify_oauth_token.py —— 换一张 Shopify Admin API token

**为什么需要这个脚本**（2026-01 之后的现实）：

- Shopify 已经**不允许在店铺后台新建自定义应用**了；
- Dev Dashboard 建的应用**不再在界面上直接显示 `shpat_` token**；
- 要拿 token 得走一次 **OAuth 授权码流程**：浏览器点一次授权，
  拿回一个几分钟后失效的 `code`，再用它换一张**长期有效**的 token。

这个脚本干三件事：**生成授权链接 → 换 token → 验 token**。

## 两条路，先试不用浏览器的那条

```bash
export SHOPIFY_CLIENT_ID=<Dev Dashboard 里的客户端 ID>
export SHOPIFY_CLIENT_SECRET=shpss_xxx
export SHOPIFY_STORE_DOMAIN=giftsjourneyrebirth.myshopify.com

# 路 A：现换一张（client credentials，24 小时有效期）
#      要求应用**已经安装**在这家店里，且同属一个 Shopify 组织
python scripts/shopify_oauth_token.py --mint

# 路 B：浏览器授权换一张长期 token（应用没装、或组织不匹配时走这条）
python scripts/shopify_oauth_token.py --print-url
# 浏览器打开 → 同意 → 地址栏跳到 http://localhost/?code=… → 复制 code
python scripts/shopify_oauth_token.py --code '粘贴那个 code'
```

**路 A 换出来的 token 24 小时后过期，但不用你管**：
盒子每次跑都会现换一张（用完即弃）。
**路 B 换出来的 token 长期有效**，但要手工走一次浏览器。


## 凭证纪律

- **客户端密钥只在环境变量里**，脚本不打印、不落盘、不进日志；
- `code` 是一次性的、几分钟后失效——所以它在**你自己的终端**里输入，
  不要发到聊天里；
- 换出来的 token 脚本会打印一次（那是它该出现的地方：你的终端），
  请自己贴进 `~/.config/beeos/shopify.env`。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fulfillment_scopes.shopify.auth import load_env_file  # noqa: E402
from fulfillment_scopes.shopify.vocabulary import DOMAIN_ENV  # noqa: E402

CLIENT_ID_ENV = "SHOPIFY_CLIENT_ID"
CLIENT_SECRET_ENV = "SHOPIFY_CLIENT_SECRET"

#: 授权回调地址：指向本机，浏览器会跳过去打不开——**我们要的是地址栏里的 code**
REDIRECT_URI = "http://localhost"

#: 验证 token 用的店铺信息接口（任何有效 token 都能读）
SHOP_API_VERSION = "2025-01"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not _env(n)]
    if missing:
        raise SystemExit(
            f"没配 {', '.join(missing)}——凭证只从环境变量读（不要写进命令行，"
            "会进 shell 历史）"
        )
    return {n: _env(n) for n in names}


def _handle(handle: str) -> str:
    """店铺域名 → 句柄（myshopify.com 前面那段）"""
    handle = handle.strip()
    if handle.endswith(".myshopify.com"):
        return handle[: -len(".myshopify.com")]
    return handle


def authorize_url(client_id: str, handle: str) -> str:
    """生成授权链接

    **不带 scope 参数**：Dev Dashboard 应用的范围在配置里定好了，
    带上去反而可能报参数错。
    """
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
    })
    return f"https://{handle}.myshopify.com/admin/oauth/authorize?{query}"


def exchange_code(client_id: str, client_secret: str, handle: str, code: str) -> dict[str, Any]:
    """用 code 换 token"""
    url = f"https://{handle}.myshopify.com/admin/oauth/access_token"
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400:
            raise SystemExit(
                f"HTTP 400：授权码多半已经过期（只有几分钟有效期）——"
                f"重新跑 --print-url 再授权一次，拿到 code 立刻换。"
                f"\n  详情：{detail[:300]}"
            )
        raise SystemExit(f"HTTP {exc.code}：换 token 失败。详情：{detail[:300]}")


def exchange_client_credentials(
    client_id: str, client_secret: str, handle: str
) -> dict[str, Any]:
    """client_credentials 换一张 token（24 小时有效期）"""
    url = f"https://{handle}.myshopify.com/admin/oauth/access_token"
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"HTTP {exc.code}：换不了。\n"
            "  最常见的原因：**应用还没安装在这家店里**，"
            "或者应用与店不在同一个 Shopify 组织。\n"
            f"  详情：{detail[:300]}"
        )
    except urllib.error.URLError as exc:
        raise SystemExit(f"连不上店铺：{exc.reason}")


def verify(token: str, handle: str) -> dict[str, Any]:
    """验 token：读店铺信息 + 列出实际拿到的 scope"""
    headers = {
        "X-Shopify-Access-Token": token,
        "Accept": "application/json",
    }
    out: dict[str, Any] = {}

    request = urllib.request.Request(
        f"https://{handle}.myshopify.com/admin/api/{SHOP_API_VERSION}/shop.json",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            shop = json.loads(resp.read().decode("utf-8")).get("shop") or {}
            out["shop"] = {
                "name": shop.get("name"),
                "domain": shop.get("myshopify_domain"),
                "currency": shop.get("currency"),
            }
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"HTTP {exc.code}：token 不通。401 = token 与店铺不匹配或被撤销；"
            "403 = 少权限。"
        )

    request = urllib.request.Request(
        f"https://{handle}.myshopify.com/admin/oauth/access_scopes.json",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            scopes = json.loads(resp.read().decode("utf-8")).get("access_scopes") or []
            out["scopes"] = sorted(str(s.get("handle")) for s in scopes)
    except urllib.error.HTTPError:
        out["scopes"] = []

    return out


def _mint(client_id: str, client_secret: str, handle: str) -> int:
    """试 client_credentials：不用浏览器，24 小时有效期"""
    print("=" * 68)
    print("试 client_credentials（应用必须**已安装**在这家店里）…")
    try:
        result = exchange_client_credentials(client_id, client_secret, handle)
    except SystemExit as exc:
        print(str(exc))
        print()
        print("→ 换不了就走浏览器那条：")
        print("   python scripts/shopify_oauth_token.py --print-url")
        return 1

    token = str(result.get("access_token") or "")
    print()
    print("token 换到了（**24 小时后过期**，但盒子每次跑会现换，用完即弃）：")
    print("   ", token)
    print("   scope:", result.get("scope") or "(空)")
    info = verify(token, handle)
    print("验证：", json.dumps(info, ensure_ascii=False, indent=2))
    print()
    print("→ 把它贴进 ~/.config/beeos/shopify.env 的 SHOPIFY_ADMIN_TOKEN")
    print("   （或者只留 client_id + client_secret，盒子会自己换）")
    print("=" * 68)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="换一张 Shopify Admin API token")
    parser.add_argument("--print-url", action="store_true",
                        help="只打印授权链接（走浏览器那条路）")
    parser.add_argument("--mint", action="store_true",
                        help="试 client_credentials（不用浏览器，24 小时有效）")
    parser.add_argument("--code", default="", help="授权后从地址栏复制回来的 code")
    args = parser.parse_args(argv)

    load_env_file()   # 填过 ~/.config/beeos/shopify.env 就不用 export
    env = _require_env(CLIENT_ID_ENV, CLIENT_SECRET_ENV, DOMAIN_ENV)
    client_id = env[CLIENT_ID_ENV]
    handle = _handle(env[DOMAIN_ENV])

    if args.mint:
        return _mint(client_id, env[CLIENT_SECRET_ENV], handle)

    if args.print_url or not args.code:
        url = authorize_url(client_id, handle)
        print("=" * 68)
        print("1) 打开这条链接（**用登录着店铺后台的那个浏览器**）：")
        print()
        print("   ", url)
        print()
        print("2) 点同意安装 → 浏览器会跳到 http://localhost/?code=…")
        print("   那一页打不开是**正常的**——我们要的是地址栏里的 code。")
        print("3) 复制 code（只有 `code=` 后面那一段），然后跑：")
        print()
        print(f"    python scripts/shopify_oauth_token.py --code '<code>'")
        print("=" * 68)
        return 0

    result = exchange_code(client_id, env[CLIENT_SECRET_ENV], handle, args.code.strip())
    token = str(result.get("access_token") or "")
    if not token:
        raise SystemExit(f"没拿到 access_token：{json.dumps(result, ensure_ascii=False)[:300]}")
    granted = str(result.get("scope") or "")

    print("=" * 68)
    print("token 换到了（**只显示这一次，请立刻存进 ~/.config/beeos/shopify.env**）：")
    print()
    print("   ", token)
    print()
    print("   拿到的 scope：", granted or "(空)")
    print("=" * 68)

    info = verify(token, handle)
    print()
    print("验证：", json.dumps(info, ensure_ascii=False, indent=2))
    needed = {"read_reports", "read_products", "write_products"}
    have = set(info.get("scopes") or [])
    missing = needed - have
    if missing:
        print()
        print(f"⚠️ 少了这些 scope：{', '.join(sorted(missing))}")
        print("   → Dev Dashboard 里补上 → **Release 版本** → 重新安装 → 再换一次 token")
    else:
        print()
        print("✅ 该有的 scope 都在。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
