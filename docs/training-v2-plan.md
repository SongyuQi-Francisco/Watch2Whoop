# 训练模块 v2 设计草案（以后的版本，暂不做）

> 状态：2026-10-19 记入路线图，排在公开发布之后。现在 AI 能记训练（`log_workout`）、读训练历史和日历（`get_training_log`、`get_training_calendar` / `get_training_day`），**还不能**建 / 改训练计划，也查不了动作库。

## 目标
- **单一入口**：今天页「记录」tab → 训练，不另装 app。
- **动作库**：动作要点、主要 / 次要肌群、器械、图片。
- **分化选择**（3 / 4 / 5 分化）和**每周计划**。
- **动作推荐**：结合 `exercise_meta` 的禁用 / 小心动作和恢复分。
- 训练记录的后端暂时仍是 SparkyFitness。

## 数据来源（许可证 2026-10-19 查过，导入前再核对一次）
| 来源 | 许可 | 用法 |
|---|---|---|
| [free-exercise-db](https://github.com/yuhonas/free-exercise-db) | Unlicense | **基础动作库**，直接放进仓库：800+ 动作，带说明、主要 / 次要肌群、器械、图片 |
| [wger](https://github.com/wger-project/wger) 动作数据库 | 代码 AGPL；动作数据 CC-BY-SA（导入前再核对） | 第二来源和多语言翻译（含中文），有 REST API |
| [openGym](https://github.com/DuarteSantos8/openGym) | AGPL-3.0 + CLA | 5600+ 演示图 / 动画是只授权给 openGym 的素材，**不能复用**；动作文字和入门计划（PPL、上下肢、全身、5×5）是 AGPL，计划模板可以改写并注明出处，复制前逐项确认。评估记录见 [system-design.md](system-design.md) 第 11 节 |

## 和 AI 的交互（主要输入方式，延续「没有表单」）
1. 用户把抖音 / 小红书链接、截图、照片或文字发给自己的 AI。
2. AI 提取动作、组数、分化方式。视频链接：客户端能抓页面文字 / 画面就抓，抓不到就请用户发截图。
3. 映射到动作库里的动作；不确定就问；遵守 `exercise_meta` 的禁用 / 小心动作。
4. 提出每周计划 → 用户确认 → 保存。

需要的 MCP 工具（还没做）：`search_exercises`、`get_exercise`（要点、肌群）、`propose_plan` / `save_plan`（保存前确认）、`get_week_plan`、`swap_exercise`。
