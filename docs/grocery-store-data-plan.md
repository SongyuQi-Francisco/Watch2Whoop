# 超市商品数据：Coles / WWS（Woolworths）的真实商品、价格和每周特价

> **状态（2026-10-27 更新）：可选组件 `grocery-sync` 已实现，默认关闭，并且不会开启。** 时间线：
> 1. 2026-10-20 提案（本文档第 1–7 节）。
> 2. 2026-10-21 维护者读完第 3 节的条款风险后，决定做**自有商品库**（方案 A 的收紧版）：部署者自己开启的可选组件 `grocery-sync`（PR #48，第 8 节），默认关闭。
> 3. 2026-10-21 实测：Coles 抓到第 6 页就出现机器人检测页、WWS 对可识别的 User-Agent 直接 403（第 8.4 节）。组件按设计停下，不重试、不绕过。
> 4. **2026-10-22 起方向改为「价格交给用户自己的 AI」**（[grocery-plan.md](grocery-plan.md) 第 9 节，迁移 420）：我们的服务器不访问超市网站，`grocery-sync` 保持关闭。
> 5. 2026-10-23 / 24 用户反馈 FB-11、FB-13、FB-14 再次确认：商品页读取被拦截（`no_fetch_host`）、公开目录同步被站点拦截、「本周半价」工具未开启。**结论见第 9 节**：不绕过反爬；合规来源的调研和推荐在 issue #292。
>
> 第 8 节保留为 `grocery-sync` 组件的设计记录（代码仍在 `body_engine/src/body_engine/grocery/sync/`，部署者自担条款风险时才可能用到）；第 4–7 节保留为当时的分析记录。
> 相关：[grocery-plan.md](grocery-plan.md)（采购：周清单、商品库、存货；第 7 节门店链接和「已连接的超市」，issue #44；第 9 节价格交给用户自己的 AI）。
>
> **免责：** 下面的条款分析是开发者阅读公开网页后的整理，**不是法律意见**。条款风险由开启组件的部署者自行评估。

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

## 5. 建议（决定前的版本，已被第 8 节取代）
1. **现在只做方案 C**（零风险）：价格观测表 + 每周特价表 + AI 工具，数据来自用户的截图、链接、小票和实付价格。U2–U5 都能用用户自己的数据部分实现，U1 用用户发来的特价截图实现。
2. 可选加 **Open Food Facts** 按条码补营养（方案 D 的一部分，许可清楚）。
3. **所有者要先决定的事**（在这之前不写任何抓取代码）：
   - 是否接受第 3 节的风险去做方案 A；如果考虑，**先做一次法律咨询**（澳大利亚律师，问：开源发布抓取插件、部署者个人使用、条款可执行性）。
   - 如果做，只能是核心仓库之外、默认关闭、部署者自己安装的插件；核心仓库只提供第 6 节的数据表和导入接口。
   - 是否发邮件给 Shoplet（方案 E）或联系联盟计划 / 数据商（方案 D）。

## 6. 数据模型草稿（决定前的版本，实际的表见 8.5 和 grocery-plan.md 第 9 节）
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

## 7. 验收标准（方案 C，决定前的版本；最终版见 8.10）
- [ ] 用户把 Coles / Woolworths 特价页截图或商品链接发给 AI，AI 能存成 `store_products` + `price_observations`，重复发同一张不产生重复（幂等）。
- [ ] 清单每一行（有数据时）显示两家最近价格、单价、多久前看到的、来源；没有数据时不显示、不报错。
- [ ] `get_weekly_specials` 返回本周和清单对得上的特价，违反健康规则的带标签；红肉周上限等阈值仍只在 `params.py`。
- [ ] 价格历史至少能画 12 周；实付价格（`mark_bought` 的 paid_price）自动成为一条观测。
- [ ] 每张新表带 `user_id`，接口按用户 / shares 鉴权，测试覆盖「看不到别人的价格观测」。
- [ ] 服务器端**没有任何对 coles.com.au / woolworths.com.au 的请求**（测试或代码审查确认）。

## 8. 实施设计（维护者决定后的版本，2026-10-21）
### 8.1 决定要点
- 建 Coles 和 WWS 两家的**食品类**商品库：名称、品牌、规格、条码（有就存）、类目、商品页链接、当前价、单价（每 kg / L / 个）、是否特价、特价类型（半价 / 降价 / 多买优惠）、有效期，以及价格历史。
- **每周三 01:00（Australia/Sydney）全量刷新一次**，平时不刷。两家的特价周是周三到下周二。
- 生成采购清单、推荐食谱时，**优先本周半价**且符合用户目标和健康约束的商品；清单里标「本周半价」和节省金额。
- **只对已连接该超市的用户显示**该超市的商品和价格（issue #44）。用户自己商品库里的条目（AI 加的链接、价签照片）**优先于**抓取数据。
- 收到超市方面的要求：立即停用并删除抓取数据（`grocery-sync purge`）。

### 8.2 组件和开关
- 组件名 `grocery-sync`，代码在 `body_engine/src/body_engine/grocery/sync/`，每家超市一个独立的抓取 + 解析模块（`coles.py`、`wws.py`），互不依赖；核心采购代码只读数据库里的结果。
- **默认关闭**。部署者在环境变量里开：
  - `BODY_GROCERY_SYNC=coles`（逗号分隔，开哪几家；不设 = 关闭）；
  - `BODY_GROCERY_SYNC_CONTACT=<邮箱或网址>`（必填，写进 User-Agent；不填不跑）；
  - 可选 `BODY_GROCERY_SYNC_DELAY_S`（请求间隔，默认 6 秒，最小 3 秒）、`BODY_GROCERY_SYNC_MAX_REQUESTS`（每家每次最多请求数，默认 800）。
- **关闭后**：不再抓取；读取端也不再用抓取数据（采购页、清单、推荐都回到只用 AI 加的商品）。删除数据用 `purge`。
- 命令：`body-engine grocery-sync run [--store coles] [--force] [--max-pages N]`、`grocery-sync status`、`grocery-sync purge [--store coles] --yes`。`run` 6 天内跑过就跳过（`--force` 例外），所以定时器重复触发也只抓一次。

### 8.3 抓取约束（对站点友好，降低风险）
- 只访问公开页面，不登录、不带 cookie 会话、不执行 JavaScript、不调站点内部接口、**不绕过任何访问限制**（不伪装浏览器 UA、不轮换 IP、不过验证码）。
- 每次运行先取 `robots.txt`，自己实现 RFC 9309 的匹配（`*` / `$` 通配、最长规则优先、同长 Allow 优先）；**每个 URL 请求前都检查**，不允许就不请求。取不到 robots.txt（401 / 403 / 5xx / 超时）= 整家不抓。
- 单线程，请求间隔 ≥ 6 秒（加 0–2 秒抖动）；超时 / 5xx 指数退避重试最多 3 次（30 → 60 → 120 秒）；**403 / 429 / 页面结构不对（例如出现挑战页、没有 `__NEXT_DATA__`）立即停止这一家**，记一次 `blocked` / `unclear`，不重试，等人看。
- User-Agent：`Watch2Whoop-grocery-sync/<版本> (+<部署者联系方式>)`（联系方式是 `BODY_GROCERY_SYNC_CONTACT`，必填）。
- 每周一次；同一次运行里同一个 URL 只请求一次。
- **不保存、不展示**商品图片、长描述、广告位；只存 8.4 列出的字段。

### 8.4 站点调研（2026-10-21，用上面的 User-Agent，只取了 robots.txt 和 3 个页面）
**Coles**：
- `robots.txt`（200）允许 `/browse/…`（没有 Disallow）和 `Allow: /product/*-*`、`Allow: /on-special?filter_Special=halfprice`；禁止 `/search/`、`*?filter_Special=*`（除了上面那个）、`/*/*/*/on-special*`、`/brand/*`、`/images/*`、`*?pid=*`、`*?utm_source*` 等。
- 类目页 `/browse/<类目>?page=N` 是服务端渲染的 Next.js 页面：`<script id="__NEXT_DATA__">` 里有 `props.pageProps.searchResults`，每页 48 个商品（另有少量广告位，`_type` 不是 `PRODUCT` 的跳过、重复的按 id 去重）、`noOfResults` 总数。每个商品：`id`、`name`、`brand`、`size`、`merchandiseHeir` / `onlineHeirs`（类目）、`pricing`：`now`、`was`、`saveAmount`、`priceDescription`（半价是「1/2 Price」）、`promotionType`（`SPECIAL`）、`specialType`（`PERCENT_OFF` / `MULTI_SAVE`）、`offerDescription`（「Buy 2 for $13.50」）、`multiBuyPromotion`（`minQuantity`、`reward` = 每件折后价）、`comparable`（「$19.60/ 1kg」「$3.85/ 100mL」）。没有条码。
- 商品页链接按 `https://www.coles.com.au/product/<slug>-<id>` 拼（和 #29 的链接规则一致）。
- 只抓食品类的一级类目（`grocery/sync/params.py` 的 `COLES_CATEGORIES`：meat-seafood、fruit-vegetables、dairy-eggs-fridge、bakery、deli、pantry、frozen、drinks、chips-chocolates-snacks、health-dietary、international-foods）。肉类约 570 个商品 = 12 页；全部食品类预计几百页，按 6 秒间隔约 1 小时内跑完。
- 页面里还有 Imperva 的脚本。**实测（2026-10-21，一次，本地测试库）**：robots.txt + `meat-seafood` 前 5 页正常（245 个商品、79 个特价），第 6 页返回的页面没有 `__NEXT_DATA__`（多半是机器人检测页），用时 43 秒、共 7 个请求；程序按 8.3 停下、记 `unclear`，**没有重试，也没有改任何东西去绕过**。所以按现在的节奏，全量刷新（约 250–300 页）过不了它的检测。在维护者决定下一步之前（例如联系 Coles 请求放行这个 User-Agent），服务器上**不开启**这个组件。

**WWS（Woolworths）**：
- 用诚实的 User-Agent 请求 `https://www.woolworths.com.au/robots.txt` 被 Akamai 直接返回 **403 Access Denied**（商品页同理）。按约束：**不抓、不绕过**。`wws.py` 只实现 robots 检查和「被拒绝 → blocked」的状态，解析器等确实能合规访问时再写（例如 Woolworths 书面同意或把我们的 User-Agent 放行）。
- 在那之前，WWS 用户仍然用 #29 / #44 的商品链接 + 搜索链接，价格来自 AI 记的价签 / 小票 / 实付。

### 8.5 数据表（迁移 320_grocery_store_data.sql）
这几张表存的是**超市公开的商品目录**，不是任何人的个人数据，所以**不带 user_id**、所有用户共用一份（例外写在迁移里）；谁能看由「已连接的超市」+ 组件开关控制。

| 表 | 字段 |
|---|---|
| `store_products` | `id`、`store`（coles / woolworths）、`sku`（超市商品编号）、`name`、`brand`、`size_text`、`package_size` + `package_unit`（g / ml / pcs，从规格解析）、`barcode`（可空）、`category`（超市一级类目）、`url`（商品页）、`price`、`was_price`、`unit_price` + `unit_price_unit`（kg / L / ea）、`special_type`（half_price / price_drop / multibuy / special / 空）、`special_label`（「Buy 2 for $13.50」这类短文本）、`save_amount`、`multibuy_qty` + `multibuy_unit_price`、`first_seen`、`last_seen`、`active`（最近一次运行看到）；唯一 (`store`, `sku`) |
| `price_observations` | `store_product_id`、`observed_on`（悉尼日期）、`price`、`was_price`、`unit_price`、`special_type`、`run_id`；唯一 (`store_product_id`, `observed_on`) → 价格历史，重复跑幂等 |
| `weekly_specials` | `store`、`week_start`（特价周的周三）、`store_product_id`、`special_type`、`price`、`was_price`、`save_amount`、`label`、`valid_to`（下周二）；唯一 (`store`, `week_start`, `store_product_id`) |
| `grocery_sync_runs` | 每次运行：`store`、开始 / 结束时间、`status`（ok / partial / blocked / unclear / failed）、请求数、页数、商品数、特价数、半价数、错误 |

### 8.6 和用户数据怎么对上
- 读的时候现算（不建每人一份的副本）：清单行 / 商品库条目 → 英文搜索词（商品英文名 + 品牌、`matches` 里的英文名、`grocery/sync/params.py` 的常用食材中英对照，如 鸡胸肉 → chicken breast），在已连接超市的 `store_products` 里按「所有词都出现在商品名里」找，多余的词越少越好；同样贴近的里面**本周半价优先**，再按单价低的。
- 用户商品库里存了商品页（`product_url` / `store_sku`）→ 直接按编号对上，不再模糊匹配；用户自己记的价格优先于抓取价格（估价用用户的，抓取价格只作「本周价」显示）。
- 健康约束：推荐里排除零食 / 冰淇淋 / 甜饮料 / 加工肉等关键词和类目（`EXCLUDE_KEYWORDS`、`EXCLUDE_CATEGORIES`），红肉按本周红肉余量（`rules/params.py`）、牛肋条按频率、高嘌呤关键词打标签；抓取数据没有营养成分，所以只按名字 / 类目判断，并标明是估计。

### 8.7 推荐和界面
- `generate_shopping_list` / `get_shopping_list`：每行附 `offer`（已连接超市里最合适的一个：哪家、价格、单价、是否本周半价、省多少、商品页），清单汇总 `half_price`：本周半价的行数和合计节省；没有用户价格的行用 offer 价估价。
- 新工具 `get_weekly_specials`：本周已连接超市的半价商品（按清单 / 商品库能对上的排前面，带健康标签，排除不符合约束的）。
- `suggest_meals`：食材能对上本周半价的菜加分，理由里写「本周半价：鸡胸肉（Coles 省 $x）」。
- 今天页「采购」：清单行加「半价」角标和「省 $x」；统计卡加「半价省」；商品库行有对上的半价商品时加角标。不放解释文字。

### 8.8 部署（Ali，私有 ops 仓库）
- 环境变量（Ali 的 `.env` / compose）：`BODY_GROCERY_SYNC=coles`、`BODY_GROCERY_SYNC_CONTACT=<部署者联系方式>`。
- systemd timer：`OnCalendar=Wed *-*-* 01:00:00 Australia/Sydney`，`Persistent=true`，跑 `deploy/ali/engine.sh grocery-sync run`（一次性容器，和 hourly 一样）；doctor 读 `grocery-sync status`：最近一次不是 ok 或超过 8 天没跑 → ⚠。
- 停用：去掉 `BODY_GROCERY_SYNC` 并停 timer；收到要求时再跑 `grocery-sync purge --yes`。

### 8.9 风险边界（实现里写死的）
- 只读公开页面、遵守 robots.txt、限速、可识别 User-Agent、不存图片和描述、默认关闭、一键删除。
- 发现被拒绝 / 页面结构变了：停下来记状态，**不改代码去绕**；要恢复需要人确认。
- CI 和测试从不访问超市网站：解析器用 `tests/fixtures/grocery_sync/` 里的合成页面（同样结构、虚构商品），抓取器用假的 HTTP 客户端。

### 8.10 验收
- [ ] 周三 01:00 任务跑完：Coles 商品库价格更新，`weekly_specials` 里本周半价 / 降价 / 多买标记正确；`grocery-sync status` 显示请求数、商品数、半价数、用时。
- [ ] 采购清单和推荐里能看到「本周半价」优先和节省金额；只对已连接 Coles 的用户显示。
- [ ] 关闭组件后不再抓取，界面回到只用 AI 添加的商品；`purge` 后抓取数据全部删除。
- [ ] WWS：被拒绝时状态为 blocked，不产生任何后续请求。

## 9. 现状和结论（2026-10-27，对应用户反馈 FB-11 / FB-13 / FB-14，issue #292）
| 事实 | 说明 |
|---|---|
| Coles | 公开目录页的 robots.txt 允许 `/browse/`，但连续访问几页后返回机器人检测页（8.4 实测）。商品页读取同样被拦截。 |
| WWS（Woolworths） | Akamai 对可识别的 User-Agent 返回 403（连 robots.txt 也是），商品页同理。条款（集团条款 1.3(b)(v)）也明文禁止机器人检索。 |
| `grocery-sync` | 代码在，默认关闭；因为上面两条，**不开启**，也不会为了让它跑通去改 User-Agent、轮换 IP 或过验证码。要恢复需要超市方面书面放行（写在 8.4 / 8.9）。 |
| 本周半价工具 | 数据来自 `grocery-sync` 的 `weekly_specials`，组件关着所以返回「未开启」，是预期行为，不是故障。 |
| 价格历史为空 | 价格只来自用户的小票 / 价签 / 实付和用户自己 AI 看到的页面（grocery-plan.md 第 9 节）；用户还没积累就是空的。商品卡（FB-12）引导用户拍价签或小票。 |

**原则（不变）**：不绕过反爬，不整站抓取，不轮换 IP；服务器不访问 Coles / WWS 的网站；图片只存 URL 引用。

**下一步**：[issue #292](https://github.com/SongyuQi-Francisco/Watch2Whoop/issues/292) 比较了合规的价格 / 特价来源（小票识别、官方 API、Open Food Facts、公开 catalogue、比价网站、用户众包），推荐写在那里的评论里；选定后再把流程接到 `record_store_prices`，并决定「本周半价」何时开。
