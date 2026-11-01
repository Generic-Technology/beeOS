# 英语盒

把一次英语练习记成一条带出处的学习事件

| | |
|---|---|
| box_id | `learning_english_box` |
| 域 | Learning |
| 价值流 | Learning |
| 成熟度 | **live**（腿是真的） |
| 判据 | 3 条（`mastery` ≥ 0 / `mastery` ≤ 1 / `ratio_exact`） |
| 例外 | 2 条（`mastery > 1` / `ratio_exact == false` → escalate） |
| 接线 | `learning_growth_box`（本域的汇点） |

## 它吃什么

**拍一张作业照就行**，也可以手工填：

| 字段 | 是什么 |
|---|---|
| `photo` | 作业照片（手机拍的作业/试卷）。投了它，本盒会**看图**读出得分、满分、日期和错题 |
| `activity` | 这次做了什么（作业 / 单元测 / 订正 / 复习） |
| `observed_at` | 学习发生的时间（ISO） |
| `evidence` | 出处：作业本 P12 / 单元测卷 / 口述 |
| `source_kind` | `observed`（真的看到了）/ `reported`（家里口述的） |
| `dictation_score` | 听写的得分 |
| `dictation_total` | 听写的满分 |
| `errors` | 这次错在哪（一条一个，人话摘要） |
| `notes` | 备注 |

| new_words | 新学的单词数 |

## 它吐出什么

一条**带出处**的学习事件：`record_id` / `subject` / `activity` /
`observed_at` / `mastery` / `evidence` / `source_kind` / `detail` / `notes`，
外加一个 `ratio_exact`（装配跳复算过得分率）。

**产出字段是成长记录盒要收的那份形状的同构副本** ——
那边是消费者，那边说了算；两份必须对得上，
守卫盯着：`tests/test_learning_boxes.py`。

## 照片怎么读（2026-11-01 起）

- **看图是独立的一跳**（`english_read_photo`）：模型只**抄**图上印着的东西，
  认不出的字段留空——**不猜**。猜一个满分，错的是以后每一份档案里的比率。
- **手工填的优先，照片只补空**：人填的是他当场知道的；
  两边都有时以人填的为准，不悄悄叠成一个说不清是谁说的数。
- **缺什么一次报齐**：少了日期、少了满分，履约会中止并把要补的字段一次列全
  （「照片里没读出来学习发生的时间（observed_at）」）。
- **投了照片就不能标 `reported`**：照片是「看到了」的证据。
- **图片字节不进产出**：产出只带照片的指纹（`evidence` 里写着
  `作业照片 photo:<指纹>`），不带着几 MB 的 base64 落盘。

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
