"""Rollout flag ai_parity (T0): every user's AI has the same tools, and Jarvis never names the back end that keeps food and
training logs.

With the flag on for a user:
- the fallback AI (fallback.py) reaches every tool the other channels have for the user's own data, minus
  fallback.CONSENT_EXCLUDED (ECG, beat-to-beat heart data, family data: what its consent keeps from DeepSeek); as before,
  only the core + the current page's first group go with each call (params.FALLBACK_PARITY_PAGE_GROUPS), the rest is
  one find_tools away;
- texts the AI reads (system prompt, tool definitions, tool results and errors, find_tools, playbooks) and texts the user
  sees (the streamed answer, stored messages, confirm cards, errors) go through naming.scrub (ai/naming.py), in the in-page
  chat and on MCP (/mcp and the body butler);
- PROMPT is added to the system prompt.
With the flag off everything behaves as before.
"""
import psycopg

FLAG = "ai_parity"

# AI-facing (Chinese). No back end name in here: naming.scrub would turn it into nonsense.
PROMPT = ("## 说法\n回答里不提内部组件和第三方软件的名字：饮食、训练、体重记录存在哪里，一律说「底层数据库」"
          "（英文 our database）；也不要把工具名、字段名说给用户。")
# Added for the fallback (its short prompt does not list these): the write tools parity gives it
LITE_PROMPT = ("- 改已记的：饮食 `update_food_entry`、几条合成一条 `combine_food_entries`、力量训练某组 `update_training_set`；"
               "快捷记餐 `log_meal_template`（撤销 `undo_meal_log`），存 / 改模板 `create_meal_template` / `update_meal_template`；"
               "食谱 `create_recipe` / `update_recipe`，按份数记一餐 `log_recipe`；状态卡 `set_status_items`；"
               "AI 写错了：`get_write_log` 查、`rollback_write` 回滚（会弹确认）。这些多半不在工具列表里，用 find_tools 找。")


def on(conn: psycopg.Connection, user_id: int) -> bool:
    from ..rollout import flags
    return flags.enabled(conn, user_id, FLAG)


def prompt(fallback: bool) -> str:
    return f"{PROMPT}\n\n## 改记录\n{LITE_PROMPT}" if fallback else PROMPT
