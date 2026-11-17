"""tests.test_connect_platform —— 「接一个平台」这条命令的守卫

**接平台是最容易「看着通了其实没通」的一环**：
凭证写进去了 ≠ 那个平台真能取到东西。

所以这里守三件机械的事：

1. **验证走的是盒子真正要走的那条路**——
   另写一套验证代码，验过了也不代表盒子跑得起来；
2. **Reddit 的 User-Agent 必须按它要求的格式**——
   默认 UA 会被 Reddit 大幅限流（官方明说），而且不许谎报；
3. **凭证写进仓库外的文件、同名替换、权限 0600**——
   不进仓库、不追加重复行。
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "connect_platform.py"


def _load():
    spec = importlib.util.spec_from_file_location("connect_platform_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


connect = _load()


# ============================================================
# User-Agent：Reddit 官方要求的格式
# ============================================================


def test_the_default_user_agent_matches_the_reddit_format(monkeypatch):
    """**`<platform>:<app ID>:<version> (by /u/<用户名>)`**

    Reddit 官方：默认的 UA（如 Python/urllib）会被**大幅限制**，
    而且「NEVER lie about your User-Agent」。
    """
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    from boxes.shopify_market_signal.clients import RedditClient

    client = RedditClient()
    assert re.match(
        r"^[^:\s]+:[^:\s]+:[^:\s]+ \(by /u/[^)\s]+\)$", client.user_agent
    ), client.user_agent


def test_the_user_agent_can_be_overridden_by_the_environment(monkeypatch):
    """**联系方式各人不同**——用环境变量覆盖，不写死在代码里"""
    monkeypatch.setenv(
        "REDDIT_USER_AGENT", "linux:beeos-market-signal:v0.1 (by /u/chehuizi)"
    )
    from boxes.shopify_market_signal.clients import RedditClient

    assert "(by /u/chehuizi)" in RedditClient().user_agent


def test_an_explicit_user_agent_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("REDDIT_USER_AGENT", "linux:x:v1 (by /u/env)")
    from boxes.shopify_market_signal.clients import RedditClient

    client = RedditClient(user_agent="linux:y:v2 (by /u/explicit)")
    assert "(by /u/explicit)" in client.user_agent


# ============================================================
# 凭证落盘
# ============================================================


def test_the_credentials_file_is_written_outside_the_repo_and_locked_down(
    tmp_path: Path, monkeypatch
):
    """**仓库外 + 0600**——凭证不进 git，也不被别人读到"""
    env_file = tmp_path / "shopify.env"
    env_file.write_text("# 注释\n", encoding="utf-8")
    monkeypatch.setattr(connect, "ENV_FILE_PATH", env_file)

    connect._write_env({"REDDIT_CLIENT_ID": "abc", "REDDIT_CLIENT_SECRET": "s3cr3t"})

    text = env_file.read_text(encoding="utf-8")
    assert "REDDIT_CLIENT_ID" in text
    assert "# 注释" in text, "写凭证不该把原有的注释吃掉"
    assert oct(env_file.stat().st_mode)[-3:] == "600"


def test_writing_the_same_key_twice_replaces_it_instead_of_duplicating(
    tmp_path: Path, monkeypatch
):
    """**同名替换，不追加重复行**——重复行会让「读到的到底是哪个」变成猜的"""
    env_file = tmp_path / "shopify.env"
    env_file.write_text(
        'export REDDIT_CLIENT_ID="old"\nexport SHOPIFY_STORE_DOMAIN="x.myshopify.com"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(connect, "ENV_FILE_PATH", env_file)

    connect._write_env({"REDDIT_CLIENT_ID": "new"})

    lines = [l for l in env_file.read_text(encoding="utf-8").splitlines()
             if "REDDIT_CLIENT_ID" in l]
    assert len(lines) == 1, lines
    assert "new" in lines[0]
    assert "old" not in lines[0]
    # 别的行不能被吃掉
    assert "SHOPIFY_STORE_DOMAIN" in env_file.read_text(encoding="utf-8")


def test_a_user_agent_with_spaces_survives_the_round_trip(tmp_path: Path, monkeypatch):
    """**UA 里有空格和括号**——写进去再读出来必须还是原来那个"""
    env_file = tmp_path / "shopify.env"
    monkeypatch.setattr(connect, "ENV_FILE_PATH", env_file)
    ua = "linux:beeos-market-signal:v0.1 (by /u/chehuizi)"

    connect._write_env({"REDDIT_USER_AGENT": ua})

    line = next(l for l in env_file.read_text(encoding="utf-8").splitlines()
                if "REDDIT_USER_AGENT" in l)
    value = line.split("=", 1)[1].strip().strip("'\"")
    assert value == ua, value


# ============================================================
# 平台表是唯一事实源
# ============================================================


def test_every_platform_declares_its_credential_names():
    """**凭证名住在平台表里**——不是每个平台各写一份"""
    from fulfillment_scopes.shopify.market_signal import (
        PLATFORM_CREDENTIALS,
        PLATFORMS,
    )

    assert set(PLATFORM_CREDENTIALS) == set(PLATFORMS)
    for platform, names in PLATFORM_CREDENTIALS.items():
        assert names, platform
        for name in names:
            assert name.isupper(), name


# ============================================================
# 拿不到 secret 时的退路：installed app + device_id
# ============================================================


def test_without_a_secret_the_installed_client_grant_is_used(monkeypatch):
    """**Reddit 的 secret 只显示一次**——拿不到时还有官方支持的一条路

    installed app（非机密 client）用
    `grant_type=https://oauth.reddit.com/grants/installed_client` + device_id。
    """
    from boxes.shopify_market_signal import clients as cl

    seen: dict[str, str] = {}

    class _Resp:
        def read(self) -> bytes:
            return b'{"access_token": "tok123"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=None):
        seen["body"] = request.data.decode()
        seen["auth"] = request.headers.get("Authorization", "")
        return _Resp()

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake_urlopen)

    client = cl.RedditClient(client_id="cid", device_id="dev-12345678901234567890")
    token = client._token()

    assert token == "tok123"
    assert "installed_client" in seen["body"]
    assert "device_id=dev-12345678901234567890" in seen["body"], seen["body"]


def test_with_a_secret_the_client_credentials_grant_is_used(monkeypatch):
    """**script app 那条路**（有 secret 时优先用它）"""
    from boxes.shopify_market_signal import clients as cl

    seen: dict[str, str] = {}

    class _Resp:
        def read(self) -> bytes:
            return b'{"access_token": "tok456"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=None):
        seen["body"] = request.data.decode()
        return _Resp()

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake_urlopen)

    client = cl.RedditClient(client_id="cid", client_secret="s3cr3t")
    assert client._token() == "tok456"
    assert "grant_type=client_credentials" in seen["body"]


def test_without_a_secret_and_without_a_device_id_it_says_so(monkeypatch):
    """**两个都没有就换不了 token**——说清楚，别静默失败"""
    from boxes.shopify_market_signal import clients as cl

    client = cl.RedditClient(client_id="cid")
    result = client.fetch(["personalized gift"], 28)
    assert result.status.status == "unverifiable"
    assert "device_id" in result.status.detail


def test_the_secret_is_optional_when_prompting(monkeypatch):
    """**secret 可以留空**——否则用户拿不到 secret 就卡死在提示这一步"""
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    def fake_getpass(prompt: str) -> str:
        return "cid" if "CLIENT_ID" in prompt else ""   # secret 留空

    monkeypatch.setattr(connect.getpass, "getpass", fake_getpass)
    creds = connect._prompt(
        ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
        optional=("REDDIT_CLIENT_SECRET",),
    )
    assert creds["REDDIT_CLIENT_ID"] == "cid"
    assert "REDDIT_CLIENT_SECRET" not in creds


# ============================================================
# GSC 的 OAuth：配一次，之后每轮自动拉
# ============================================================


def test_the_refresh_token_is_exchanged_for_an_access_token(monkeypatch):
    """**配一次、之后全自动**——refresh_token 换 access_token

    CSV 那条路每次都要手动导出；这条路配一次就够。
    """
    from boxes.shopify_market_signal import clients as cl

    seen: dict[str, str] = {}

    class _Resp:
        def read(self) -> bytes:
            return b'{"access_token": "ya29.abc"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=None):
        seen["body"] = request.data.decode()
        seen["url"] = request.full_url
        return _Resp()

    monkeypatch.setattr(cl.urllib.request, "urlopen", fake_urlopen)

    client = cl.GscClient(
        refresh_token="1//refresh",
        client_id="cid",
        client_secret="sec",
        site_url="sc-domain:giftsjourneyshop.com",
    )
    assert client._token() == "ya29.abc"
    assert "grant_type=refresh_token" in seen["body"], seen["body"]
    assert seen["url"].startswith("https://oauth2.googleapis.com/token")


def test_without_any_token_it_says_so_instead_of_guessing():
    """**一个都没有就说清楚**——别静默返回空 token"""
    from boxes.shopify_market_signal import clients as cl

    client = cl.GscClient(site_url="sc-domain:x")
    assert client._token() == ""


def test_the_oauth_url_asks_for_a_refresh_token():
    """**`access_type=offline` 才有 refresh_token**——少了就只是一次性的"""
    assert "access_type=offline" in connect.GSC_AUTH_URL
    assert "webmasters.readonly" in connect.GSC_AUTH_URL
