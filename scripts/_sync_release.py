"""以 git 内容为准，同步到部署目录。

为什么需要这个：`git archive | tar xzf -` **只解压，不删除**。
所以每一个被 git 删掉的文件，都会永远留在服务器上。

实测踩过：把 9 条 beeline 从 `beelines/` 搬进 `boxes/<box>/` 之后，
服务器上 `beelines/` 整个包还在——两份 beeline 并存，
旧的还带着过时的 op 链。谁 import 到哪一份，取决于 import 路径的写法，
而没有任何东西会报错。

**两份真相并存，且不会自己暴露**——这是静默降级里最难查的一种。

所以部署必须做减法：目标目录里凡是 git 里没有的，一律删掉
（运行时数据目录除外：.venv / logs / data）。

## 为什么同步完还要再校验一遍

同步前那句「更新 N 个文件，删除 M 个过期文件」**分不清两件事**：
- 「服务器上已经和 git 一致，这次无事可做」
- 「同步没成功，什么都没更新」

2026-11-01 实测差点误判：部署一批新盒子，计数打印「更新 0 个文件」，
看着像假绿（明明新增了 28 个文件）。查下来是**并行会话在一小时前
已经部署过同一个 commit**——服务器目录本来就和 git 逐字节一致，
所以 0 是老实的。但当时没人能一眼看出来，因为那句话**没说自己凭什么**。

所以现在同步完做一次**逐字节复核**：stage 里的每个文件，
在 target 里必须存在且内容完全相同。对不上就**非零退出**，
让 `deploy.sh` 的 `set -e` 在重启服务**之前**中止——
带着坏掉的目录重启，等于把「没部署成功」变成「跑起来了但跑的是旧的」。

## 计数为什么分三类

「更新 N 个文件」把「新增」和「改了」混在一起：新盒子的目录是凭空长出来的，
和「已有文件被覆盖」是两回事，排查时要分得开。
"""

import filecmp
import os
import shutil
import sys
from pathlib import Path

# 这些目录是运行时资产，不归 git 管，不能删
PRESERVE_DIRS = {".venv", "logs", "data", "__pycache__", ".git"}


def should_skip(path, target):
    rel = path.relative_to(target)
    return bool(PRESERVE_DIRS & set(rel.parts))


def sync(stage: Path, target: Path):
    """把 stage 的内容同步到 target，返回 (统计, 过期文件列表)。

    统计分三类：新增 / 更新 / 未变。过期文件**当场删掉**。
    """
    # 目标里多出来的文件 = git 里已经没有的
    stale = []
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if should_skip(path, target):
            continue
        rel = path.relative_to(target)
        if not (stage / rel).exists():
            stale.append(path)

    for path in stale:
        path.unlink()
        print(f"  删（git 里已无）: {path.relative_to(target)}")

    # 清掉因此变空的目录
    for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not should_skip(path, target):
            try:
                next(path.iterdir())
            except StopIteration:
                path.rmdir()
                print(f"  删空目录: {path.relative_to(target)}")

    # 覆盖写入
    added = updated = unchanged = 0
    for src in stage.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(stage)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if filecmp.cmp(src, dst, shallow=False):
                unchanged += 1
                continue
            updated += 1
        else:
            added += 1
        shutil.copy2(src, dst)

    return {"added": added, "updated": updated, "unchanged": unchanged}, stale


def verify(stage: Path, target: Path):
    """逐字节复核：stage 里的每个文件在 target 里都必须存在且内容相同。

    返回不一致的相对路径列表（空 = 一致）。
    """
    mismatched = []
    for src in stage.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(stage)
        dst = target / rel
        if not dst.exists():
            mismatched.append(rel)
        elif not filecmp.cmp(src, dst, shallow=False):
            mismatched.append(rel)
    return mismatched


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    stage, target = Path(args[0]), Path(args[1])

    stats, stale = sync(stage, target)
    print(
        f"  新增 {stats['added']} 个，更新 {stats['updated']} 个，"
        f"未变 {stats['unchanged']} 个，删除 {len(stale)} 个过期文件"
    )

    mismatched = verify(stage, target)
    if mismatched:
        print(f"✗ 同步后复核不通过：{len(mismatched)} 个文件与 git 不一致")
        for rel in mismatched[:20]:
            print(f"    不一致: {rel}")
        if len(mismatched) > 20:
            print(f"    ……还有 {len(mismatched) - 20} 个")
        print("  目标目录没有和 git 一致——不要重启服务，先查原因。")
        return 1

    print(f"✓ 复核通过：目标目录与 git 逐字节一致（{stats['added'] + stats['updated'] + stats['unchanged']} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
