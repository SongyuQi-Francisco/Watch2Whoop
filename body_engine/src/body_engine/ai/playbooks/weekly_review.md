# 复盘一周
> 把这一周的恢复、睡眠、训练、饮食、体重、日记和上一周比，给出结论和下周 1–3 条能做到的建议。

<!-- AI-facing playbook (issue #100). Served as the MCP prompt "weekly_review" and by get_playbook; keep tool names exact
     (tests/test_playbooks.py checks every `tool_name` exists). -->

## 步骤
1. 调 `get_week_review_context`（默认最近 7 天到今天；用户说「上周」就把 `end` 设成上周日）。里面有这一周的数字、和上一周的对比（`vs_previous`）、日记和身体事件、`rules`、`needs`。`rules` 必须照做。
2. 看 `needs`：数据不全的（没戴表、饮食只记了几天、没称重）在复盘里直接说明，不要当成「没做」；能问的就问一句（例如其他几天大概吃了什么）。
3. 需要更细的再查，只查和结论有关的：
   - 恢复差 / 睡眠差的那几天：`get_scores`、`get_sleep_details`；
   - 训练：`get_training_calendar`；下周计划：`get_week_plan`；
   - 饮食：`get_nutrition`；某个日记习惯的长期影响：`analyze_journal`。
4. 写复盘（手机上看，短）：
   - **一句话结论**：这周整体怎么样；
   - **最好的一点、最需要注意的一点**，各带一个数字依据（用 `vs_previous` 里的变化，不要自己重新算）；
   - **下周 1–3 条建议**：具体、能做到（例「周三、周五 23:30 前上床」），和用户的目标、饮食约束、训练计划一致。
5. 用户同意调整的：训练用 `reschedule_day` / `replan_week`（整套换计划走「排一周训练」），营养目标用 `set_nutrition_goals`，饮食约束用 `set_diet_constraint`；长期有用的发现用 `add_observation` 记下来（evidence 写这周的数字）。
   你的工具里没有 `set_nutrition_goals` 时（find_tools 也找不到）：只给出建议的数值和理由，告诉用户要改营养目标就用自己接入的 AI 来改。

## 什么时候先问用户
- 改计划、营养目标、饮食约束之前：先说理由，用户同意再写。
- 数据缺得多、结论靠不住的时候：先说明，问用户要不要补记。

## 护栏
- 只复盘这个用户自己的数据；看家人的只看 `hidden` 以外的部分，只给建议、不写。
- 不编数字，没记录 ≠ 没做；不做医学诊断，指标异常建议看 GP。
- 这个场景不用联网。
