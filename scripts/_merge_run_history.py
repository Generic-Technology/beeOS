"""合并两边的履约历史（本地 + 服务器），给看板一份完整的记录。

**为什么需要它**：服务器和本机各跑各的履约，**两边会分叉**——
实测：服务器 156 条、本地 149 条，但**各有 100 多条是对方没有的**
（不是包含关系）。所以**不能直接覆盖**，会丢历史。

不同步的代价：**看板照常渲染，只是那几只盒子显示「RUNS 0」**——
读者会以为「这些盒子从没跑过」，而它们今天真的跑过（还改了店里的页面）。
**页面正常 ≠ 内容对**。

## 合并规则

- **按 task_run_id 取并集**：两边的记录一条都不丢。
- **同一个号留更新的那条**：履约记录是**追加式日志**，
  同一个号可能有多条（典型：先「等判定」，人给了判定再追加终态），
  **后写的才是终态**。
- 合并后按 `created_at` 排好——它还是一份追加式日志。

## 用法

```bash
python scripts/_merge_run_history.py --local logs/task_runs.jsonl \
    --remote /path/to/server_runs.jsonl --out /tmp/merged.jsonl
```

`deploy.sh` 会调它；也可以手动跑（先把服务器那份 scp 下来）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_records(path: Path) -> list[dict[str, Any]]:
    """读一份履约日志（坏行跳过，不让一行脏数据毁掉整份历史）"""
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def merge_records(
    remote_records: list[dict[str, Any]],
    local_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """**按 task_run_id 取并集，同一个号留更新的那条**

    - **两边原有的记录一条都不能丢**——这是硬要求。
    - 追加式日志里同一个号可能有多条（等判定 → 终态），
      **后写的才是终态**。
    """
    merged: dict[str, tuple[tuple[str, int, int], dict[str, Any]]] = {}
    for rank, source in enumerate((remote_records, local_records)):
        for idx, rec in enumerate(source):
            rid = str(rec.get("task_run_id") or "")
            if not rid:
                continue
            key = (str(rec.get("created_at") or ""), rank, idx)
            prev = merged.get(rid)
            if prev is None or key > prev[0]:
                merged[rid] = (key, rec)
    return sorted(
        (rec for _, rec in merged.values()),
        key=lambda r: (str(r.get("created_at") or ""), str(r.get("task_run_id") or "")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="合并本地与服务器的履约历史")
    parser.add_argument("--local", required=True, help="本地那份（logs/task_runs.jsonl）")
    parser.add_argument("--remote", required=True, help="服务器那份（scp 下来的）")
    parser.add_argument("--out", required=True, help="合并结果写哪儿")
    args = parser.parse_args(argv)

    local_records = load_records(Path(args.local))
    remote_records = load_records(Path(args.remote))
    merged = merge_records(remote_records, local_records)

    # **一条都不能少**——这是这条规则的全部意义
    local_ids = {str(r.get("task_run_id")) for r in local_records if r.get("task_run_id")}
    remote_ids = {str(r.get("task_run_id")) for r in remote_records if r.get("task_run_id")}
    merged_ids = {str(r.get("task_run_id")) for r in merged}
    lost = (local_ids | remote_ids) - merged_ids
    if lost:
        print(f"合并丢了 {len(lost)} 条记录——中止，不写", flush=True)
        return 1

    Path(args.out).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in merged) + "\n",
        encoding="utf-8",
    )
    print(
        f"服务器 {len(remote_records)} 条 + 本地 {len(local_records)} 条"
        f" → 合并 {len(merged)} 条（一条没丢）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
