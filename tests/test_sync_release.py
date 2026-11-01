"""部署同步脚本：计数口径要说实话，复核失败必须**挡住部署**

**为什么这是条硬要求**（2026-11-01 实测撞出来的）：
那一轮部署一批新盒子，同步脚本打印「更新 0 个文件，删除 0 个过期文件」。
看着像**假绿**——明明新增了 28 个文件。

查下来计数是**老实的**：并行会话在一小时前已经部署过同一个 commit，
服务器目录本来就和 git 逐字节一致，所以 0。

但这件事暴露了一个真问题：**那句话分不清「无事可做」和「没同步成功」**。
当时谁也说不清，只能去服务器上翻文件的创建时刻才查明白。

所以现在：
1. 计数分三类（新增 / 更新 / 未变）——新盒子的目录和被覆盖的文件是两回事。
2. 同步完**逐字节复核**：stage 里每个文件在 target 里都必须存在且内容相同；
   对不上就**非零退出**，让 deploy.sh 在重启服务**之前**中止。
   带着坏掉的目录重启 = 把「没部署成功」变成「跑起来了但跑的是旧的」。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest


def _load_sync_module():
    spec = importlib.util.spec_from_file_location(
        "sync_release_mod",
        pathlib.Path(__file__).resolve().parent.parent / "scripts" / "_sync_release.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def sync_mod():
    return _load_sync_module()


def _stage(tmp_path, files: dict) -> pathlib.Path:
    stage = tmp_path / "stage"
    stage.mkdir()
    for rel, body in files.items():
        path = stage / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return stage


def _target(tmp_path) -> pathlib.Path:
    target = tmp_path / "target"
    target.mkdir()
    return target


def test_new_files_are_counted_as_added_not_lost(sync_mod, tmp_path):
    """新盒子 = 凭空长出来的文件，必须计入「新增」，不能报 0"""
    stage = _stage(tmp_path, {f"boxes/learning_math/f{i}.py": "x" for i in range(28)})
    target = _target(tmp_path)

    stats, stale = sync_mod.sync(stage, target)

    assert stats["added"] == 28
    assert stats["updated"] == 0
    assert stale == []
    assert (target / "boxes" / "learning_math" / "f0.py").exists()


def test_a_changed_file_is_counted_as_updated(sync_mod, tmp_path):
    stage = _stage(tmp_path, {"a.py": "new"})
    target = _target(tmp_path)
    (target / "a.py").write_text("old", encoding="utf-8")

    stats, _ = sync_mod.sync(stage, target)

    assert stats["updated"] == 1
    assert stats["added"] == 0
    assert (target / "a.py").read_text(encoding="utf-8") == "new"


def test_an_identical_file_is_counted_as_unchanged(sync_mod, tmp_path):
    stage = _stage(tmp_path, {"a.py": "same"})
    target = _target(tmp_path)
    (target / "a.py").write_text("same", encoding="utf-8")

    stats, _ = sync_mod.sync(stage, target)

    assert stats["unchanged"] == 1
    assert stats["updated"] == 0


def test_files_absent_from_git_are_deleted(sync_mod, tmp_path):
    stage = _stage(tmp_path, {"a.py": "x"})
    target = _target(tmp_path)
    (target / "old").mkdir()
    (target / "old" / "beeline.py").write_text("stale", encoding="utf-8")

    stats, stale = sync_mod.sync(stage, target)

    assert len(stale) == 1
    assert not (target / "old" / "beeline.py").exists()
    assert not (target / "old").exists()  # 空目录也清掉
    assert stats["added"] == 1


def test_verify_passes_when_everything_landed(sync_mod, tmp_path):
    stage = _stage(tmp_path, {"a.py": "x", "b/c.py": "y"})
    target = _target(tmp_path)

    sync_mod.sync(stage, target)

    assert sync_mod.verify(stage, target) == []


def test_a_silently_skipped_copy_is_caught_by_verify(sync_mod, tmp_path, monkeypatch):
    """同步「看起来成功」但某个文件没落盘 → 必须被逮到"""
    stage = _stage(tmp_path, {"a.py": "x", "b.py": "y"})
    target = _target(tmp_path)

    real_copy = sync_mod.shutil.copy2

    def copy_but_drop_one(src, dst):
        if str(src).endswith("b.py"):
            return  # 模拟「复制了，但没落盘」
        return real_copy(src, dst)

    monkeypatch.setattr(sync_mod.shutil, "copy2", copy_but_drop_one)
    sync_mod.sync(stage, target)

    mismatched = sync_mod.verify(stage, target)
    assert [str(m) for m in mismatched] == ["b.py"]


def test_main_exits_nonzero_when_the_tree_does_not_match_git(sync_mod, tmp_path, monkeypatch):
    """复核不过 → 非零退出 → deploy.sh 的 set -e 会在重启服务前中止"""
    stage = _stage(tmp_path, {"a.py": "x"})
    target = _target(tmp_path)

    monkeypatch.setattr(sync_mod.shutil, "copy2", lambda src, dst: None)

    assert sync_mod.main([str(stage), str(target)]) == 1


def test_main_exits_zero_and_says_so_when_the_tree_matches(sync_mod, tmp_path, capsys):
    stage = _stage(tmp_path, {"a.py": "x"})
    target = _target(tmp_path)

    assert sync_mod.main([str(stage), str(target)]) == 0
    out = capsys.readouterr().out
    assert "复核通过" in out
    # 计数要说清楚是哪几类，别只给一个总数
    assert "新增 1 个" in out
    assert "更新 0 个" in out


def test_a_second_run_over_an_identical_tree_reports_nothing_changed(sync_mod, tmp_path):
    """幂等：第二次同步是「无事可做」，不是「没同步成功」"""
    stage = _stage(tmp_path, {"a.py": "x"})
    target = _target(tmp_path)

    sync_mod.sync(stage, target)
    stats, stale = sync_mod.sync(stage, target)

    assert stats == {"added": 0, "updated": 0, "unchanged": 1}
    assert stale == []
    assert sync_mod.verify(stage, target) == []
