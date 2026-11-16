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
import os
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


def _prompt(names: list[str]) -> dict[str, str]:
    """逐个提示输入凭证（**不回显**）"""
    out: dict[str, str] = {}
    for name in names:
        value = os.environ.get(name, "").strip()
        if not value:
            try:
                value = getpass.getpass(f"{name}（不回显）: ").strip()
            except (EOFError, KeyboardInterrupt):
                raise SystemExit(
                    f"没拿到 {name}——凭证也可以直接写进环境变量再跑一次"
                )
        if not value:
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
            )
        result = client.fetch(["personalized"], 28)
    else:
        return False, f"不认识的平台：{platform}"

    detail = f"{result.status.status}｜{result.status.detail}"
    if result.status.status == "observed":
        return True, f"取到 {len(result.signals)} 条信号（{detail}）"
    return False, detail


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
    parser.add_argument("--dry-run", action="store_true", help="只验，不落盘")
    args = parser.parse_args(argv)

    names = list(PLATFORM_CREDENTIALS.get(args.platform, ()))
    creds: dict[str, str] = {}

    if args.platform == "gsc" and args.gsc_csv:
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
        creds = _prompt(names)
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
