# 语文盒

把一次语文练习记成一条带出处的学习事件

| | |
|---|---|
| box_id | `learning_chinese_box` |
| 域 | Learning |
| 价值流 | Learning |
| 成熟度 | **live**（腿是真的） |
| 判据 | 3 条（`mastery` ≥ 0 / `mastery` ≤ 1 / `ratio_exact`） |
| 例外 | 2 条（`mastery > 1` / `ratio_exact == false` → escalate） |
| 接线 | `learning_growth_box`（本域的汇点） |

## 它吃什么

手工投料，一次语文练习：

| 字段 | 是什么 |
|---|---|
| `activity` | 这次做了什么（作业 / 单元测 / 订正 / 复习） |
| `observed_at` | 学习发生的时间（ISO） |
| `evidence` | 出处：作业本 P12 / 单元测卷 / 口述 |
| `source_kind` | `observed`（真的看到了）/ `reported`（家里口述的） |
| `reading_score` | 阅读理解的得分 |
| `reading_total` | 阅读理解的满分 |
| `errors` | 这次错在哪（一条一个，人话摘要） |
| `notes` | 备注 |

| writing_words | 作文写了多少字 |
| new_chars | 新学会的字数 |

## 它吐出什么

一条**带出处**的学习事件：`record_id` / `subject` / `activity` /
`observed_at` / `mastery` / `evidence` / `source_kind` / `detail` / `notes`，
外加一个 `ratio_exact`（装配跳复算过得分率）。

**产出字段是成长记录盒要收的那份形状的同构副本** ——
那边是消费者，那边说了算；两份必须对得上，
守卫盯着：`tests/test_learning_boxes.py`。

## 它不干什么

- **不判断孩子学得好不好**。掌握度只是这次练习的**得分率**，
  不是评价，也不是预测。
- **不替他说话**。`source_kind=reported` 的记录，
  半年后翻档案时仍然看得出它是「听说的」而不是「看到的」。

## 规矩（跟本仓其它盒子一样）

- **判据只判能机械判的**：读产出里的字段，不读「我觉得对不对」。
- **schema 归消费者所有**：本盒声明自己吃什么、吐什么。
- **不许 import 别的盒子**：`tests/test_layering.py` 管着这条。
- **业务知识住在盒子里**，不进 `core/`。
