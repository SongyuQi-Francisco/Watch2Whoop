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

## 4. 接口和工具
接口（前缀 `/v1/users/{user}/grocery`，scope `nutrition`）：`GET /products?q=&category=`、`POST /products`、`PATCH /products/{id}`、`DELETE /products/{id}`；`GET /list?week=`（周内任一天，默认本周）、`POST /list/generate`、`POST /list/items`、`PATCH /list/items/{id}`（bought、paid_price）、`DELETE /list/items/{id}`；`GET /pantry`、`PUT /pantry`。

AI 工具（`grocery/tools.py`）：`find_products`、`save_product`（新建或按 id / 条码 / 商品页 / 名字 + 品牌更新；`product_url`、`nutrition_source`）、`lookup_barcode`（只读，第 7 节）、`remove_product`、`generate_shopping_list`、`get_shopping_list`（含存货）、`add_shopping_items`、`mark_bought`、`remove_shopping_items`、`set_pantry`。删除类（`remove_product`、`remove_shopping_items`）进 `ai/chat.py` 的 `ALWAYS_CONFIRM` 和 `assistant/.claude/settings.json` 的 `ask`，其余直接写。

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
