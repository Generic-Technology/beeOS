# 成长记录盒

把每一次学习记成一条带出处的成长记录。

| | |
|---|---|
| box_id | `learning_growth_box` |
| 协作边界 | Learning |
| 价值流 | Learning |
| 成熟度 | **live**（腿是真的） |
| 判据 | 4 条（`mastery` ≥ 0 / `mastery` ≤ 1 / `source_kind` / `source_record_present`） |
| 例外 | 2 条（`source_record_present == false` / `mastery > 1` → escalate） |
| 接线 | **本边界的终点**（汇点：三只学科盒的产出都落进这里） |

## 它吃什么

`subject_result` —— 一条 `ref:` 字段，由**交接通道**从上游那次履约接进来。
**形状归消费者所有**：本盒在 `schemas.py` 里声明它收什么
（`schema_learning_subject_result`），交接通道按这份形状校验上游产出。

## 它吐出什么

**一条**记录：`record_id` / `subject` / `activity` / `observed_at` /
`mastery` / `evidence` / `source_kind` / `detail` / `notes`
+ `source_record_present`。

**档案 = 这只盒子的历次履约产出**，按 `observed_at`（学习发生的时间）排。
落盘是运行时的事：产出进了履约记录，就是进了档案。

**为什么不给档案单建一个文件**：那就是**第二份事实**——
它跟履约记录会漂，而且漂了没有任何东西会报错。

## 档案里没有一个裸数字

每条记录都带着：

| 字段 | 为什么 |
|---|---|
| `evidence` | 出处：作业本 P12 / 单元测卷 / 口述 |
| `source_kind` | `observed`（真的看到了）/ `reported`（家里口述的） |
| `record_id` | 这次学习事件的号（**同一个事实一个号**） |

「批改过的作业上 92 分」和「今天练了半小时」是两种事实，
混在一起的话，半年后翻这份档案就分不清哪些是**看见的**、哪些是**听说的**。
分不清的档案 = 看着完整，其实不能当依据。

## 它不干什么

- **不评价**。它只归档，不打分、不排名、不预测。
- **不补全**。缺字段的记录不入档（`intake` 直接报错）——
  补一条空记录会让人以为那天学过。

## 规矩（跟本仓其它盒子一样）

- **判据只判能机械判的**：读产出里的字段，不读「我觉得对不对」。
- **schema 归消费者所有**：本盒声明自己吃什么、吐什么。
- **不许 import 别的盒子**：`tests/test_layering.py` 管着这条。
- **业务知识住在盒子里**，不进 `core/`。
