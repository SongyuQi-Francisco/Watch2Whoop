# 超市商品数据：Coles / Woolworths 的真实商品、价格和每周特价（提案，未开始，2026-10-21）

> **状态：提案 / 未开始。** 这份文档只写需求、法律风险和可选方案，**没有任何抓取代码**，在维护者做出第 5 节的决定之前也不应该写。接着 [grocery-plan.md](grocery-plan.md)（采购：周清单、商品库、存货）。
> 另外一个 PR 正在做「用户自己提供的 Coles / Woolworths 商品链接 + 门店搜索链接」（零抓取），本文第 4 节方案 C 建立在它上面，不重复设计。
>
> **免责：** 下面的条款分析是开发者阅读公开网页后的整理，**不是法律意见**。真要做方案 A 之前，请找澳大利亚执业律师确认。

## 1. 目标和用户故事
像澳洲 app Shoplet 那样，让采购清单显示**真实的商品、当前价格和本周特价**，并和健康规则结合。

| # | 作为用户，我想…… | 验收要点 |
|---|---|---|
| U1 | 每周看到**和我清单 / 常买商品对得上的特价**（半价、降价），并且只推荐符合我健康规则的（红肉周上限、嘌呤、饱和脂肪 / 糖 / 钠，见 `grocery/params.py`、`rules/params.py`） | 特价列表按清单行匹配；违反规则的打标签，不推荐 |
| U2 | 清单每一行有**真实商品链接 + 当前价格**（哪家、多少钱、每 100g / 每 kg 单价） | 有链接、价格、单价、`observed_at`（多久之前看到的） |
| U3 | 看商品的**价格历史**，分辨真降价和「先涨后降」 | 至少 12 周的价格点，来源可追溯 |
| U4 | 同一食材 **Coles vs Woolworths 对比**，按单价挑，必要时按门店拆单 | 每行显示两家单价（有数据时），清单底部两家合计 |
| U5 | 我的 AI 能读到这些数据，帮我改菜单（「这周鸡腿半价，换成鸡腿」） | 工具返回特价和价格，不用 AI 自己去网上搜 |

## 2. Shoplet 是怎么做的（只按公开信息）
- 开发商 Tech Nautilus Pty Ltd。App Store：<https://apps.apple.com/app/id6764248924>（2026-10-21 查看）。宣传的功能：Woolworths / Coles / Chemist Warehouse 同一商品比价、全部超市的半价商品汇总页、扫码看当前价和价格历史（约 3 个月价格变化）、收藏商品降价推送、跳转各家特价页。
- **数据来源**：服务条款 5.1（<https://shoplet.technautilus.com/legal/terms/?lang=en>，2026-10-21 查看）说商品名、描述、价格、图片、规格「来自各零售商公开可得的数据」，只做「汇总和展示」，并声明**和任何零售商没有合作、代理或赞助关系**；5.2 说价格 / 库存 / 促销以零售商官方发布为准，不保证准确。
- 条款和 App Store 页面**都没写**怎么拿数据（抓网页、调网站内部接口还是第三方数据商）、多久刷新一次。合理推测是自动采集零售商网站，但**未核实**。
- 联系方式：官网 Contact 页（<https://shoplet.technautilus.com/contact/index.html>）列了一般联系邮箱（contact）和商务邮箱（business），都在 technautilus.com 域名下。
- 要点：一家商业公司自己承担风险做了这件事，不等于开源项目照做就没问题（见第 3.3 节）。

## 3. 法律 / 服务条款分析
### 3.1 Coles
- **网站条款** <https://www.coles.com.au/important-information/terms/website-terms-conditions>（2026-10-21 查看）：
  - 第 2 条：网站和内容只能「for personal purposes」使用（除非 Coles 书面同意）；不得以任何方式 **copy、reproduce、republish、adapt、upload、link、post、frame** 网站或内容的任何部分；不得给网站造成不合理的负载。
  - 第 4 条：只允许个人非商业用途地浏览内容；文字、图片、logo 归 Coles 或其授权方（页面的所有权表述里把价格数据单独排除了，但第 2 条的使用限制仍然适用）。
  - 第 6 条：违反条款时 Coles 可以不经通知停止你访问网站。第 7 条：条款可随时修改，继续使用即视为同意。
  - 条款里**没有点名** robot / spider / scraper，但「不得复制、不得 link / frame」已经覆盖把商品数据拿进别的产品。
- **robots.txt** <https://www.coles.com.au/robots.txt>（2026-10-21 查看，`User-agent: *`）：`Allow: /product/*-*`（商品详情页允许爬）；`Disallow: /search/`、`/brand/*`、`/images/*`、`/lists/`、`*/on-special*`、`*/half-price-specials*`、`*?filter_Special=*`（但单独 `Allow: /on-special?filter_Special=halfprice`）。
  - robots.txt 是给搜索引擎的技术约定，**不是使用许可**；它允许爬商品页不代表条款允许复制、再发布这些内容。

### 3.2 Woolworths
- **Woolworths Group 条款** <https://woolworthsgroup.com.au/au/en/privacy/policy-documents/terms-and-conditions.html>（2026-10-21 查看，适用于 woolworthsgroup.com.au 这个集团网站）：
  - 1.3(b)(v)：不得使用「any robot, spider, site search and retrieval application」或其他机制检索、索引网站任何部分；1.3(b)(vi)：不得修改、重排格式、反向工程。
  - 2.2(a)(ii)(iii)：未经书面同意不得复制、复制发布、分发材料，不得 frame / embed 到别的网站；2.2(b)：只允许个人（非商业）浏览。
- **零售网站 woolworths.com.au 自己的条款**：调研时这一页对我们的请求返回 403，换浏览器 UA 能拿到页面框架但条款正文是前端渲染的，**内容未核实**。合理假设它和集团条款一样或更严，做任何事之前要人工在浏览器里读一遍。
- **robots.txt** <https://www.woolworths.com.au/robots.txt>（WebFetch 返回 403，用 curl 拿到，2026-10-21）：`Disallow: /shop/search`、`/shop/productgroup`、`/shop/__partial`、`/shop/productdetails/__partial/*`、`/shop/mylists`、`/shop/terms/*` 等；商品详情页 `/shop/productdetails/…` 本身没禁（只禁带 `region_id=`、`googleshop=` 参数的）。同样，这不是使用许可。

### 3.3 对一个公开开源仓库意味着什么
- **条款冲突是明确的**：两家都只许个人非商业浏览、禁止复制 / 再发布；Woolworths 集团条款还明文禁止机器人检索。把抓取器放进公开仓库 = 公开发布一个专门用来违反这些条款的工具，并让每个自建部署的人都去跑它。
- **可能的后果**：律师函 / 删除要求（GitHub 也会收到 DMCA 等通知，可能下架整个仓库）；IP 封禁——自建服务器（同一台机器上可能还有别的服务）的出口 IP 被拉黑，连带用户自己正常浏览 / 网购也受影响；云厂商因投诉处理账号；维护者个人承担责任（仓库是个人账号，没有公司隔离）。条款违约之外还可能涉及版权（商品图片、描述文字）和《Criminal Code》里未经授权访问的条款（绕过封禁、伪造身份时风险更高）。
- **对比**：Shoplet 是公司、自己的服务器、自己的法律判断；我们是 AGPL 开源代码 + 很多人各自部署，风险被复制到每个部署者身上，而部署者大多不会去读这些条款。
- 以上仍然**不是法律意见**。

## 4. 可选方案
| 方案 | 能拿到什么 | 风险 | 工作量 |
|---|---|---|---|
| **A. 抓公开网页 / 网站内部接口** | 商品名、规格、价格、单价、是否特价、原价；可每周刷新 | **高**（第 3 节） | 中，且要长期维护（网站改版就坏） |
| **B. 零售商自己发布的每周电子目录** | 本周特价（通常周三起换），无完整价格 | 中（同样受网站条款约束，目录页条款未单独查到，**未核实**） | 中 |
| **C. 用户经自己的 AI 提供** | 用户贴的链接、特价截图、价签 / 小票照片 → AI 读出来存 | **零**（用户个人使用，自己看的页面） | 小，部分已做 |
| **D. 有授权的数据** | 联盟计划的商品 feed、商业零售数据商、Open Food Facts（营养） | 低（按合同 / 许可） | 看对方 |
| **E. 找 Shoplet 官方合作** | 他们已经有的价格 / 特价数据，走他们的 API | 低（合同） | 小（如果他们愿意） |

### A. 抓取（只作记录，**不建议做**）
- 如果将来真的做：**不进核心仓库**，做成部署者自己另外安装的可选插件（单独仓库），默认关闭，安装时要求部署者确认已读两家条款并自担风险。
- 只抓需要的：用户清单 / 商品库里已有的商品页（不爬目录、不爬搜索页，遵守 robots.txt 的 Disallow），每周最多一次（Coles / Woolworths 特价通常周三换），每个请求间隔 ≥ 10 秒、单线程、失败指数退避、遇到 403 / 429 立刻停一周；诚实的 User-Agent（写明项目和联系方式，不伪装浏览器、不轮换 IP、不绕过验证码 / 登录）；只存价格、单价、特价标记、商品名和规格，**不存图片和描述文字**；结果缓存到数据库，页面和 AI 只读缓存。
- 即使这样，条款冲突依然存在，所以需要第 5 节的决定。

### B. 每周电子目录
- Coles 2020 年起停止印刷目录、改成线上目录；Woolworths 线上目录一般周三到下周二（二手资料，**未核实**）。还有 Lasoo、SaleFinder 等目录聚合站。
- 要先查：目录页 / PDF 有没有单独的使用条款、是否有官方订阅（邮件 / RSS）能让用户自己收到；聚合站的条款。在查清之前和 A 同样对待。
- 零风险的变体：用户自己订阅零售商的特价邮件，转给 AI 或截图发给 AI（就是方案 C）。

### C. 用户经自己的 AI 提供（推荐先做）
- 已有：商品库 `grocery_products`（AI 从价签 / 小票 / 营养成分表照片结构化）、实付价格回写；另一个 PR 在加用户提供的商品链接和门店搜索链接（今天页一点就在 Coles / Woolworths 网站搜这个食材，用户自己在零售商网站上看）。
- 要补的：用户把**特价页截图**或商品页链接发给 AI → AI 读出商品、价格、原价、有效期 → 存成价格观测（`source = user_screenshot / user_link / receipt`）和本周特价；每次买了的实付价也是一条观测，自然积累出价格历史和两家对比。
- 这是用户本人浏览、本人转述给自己的 AI，属于个人使用；服务器不访问零售商网站。

### D. 有授权的数据
- **联盟计划**：查 Coles / Woolworths 是否有联盟（affiliate）计划和商品 feed（据说通过联盟网络提供，**未核实**）；要联系联盟网络或零售商的 partnerships 团队，问 feed 是否允许非商业开源项目、能否在自建部署里用。
- **商业零售数据商**：澳洲有做超市价格 / 促销监测的数据公司，按年收费，通常不允许再分发到开源项目；适合部署者自己签约，插件读他们的 API。需要部署者自己去询价。
- **Open Food Facts**（<https://world.openfoodfacts.org>）：营养成分、配料、条码，数据库许可 ODbL（署名 + 相同方式共享）。**不含价格**，但可以给商品库补营养（按条码查）。使用前确认 ODbL 对「派生数据库」的署名和共享要求，商品库里来自 OFF 的字段要标来源。

### E. 找 Shoplet
- 发邮件给 Shoplet 官网 Contact 页上的一般联系邮箱（contact，商务合作也可以用 business 邮箱），问有没有官方 API / 数据合作，个人非商业开源项目能否接入，以及他们的数据来源是否允许再分发。
- **由仓库所有者决定是否发、以谁的名义发**；本 PR 不发任何邮件。注意：如果他们的数据本身来自抓取，接他们的数据并不能消除第 3 节的问题，要问清楚。

## 5. 建议
1. **现在只做方案 C**（零风险）：价格观测表 + 每周特价表 + AI 工具，数据来自用户的截图、链接、小票和实付价格。U2–U5 都能用用户自己的数据部分实现，U1 用用户发来的特价截图实现。
2. 可选加 **Open Food Facts** 按条码补营养（方案 D 的一部分，许可清楚）。
3. **所有者要先决定的事**（在这之前不写任何抓取代码）：
   - 是否接受第 3 节的风险去做方案 A；如果考虑，**先做一次法律咨询**（澳大利亚律师，问：开源发布抓取插件、部署者个人使用、条款可执行性）。
   - 如果做，只能是核心仓库之外、默认关闭、部署者自己安装的插件；核心仓库只提供第 6 节的数据表和导入接口。
   - 是否发邮件给 Shoplet（方案 E）或联系联盟计划 / 数据商（方案 D）。

## 6. 数据模型草稿
新迁移（编号到时再定，例如 `2xx_store_data.sql`），每张表带 `user_id`，按 shares 的 `nutrition` 授权，和 `grocery_*` 一样。

| 表 | 字段（草稿） |
|---|---|
| `store_products` | `id`、`user_id`、`retailer`（coles / woolworths / aldi / other）、`retailer_product_id`（可空）、`url`（用户给的链接）、`name`、`brand`、`size`、`unit`、`barcode`、`grocery_product_id`（对上商品库，可空）、`created_at`、`updated_at`；唯一键 (`user_id`, `retailer`, `retailer_product_id` 或 `url`) |
| `price_observations` | `id`、`user_id`、`store_product_id`、`price`、`was_price`（原价，可空）、`unit_price` + `unit_price_unit`（每 100g / kg / L）、`currency`、`on_special`、`special_type`（half_price / price_drop / multibuy …）、`valid_from` / `valid_to`、**`source`**（user_screenshot / user_link / receipt / paid_price / catalogue / plugin:<名字>）、**`observed_at`**、`raw_ref`（截图 / 上传文件 id，可空）；幂等键 (`store_product_id`, `source`, `observed_at`) |
| `weekly_specials` | `id`、`user_id`、`retailer`、`week_start`（零售商的特价周，通常周三开始，按用户时区存日期）、`store_product_id`、`price_observation_id`、`matched_item_ids`（对上的本周清单行）、`health_flags`（复用 `grocery/params.py` 的警告），按 (`user_id`, `retailer`, `week_start`, `store_product_id`) 唯一 |

接入现有代码（实现时）：
- `grocery/store.py`：生成清单时，每行在已对上的商品上附带最近一次观测价（两家各一条，带 `observed_at` 和 `source`），估价优先用 7 天内的观测价；新函数 `match_specials(week)` 把本周特价对到清单行和商品库的 `matches`，套用健康规则。
- `grocery/tools.py`（新工具，描述要短，`tests/test_briefing.py` 有总长度预算）：`save_price_observation`（AI 从截图 / 链接 / 小票读出来后调用，批量）、`get_price_history`（商品，默认 12 周摘要）、`get_weekly_specials`（本周、按清单匹配、带健康标签）、`compare_stores`（清单两家合计）。删除类的（如 `remove_price_observation`）要同时进 `ai/chat.py` 的 `ALWAYS_CONFIRM` 和管家设置的 `ask`。
- 今天页「采购」：每行加「Coles $x.xx / Woolworths $y.yy」短标签和特价角标、价格历史迷你折线；不放解释文字。
- 将来的抓取插件（如果第 5 节决定做）只通过同一个导入接口写 `price_observations`（`source = plugin:<名字>`），核心代码不认识任何零售商网站。

## 7. 验收标准（方案 C 这一期）
- [ ] 用户把 Coles / Woolworths 特价页截图或商品链接发给 AI，AI 能存成 `store_products` + `price_observations`，重复发同一张不产生重复（幂等）。
- [ ] 清单每一行（有数据时）显示两家最近价格、单价、多久前看到的、来源；没有数据时不显示、不报错。
- [ ] `get_weekly_specials` 返回本周和清单对得上的特价，违反健康规则的带标签；红肉周上限等阈值仍只在 `params.py`。
- [ ] 价格历史至少能画 12 周；实付价格（`mark_bought` 的 paid_price）自动成为一条观测。
- [ ] 每张新表带 `user_id`，接口按用户 / shares 鉴权，测试覆盖「看不到别人的价格观测」。
- [ ] 服务器端**没有任何对 coles.com.au / woolworths.com.au 的请求**（测试或代码审查确认）。
