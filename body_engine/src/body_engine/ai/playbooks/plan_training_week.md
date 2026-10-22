# 排一周训练
> 按恢复、最近的负荷、禁用动作和用户能练的日子，排好或调整这一周的训练，用户确认后保存。

<!-- AI-facing playbook (issue #100). Served as the MCP prompt "plan_training_week" and by get_playbook; keep tool names exact
     (tests/test_playbooks.py checks every `tool_name` exists). -->

## 步骤
1. 调 `get_training_context`（本周计划、`readiness` 恢复和准备度、`exercises` 禁用 / 注意动作、`recent` 最近 14 天训练和负荷、能练的日子和器械、`rules`、`needs`）。`rules` 必须照做。
2. **先处理 `needs`**：不知道哪几天能练、有哪些器械、从哪天开始，就先问用户；器械问到了用 `update_profile_card` 记进档案卡（key `equipment`），下次不用再问。
3. **还没有周计划，或用户要换一套**：
   - 用户说清楚分化方式、每周几天、能练的星期几后，调 `propose_plan`（不写库）；它返回的 `ask_user_first` 里的问题要先问完；
   - 把草案（每天练什么、组数、推荐重量、本周已经练过之后的 `first_week`）用几行说给用户；
   - **用户确认后**才 `save_plan`；按 `tell_user` 转告（例如训练记录没连上）。
4. **已经有计划，只是这周要调**：
   - 按 `readiness`：红区把今天改成恢复日或减量，黄区不加重量；
   - 某天换练什么用 `reschedule_day`（默认顺带重排本周剩下几天），两天互换用 `swap_days`，从某天起整体重排用 `replan_week`（先 `apply=false` 预览给用户看）；
   - 某个动作不合适（禁用、器械没有、关节不舒服）：`get_exercise` 看替代动作，再 `swap_exercise`。
5. 改完用 `get_week_plan` 核对一遍，用两三行告诉用户这周每天练什么、改了哪里、为什么。

## 什么时候先问用户
- 新排或整套换计划：草案必须先给用户看，确认后才 `save_plan`。
- 不知道能练的日子、器械、开始日期的时候。
- 撤销改动（`undo_day_changes`，会弹确认）之前。

## 护栏
- 只改这个用户自己的计划；看家人的数据时只给建议、不写。
- 禁用动作一律不排、不推荐；伤病和注意事项以档案卡、`exercises` 为准，不凭印象。
- 不编推荐重量：没有训练记录就说「先按体感试，RIR 2–3」。
- 这个场景不用联网。
