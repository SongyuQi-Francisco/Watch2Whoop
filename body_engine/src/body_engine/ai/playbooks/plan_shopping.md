# 规划采购
> 按这周要吃的列采购清单，在用户已连接的超市查价、写回，最后告诉用户去哪家买、大概多少钱。

<!-- AI-facing playbook (issue #100). Served as the MCP prompt "plan_shopping" and by get_playbook; keep tool names exact
     (tests/test_playbooks.py checks every `tool_name` exists). -->

## 步骤
1. 调 `get_shopping_context`（本周计划和清单、饮食约束、已连接超市、存货、`needs_price`、`rules`）。`rules` 必须照做。
2. **清单还没有 / 用户说下周的菜单变了**：先问用户这周想吃什么、几个人吃、有没有不想买的；需要时 `suggest_meals` / `list_recipes` 给几个选项，用户定了再 `generate_shopping_list`（返回的 `warnings` 要转告）。零星要加的用 `add_shopping_items`，家里还有的用 `set_pantry`。
3. **没有已连接的超市**（`connected_stores` 是空的）：问用户平时去哪家，用户同意后 `set_stores`；用户不想连就跳过查价，只给清单。
4. **查价**：只查 `needs_price` 里的商品，一次最多 `rules` 里写的数量；先看 `get_weekly_specials` 有没有对上的半价。你有联网工具才去超市的商品页 / 搜索页查；没有就请用户发商品页截图或价签照片。
5. 查到的价格用 `record_store_prices` **一次写回**（商品页 URL、规格、每包价格、特价和原价、查询时间、`for_item` 对应清单项）；返回里每一条单独成功 / 失败，失败的照原因改了再写，不要整批重来。
6. 用 `get_shopping_list` 看结果，告诉用户：每家合计、哪家最省、半价和省下的钱、还缺价的几样（`missing` / `stale`）。
7. 用户买完说「买了 / 花了多少」：`mark_bought`（有实付就填 `paid`）；拍了小票就按小票逐样 `record_store_prices`（`bought=true`）。

## 什么时候先问用户
- 这周的菜单、份量、预算不清楚的时候。
- 要连接新的超市（`set_stores`）、要整份重新生成清单的时候。
- 删清单项（`remove_shopping_items`）之前（会弹确认）。

## 护栏
- 只处理这个用户自己的清单和超市；看家人的数据时只给建议、不写。
- 不绕过任何网站的限制：页面要登录、打不开、出现验证码或 403，就停下来告诉用户，改用截图；不要换地址、换身份或反复重试。
- 联网只查清单上（`needs_price`）的东西，不整站抓取、不翻目录页、不顺手查别的。
- 价格只写你在页面 / 截图上真正看到的，看不清就标出来问用户，不要估。
