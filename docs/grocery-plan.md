# 采购：周采购清单、商品库、家里存货（2026-10-20）

饮食 tab 的第二段「采购」。接着系统设计第 15 节（食谱管理）：食谱和快捷记餐模板已经带食材、克数、采购单位和门店，这一步把「下周吃什么」变成「这周买什么」。

## 0. 原则
- **靠 AI 输入**：用户把小票、价签、营养成分表的照片发给自己的 AI，AI 读出来调工具存结构化数据（商品库）；「下周吃这几道菜」也是对 AI 说，AI 调 `generate_shopping_list`。今天页不做输入表单。
- **界面只放数字和短标签**：本周清单（按类别分组，点一下勾掉已买）和商品库（规格、价格、每 100g 营养、健康标记）。解释和建议留在工具返回里给 AI 读。
- **不引入新服务**：评估过 Grocy / KitchenOwl（第 15 节），它们的存货和清单模型比这里需要的重，又是单独的账号体系；这里只是 Body Engine 里 4 张表。
- **多用户**：每张表都有 `user_id`；读别人的要 shares 里有 `nutrition`（和食谱一样，不另开 scope）；写只能写自己的。

## 1. 表（迁移 `240_grocery.sql`）
| 表 | 内容 |
|---|---|
| `grocery_products` | 商品库：名字、品牌、门店（自由文本）、Coles / Woolworths 商品页 `product_url` + 从链接解析的 `store_sku`（迁移 `260_grocery_store_links.sql`，第 7 节）、营养来源 `nutrition_source`、类别、包装规格（数量 + 单位 g / ml / pcs，按个卖的可以写每个多少克 `piece_g`）、价格 + 币种 + 更新日期、每 100g（ml）营养（kcal、蛋白、碳水、脂肪、饱和脂肪、糖、钠 mg，知道多少写多少）、嘌呤等级（low / medium / high，和快捷记餐模板同一套）、是不是红肉 / 加工肉、条码、`matches`（这件商品能顶哪些食材名，例如「鸡胸肉」「chicken breast」） |
| `pantry_items` | 家里存货（粗略）：名字、数量、单位、对应商品。买了自动加，AI 听用户说「鸡蛋还有 6 个」时直接改 |
| `shopping_lists` | 每人每周一张（`week_start` = 用户时区的周一），存生成时用的计划 `plan` |
| `shopping_items` | 清单的每一行：名字、对应商品、类别、门店、计划需要的量（扣掉存货后）、要买几包、买多少、估价、来源（plan / manual）、用在哪些菜、买了没有（`bought_at`、实付价格） |

## 2. 生成周清单
输入是这周要吃的东西（AI 根据用户说的、食谱推荐 `suggest_meals` 或自己规划给出）：
- 食谱 `recipe` × 份数：按整锅食材 × 份数 / 菜谱份数折算；
- 快捷记餐模板 `template` × 次数（例如固定早餐 × 7）；
- 单独的食材 `food`（数量 + 单位），给 AI 自己规划、不在食谱库里的东西。

步骤：按食材名合并 → 对上商品库（名字或 `matches` 完全一致优先，其次互相包含）→ 换成商品的单位（按个卖的用 `piece_g` 换算）→ 扣掉家里存货 → 按包装规格向上取整（没对上商品的按 50 g 取整）→ 估价 → 按类别分组。
重新生成只替换「计划来的、还没买的」行；手动加的和已经买的保留（已买的量已经进了存货，会被扣掉）。

**健康约束提示**（返回 `warnings`，阈值在 `grocery/params.py`，带出处）：清单里红肉合计超过每周上限（`rules/params.RED_MEAT_G_PER_WEEK`）、牛肋条超过每周次数、高嘌呤商品、每 100g 饱和脂肪 / 糖 / 钠偏高的商品（英国 FSA 包装正面红绿灯的「高」线）。都是提示，不拦着买；AI 读到后决定怎么跟用户说。

## 3. 买了 / 存货
- 勾掉一行（今天页点一下，或 AI 调 `mark_bought`）→ `bought_at`，存货加上这一行的购买量；取消勾选再减回去。
- 告诉 AI 实付价格 → 按包数折成单价，更新商品库价格和日期。
- 做饭 / 记餐暂时不自动扣存货（粗略即可）：用户说「鸡蛋吃完了」时 AI 调 `set_pantry`。
- **今天页看得到存货（#102，迁移 495）**：「采购」里本周清单下面是「家里存货」：按类别（商品的类别，没对上商品的按名字猜）再按名字排，每行名字、数量、「x 天前更新」。自己的页面每行有「用完了」（`PUT …/pantry` 数量 0，toast 里可以撤销，撤销把原来的数量写回去）；看别人的只读。改数量仍然对 AI 说。生成清单时被存货扣掉一部分的行记下扣了多少（`shopping_items.pantry_qty`），清单上显示短标签「家里有 6 个」（`pantry_label`，按页面语言），解释为什么少买；全部被存货盖住的不进清单（`covered_by_pantry`）。

- **常买（#103，迁移 500）**：商品可以标成「常买」（`grocery_products.staple`，带间隔天数 `staple_every_days`、每次买多少 `staple_qty` + `staple_unit`，`pack` = 包数）。
  - **推荐**：按这个用户自己的 `grocery_purchases`，最近 `STAPLE_SUGGEST_WEEKS`（8）周买过 ≥ `STAPLE_SUGGEST_MIN`（3）次、还没标的商品，放进 `get_shopping_context` 的 `staple_suggestions`（次数、建议间隔 = 自己购买间隔的中位数）。只推荐：AI 问过用户、用户同意后才调 `set_staple`，不自动标；用户在对话里拒绝过的推荐不再提（`rules` 里写明，不加新列）。
  - **下次该买** = 上次买的日期 + 间隔（从没买过 = 今天）。
  - **生成周清单**：到这周日为止到期的常买商品自动加进清单，来源 `staple`；存货已经够一次的量就不加（进 `covered_by_pantry`）；还没到期的不加。菜单里也有同一样东西时合并成一行（来源 `plan`），取两者里大的那个量（菜单的量已经补够了，不再叠加）。重新生成时 `plan` / `staple` 来的未买行一起替换，手动加的和已买的保留。有常买商品时可以只生成常买（`plan` 为空）。
  - **工具**：`set_staple`（写，直接跑）、`remove_staple`（`ALWAYS_CONFIRM` + 管家的 `ask`）。接口 `PUT / DELETE …/products/{id}/staple`（只能改自己的），商品接口多 `staple`、`staple_info`（`every_days`、`qty`、`unit`、`last_bought`、`next_due`、`due`）；清单行多 `staple`。
  - **今天页**：清单行显示「常买」；商品库里常买商品显示「常买」和「下次该买 10/28」（到期了用提示色），详情里显示间隔和每次的量。

## 4. 接口和工具
接口（前缀 `/v1/users/{user}/grocery`，scope `nutrition`）：`GET /products?q=&category=`、`POST /products`、`PATCH /products/{id}`、`DELETE /products/{id}`；`GET /list?week=`（周内任一天，默认本周）、`POST /list/generate`、`POST /list/items`、`PATCH /list/items/{id}`（bought、paid_price）、`DELETE /list/items/{id}`；`GET /pantry`、`PUT /pantry`。

AI 工具（`grocery/tools.py`）：`find_products`、`save_product`（新建或按 id / 条码 / 商品页 / 名字 + 品牌更新；`product_url`、`nutrition_source`）、`lookup_barcode`（只读，第 7 节）、`remove_product`、`generate_shopping_list`、`get_shopping_list`（含存货）、`add_shopping_items`、`mark_bought`、`remove_shopping_items`、`set_pantry`、`set_staple`、`remove_staple`（#103）。删除类（`remove_product`、`remove_shopping_items`、`remove_staple`）进 `ai/chat.py` 的 `ALWAYS_CONFIRM` 和 `assistant/.claude/settings.json` 的 `ask`，其余直接写。

## 5. 今天页
饮食 tab →「采购」：本周（周一到周日）清单按类别分组，每行名字 + 「2 包 · 1 kg」+ 估价 + 门店小标签（「Coles」「Woolies」，第 7 节），点一下勾掉；顶部「已买 3 / 12」和估计总价；健康提示用短标签。下面是商品库（搜索、点开看规格 / 价格 / 每 100g 营养）。前端在 `web/static/grocery.js`（和 profile.js、history.js 一样登记在 `window.BodyPlugins`，app.js 只加了挂载点）。

## 6. 以后
- 做饭 / 记餐后按食材自动扣存货；保质期提醒。
- 按门店拆单、比价（同一食材多个商品时按单价挑）。
- 条码扫描（app 里调相机）直接查商品库（查到 Open Food Facts 的用 `lookup_barcode`）。

## 7. 门店链接和条码（2026-10-21）
目标：清单和商品库的每一行都能点到 Coles / Woolworths 的真实商品页，iPhone 上直接打开门店 app。

**调研结论**：Coles、Woolworths 都没有公开 API（也没有 Shoplet 之类的官方合作接口）；它们的使用条款禁止爬取、自动抓取。所以：
- **服务器从不访问门店网站**：不抓页面、不查价格、不批量刷新、不用 iframe 嵌入。链接只是普通的 `<a href>`，由用户自己点开。
- **价格只来自用户告诉 AI 的**（价签 / 小票照片、口述、`mark_bought` 的实付）。AI 也不要自己去门店网站查价。
- 商品页链接只接受两种（`grocery/links.py`，规则在 `grocery/params.STORES`）：`https://www.coles.com.au/product/<slug>-<编号>`、`https://www.woolworths.com.au/shop/productdetails/<stockcode>/<slug>`；别的域名 / 路径直接拒绝。存的时候去掉 query 和 #（分享链接里的跟踪参数），编号存进 `store_sku`，门店字段不是这家时改成这家。来源：用户在 AI 里发商品链接，或发带网址的截图，AI 调 `save_product` 时填 `product_url`；同一个商品页再存会更新原来那条。

**每一行的链接**（服务器生成 `links`，给页面用；AI 的工具返回里去掉了，省 token）：
1. 商品存了商品页 → 只给这个链接（实心小标签）；
2. 否则按名字搜（空心小标签）：Coles `https://www.coles.com.au/search/products?q=`、Woolworths `https://www.woolworths.com.au/shop/search/products?searchTerm=`。门店字段是 Coles / Woolworths（含 Woolies）→ 只给那一家；门店空 → 两家都给；别的门店（Aldi、Costco、菜市场）→ 不给。搜索词用英文：英文名（加品牌），否则 `matches` 里第一个英文名（「鸡胸肉」→「chicken breast」），都没有才用原名。

**已连接的超市（issue #44）**：超市链接不是默认的。每个人在今天页「饮食 → 采购」底部的「超市」里点亮自己用的超市（目前 Coles、WWS = Woolworths），或者对 AI 说「我用 Coles」（`set_stores`）；存在表 `grocery_settings`（迁移 270），接口 `GET / PUT /v1/users/{user}/grocery/stores`。**一个都没连接时不显示任何超市按钮**；连接了以后只给已连接的那几家（存了商品页但那家没连接 → 不显示，`save_product` 返回 `store_not_connected` 让 AI 问要不要连接）。界面上 Woolworths 一律显示 **WWS**（`params.STORES` 的 label；门店字段写 Woolworths / Woolies / WWS 都认）。商品和链接仍然由 AI 添加，界面不提供手动表单。

**iPhone 上怎么打开**：商品页和 Woolworths 搜索是 universal link，会直接开门店 app；Coles 搜索只开 Safari。在 Watch2Whoop app 里链接不带 `target`：WebView 的导航代理（`apps/watch2whoop/lib/src/pages/home_page.dart` 的 `_onNavigation`）把跨域的主框架跳转交给系统，系统按 universal link 开 app（和「问管家」打开 Claude app 是同一条路）；浏览器里在新标签页打开，今天页留着。**不需要新的 TestFlight 版本**。

**条码 → 营养（可选）**：`lookup_barcode` / `body-engine lookup-barcode <条码>` 查 Open Food Facts 的商品接口（`grocery/off.py`）：名字、品牌、规格、每 100g 营养（钠 g → mg）。
- 默认关闭：管理员设 `BODY_OFF_CONTACT`（邮箱或网址）才开，User-Agent 是 `Watch2Whoop/<版本> (<联系方式>)`（OFF 要求）。
- 只做单个查询，不批量：每个进程每分钟最多 15 次（OFF 商品读取限制是每 IP 每分钟 100 次），每人每天 50 次，同一个条码一天内用内存缓存。
- 数据是 ODbL：结果带 `source = "Open Food Facts (ODbL)"`，AI 存进商品库时填 `nutrition_source`，今天页商品详情显示「来源」。只读工具，不自动写；用户拍的营养成分表优先于 OFF。

## 8. 超市商品库和每周半价（grocery-sync，2026-10-21）
维护者在 PR #28 决定建自有超市商品库：可选组件 `grocery-sync`（默认关闭），每周三 01:00（悉尼）刷新已开启超市的公开食品目录，采购清单 / 推荐优先「本周半价」。设计、抓取约束、站点调研（Coles 可以；WWS 对可识别的 User-Agent 返回 403，不抓）和部署见 [grocery-store-data-plan.md](grocery-store-data-plan.md) 第 8 节；本节第 7 节「服务器从不访问门店网站」在组件**开启**时不再成立，关闭时仍然成立。


## 9. 价格交给用户自己的 AI（2026-10-22，迁移 420）
负责人定的方向：**我们的服务器不访问超市网站**（grocery-sync 保持关闭，见第 8 节），查价格交给用户自己的 AI，用它自带的联网能力；我们只提供结构和存储（`grocery/prices.py`）。

**流程**
1. `get_shopping_context`：本周计划和清单、健康约束（档案卡 health_constraints、红肉周上限、清单 warnings）、已连接超市（含搜索地址）、存货、`needs_price`（缺价或超过 7 天的，最多 15 样）、`rules`（查价规则）。
2. 用户的 AI 打开已连接超市的商品页 / 搜索页（或看用户发的截图），用 `record_store_prices` 一次写回（≤ 30 样）：超市、商品页 URL、商品名、规格、价格、单价、特价类型和原价、条码、图片地址（只存引用）、查询时间、来源（ai / screenshot / manual）、对应的清单项 `for_item`；`bought=true` 同时记成一次购买。
3. 采购清单（`generate_shopping_list` / `get_shopping_list` / 今天页）用这些价格算：每行在已连接超市里取需要的整包数最便宜的那家（同价特价优先），`prices.by_store` 每家几样多少钱、`savings` 特价省多少、`missing` 缺价、`stale` 旧价；行上显示「x 天前」。用户商品库里比它更新的价格（实付、价签）仍然是估价。
4. **护栏**（写在工具描述、管家设定、根规则 v2 第 7 条里）：只查用户自己清单上的东西，一次十几样，不整站抓取、不翻目录；页面打不开、要登录或被拦截（403、验证码）就告诉用户、改用截图，**不尝试绕过**。

**数据模型**
- **商品去重池** `store_products`（沿用 #48 的表）：同一个商品物理上只存一份。去重键依次是「超市 + 超市商品 ID」（从 Coles / WWS 商品页 URL 解析）→「超市 + 条码」→「超市 + 规范化名称 + 规格」（`name_key`）；后来带了商品页的上传会把只有名字的那条补上 ID，不另建一条。池里只放商品信息（名称、品牌、规格、条码、商品页、图片地址引用），**不放任何用户的价格**；已有的字段不会被别人的上传覆盖。这张表不带 user_id 是例外（迁移里写明：只有商品元数据，没有个人数据）。
- **按用户隔离** `user_store_products`：每个用户只能看到自己上传 / 关联过的商品（也记下这个用户叫它什么，`matches`）。所有读接口和工具都 JOIN 这张表；没关联过的商品和不存在的商品回答完全一样（404 / 「没有这个商品（或不是你的）」），猜 ID 也拿不到。#48 的同步读取端只读 `origin = 'sync'` 的目录行，不会读到用户上传的商品。
- **价格观察** `price_observations`（加了 user_id、observed_at、source）：用户 + 商品 + 价格 + 特价 + 原价 + 时间 + 来源，**永不过期**，界面显示「x 天前」。同一条（同一时间、同一来源）重复写只存一次。
- **购买历史** `grocery_purchases`：清单上勾掉（带实付就用实付，不带就记清单上的超市价并标「估计」）或 AI 记一笔（`bought=true`），记实际价格、超市、日期、是否特价、原价；取消勾选会删掉那一条。
- **统计**（`prices.stats`，接口 `GET …/store-products/{id}`，工具 `get_price_history`）：价格曲线（观察 + 实付）、最低 / 平均 / 最近、打过几次折、大约多久一次；上次买的 verdict：和这个用户自己 180 天的平均比，低 8% 以上 = 买得便宜、高 12% 以上 = 买贵了；最近 21 天见过特价、自己没赶上特价且贵 25% 以上也算买贵了（阈值在 `grocery/params.py`）。
- **图片不复制**：只存超市图片的 URL 引用，不下载、不存储；今天页的 CSP 只加载自己的图片，所以不显示。商品图版权归超市。用户截图的缩略图这一期没做。

**页内对话联网**：API 类（Anthropic / OpenAI / Gemini）可以在「AI 设置」里打开各家**官方**的联网工具（`ai_providers.web_tools`，默认关，费用算在用户自己的 key 上）：Anthropic `web_search_20260209` + `web_fetch_20260209`（老模型只有 `web_search_20250305`，`pause_turn` 自动续），OpenAI Responses `web_search`，Gemini `google_search` + `url_context`。托底 AI、OpenAI 兼容接口、服务端 Gemini CLI 不开。订阅类（Claude / ChatGPT / Gemini app）本来就有自己的联网，通过 MCP 用同样的工具。

**接口**（scope nutrition，读别人的要 shares 授权，写只能写自己的）：`GET …/grocery/context`、`POST …/grocery/store-prices`、`GET …/grocery/store-products?q=`、`GET …/grocery/store-products/{id}`。今天页「采购」：行上「x 天前」和特价 / 半价角标，已买的行显示「买得便宜 / 买贵了」，统计下面是每家合计、特价省多少、缺价 / 旧价几样；新的「价格记录」列出自己的超市商品，点开看价格走势、最低 / 平均 / 最近、打折频率、上次买的判断。
