"""tests.test_merge_run_history —— 合并履约历史的规则

**部署是最容易「静默失败」的一环**：服务器没有 git，代码靠同步；
履约历史也是**两边各跑各的**（实测：服务器 156 条、本地 149 条，
但各有 100 多条是对方没有的）。

合并错了不报错，只是**看板上的记录少了一半**——
所以这里守三件机械的事：

1. **两边的记录一条都不能丢**（取并集，不是覆盖）；
2. **同一个 task_run_id 留更新的那条**
   （履约记录是追加式日志，「等判定」之后追加的终态才算数）；
3. **合并后的日志按时间排**——它还是一份追加式日志。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "_merge_run_history.py"


def _load():
    spec = importlib.util.spec_from_file_location("merge_run_history_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


merge_mod = _load()


def _rec(rid: str, created: str, **extra: Any) -> dict[str, Any]:
    rec = {"task_run_id": rid, "created_at": created}
    rec.update(extra)
    return rec


# ============================================================
# 合并规则
# ============================================================


def test_both_sides_are_kept():
    """**两边原有的记录一条都不能丢**"""
    remote = [_rec("a", "2026-11-01T00:00:00"), _rec("b", "2026-11-01T01:00:00")]
    local = [_rec("c", "2026-11-02T00:00:00")]
    out = merge_mod.merge_records(remote, local)
    assert {r["task_run_id"] for r in out} == {"a", "b", "c"}


def test_the_newer_record_of_the_same_run_wins():
    """**同一个号留更新的那条**——「等判定」之后追加的终态才算数"""
    remote = [_rec("x", "2026-11-01T00:00:00", status="waiting_external")]
    local = [_rec("x", "2026-11-02T00:00:00", status="completed")]
    out = merge_mod.merge_records(remote, local)
    assert len(out) == 1
    assert out[0]["status"] == "completed"


def test_a_remote_update_wins_over_an_older_local_one():
    remote = [_rec("x", "2026-11-03T00:00:00", status="completed")]
    local = [_rec("x", "2026-11-01T00:00:00", status="waiting_external")]
    out = merge_mod.merge_records(remote, local)
    assert len(out) == 1
    assert out[0]["status"] == "completed"


def test_the_merged_log_is_chronological():
    """**合并后的日志按时间排**——它还是一份追加式日志"""
    remote = [_rec("b", "2026-11-03T00:00:00")]
    local = [_rec("a", "2026-11-01T00:00:00"), _rec("c", "2026-11-02T00:00:00")]
    out = merge_mod.merge_records(remote, local)
    assert [r["task_run_id"] for r in out] == ["a", "c", "b"]


def test_records_without_an_id_are_dropped():
    """没有号的记录没法去重，也没法被引用——别混进日志"""
    assert merge_mod.merge_records([{"created_at": "2026-11-01T00:00:00"}], []) == []


# ============================================================
# 读日志
# ============================================================


def test_a_broken_line_does_not_ruin_the_whole_history(tmp_path: Path):
    """**一行脏数据不该毁掉整份历史**——跳过它，继续读"""
    path = tmp_path / "runs.jsonl"
    path.write_text(
        '{"task_run_id": "a", "created_at": "2026-11-01T00:00:00"}\n'
        "这不是 JSON\n"
        "\n"
        '{"task_run_id": "b", "created_at": "2026-11-02T00:00:00"}\n',
        encoding="utf-8",
    )
    recs = merge_mod.load_records(path)
    assert [r["task_run_id"] for r in recs] == ["a", "b"]


def test_a_missing_file_is_empty_not_a_crash(tmp_path: Path):
    assert merge_mod.load_records(tmp_path / "nope.jsonl") == []


# ============================================================
# 命令行：合并后必须一条都没丢
# ============================================================


def test_the_command_writes_a_merged_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    local = tmp_path / "local.jsonl"
    remote = tmp_path / "remote.jsonl"
    out = tmp_path / "merged.jsonl"
    local.write_text('{"task_run_id": "c", "created_at": "2026-11-02T00:00:00"}\n', encoding="utf-8")
    remote.write_text('{"task_run_id": "a", "created_at": "2026-11-01T00:00:00"}\n', encoding="utf-8")

    code = merge_mod.main([
        "--local", str(local), "--remote", str(remote), "--out", str(out),
    ])
    assert code == 0
    ids = {r["task_run_id"] for r in merge_mod.load_records(out)}
    assert ids == {"a", "c"}
    assert "一条没丢" in capsys.readouterr().out
