# kanban - 履约盒子运行面板（CLI + Web）

> 按 [beebox-design.md §4.2] 实现的运行面板（PoC 4 CLI 版 + PoC 5 Web 版）。
> 设计定位：**只读 + 运维操作**（启动/停止/切流），不编辑设计层对象。

## 与 workshop 的区别

| 面板 | 角色 | 对象 | 何时用 |
|---|---|---|---|
| **workshop** | owner / 管理员 | 设计层（definition / release / beeline / schema）| 想改盒子时 |
| **kanban**（本包）| 运维 / 观察者 | 运行时（task run / 异常 / 指标）| 想看盒子情况时 |

## 模块

```
kanban/
  __init__.py
  cli.py        # CLI 看板（python -m kanban.cli）
  web.py        # Web 看板（python -m kanban.web，HTTP server + HTML + JS）
  trigger.py    # task 投料口（BOX_REGISTRY + trigger_task：校验 → 执行 → 验收 → 落盘）
runtime/
  store.py      # TaskRunStore（JSONL 持久化 + 查询 + view 过滤）
```

> **可见性**：看板只展示 `BOX_REGISTRY`（trigger.py）里已注册的盒子；
> 未注册盒子的历史记录保留在 JSONL 审计日志里，只是不显示、不可触发。
> 当前已注册的盒子全部都已注册；仓库里不再有「代码保留但未注册」的盒子。

## CLI 入口

```bash
# CLI 看板（默认读 logs/task_runs.jsonl）
.venv/bin/python -m kanban.cli

# 只看业务设计盒
.venv/bin/python -m kanban.cli --box wms_design_box

# 自定义 store + recent 数量
.venv/bin/python -m kanban.cli --store path/to/store.jsonl --recent 20
```

## Web 入口

```bash
# 启动 Web 看板（默认 127.0.0.1:8765）
.venv/bin/python -m kanban.web

# 自定义端口 + store
.venv/bin/python -m kanban.web --port 9876 --store logs/task_runs.jsonl

# 外部可访问（不推荐，仅本地开发）
.venv/bin/python -m kanban.web --host 0.0.0.0 --port 8765
```

然后浏览器打开 `http://localhost:8765`

### Web 特性

- **boxes 过滤器**：顶部 chip 一键切换（全部 / 单 box）
- **固定头部**：滚动时头部不跟着走——它是「这是 beeOS 看板 / 上次刷新是什么时候」
  这两句话唯一的位置，往下翻拓扑图时不该消失
- **总览页整页不滚**：宽屏下 body 锁成一屏，左（任务列表）/ 中（画面）/ 右（轨迹）
  **三栏各自内部滚**——读者想盯着某只盒子的 op 顺序看，一滚就没了。
  窄屏（≤760px）三栏叠成一行，这时恢复整页滚
- **中栏三段式**：固定头（视图 / 范围选择器）+ 可滚画面（BeeBox Map）+ 固定底
  （任务描述输入框）。选择器控制的就是下面那块画面，画面滚下去时它也得看得见；
  「我现在要干一件事」这句话不该被滚走
- **任务描述输入框**：总览页底部的自然语言投料口。写一句业务需求 →
  投给**当前范围里吃自然语言的那只盒**（按盒子自己声明的 `accepts_free_text` 找，
  不按 box_id 硬编码）。当前范围里没有这种盒子时框是**灰的**并写明原因——
  一个能写却不知道会投去哪的框更糟。投递走 `POST /api/structure`
- **环上的线只画三类**：相邻的**交接**（粗实线，主链，一圈清清楚楚）、
  **累积**（淡虚线辐条，产出并进档案）、**反馈**（点线，上一轮结论回到起点）。
  辐条退到背景——它真实但次要，抢眼时中心那一块就成了一团星芒
- **跨位的交接不画线，写在盒子上**（「也交给 WMS Testing Box」）：
  环上两条边的端点**交替**时它们的弦必然相交，画出来是**乱线**，
  读者分不出谁交给谁；写成一句话反而更准——它是**额外**的一条交接，
  不是替代主链。关系一条不少，图上却干净了
- **右侧操作轨迹面板**：常驻一条，跟左侧盒子列表并排——左边回答「有哪些盒子」，
  右边回答「它们刚才跑了哪几步」。每只盒子显示最近一次履约的 op 执行顺序
- **自动刷新**：JS 每 2 秒 fetch `/api/data`，DOM 增量更新（无 full reload）
  ——**重画会把滚动位置弹回顶部**，所以中栏记下位置再放回去
- **状态着色**：
  - `completed` = 蓝 / `failed` = 红
  - `accepted` = 绿 / `rejected` = 红 / `pending` = 灰
- **响应式卡片**：status / acceptance / metrics 各为卡片网格
- **表格视图**：recent task runs + active exceptions

### Web API

| 端点 | 用途 |
|---|---|
| `GET /` | HTML 页面 |
| `GET /api/data` | JSON 全量 dashboard 数据 |
| `GET /api/data?box=xxx` | JSON 单 box 数据 |
| `GET /api/boxes` | 盒子目录 + 各自 API 面 |
| `GET /api/runs/{run_id}` | 单次履约的完整记录（含产出本体）|
| `GET /api/boxes/{id}` | 单只盒子的定义（它是什么）|
| `GET /api/boxes/{id}/artifacts` | 单只盒子产出过的产物（形状由 result_schema 定）|
| `POST /api/trigger` | 触发 1 次履约（`{box_id, task_type, payload}`），返回执行轨迹 `op_trace` + 验收结果 |
| `POST /api/structure` | 自然语言业务表述 → 结构化表达（走盒子的 task，不是影子函数）|

## 数据落盘

TaskRunStore 用 JSONL 追加写（每行一条 record）：

```json
{
  "task_run_id": "abc-123",
  "box_id": "wms_analysis_box",
  "beeline_id": "beeline_wms_analysis_v1",
  "beeline_version": 1,
  "intent": "handle_effect_analysis_request",
  "originator": "analysis.api",
  "status": "completed",
  "acceptance_status": "accepted",
  "started_at": "2026-09-23T23:00:00+00:00",
  "finished_at": "2026-09-23T23:00:00.123456+00:00",
  "created_at": "2026-09-23T23:00:00+00:00",
  "op_count": 3,
  "exception_count": 0,
  "duration_ms": 123
}
```

## 手动 record 示例

```python
from runtime.store import TaskRunStore
from runtime import create_task_run, BeelineExecutor, evaluate_acceptance
from boxes.wms_analysis import get_definition, get_beeline
from boxes.wms_analysis.runner import RUNNER

box = get_definition()
beeline = get_beeline()

# 跑 task run
tr = create_task_run(...)
tr = BeelineExecutor(RUNNER).execute(tr, beeline, input_data)
eval_result = evaluate_acceptance(tr, box.result)

# 落盘
store = TaskRunStore(path="logs/task_runs.jsonl")
store.append(tr, box_id=box.id, acceptance_status=eval_result.status.value)
```

## 查询接口

| 方法 | 用途 |
|---|---|
| `list_recent(limit, box_id)` | 最近 task run 列表（倒序，可按 box 过滤）|
| `count_by_status()` | 按 status 计数 |
| `count_by_acceptance()` | 按 acceptance status 计数 |
| `list_exceptions(limit)` | 含异常或失败的 task run |
| `aggregate_metrics(box_id)` | 聚合指标（总数 / 通过率 / 平均时长）|
| `list_boxes()` | 所有出现过的 box |

## 限制

- store 由调用方 append——`trigger.py` 的 `trigger_task` 会落盘，但 executor 本身不自动落盘
- 触发是**同步执行**（设计盒 7 op 毫秒级，POST 直接返回终态）；
  真实 worker 接入后应异步化（POST 只落盘 triggered 状态）
- 没有 instance / runtime 视图（设计稿有，还没做）
- Web 看板无认证（线上部署也是裸的——这是个已知缺口，不是设计选择）|