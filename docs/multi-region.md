# 多区域部署：中国内地区和海外区（方案，待审）

> Issue #249。状态：**方案，待负责人确认**；这一轮只交文档，没有改代码、服务器和数据。
> 相关：#219（自建 Apple 健康同步引擎，替换 Open Wearables，见 [apple-sync-engine.md](apple-sync-engine.md)）、#247（app 本地化 + iOS 原生）、[friends-social-plan.md](friends-social-plan.md)（朋友 / 圈子）、[self-signup-plan.md](self-signup-plan.md)（自助注册、邀请码）、[family-consent.md](family-consent.md)（同意书）。
> 本文只写通用设计：域名一律用 `intl.example.com` / `cn.example.com` 这样的占位。部署者自己的服务器、域名、备案和迁移名单在私有运维文档里。

## 0. 背景和目标

- 现在只有一个部署（下面叫**海外区 `intl`**）。离服务器近的用户很流畅；中国内地用户跨境访问，延迟高、容易丢包。
- 决定：加一个**中国内地区 `cn`**。内地用户的数据库、评分、上传文件、备份都在境内；海外用户留在原来的部署。
- 目标：
  1. **每个账号只属于一个区域**，它的健康数据只存在这个区域。
  2. 客户端（iOS app、app 里的今天页、用户自己的 AI 客户端）自动连到账号所在的区域。
  3. 默认不跨区域传数据。跨区域的功能要么不做，要么只交换用户明确同意的最少字段（第 3 节）。
  4. 账号可以在区域之间迁移：导出 → 导入 → 逐表核对 → 切换 → 原区域删除。迁移幂等、可回退（第 6 节）。
  5. 代码只有一份。区域之间的差别全部写在配置里，不分叉代码。
- 不做的：
  - 不做跨区域的主从复制，也不做双写。
  - 不按访问地点自动路由（GeoDNS），理由见 2.5。

## 1. 区域模型

### 1.1 一个区域 = 一套完整部署
每个区域都是一套完整、独立的部署：
- 组件：Body Engine + PostgreSQL、同步入口（Open Wearables 或 #219 的自建引擎）、SparkyFitness、语音识别、备份。
- 各区域有自己的数据库、自己的 secrets（AI 主密钥、上传 token、APNs 等），也有自己的公网域名。
- 区域之间没有共享数据库，也没有常驻的数据通道。

新增两项配置，所有区域都要配：

| 配置 | 例子 | 作用 |
|---|---|---|
| `BODY_REGION` | `intl` / `cn` | 本部署是哪个区域。写进 `/healthz`、`/v1/region`、doctor、日志 |
| `BODY_REGIONS_FILE` | `secrets/regions.json` | 全部区域的公开信息：`id`、显示名（中英）、今天页地址、同步地址、MCP 地址、ID 区间。每个区域放同一份，**只有公开地址，不含任何用户数据** |

`regions.json` 示例：
```json
{
  "regions": [
    {"id": "intl", "name": {"zh": "海外", "en": "International"},
     "today_url": "https://intl.example.com", "sync_url": "https://sync-intl.example.com",
     "id_range": [1, 9999999]},
    {"id": "cn", "name": {"zh": "中国内地", "en": "Mainland China"},
     "today_url": "https://cn.example.com", "sync_url": "https://sync-cn.example.com",
     "id_range": [10000000, 19999999]}
  ]
}
```

### 1.2 账号属于哪个区域，记在哪里
- **账号所在的那个区域的库就是记录本身**：账号行在哪个库里，它就属于哪个区域。所以不需要一个全局的「账号 → 区域」目录。
  - 全局目录必须放在某一个区域。内地用户的标识（Apple ID 对应的 sub、邮箱）哪怕只存哈希，也是个人信息，等于出境。所以不做全局目录。
- 每个区域的 `users` 表加一列 `region`（默认取 `BODY_REGION`）和一列 `moved_to`（可空）。账号迁走后，原区域只留一条**墓碑**：`status = 'moved'`、`moved_to = 'cn'`、迁移时间，加上这个账号的 token 哈希。没有任何健康数据和资料。墓碑用来把老客户端引到新区域（2.4），保留 90 天后删除。
- **ID 全局唯一**：每个区域独占一段 ID 区间（上面的 `id_range`）。新区域建库时，把所有自增序列拨到本区间的起点（`body-engine region-init`）。这样：
  - 账号迁移时**所有行保留原来的 ID**，外键、token、AI key 的附加数据（绑定 user_id）、圈子里引用的 user_id 都不用改写。
  - 一个 user_id 在全部区域里只指向一个人，跨区域的引用（如果以后做）不会撞号。
  - doctor 每小时检查每个序列都还在本区间内，接近上限时报 ⚠。
  - Open Wearables 和 SparkyFitness 的主键大多是 UUID，少数自增表一样拨序列。

### 1.3 新用户怎么分区域
三种做法：

| 做法 | 说明 | 利 | 弊 |
|---|---|---|---|
| A. 注册时选择 | app 第一屏「你主要在哪里使用？中国内地 / 其他地区」；按设备时区（`Asia/Shanghai` 等）预选 | 透明；用户自己决定数据放哪；没有码也能用 | 多一步；选错要迁移 |
| B. 码决定区域 | 激活码 / 邀请码 / 圈子码在哪个区域生成，就属于哪个区域，兑换时自动落到那个区域 | 测试用户零选择；管理员开户时就定好 | 境外朋友邀请内地用户时，码会把他带到海外区 |
| C. 按 IP 判断 | 服务器看来源 IP | 无感 | 出差、VPN 会判错；不透明，不建议 |

**推荐 A + B**：
- 有码时，码带出默认区域。
- 第一屏显示这个区域，用户可以改。码的区域和用户选的区域不一致时，提示「这个邀请来自 X 区，你选了 Y 区：可以注册，但不能加入对方的圈子」（第 3 节）。
- 没有码时，按时区预选，用户确认。

**码怎么查区域**：码本身不是个人信息。app 把码并发发给各区域的 `POST /v1/region/resolve-code`（不鉴权、限流和兑换一样）。哪个区域答「是我的」，就在那个区域注册。
- 码的格式不变，所以 1.0.6 之后的 10 位码、可读码、桥接码都照常能用。
- 两个区域同时认同一个码时，按 app 内置的区域顺序取第一个。生成码时各区域会检查别的区域没有同样的可读码。

## 2. 客户端怎么连对服务器

### 2.1 app 内置区域表
- 编译时把 `regions.json` 的公开部分编进 app：`--dart-define-from-file` 新增 `W2W_REGIONS`，内容是 JSON。这一项替代现在单一的 `W2W_DEFAULT_TODAY_URL` / `W2W_DEFAULT_OW_HOST`，旧的两项仍兼容，视为只有一个区域。
- `W2W_LOCK_SERVER=true` 时，只能连内置表里的服务器。设置链接、迁移跳转都要在表内，否则拒绝，这一点沿用 `server_lock.dart` 的 `admit()`。
- 自建部署的人不配 `W2W_REGIONS`，行为和现在一样：一个服务器，可以手填地址。

### 2.2 设置二维码、登录码、激活链接
- 设置链接 `watch2whoop://setup?today=…&ow=…&code=…&login=…` 格式不变：`today` / `ow` 本来就是服务器地址，由生成它的区域填自己的地址（`BODY_PUBLIC_URL` / `BODY_OW_PUBLIC_URL`）。这样设置二维码天然带区域。
- 可选加 `&region=cn`，只用来显示，以 `today` 为准；锁定模式下 `today` 必须在内置表里。
- 一次性登录码、激活码只在生成它的区域有效。
- 激活页 `/app/activate.html` 由各区域自己提供，链接本身就是区域的域名。

### 2.3 自助注册流程的调整
现在的顺序是：服务器 → 登录（Apple / 邮箱）→ 邀请码 → 健康权限。
改成：**区域（有码就先输码）→ 登录 → 健康权限**。

- 登录请求只发到选定的区域。这样内地用户的 Apple 标识、邮箱不会发到海外区。
- Apple 登录的 token 校验、邮件验证码各区域自己做：
  - Apple 的公钥地址在境内可以访问；
  - 内地区的发信服务要用境内服务商。阿里云的服务器默认封 25 端口，用 465 或服务商的 API。

### 2.4 账号迁走后，老客户端怎么跟过去（尽量零操作）
- 原区域对墓碑账号的 token，所有接口统一返回 `410`：
  ```json
  {"error": "account_moved", "region": "cn", "today_url": "https://cn.example.com", "sync_url": "https://sync-cn.example.com"}
  ```
  只回区域的公开地址，不回任何数据。
- **app（新版本）收到后**：
  1. 先确认地址在内置区域表里，不在就忽略并提示。
  2. 改存的 `todayUrl` / `owHost`。
  3. 用 Keychain 里原来的账号 token 访问新区域。token 哈希随账号一起迁过去，所以不用重新登录。
  4. 静默重连同步：现在已有的 `POST /v1/account/ow-invitation`，或 #219 引擎的上传地址。
  5. 重新换一个今天页登录码（`/v1/account/web-login-code`），打开新的今天页。
  6. 整个过程只显示一条「你的数据已搬到中国内地服务器」的提示。
- **老版本 app**（不认识 `account_moved`）：今天页的接口收到 410 后，提示「请更新 app，或点这里重新设置」，附带新区域的设置链接，点一下完成。
  - 这个设置链接带一次性登录码。登录码由目标区域在切换时生成（7 天有效），交给源区域，只存在墓碑里；用过或过期就删。
  - 尽管有这个兜底，**迁移前最好先发一版认识 410 的 app**。
- **用户自己的 AI 客户端**（远程 MCP）：MCP 客户端不会跟随跳转。
  - `/mcp` 对墓碑 token 返回 410 + 新地址；
  - 今天页「AI 设置」显示新的 MCP 地址和「复制」按钮。
  - 用户要在 Claude / ChatGPT 里把连接器地址改一次，这是唯一躲不开的手动步骤。用 OAuth 连接的要重新授权一次。
- **快捷指令上传**：地址写在用户自己的快捷指令里，原区域的 `/upload` 返回 410，用户要把地址改一次。只影响还在用快捷指令的人。

### 2.5 为什么不用 GeoDNS（同一个域名按访问地点解析到不同服务器）
GeoDNS 按「人在哪」分，但数据是按「账号在哪」放的：
- 内地用户出国时会连到海外区，那里没有他的数据。
- 海外用户到内地时会连到内地区。
- 要么报错，要么服务器之间转发；转发等于数据跨境。

所以按账号分区域，每个区域一个域名。

### 2.6 今天页
- 今天页是 app 里的 WebView，跟着 app 的 `todayUrl` 走，不需要自己切换区域。
- 「我」页加一行只读的「数据存放：中国内地 / 海外」。
- 换区域只能通过迁移（第 6 节），今天页上不提供「切换区域」按钮，免得用户以为切过去数据就跟着走了。

## 3. 跨区域的功能

原则：**内地区的数据默认不出境**；海外区的数据默认也不进内地区（海外用户所在地的隐私法同样有跨境要求）。下面每项都给出做法和利弊。

| 功能 | 现在怎么做 | 推荐做法（v1） | 以后可选 |
|---|---|---|---|
| 圈子（朋友页） | 同一个库里 `circles` / `circle_members`，成员卡片直接读对方的评分 | **只在区域内**。圈子属于创建者的区域；别的区域的人用圈子邀请码时，`resolve-code` 找到的是另一个区域，app 提示「对方在 X 区，不能加入」 | 跨区域「卡片联邦」：见 3.1 |
| 授权互看（shares） | `shares` 行，直接读对方数据 | **只在区域内**。跨区域的 share 不能建 | 不做（太宽） |
| 排行榜 | 实时读圈子成员的数据 | 跟圈子走，只在区域内 | 跟 3.1 一起，只交换 0–100 的相对分 |
| 圈子动态（训练帖、点赞、评论） | 实时读训练记录 | 只在区域内 | 不做（训练内容细节太多） |
| 鼓励 / 通知 | 同库写 + 推送 | 只在区域内 | 跟 3.1 |
| 头像、备注名 | 同库 | 只在区域内 | 跟 3.1：头像不跨区，只显示首字母 |
| 个人邀请码（推荐奖励） | `inviter_user_id` 同库 | 码把被邀请人带到邀请人的区域；改选别的区域时只能注册，不加圈子，也不记推荐奖励 | 只交换「某个码被兑换了」这个事件，不带被邀请人信息 |
| 管理员统计 / 插件 | 读本库全部用户 | 各区域分开跑。汇总到一个地方时只汇总**不含个人的计数**，例如每区活跃人数 | — |

### 3.1 以后如果要跨区域圈子：「卡片联邦」（不在 v1）
- 只交换成员卡片上已有的最少字段：称呼、当天恢复分区间、睡眠分、负荷、今天练没练、7 天这些值，以及排行榜的 0–100 相对分。
- 不交换原始数据、饮食明细、体重、化验、日记、心电图。
- 交换的是**拉取时的快照**，不在对方区域落库，只放内存缓存，10 分钟过期。
- 前提：
  - 每个人对「跨区域共享」**单独勾选同意**，写明对方在哪个国家或地区；
  - 内地区一侧按个人信息出境的规定办手续（健康数据属于敏感个人信息）；
  - 同意书改版。
- 利：海外和内地的朋友能留在同一个圈子。
- 弊：要在两个区域之间开服务器到服务器的接口（双向鉴权、审计）；还有合规手续；一个区域宕机，另一边的卡片就空了。
- **推荐 v1 不做**。现有的跨区域圈子按「要负责人决定的点」处理：迁移时拆开，提前告诉圈子成员。

## 4. AI

| 部分 | 海外区 | 内地区 |
|---|---|---|
| 托底 AI | 部署者的 DeepSeek key、额度、余额检查 | **单独一份**：内地区自己的 key（`secrets/deepseek_api_key`）、自己的余额和每人每月额度。请求从境内服务器发往境内服务商，不出境 |
| API 类 AI（用户自己的 key） | 所有提供商 | 可用提供商由配置 `BODY_AI_PROVIDERS_ALLOWED` 决定。内地区推荐**只开境内服务商**（DeepSeek、通义千问等走 `openai_compatible`）：境外 API 从境内服务器多数连不上；而且由服务器转发给境外公司，相当于运营方把数据送出境 |
| 远程 MCP（用户自己的订阅客户端） | 照旧 | 每个区域自己的 `/mcp`，地址 = 本区 `BODY_PUBLIC_URL` + `/mcp`。用户自己用境外 AI 客户端来读，是用户自己发起的传输：在「AI 设置」里加一句提示，写明数据会发给哪家公司 |
| 身体管家（部署者本人的 Claude Code 会话） | 只在海外区 | **不部署**。只给部署者本人用，部署者在海外区 |
| AI 联网搜索 | 现有的搜索服务 | 境外搜索 API 可能连不上：配置成关闭，或换境内搜索服务 |
| 语音转文字 | 本机的语音识别容器 | 同样本机部署，模型文件随镜像带过去（不从境外下载） |
| 生成内容的标识、备案 | — | 内地区如果向公众提供 AI 对话，可能涉及生成式 AI 服务的登记和标识要求，上线前要单独确认（见私有运维文档的合规清单） |

迁移账号时 AI 相关数据的处理：
- 对话、用量、托底同意记录随账号迁移。
- 当月托底额度按两个区域合计，迁移当月不重置。
- 用户自己的 API key 是用区域的 AI 主密钥加密的，两个区域的主密钥不同。迁移工具用**一次性迁移密钥**转一遍：源区域解密后用迁移密钥加密 → 目标区域解密后用自己的主密钥加密。迁移密钥由管理员带外传递，用完销毁。
- 也可以不迁 key，让用户重填一次。代价是用户要多做一步。

## 5. 同步入口

内地区也要有自己的同步入口：app 上传健康数据的地址，每个区域一个（`sync_url`）。两种做法：

| 做法 | 说明 | 利 | 弊 |
|---|---|---|---|
| 1. 内地区部署一份 Open Wearables | 和海外区同样的 OW 版本和配置，公网只开 iOS SDK 的 5 个接口 | 现在的 app（1.0.7）就能用；不等 #219 | 约 1.3 GB 内存，内地服务器要选大一档；#219 切换后两个区域都要再下线一次 |
| 2. 直接用 #219 的自建同步引擎 | 内地区不装 OW，app 走 `/v1/ingest/healthkit` | 省内存、少一套服务；数据直接进 Body Engine | 要等 #219 的 app 端（1.0.8 起）和影子对比完成；内地用户要先升级 app |

**推荐顺序**：
1. 多区域代码（第 7 节）和 #219 并行开发。
2. 内地区先按**做法 1**部署，OW 用精简配置：不起 Flower，门户只走内网。这样内地区上线不被 #219 卡住。
3. 如果到内地用户迁移那天，#219 已经切到「app 模式」且影子对比通过，就**跳过 OW**，直接用做法 2。迁移工具对两种做法都支持。
4. #219 第 4 阶段（OW 下线）两个区域一起做。

## 6. 账号迁移工具

新命令 `body-engine region-move`，由管理员在两个区域上各跑一部分。每一步都幂等、可以重跑，状态记在源区域的表 `region_moves` 里。

```
状态：planned → frozen → exported → imported → verified → switched → (purged | rolled_back)
```

| 步骤 | 在哪跑 | 做什么 | 回退 |
|---|---|---|---|
| 0. 计划 | 源 | `region-move plan --user N --to cn`：检查目标区域可达、ID 不冲突（目标库里没有这个 id 的账号）、这个人有没有跨区域圈子 / share（有就列出来，必须先处理） | 删计划 |
| 1. 冻结 | 源 | `freeze`：账号设为 `status = 'moving'`。写接口返回 `503 + Retry-After`（app 会重试），读照常；同步上传先收下、放进暂存队列（不丢）；每小时任务跳过这个人 | `unfreeze` |
| 2. 导出 | 源 | `export`：沿外键找出这个人在 Body Engine / 同步库 / SparkyFitness 的全部行（复用开户脚本的 `person_filters` 思路），每张表一份 CSV 加行数和校验和（按主键排序后的 md5），加上上传文件（快捷指令存档、待处理的历史导入）。AI key 用一次性迁移密钥重新加密。整包加密后放到源区域的暂存目录 | 删导出包 |
| 3. 传输 | 管理员 | 导出包从源区域传到目标区域。**方向必须是海外 → 内地**（数据入境）。内地 → 海外的迁移（极少见）要先确认合规 | — |
| 4. 导入 | 目标 | `import`：一个事务里按外键顺序 `COPY` 进去，保留原 ID。目标库已经有这个人的同样内容时跳过（幂等）；有冲突的不同内容就整体回滚、报出冲突表 | 一个事务，失败自动回滚；成功后可以 `import --undo` 删掉这个人 |
| 5. 核对 | 两边 | `verify`：逐表比较行数和校验和；目标区域对这个人跑一遍 `compute-scores`，和源区域的 `daily_scores` 逐行比较。必须全部一致才能往下走 | — |
| 6. 切换 | 源 | `switch`：源区域把账号变成**墓碑**（2.4），这之后的请求都返回 410；把冻结期间暂存的上传转交给目标区域；目标区域把账号设为 `active` | `rollback`（见下面） |
| 7. 删除 | 源 | `purge`：删掉源区域里这个人的全部数据（复用 `remove` 的分轮删除），只留墓碑。**必须用户本人和负责人都确认过**（命令要求 `--confirmed-by-user --confirmed-by-owner`，并写进审计） | 删除不可逆。所以要先观察一段时间（推荐 7 天），这期间源区域的数据还在，随时可以回退 |
| 8. 墓碑过期 | 源 | 90 天后删掉墓碑（再来的老客户端只能拿到 401） | — |

- **回退**（切换后、删除前）：
  1. 目标区域冻结这个人；
  2. 把切换后在目标区域新增的行导回源区域，用同样的 `export` / `import`，只导切换后新增或改动的行；
  3. 源区域撤销墓碑；
  4. 目标区域的这个人变成墓碑，指回源区域。
  因为 ID 全局唯一、两边的表结构一样，回退就是反方向再迁一次。
- **前提**：两个区域的数据库迁移版本必须一致，`plan` 会检查。部署时两个区域一起升级。
- **备份**：迁移前，源区域和目标区域各跑一次完整备份（`ops_jobs dump` 或 `ops.py backup`），备份 ID 写进 `region_moves`。
- **同意书**：目标区域导入时，带上这个人在源区域的同意记录。如果同意书的版本要求重新确认（例如加了数据存放地点），用户下次打开时会弹出新版本让他确认。确认之前，目标区域照常只暂存新数据、不处理，规则和现在一样。

## 7. 代码改动清单（估工作量，按一个熟悉代码的人计）

| # | 部分 | 改动 | 工作量 |
|---|---|---|---|
| 1 | 配置 | `BODY_REGION`、`BODY_REGIONS_FILE` 读取和校验（`config.py`）；`/healthz` 和新的 `GET /v1/region` 返回本区 id 和公开地址 | 0.5 天 |
| 2 | 迁移（数据库） | `users.region`、`users.moved_to`，`status` 加 `moving` / `moved`；表 `region_moves`；授予 `grafana_ro` 只读 | 0.5 天 |
| 3 | ID 区间 | `body-engine region-init`：把所有序列拨到本区起点（OW / SparkyFitness 的少数自增表同样处理）；doctor 检查序列在区间内 | 1 天 |
| 4 | 鉴权 | `auth.py`：`moving` → 写接口 503；`moved` → 所有接口（含 `/mcp`、`/upload`、cookie 会话）统一 410 + 新区域公开地址；测试覆盖每种入口 | 1.5 天 |
| 5 | 码解析 | `POST /v1/region/resolve-code`（邀请码 / 激活码 / 圈子码 / 登录码，只回「是否属于本区」，限流）；生成可读码时检查别的区域有没有同样的码（读 `regions.json` 调对方的 resolve 接口） | 1 天 |
| 6 | 跨区域限制 | 圈子加入、share、推荐奖励：发现对方在别的区域时，给出明确的错误码和文案（中英） | 0.5 天 |
| 7 | AI | `BODY_AI_PROVIDERS_ALLOWED`；联网搜索可关闭；托底 AI 按区域配置（参数已经在 `fallback_params.py`，只是部署不同）；AI 设置页显示本区 MCP 地址和数据去向提示 | 1 天 |
| 8 | 迁移工具 | `region-move plan / freeze / unfreeze / export / import / verify / switch / rollback / purge`；三个库按外键导出和导入；校验和；一次性迁移密钥转 AI key；上传文件；审计；用临时 Postgres 跑的端到端测试（两个库模拟两个区域：迁过去 → 核对 → 回退 → 再迁 → 删除） | 5–6 天 |
| 9 | 同步暂存 | 冻结期间收下的上传（快捷指令、#219 引擎、OW 的 webhook 侧）放进暂存队列，切换时转交 | 1 天 |
| 10 | app | 内置区域表 `W2W_REGIONS`；第一屏选区域 / 先输码；`resolve-code`；处理 410 `account_moved`（检查地址在表内 → 改配置 → 静默重连 → 新今天页登录码）；「我」页显示数据存放地点；中英文案；测试 | 3–4 天（1.0.8 或之后的版本，和 #247 的原生化一起排） |
| 11 | 今天页 | 「我」页显示区域；老 app 收到 410 时的「重新设置」提示页；可选配置 `BODY_ICP_NUMBER` / `BODY_PSB_NUMBER`，配了就在激活页、手册、「我 → 关于」底部显示备案号（内地区要求）；中英 | 1 天 |
| 12 | 部署脚本（通用部分） | `deploy/` 里的 compose 和 doctor 支持「不装 Grafana / 不装管家」的区域；访问日志保留天数做成可配置（有的地区要求至少 6 个月）；镜像离线导入的说明（`docker save` / `docker load`，或镜像仓库） | 1 天 |
| 13 | 文档 | 管理员指南加多区域一节；手册加「数据存放地点」；本方案根据实现更新 | 0.5 天 |
| | **合计** | | **约 18–21 天**（服务端约 13 天，app 约 4 天，其余约 2 天） |

建议拆成 4 个 PR：
1. 第 1–3、7、12 项：内地区能独立部署。
2. 第 4–6、9、11 项：墓碑、码解析、跨区域限制。
3. 第 8 项：迁移工具。
4. 第 10 项：app，跟 app 发版节奏走。

前两个 PR 合并后，内地服务器就能在不开公网的情况下部署和测试。

## 8. 测试和验收
- 单元测试：
  - 序列区间；
  - 墓碑在每种入口（Bearer、cookie、MCP token、上传 token）都返回 410；
  - `resolve-code` 不泄露码以外的信息，有限流；
  - 跨区域的圈子 / share 被拒；
  - 内地区配置下境外提供商不可选。
- 迁移的端到端测试（CI 里两个临时库）：
  - 迁移后逐表一致、评分一致；
  - 重跑每一步都不产生重复；
  - 回退后源区域和迁移前一致（切换后新写的行也在）；
  - 删除后源区域只剩墓碑。
- 真机：
  - 用占位账号走完整流程：海外区注册 → 迁到内地区 → app 自动跟过去（不重新登录）→ 同步继续 → 回退 → 再迁；
  - 老版本 app 看到「重新设置」提示。
- 验收：
  1. 内地用户日常使用的所有请求只到内地区；
  2. 内地区的数据和备份不离开境内；
  3. 迁移的测试用户除了 AI 连接器地址以外不需要操作。

---

## English summary

Issue #249 plan: **multi-region deployment** (an international region and a mainland-China region). Design only; no code, servers or data changed in this round.

- **Region model.** A region is a complete, independent deployment with its own databases, secrets and domain. There is no shared database and no global account directory: a directory would put mainland identifiers abroad.
  - Every deployment gets `BODY_REGION` and a public `regions.json` (addresses only).
  - Each region owns a disjoint **ID range**, so all sequences start at the region's offset. Account moves keep every primary key, and a user id is unique across regions.
  - A moved account leaves a **tombstone** in the old region: no data, only the target region and token hashes, kept 90 days.
- **New users** pick their region on the first screen, preselected from the device time zone. A code (activation, invite or circle code) carries its region through an unauthenticated, rate-limited `POST /v1/region/resolve-code` that the app asks every region. Sign-in goes only to the chosen region. GeoDNS is rejected because data follows the account, not the user's location.
- **Clients.** The app ships a built-in region table; server lock only allows those hosts. Setup links already carry the server address.
  - The old region answers moved accounts with `410 account_moved` plus the new public addresses. A new app switches silently and re-uses its token, which was migrated as a hash.
  - Old apps get a one-tap re-setup link.
  - Remote MCP clients and Shortcuts need a one-time URL change.
- **Cross-region features.** Circles, shares, leaderboards, the feed, cheers and avatars stay **within a region** in v1. A cross-region "card federation" exchanges only consented summary fields as in-memory snapshots. It needs separate consent and outbound-transfer paperwork, and is deferred.
- **AI.** Each region has its own fallback AI key and budget. The mainland region allows only domestic API providers (configurable). MCP is per region. The owner's personal coach runs only in the international region.
- **Sync.** First deploy an Open Wearables copy in the mainland region, so it does not wait for #219. Skip it if #219's own engine is live by migration day, then retire OW in both regions together.
- **Account move tool** `region-move`: plan → freeze → export → import → verify → switch → purge.
  - Each step is idempotent and audited; the export carries checksums, and verification compares tables and recomputed scores.
  - Rollback before purge is a reverse move. Purge needs the user's and the owner's confirmation.
  - AI keys are re-encrypted through a one-time transfer key.
- **Work estimate.** About 18–21 days, in four PRs: region config, ID ranges and deployability; tombstones, code resolution and cross-region guards; the move tool; the app.
