#!/usr/bin/env python3
"""scripts/connect_platform.py —— 接一个站外平台（凭证 + 验证 + 落盘）

**为什么要有它**：市场信号盒要四个平台，每个平台的凭证名、
验证方式、落盘位置都一样。把这三件事写进每个平台一次，
就变成了三份会漂的清单。

所以这里只做一件事：**照着平台表把凭证接上，并当场验一遍**。

## 它做的三件事

1. **读凭证**：优先读环境变量；没有就**逐个提示输入（不回显）**——
   密钥不进命令行（会留在 shell 历史里），也不进对话。
2. **验证**：**用盒子真正要走的那条路**去验（同一个客户端类）——
   另写一套验证代码，验过了也不代表盒子跑得起来。
3. **落盘**：写进 `~/.config/beeos/shopify.env`（**仓库外**，0600），
   同名的一行替换掉，不追加重复行。

## 用法

```bash
python scripts/connect_platform.py --platform reddit
python scripts/connect_platform.py --platform pinterest
python scripts/connect_platform.py --platform etsy
python scripts/connect_platform.py --platform gsc --gsc-csv ~/Downloads/Queries.csv
```

**GSC 有一条免授权的路**（`--gsc-csv`）：把 Search Console 导出的
「查询」CSV 丢给它就行，不用 OAuth、不用 token。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fulfillment_scopes.shopify.auth import ENV_FILE_PATH  # noqa: E402
from fulfillment_scopes.shopify.market_signal import (  # noqa: E402
    PLATFORM_CREDENTIALS,
    PLATFORMS,
)


def _prompt(names: list[str], optional: tuple[str, ...] = ()) -> dict[str, str]:
    """逐个提示输入凭证（**不回显**）

    `optional` 里的可以留空——Reddit 的 secret 只显示一次，
    用户拿不到时还有 installed app（device_id）那条路。
    """
    out: dict[str, str] = {}
    for name in names:
        value = os.environ.get(name, "").strip()
        if not value:
            try:
                value = getpass.getpass(f"{name}（不回显）: ").strip()
            except (EOFError, KeyboardInterrupt):
                if name in optional:
                    break
                raise SystemExit(
                    f"没拿到 {name}——凭证也可以直接写进环境变量再跑一次"
                )
        if not value:
            if name in optional:
                continue
            raise SystemExit(f"没有 {name}——这一步过不去")
        out[name] = value
    return out


def _verify(platform: str, creds: dict[str, str], gsc_csv: str = "") -> tuple[bool, str]:
    """**用盒子真正要走的那条路验一遍**

    验的不是「凭证格式对不对」，是「这个平台能不能取到东西」。
    """
    from boxes.shopify_market_signal import clients as cl

    if platform == "reddit":
        client = cl.RedditClient(
            client_id=creds.get("REDDIT_CLIENT_ID", ""),
            client_secret=creds.get("REDDIT_CLIENT_SECRET", ""),
        )
        result = client.fetch(["personalized gift"], 28)
    elif platform == "etsy":
        client = cl.EtsyClient(
            api_key=creds.get("ETSY_API_KEY", ""),
            api_secret=creds.get("ETSY_API_SECRET", ""),
        )
        result = client.fetch(["personalized gift"], 28)
    elif platform == "pinterest":
        client = cl.PinterestClient(
            access_token=creds.get("PINTEREST_ACCESS_TOKEN", ""),
        )
        result = client.fetch(["personalized gift"], 28)
    elif platform == "gsc":
        if gsc_csv:
            client = cl.GscClient(csv_path=gsc_csv)
        else:
            client = cl.GscClient(
                access_token=creds.get("GSC_ACCESS_TOKEN", ""),
                site_url=creds.get("GSC_SITE_URL", ""),
                # **OAuth 那条路**：用 refresh_token 现场换一张 access_token
                refresh_token=creds.get("GSC_REFRESH_TOKEN", ""),
                client_id=creds.get("GSC_CLIENT_ID", ""),
                client_secret=creds.get("GSC_CLIENT_SECRET", ""),
            )
        result = client.fetch(["personalized"], 28)
    else:
        return False, f"不认识的平台：{platform}"

    detail = f"{result.status.status}｜{result.status.detail}"
    if result.status.status == "observed":
        return True, f"取到 {len(result.signals)} 条信号（{detail}）"
    return False, detail


GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_AUTH_URL = (
    "https://accounts.google.com/o/oauth2/v2/auth"
    "?client_id={client_id}"
    "&scope=" + GSC_SCOPE +
    "&redirect_uri=http://localhost:{port}"
    "&response_type=code"
    "&access_type=offline"          # ← 关键：要 refresh_token（长期）
    "&prompt=consent"
)
GSC_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _gsc_oauth_setup(client_id: str, client_secret: str, site_url: str,
                     port: int = 8765) -> dict[str, str]:
    """**走一遍 OAuth 授权**，换一张长期 refresh_token

    流程（用户只做两件事：在浏览器点「允许」）：
    1. 起一个本地小服务，等 Google 把授权码送回来；
    2. 打印授权链接 → 用户打开、同意 → 浏览器跳回本地 → 我们拿到 code；
    3. code 换 token（带 refresh_token = 以后不用再授权）；
    4. 返回要落盘的四个值。

    **为什么值得做**：CSV 那条路每次都要手动导出；
    这条路配一次，之后每轮自动查——**这才是「自动实验」需要的**。
    """
    import http.server
    import threading
    import urllib.parse
    import urllib.request

    captured: dict[str, str] = {}
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802（http.server 的命名）
            query = urllib.parse.urlparse(self.path).query
            params = dict(urllib.parse.parse_qsl(query))
            if "code" in params:
                captured["code"] = params["code"]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "授权好了，可以关掉这个页面了。"
                .encode("utf-8")
            )
            done.set()

        def log_message(self, *args: Any) -> None:      # 别刷屏
            return

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = GSC_AUTH_URL.format(client_id=client_id, port=port)
    print("\n  ① 打开这个链接（用你登录了 Google 的浏览器）：\n")
    print(f"     {url}\n")
    print("  ② 选你的 Google 账号 → 点「允许」→ 浏览器会跳回 127.0.0.1（页面会显示「授权好了」）")
    print("  ③ 回来这里，我接着换长期令牌…")
    try:
        done.wait(timeout=300)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()

    if "code" not in captured:
        raise SystemExit("没收到授权码——上面那步没走完？再跑一次就行。")

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": captured["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": f"http://localhost:{port}",
    }).encode("utf-8")
    request = urllib.request.Request(
        GSC_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    refresh_token = str(payload.get("refresh_token") or "")
    if not refresh_token:
        raise SystemExit(
            "没拿到 refresh_token——"
            "多半是之前授权过（Google 不会再发）。"
            "去 Google 账号的「第三方应用」里删掉这个应用，再跑一次。"
        )
    return {
        "GSC_CLIENT_ID": client_id,
        "GSC_CLIENT_SECRET": client_secret,
        "GSC_REFRESH_TOKEN": refresh_token,
        "GSC_SITE_URL": site_url,
    }


def _write_env(entries: dict[str, str]) -> None:
    """写进仓库外的凭证文件（同名替换，不追加重复行）"""
    path = ENV_FILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out: list[str] = []
    written: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        name = stripped.split("=", 1)[0].strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        if name in entries:
            if name in written:
                continue          # 旧文件里有重复行 → 只留一行
            out.append(f'export {name}="{entries[name]}"')
            written.add(name)
            continue
        out.append(line)
    for name, value in entries.items():
        if name not in written:
            out.append(f'export {name}="{value}"')
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="接一个站外平台")
    parser.add_argument("--platform", required=True, choices=list(PLATFORMS))
    parser.add_argument("--gsc-csv", default="",
                        help="GSC 免授权那条路：Search Console 导出的「查询」CSV")
    parser.add_argument("--gsc-oauth", action="store_true",
                        help="GSC **配一次、之后全自动**那条路：走 OAuth 授权换长期令牌")
    parser.add_argument("--dry-run", action="store_true", help="只验，不落盘")
    args = parser.parse_args(argv)

    names = list(PLATFORM_CREDENTIALS.get(args.platform, ()))
    creds: dict[str, str] = {}

    if args.platform == "gsc" and args.gsc_oauth:
        # ---- OAuth：配一次，之后每轮自动查 ----
        client_id = os.environ.get("GSC_CLIENT_ID", "").strip() or _prompt(
            ["GSC_CLIENT_ID"])["GSC_CLIENT_ID"]
        client_secret = os.environ.get("GSC_CLIENT_SECRET", "").strip() or _prompt(
            ["GSC_CLIENT_SECRET"])["GSC_CLIENT_SECRET"]
        site_url = os.environ.get("GSC_SITE_URL", "").strip() or _prompt(
            ["GSC_SITE_URL"])["GSC_SITE_URL"]
        creds = _gsc_oauth_setup(client_id, client_secret, site_url)
    elif args.platform == "gsc" and args.gsc_csv:
        source = Path(args.gsc_csv).expanduser()
        if not source.exists():
            raise SystemExit(f"CSV 不存在：{source}")
        default_csv = Path.home() / ".config" / "beeos" / "gsc_queries.csv"
        default_csv.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, default_csv)
        print(f"→ CSV 已放到 {default_csv}（市场信号盒每轮自动读它）")
        creds = {}
    else:
        print(f"[{args.platform}] 要这几个（环境变量里没有就逐个提示，不回显）：")
        for name in names:
            print(f"  - {name}")
        creds = _prompt(
            names,
            optional=("REDDIT_CLIENT_SECRET",) if args.platform == "reddit" else (),
        )
        if args.platform == "reddit":
            # **User-Agent 要按 Reddit 的格式写**：
            # `<platform>:<app ID>:<version> (by /u/<用户名>)`
            # Reddit 明确说默认 UA 会被大幅限制，而且「不许谎报 UA」。
            try:
                who = input("你的 Reddit 用户名（u/xxx，User-Agent 里要写，可回车跳过）: ").strip()
            except (EOFError, KeyboardInterrupt):
                who = ""
            if who:
                who = who.lstrip("/u/").strip()
                creds["REDDIT_USER_AGENT"] = (
                    f"linux:beeos-market-signal:v0.1 (by /u/{who})"
                )
                print(f"  → User-Agent: {creds['REDDIT_USER_AGENT']}")
            if not creds.get("REDDIT_CLIENT_SECRET"):
                # **installed app 那条路不需要 secret**——
                # 用 device_id 授权（官方支持）。device_id 必须**稳定**，
                # 所以生成一次存进凭证文件，以后复用。
                device_id = "".join(
                    secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                    for _ in range(24)
                )
                creds["REDDIT_DEVICE_ID"] = device_id
                print("  → 没给 secret → 走 installed app 那条路（用 device_id）")

    print(f"[{args.platform}] 验证中（用的是盒子真正要走的那条路）…")
    ok, detail = _verify(args.platform, creds, args.gsc_csv)
    print(f"  → {detail}")
    if not ok:
        print("  没验过就不落盘——凭证有问题，落进去也是坏的。")
        return 1

    if args.dry_run:
        print("  （--dry-run：不落盘）")
        return 0

    if creds:
        _write_env(creds)
        print(f"  → 凭证已写进 {ENV_FILE_PATH}（0600，仓库外）")
    print(f"[{args.platform}] 好了。下一轮盒子就会去这个平台取信号。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
