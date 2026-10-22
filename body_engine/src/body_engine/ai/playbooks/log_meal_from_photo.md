# 照片记餐
> 从用户发来的饭菜照片、外卖截图或营养成分表认出吃了什么、估算份量和营养，记进饮食记录。

<!-- AI-facing playbook (issue #100). Served as the MCP prompt "log_meal_from_photo" and by get_playbook; keep tool names exact
     (tests/test_playbooks.py checks every `tool_name` exists). -->

## 步骤
1. **认出每样食物和份量**：按可见份量估克数（参照餐具、手、包装），说清楚估算依据；营养成分表 / 外卖页上有数就用上面的数。看不清、认不出就问，不要猜。
2. **定日期和餐次**：日期 = 吃的那天（凌晨约 5 点前说「刚才 / 今晚」算前一天），餐次按时间和用户说的。
3. **先找现成的**：
   - 常吃的固定一餐：`get_meal_templates` → `log_meal_template`（这次份量不同用 `overrides`）；
   - 食谱库里的菜：`list_recipes`（`q` 搜菜名）→ `log_recipe`（主食另外 `log_food`）；
   - 包装食品的营养成分表 / 价签：`find_products` 查商品库，没有就 `save_product` 存下来。
4. **查重**：`get_nutrition`（`include_entries=true`）看这一餐是不是已经记过；工具也会拦同天同餐同名的重复。
5. **记录**：`log_food`（每样一条；热量和三大营养素要自洽，知道糖 / 纤维 / 钠就填，不知道留空；酒类填 `alcohol_g`）。
6. **回报**：一两行说清记了什么、每样的估算（克数、热量、蛋白），标出拿不准的；返回里的 `warnings`（例如超过用户设的上限）要转告。按用户的饮食约束（`get_briefing` 的 `diet_constraints`）提醒，没有约束不要替用户加限制。
7. 用户纠正份量：`update_food_entry` 改；记错了要删用 `undo_sparky_entry`（会弹确认）。

## 什么时候先问用户
- 照片里认不出来、份量差别很大（例如一碗饭不知道是半碗还是满碗）、有好几个人一起吃不知道吃了多少的时候。
- 不确定是不是已经记过的时候。
- 删除记录之前（会弹确认）。

## 护栏
- 只记这个用户自己吃的；看家人的数据时不写。
- 估算要说依据，不编数字；营养成分表上的数优先。
- 这个场景不用联网；外卖平台、超市页面打不开就请用户发截图，不要绕过限制。
