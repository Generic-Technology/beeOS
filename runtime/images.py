"""图片格式归一：把浏览器/手机给的照片变成模型认得的 data URL

**为什么在 runtime，不在各盒子的 runner 里**：
HEIC 转 JPEG 这件事，2026-11-01 之前只有小票盒需要（`boxes/receipt/runner.py`
里有一份）。现在学习盒也要——车宥安的作业照片是 iPhone 拍的，默认就是 HEIC。
**四只盒子各抄一份转换逻辑，就是四份会各自漂移的真相**：
修好一处，另外三处继续收 HEIC 然后在模型接口上 400。

所以它搬到了 runtime：**这是技术能力，不是业务知识**。
runtime 不含业务知识这条规矩守的是「别让内核替所有盒子背业务」，
而「HEIC 是 iPhone 的默认拍照格式、模型接口不收它」是**跟业务无关的格式事实**。
盒子的业务差异（这是小票还是作业、分数在哪一栏）仍然留在盒子里。

两条解码路，按可用性排队（见 `_heic_to_jpeg`）。
"""

from __future__ import annotations

import base64
import binascii
import shutil
import subprocess
import tempfile
from pathlib import Path

# 模型接口不收这两种（实测 400：invalid param: image data url media type
# ["image/heic"] not supported）。转换后到模型手里一定是 JPEG。
NEEDS_CONVERSION = {"image/heic", "image/heif"}


def data_url_mime(data_url: str) -> str:
    """data URL → MIME（不是 data URL 就返回空串）"""
    text = (data_url or "").strip()
    if not text.lower().startswith("data:"):
        return ""
    head = text[5:].split(";")[0].strip().lower()
    return head


def as_viewable_image(photo: str, *, heic_decoder=None) -> str:
    """HEIC/HEIF 转成 JPEG 再交给模型；别的格式原样透传

    **为什么非得转**：两个独立的原因，少一个都还能忍，两个叠一起就绕不过——
      1. 模型接口直接 400：invalid param: image data url media type
         ["image/heic"] not supported（确定性拒绝，重试无用）
      2. Chrome 解不了 HEIC，投料预览是一张破图。传出去的是 JPEG、
         PNG 还是 HEIC，取决于下游要什么，不取决于相机怎么存。

    非 data URL（http 直链之类）本地无从转，原样交给下游。
    """
    head, _, b64 = photo.partition(",")
    if "base64" not in head:
        return photo
    mime = head[len("data:"):].split(";")[0].strip().lower()
    if mime not in NEEDS_CONVERSION:
        return photo
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"HEIC 图片 base64 解不开：{exc}") from exc
    return "data:image/jpeg;base64," + base64.b64encode(
        (heic_decoder or heic_to_jpeg)(raw, mime)
    ).decode("ascii")


def heic_to_jpeg(raw: bytes, mime: str, *, decoders=None) -> bytes:
    """HEIC/HEIF 字节 → JPEG 字节

    **两条解码路都留着，按可用性排队**：

      1. `pillow_heif` —— 跨平台纯 wheel（libheif 打包在 wheel 里），
         声明成项目依赖，所以 Linux 服务端和 macOS 开发机走同一条路。
      2. `sips` —— macOS 自带，零安装。没装 pillow_heif 的 macOS 机器靠它兜底。

    **为什么以前只写 sips 会炸**：那是 macOS 专有工具。开发机在 macOS 上，
    测试全绿；部署到 Linux 服务端第一次投 HEIC 就履约中止——
    而那条真转换测试在没有 sips 的机器上是 `pytest.skip`，
    于是「唯一验证过的那条路」恰好是唯一没在生产跑过的路。

    **为什么库优先、sips 兜底**：sips 是子进程 + 临时文件，行为随 OS 变；
    库是同一个二进制、同一份结果。两条都试，还都不成，就如实报出
    每条路的原话，不悄悄退回「原样发出去」——那等于把 400 原封不动
    还给用户，只是晚了一拍。
    """
    tried: list[str] = []
    for name, decode in (decoders or (
        ("pillow_heif", heic_via_pillow_heif),
        ("sips", heic_via_sips),
    )):
        try:
            jpeg = decode(raw)
        except Exception as exc:  # noqa: BLE001 - 一条路失败要接着试下一条
            tried.append(f"{name}: {exc}")
            continue
        # 改个 MIME 标签不叫转码。SOI 魔数是「真的出了 JPEG」的最低证据。
        if jpeg[:3] == b"\xff\xd8\xff":
            return jpeg
        tried.append(f"{name}: 输出不是 JPEG（缺 SOI 魔数）")
    detail = "；".join(tried) if tried else "没有可用的解码路"
    raise ValueError(
        f"这台机器解不了 {mime}，转不成 JPEG（{detail}）。"
        "运营侧装 pillow-heif（pip install pillow-heif）即可；"
        "投料侧暂时请把照片另存为 JPEG 再投。"
    )


def heic_via_pillow_heif(raw: bytes) -> bytes:
    """pillow_heif 解码：HEIC → RGB → JPEG 字节

    延迟 import：这是个只有 HEIC 投料才会走到的分支，
    不该让没装它的机器在 import 阶段就 ModuleNotFoundError 起不来。
    """
    import io

    import pillow_heif
    from PIL import Image

    # 不用 with：HeifFile 没实现 __exit__，with 会直接 TypeError。
    # 它也不持有需要显式关闭的文件句柄（字节已在内存里），不解也行。
    heif = pillow_heif.open_heif(io.BytesIO(raw))
    # 1.8 起 HeifFile 自己就能 to_pillow()，不再挂在 .primary 上。
    # getattr 兜住老版本：这条路线在开发机和服务器上是同一个库，
    # 但两边 pip 装出来的版本不一定同一天。
    target = getattr(heif, "primary", heif)
    pil = target.to_pillow()
    # JPEG 没有 alpha 通道也不吃 16bit，HEIC 里两样都可能带；
    # 不显式 convert，save 会抛一个和「HEIC 解不开」无关的错。
    rgb = pil.convert("RGB")
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=90)
    return out.getvalue()


def heic_via_sips(raw: bytes) -> bytes:
    """macOS 自带的 sips 转码：零安装的兜底路"""
    sips = shutil.which("sips")
    if sips is None:
        raise RuntimeError("没装（macOS 自带工具，非 macOS 没有）")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in"
        dst = Path(tmp) / "out.jpg"
        src.write_bytes(raw)
        # 不用 shell=True、不经管道：参数是固定字面量，没有拼接用户输入
        proc = subprocess.run(
            [sips, "-s", "format", "jpeg", str(src), "--out", str(dst)],
            capture_output=True, timeout=60, check=False,
        )
        if not dst.is_file() or dst.stat().st_size == 0:
            raise RuntimeError(
                (proc.stderr or proc.stdout or b"").decode(
                    "utf-8", "replace"
                ).strip()
                or f"退出码 {proc.returncode}，没出文件"
            )
        return dst.read_bytes()
