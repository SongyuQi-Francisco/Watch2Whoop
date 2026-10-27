# App 流畅度：本地优先 + iOS 原生化（SwiftUI）方案

> 状态：负责人 2026-10-25 确认，第 8 节 D1–D10 全部按推荐执行。第 1 期：3.1（静态长缓存、`Server-Timing`）在 #257 上线；3.2–3.5 已实现，四个开关默认关，部署后由维护者打开（第 3.9 节）；3.6 跟 1.0.8。
> 文中的数字都来自合成数据（quickstart 的 `demo_data.py`，一个虚构用户的 90 天）和公开的静态页面，不来自任何真人的账号或数据。
> 相关：#248（动作库打进 app）、#219（自建同步引擎，`apple-sync-engine.md`）、#77（离线，`offline-plan.md`）、`apps.md`（版本号和发版）。

## 0. 摘要

- **卡在往返次数，不在渲染。** 本地服务器零延迟时，冷启动 0.25 s、记一餐 0.33 s；给每个请求加 150 ms 往返后变成 0.80 s、0.78 s，加 300 ms 变成 1.55 s、1.24 s，几乎全部是「串行往返层数 × RTT」。真机上还要再加新连接（DNS + TCP + TLS，实测约 0.5–0.66 s）、手机上解析 1.15 MB 脚本和样式的时间、正式服务器读 SparkyFitness / OW 的时间。
- **启动要 5 层串行往返**：页面 → 38 个静态文件（每次都要 304 确认）→ 脚本跑起来后 `/v1/web/me` → 11 个今天页接口 → `/health-history`。切到趋势 / 饮食 / 我各要 2 层，记一餐 3 层。
- **四期**：
  1. **不重写就能见效**（服务端为主，部署即生效；1.0.8 加一项）：静态文件带版本号长缓存（#257）、启动接口合并成一次、切 tab 的两层并成一层并空闲预取、记一餐乐观更新、先显示上次的数据、app 先打开本机保存的页面外壳。重开 app 到看到今天的数字：150 ms 往返下约 1.6–1.9 s → 约 0.3 s 先出上次的数据、0.6–0.8 s 刷新完。
  2. **本地数据层**：按「文档」增量同步（今天 / 每天 / 趋势 / 训练 / 饮食 / 静态包），写操作离线排队，服务端协议先做、今天页先用，之后原生 app 用同一个协议。
  3. **SwiftUI 原生化**：先用 SwiftUI 重写 app 外壳替换 Flutter（页面仍是 WebView，看起来不变），再按 今天 → 记录 → 趋势 → 饮食 → 我 → Jarvis 的顺序一个 tab 一个 tab 换成原生；每个原生 tab 都有服务器开关，关掉就回到 WebView。
  4. 发版：1.0.8（小，Flutter）→ 1.1.0（SwiftUI 外壳 + 本地数据层 + 原生今天）→ 1.2.0 → 1.3.0 → 1.4.0，每个版本号只过一次 Beta 审核，修复用同一版本号的新 build。
- **顺带发现**（第 1.4 节）：公网 fail2ban 把 401 也算作失败，没登录 / 登录过期的页面每次打开都有几个 401；同一个出口 IP 10 分钟累计 30 次就封 1 小时。上线以来只封过一次（就是这次测量），所以**不是测试用户反馈卡顿的原因**；但测试用户在同一个 NAT 后面（学校、社团 Wi-Fi）时理论上可能被一起封。是否调整由负责人决定（D7）。
- 要负责人决定的点在第 8 节。

---

## 1. 测量基线

### 1.1 方法

| 项 | 做法 |
|---|---|
| 服务器 | 本机 Body Engine（这个分支的代码），hypercorn + 自签名 TLS，**HTTP/2**（和正式服务器的 nginx 一样），测试库 `body_test_perf`（不碰真实库） |
| 数据 | `quickstart/demo_data.py --days 90`：虚构用户 90 天的手表数据，走真实的导入器和评分；一个快捷记餐模板；一个假的 AI 提供商（地址不可达，只为让 Jarvis 面板按「已配置 AI」打开） |
| 浏览器 | 系统 Chrome（Playwright，无头），390 × 844、手机模式；**禁用 service worker**（app 的 WKWebView 本来就没有） |
| 延迟 | CDP `Network.emulateNetworkConditions` 给每个静态请求加 RTT；API 请求在脚本里转发后再等一个 RTT（Playwright 拦截请求时 CDP 延迟不生效）。RTT = 0 / 150 / 300 ms，带宽不限 |
| 计时 | 从动作开始到最后一个请求结束（之后 1.2 s 没有新请求才算结束），所以是「网络部分」的时间；不含新连接、手机上的脚本解析、正式服务器的处理时间 |
| 本地缺的 | 本机没有 SparkyFitness：依赖它的接口（`/recipes/meal-plan`）回 502，脚本改成 404，免得触发页面的「服务器正在更新」重试；记一餐的 `POST …/meal-templates/{id}/log` 正式环境要写 SparkyFitness，这里用「一个 RTT + 250 ms」代替 |
| 正式服务器 | 只用 curl 测了公开静态页面一次：DNS 0.36 s、TCP 0.15 s、TLS 0.16 s、首字节 0.96 s（新连接）；ping 约 145–205 ms。issue 里的初测：新请求约 0.5–0.6 s |

脚本没有进仓库（一次性测量），步骤写在第 1.5 节，以后可以按同样的方法复测。

### 1.2 结果

| 操作 | 请求数（静态 / 接口） | 传输 | 串行往返 | RTT 0 | RTT 150 ms | RTT 300 ms |
|---|---|---|---|---|---|---|
| 冷启动（缓存全空） | 56（42 / 14） | 1.21 MB 未压缩；静态 gzip 后约 384 KB，接口 gzip 后 5.5 KB | 5 | 0.25 s | 0.80 s | 1.55 s |
| **热启动**（重开 app，WebView 磁盘缓存还在，最常见） | 55（42，其中 38 个 304 / 13） | 28 KB | 5 | 0.16 s | 0.79 s | 1.55 s |
| 切到「趋势」 | 4（0 / 4） | 31 KB（gzip 9.8 KB） | 2 | 0.30 s | 0.35 s | 0.64 s |
| 切到「记录」 | 3 | 6 KB | 1 | 0.05 s | 0.18 s | 0.32 s |
| 切到「我」 | 8 | 3 KB | 2 | 0.08 s | 0.34 s | 0.63 s |
| 切到「饮食」 | 3 | 0.6 KB | 2 | 0.11 s | 0.39 s | 0.70 s |
| 切回「今天」 | 0（本次打开期间有内存缓存） | — | 0 | 0 | 0 | 0 |
| 打开恢复卡（讲解弹层） | 1 | 1.6 KB | 1 | 0.26 s | 0.42 s | 0.55 s |
| 打开健康监测卡 | 0（数据跟着今天页带回来了） | — | 0 | 0 | 0 | 0 |
| 记一餐（快捷模板） | 4 | 7 KB | 3 | 0.33 s | 0.78 s | 1.24 s |
| 打开 Jarvis | 0（AI 配置跟着 `/v1/web/me` 带回来了） | — | 0 | 0 | 0 | 0 |
| Jarvis 发第一句 | 1（SSE） | — | 1 | 服务器调模型前准备约 0.38 s，再加模型时间 | | |

说明：讲解弹层的 0.26 s 里约 0.25 s 是「点下去到请求发出」（弹层先打开再请求，含测量工具本身的动作等待），切 tab 的 RTT 0 列也有同样的偏差，看串行往返层数更准。

**估算真机**（加上新连接和手机解析，不含正式服务器的处理时间）：

| | RTT 150 ms | RTT 300 ms |
|---|---|---|
| 重开 app 到看到今天的数字 | 0.5–0.66（连接）+ 0.79 + 0.2–0.4（解析）≈ **1.5–1.9 s** | 约 1.0 + 1.55 + 0.3 ≈ **2.9 s** |
| 切 tab（第一次） | 0.35–0.39 s | 0.63–0.70 s |
| 记一餐到出现「已记」 | 约 0.4 s（等 POST），全部刷新完 0.78 s | 约 0.7 s，1.24 s |

正式服务器上每个接口还要读 SparkyFitness、OW（今天页的睡眠阶段图每次实时读 OW）、算评分，本地合成数据测不出来；第 1 期的 `Server-Timing`（第 3.1 节）能在不碰任何账号的情况下从 nginx 日志里看到每个接口在服务器上花了多久。

### 1.3 最慢的 5 处

| # | 哪里 | 为什么慢 | 150 / 300 ms 下 |
|---|---|---|---|
| 1 | **启动**（冷 / 热都一样） | 5 层串行：`/app/` → 38 个静态文件（`no-cache`，每次 304 确认）→ 脚本跑起来后 `/v1/auth/methods` + `/v1/web/me` → 等 `me` 回来才发 11 个今天页接口 → 最后再发 `/health-history`；外加每次冷启动的新连接 | 1.5–1.9 s / 约 2.9 s |
| 2 | **记一餐** | 3 层串行：POST（服务器再写 SparkyFitness）→ 删掉缓存、重拉整份 `/today` 和 `/recipes/meal-plan` → `/diet-profile`；「已记」要等 POST 回来才显示 | 0.78 s / 1.24 s |
| 3 | **切到饮食 / 趋势 / 我** | 各 2 层串行：`/recipes` 之后才发 `/diet-profile`；`/reports` 之后才发 `/trends`、`/vo2max`、`/sleep/details`；6 个接口之后再发 `/account`、`/feedback`。本次打开期间切回来有内存缓存，app 重开就没了 | 0.35–0.39 s / 0.63–0.70 s |
| 4 | **讲解弹层** | 弹层打开后才请求 `/explain/metric/{key}`，空着等一个往返；健康监测卡 0 请求，说明预取过的东西就是瞬开 | 0.42 s / 0.55 s |
| 5 | **Jarvis 发消息** | 服务器在调模型前约 0.38 s（建对话、拼上下文），再加一个往返；模型时间另算。打开面板本身是 0 请求 | 0.53 s / 0.68 s + 模型 |

### 1.4 顺带发现：fail2ban 会封掉一整个出口 IP

公网 nginx 的 fail2ban（`deploy/ali/public/fail2ban-*.conf`）把 401 / 403 / 429 都算失败，10 分钟 30 次封 1 小时（只放行本机和 Tailscale）。没登录或登录过期的今天页每次打开都有几个 401（`/v1/web/me` 等）。测量时 5 轮 × 冷 / 热加载就把测量机的出口 IP 封了，同一个 Wi-Fi 下的手机也一起连不上。测试用户的风险：

- 登录过期后反复打开 app；
- 多个测试用户在同一个 NAT 后面（学校、社团、公司 Wi-Fi，移动网络的运营商级 NAT）。

上线以来只封过这一次，测试用户反馈的卡顿不是它造成的。建议（ops 改动，由负责人决定，不在这个仓库的 app 代码里）：401 不计入，或者只计登录类接口（`/v1/web/login*`、`/v1/auth/*`）的 401；其他接口只算 403 / 429，阈值调高。

### 1.5 复测步骤

1. 建测试库 `body_test_perf`（名字必须以 `body_test_` 开头），`body-engine migrate`、`user-add`、`import-apple-zip`（`demo_data.py` 生成的合成 zip）、`journal-init`、`memory-init`、`refresh-status`、`evaluate-rules`、`login-code`。
2. `uv run --with hypercorn hypercorn body_engine.api:app --certfile … --keyfile …`（自签名证书，HTTP/2）。
3. Playwright（系统 Chrome，`service_workers="block"`）登录一次，保存 cookie；新 context 依次做冷启动、热启动（reload）、切 tab、点卡片；有请求拦截时 Playwright 会关掉 HTTP 缓存，所以冷 / 热启动那一轮不能拦截请求，记一餐、Jarvis 另跑一轮。
4. **只测本地服务器或公开的静态文件（带人为延迟）**，不要对正式服务器发会产生 401 的请求（第 1.4 节）。

---

## 2. 现状（和这次方案有关的部分）

| 部分 | 现在 |
|---|---|
| app | Flutter 3.47（`apps/watch2whoop/`，Dart 约 6,800 行：引导、扫码、登录、权限、同步中心、推送）+ 原生 Swift（`AppDelegate.swift`、`RawSync.swift`，约 860 行）+ 一个全屏 WKWebView 打开 `/app/` |
| 今天页 | 原生 JS，没有构建链：38 个脚本和样式（约 16,000 行，`app.js` 4,081 行），`index.html` 直接引用文件名；`WEB_VERSION` 每次改页面都升 |
| 缓存 | `/app/*` 一律 `Cache-Control: no-cache`（每次 304 确认），`/v1/*` 一律 `private, no-store`；app 里没有 service worker（`offline-plan.md` 第 5 节） |
| 离线（#77，`offline_mode`） | 页面把本人的 GET 结果存 IndexedDB（300 条 / 4 MB / 14 天），**只在网络失败时用**；喝水、体重、训练逐组离线排队。app 1.0.7 起保存 `/app/offline-shell.html`（所有脚本和样式内联的页面），只在主页面加载失败时用 `loadHtmlString(副本, baseUrl: 今天页地址)` 打开 |
| 同步引擎（#219） | S1–S3 服务端已合并；A1（Swift 内核）、A2（后台）排在 1.0.8，A3、A4 在 1.0.8 或 1.0.9；读取、上传、锚点、后台调度全部在原生 Swift（`ios/Runner/SyncEngine/`），Dart 只做配置和界面 |

---

## 3. 第 1 期：不重写就能见效

目标：不等原生化，先把串行往返从 5 层降到 1–2 层，用户先看到上次的数据。大部分是服务端和页面的改动，**部署就生效，不用等 app 发版**；只有「先打开本机的页面外壳」要 1.0.8。

### 3.1 静态文件带版本号 + 长缓存（服务端，已做好：#257）

- `/app/` 和 `/app/index.html` 改成程序生成：每个 `<script src>`、`<link rel="stylesheet">` 后面加 `?v=<文件内容的 sha256 前 12 位>`，HTML 自己仍然 `no-cache`，带 ETag，没变就 304。
- 请求带的 `v` 等于这个文件**现在**的指纹时回 `Cache-Control: public, max-age=31536000, immutable`；不带 `v` 或者 `v` 对不上（旧页面请求新文件）仍然是 `no-cache`，不会把新内容长期缓存在旧地址下。
- 离线副本（`offline-shell.html`）、service worker 的文件列表、按需加载的插件脚本都不变（它们读原文件名）。
- 每个 `/v1/` 响应加 `Server-Timing: app;dur=<毫秒>`：只有一个耗时数字，不含用户数据；nginx 日志或者页面自己都能看每个接口在服务器上花了多久，用来补第 1.2 节测不到的部分。
- **效果**（#257 实测，同第 1.1 节的方法）：热启动 38 个 304 → 0，少一层串行往返：150 ms 下 0.79 → 0.64 s，300 ms 下 1.55 → 1.25 s；冷启动第一次不变。手机上 38 个文件的解析不变。
- **风险**：低。浏览器 / WKWebView 只会多缓存文件；回退 = revert，下次打开就回到 `no-cache`。

### 3.1.1 首屏瘦身：按需加载的脚本（2026-10-26 体检 W2，今天页，部署即生效）

- `index.html` 里首屏用不到的脚本写成 `<script type="w2w/lazy" src="…">`：浏览器不下载也不执行；服务端照样给 `src` 加 `?v=<指纹>`；`app.js` 的 `lazyScript(name)` 第一次需要时才加载（失败下次重试），加载后新注册的插件会补调 `init`。离线副本把它们当普通脚本内联（`offline_shell_html`），所以离线冷启动不受影响。
- 现在按需的：`i18n-en.js`（89 KB，只有英文页面要；`lang-gate.js` 在解析时按和 `i18n.js` 一样的规则判断语言，是英文才 `document.write` 一个 defer 的 `<script>`，测试逐例对比两者）、`muscle-body-data.js`（105 KB，有肌肉负荷数据的人第一次画卡片 / 训练页时）、`intro.js`（37 KB，新账号首次引导 / 「我 → 新手引导」）、`diet-profile.js` + `water.js` + `grocery.js`（饮食 tab 第一次打开）。
- 首屏的「装饰性」读取（`profile`、`account`、`sync/connection`、`sync/status`、`notices`、`health-history`，插件启动时各发一次）等第一页画完再发（`app.js` 的 `DEFERRED_GETS` / `firstPaint`，最迟 4 秒），首屏同一瞬间的 API 请求从 11 个降到 9 个。
- 实测（本地，`/app/`，中文，普通账号）：脚本 + 样式 40 → 35 个，1,242,015 → 974,870 字节（gzip -6：414,047 → 314,384）；预算守在 `tests/test_today_first_screen.py`。
- 还在 nginx 一侧要做（运维，不在代码里）：`limit_conn w2w_conn` 提到 100–150（HTTP/2 每个并发流都算一个连接），`/sdk/*/logs` 不限连接；`gzip_comp_level` 2 → 5。

### 3.2 启动接口合并：一次 `boot`（服务端 + 页面，已实现，开关 `boot_api`）

- 接口 `POST /v1/web/boot {"paths": [...]}`（`web/boot.py`）：页面把**上一次启动时自己发过的 GET 路径**（只限本人的数据：`/v1/web/me`、`/v1/auth/methods`、`/v1/account`、`/v1/friends…`、`/v1/users/me/…`，最多 24 个）发过来，日期写成 `{today}` / `{d:-6}`（相对用户自己的今天）。服务器先算 `/v1/web/me`（拿用户的今天和开关），其余并行；**每一项都是在服务器里把原接口原样跑一遍**（同一个 cookie / Bearer、同一种语言、同样的 share 鉴权、同样的状态码和内容），所以不用另写鉴权，原接口照常给别处用。某一项出错 / 超时（10 s）只让那一项带着它的错误状态，页面再单独请求那一项；`/v1/web/me` 续期 cookie 的 `Set-Cookie` 也带回来。
- 为什么用「上次发过的路径」而不是写死 11 块：开了哪些开关（日记卡、肌肉图、通知、朋友…）、插件、启动停在哪个 tab，启动时的请求都不一样；记下来的路径永远和页面实际要的一致，页面代码以后改了也不会和 `boot` 对不上（对不上的那一项只是没用上）。第一次启动（或刚打开开关后的那一次）照旧逐个请求，同时记下路径。
- 页面（`app.js`）：`boot` 的每一项放进一个只用一次的预取表（60 秒内有效），之后 `api()` 发同一个 GET 时直接用，不再发请求；`identities.js`、`account.js` 自己发的请求也先查这张表（`window.BodyPre`）。任何写操作清空预取表。开关状态在 `/v1/web/me` 回来之前就要用，所以记在本机（`localStorage` 的 `be.fast`：开关名 + 路径，没有健康数据），退出登录和 401 时清掉；关掉开关后，下一次启动起生效（`boot` 也直接 404）。
- **效果**：启动时的接口请求 19–20 个 → 1 个，数据部分从 3 层（`auth/methods` + `me` → 今天页各块 → `health-history`）变成 1 层，实测见第 3.9 节。

### 3.3 切 tab：并成一层 + 空闲预取（页面，已实现，开关 `tab_prefetch`）

- 趋势：`/reports` 和指标 / 睡眠的数据同时发（代码里直接并行）。
- 每个 tab 第二层的请求来自 tab 画完后各脚本自己的读取（记录页的训练日、我页的数据状态 / 朋友、饮食页的饮食档案 / 喝水）。做法和 3.2 一样：记下每个 tab 上次打开时发过的 GET 路径（`be.fast.tabs`），下次切过去时**和第一层一起发出**，第二层直接用预取表里的答案。
- 启动完成、空闲 2 秒后（`requestIdleCallback`）预取其他 tab 的这些路径，切过去 0 往返；最多 30 个请求。网络：浏览器有 `navigator.connection` 时，蜂窝、省流量（`saveData`）、2G 不预取；app 可以用 `window.Watch2WhoopFeatures.net = "wifi" | "cellular" | "constrained"`（iOS 低数据模式）告诉页面，`cellular` / `constrained` 不预取。**iOS 的 WKWebView 自己拿不到网络类型，1.0.7 也没传**，这时按第 7 节的规则只做有上限的小量预取（gzip 后约 20 KB）；1.0.8 用 `NWPathMonitor` 的 `isExpensive` / `isConstrained` 设上 `net` 后即严格不在蜂窝 / 低数据模式下预取。
- 没做：讲解弹层的预取（点开时才请求，留到第 2 期的静态包）。

### 3.4 写操作乐观更新（页面 + 服务端返回，已实现，开关 `optimistic_writes`）

- 快捷记餐：点下去立刻「已记 ✓」、今天的热量 / 三大营养素先在本地加上（改过克数的按比例算），POST 在后台发；失败时数字和「已记」撤回，提示原因和「重试」（409 = 今天已记过，仍是「仍要记」）。POST 和撤销（`DELETE …/meal-logs/{id}`）的返回带上 `today.nutrition`（`views.nutrition_today`，开关开着时才算），页面只换这一块，不再删缓存整份重拉 `/today`。刚记完马上点「撤销」会等这次 POST 拿到记录 ID 再撤。
- 喝水：点下去总量立刻加上，连续点几次各自提交（各自的 `client_id`），失败的那一次减回去并提示；服务器返回的当天数据以条目多的为准，晚到的旧答案不会把数字减回去。
- 体重：点「记下」立刻提示已记录、清空输入框，后台提交；被拒（同一天已有不同的体重）时照旧提示「用这次的覆盖」。
- 离线时的行为不变（`offline_mode` 的写入排队）。训练逐组本来就先存本机草稿，不在这次范围里。

### 3.5 先显示上次的数据（页面，复用 `offline.js`，已实现，开关 `cached_first_paint`，需要 `offline_mode` 也开着）

- 打开今天页时，如果本机有**同一个账号**的副本（`offline.js` 的 IndexedDB，只存本人数据），而且 `/v1/web/me` 的副本是**今天的、12 小时以内的**，先用副本把今天页画出来，顶部标「更新中 · 数据更新于 hh:mm」；同时 `boot`（或逐个请求）照常发出，新数据回来后原地替换（保留滚动位置；记录页和打开着的弹层不重画）。
- 先画的这一遍不写任何东西、不通知 app（「已登录」「语言」等等新数据回来后才发），读不到副本的块等网络。副本只在内存里用一次，画完就清掉，之后的读取都是新的。
- 退出登录清空副本（本来就有）和 `be.fast`；登录过期（401）时副本缓存也清空（本来就有）。
- 只在启动停在今天页、没带 `#login=`、没指定日期时用。

### 3.6 app 先打开本机的页面外壳（1.0.8）

- 1.0.7 已经会保存 `offline-shell.html`（无用户数据），但只在主页面失败时用。1.0.8 改成：**有副本就先用副本打开**（`loadHtmlString`，baseUrl = 今天页地址，同源），再在后台用 ETag 检查服务器有没有新版本，有就下载、下次打开换上（或者页面发现 `WEB_VERSION` 变了时请 app 无感地重载一次）。
- app 包里另带一份构建时的副本，第一次安装、还没下载过的时候用。
- 副本里的页面目前**不发请求**（`window.W2W_SHELL`）：要改成「副本 + 在线」模式也正常发请求。**待验证**：`loadHtmlString` 打开的页面发 `fetch` 时是否带上 cookie（同源应该带，但要在真机上确认）；不行就让 app 用 Keychain 里的账号 token 换一个 bearer 给页面（页面已有 `Authorization` 支持的接口），或者退回只用第 3.1 节的长缓存。
- 效果：启动时页面和静态文件那两层（加上新连接的等待）都不用等，150 / 300 ms 下首屏提前约 0.8–1.3 s；和 3.5 合起来，重开 app 约 0.3 s 先出上次的数据。
- 开关：服务器开关 `app_shell_first`，页面通过 bridge 告诉 app（`shell:first on/off`），app 记住上一次的值；关掉就和 1.0.7 一样走网络。

### 3.7 API 的 ETag / 304：先不做

接口返回很小（今天页 gzip 后 2.3 KB，启动全部 5.5 KB），304 省的是字节不是往返；真正省往返的是 3.2 / 3.3。ETag 留到第 2 期的文档同步一起做（那里每个文档本来就有版本号）。

### 3.8 第 1 期汇总

| 项 | 改哪里 | 要发 app 吗 | 预计效果（150 / 300 ms） | 现在能做吗 |
|---|---|---|---|---|
| 3.1 静态长缓存 + `Server-Timing` | 服务端 | 不用 | 启动 −0.15 / −0.3 s（实测），38 个 304 → 0 | **已做好**（#257） |
| 3.2 `boot` 合并 | 服务端 + 页面 | 不用 | 启动 −0.3 / −0.6 s | 能，约 2–3 人日 |
| 3.3 切 tab 并层 + 预取 | 页面 | 不用 | 切 tab −0.15 / −0.3 s，预取后 ≈ 0 | 能，约 1–2 人日 |
| 3.4 乐观更新 | 页面 + 服务端返回 | 不用 | 记一餐感知 ≈ 0 | 能，约 1–2 人日 |
| 3.5 先显示上次的数据 | 页面 | 不用 | 有副本时首屏不等数据 | 能，约 1–2 人日 |
| 3.6 先开本机外壳 | app 1.0.8 + 页面 | 1.0.8 | 首屏 −0.8 / −1.3 s | 1.0.8，约 3–4 人日（含真机验证） |
| fail2ban 401 规则 | ops | 不用 | 不再误封整个出口 IP | 能（ops 仓库） |

只做服务端部分时，重开 app 到看到今天的数字：150 ms 约 1.6–1.9 s → **0.9–1.2 s**，300 ms 约 2.9 s → **1.6–2.0 s**；加上 1.0.8 的外壳：先出上次的数据约 **0.3 s**，刷新完 0.6–0.8 / 1.0–1.3 s。

### 3.9 第 1 期 3.2–3.5 实测

方法同第 1.1 / 1.5 节：本机 Body Engine（这个分支）、hypercorn + 自签名 TLS（HTTP/2）、测试库 `body_test_perf`（`demo_data.py` 合成用户 90 天 + 一个快捷记餐模板）、系统 Chrome 无头 390 × 844、CDP 给每个请求加 RTT。除 `friends` 外其他开关全开（`friends` 关着，饮食才是独立的 tab，和 #255 一致）。「前」= 四个开关全关（和 main 一样），「后」= 四个全开；每组 2 轮取中位数。首屏 = 今天页恢复环出现的时间（从开始导航算）。切 tab 是稳态：每个 tab 之前打开过一次（学到路径）、重开后空闲预取已完成。记一餐的 POST 正式环境要写 SparkyFitness，这里用「一个 RTT + 250 ms」的假响应（不带 `today` 块）。**只测本地，没有对正式服务器发任何请求。**

| 操作 | 150 ms 前 | 150 ms 后 | 300 ms 前 | 300 ms 后 |
|---|---|---|---|---|
| 冷启动（缓存全空）首屏 | 0.66 s | 0.52 s | 1.27 s | 0.97 s |
| 冷启动网络结束 / 接口请求数 | 0.80 s / 19 | 0.50 s / 1 | 1.57 s / 19 | 0.94 s / 1 |
| **热启动（重开 app）首屏** | 0.49 s | **0.19 s**（上次的数据，标「更新中」） | 0.95 s | **0.33 s** |
| 热启动换成新数据 | 0.49 s | 0.35 s | 0.95 s | 0.63 s |
| 热启动网络结束 / 接口请求数 | 0.64 s / 18 | 0.52 s / 2 | 1.25 s / 18 | 0.93 s / 2 |
| 切到趋势 | 0.35 s（5 个，2 层） | 0（0 个） | 0.64 s | 0 |
| 切到记录 | 0.34 s（5 个，2 层） | 0.20 s（1 个） | 0.63 s | 0.35 s |
| 切到我 | 0.33 s（7 个，2 层） | 0（0 个） | 0.63 s | 0 |
| 切到饮食 | 0.38 s（4 个，2 层） | 0（0 个） | 0.68 s | 0 |
| 记一餐到「已记 ✓」 | 0.41 s | **0**（点下去即显示） | 0.56 s | **0** |
| 记一餐网络结束 | 0.79 s | 1.02 s | 1.25 s | 1.46 s |

说明：
- 热启动后的 2 个请求 = `boot` + `/v1/friends/feed/unread`（同一路径有两处读取，启动包只答一次）；记录页剩下的 1 个是 `training/muscle-load`（今天页已读过一次、记录页的脚本又自己读一次）。都是单层、不影响首屏。
- 记一餐网络结束变长：先显示时饮食页重画一次（饮食档案、喝水卡跟着重读），本地的假响应又不带 `today.nutrition`，所以回来后再重拉一次 `/today`。正式环境 POST 返回 `today.nutrition`，今天页数字一致时不再重画；这一项只影响后台流量，不影响看到「已记」的时间。
- 第一次启动（或刚打开开关后的那一次）、每个 tab 第一次打开时还是原来的速度，同时学路径；之后才是上表的数字。
- 真机还要加新连接（约 0.5–0.66 s）和手机上解析脚本的时间（第 1.2 节）；这些不受这次改动影响，3.6（1.0.8 先开本机外壳）去掉的是页面那一层。

---

## 4. 第 2 期：本地数据层

目标：常用的东西都在手机上，打开就有、离线能看能记，后台增量同步。服务端协议先做，今天页（`offline.js` 的扩展）先当第一个客户端；第 3 期的原生 app 用同一个协议和同一套测试用例。

### 4.1 手机上存什么

| 类别 | 内容 | 范围 | 来源 |
|---|---|---|---|
| **静态包**（不常变，所有人一样） | 动作库（#248：名称、部位、器械、肌群、步骤，带来源和许可证字段）、讲解词条 `glossary`、指标 / 状态卡的名称和单位、食谱标签、界面英文翻译 | 全量，带版本号 | 打进 app 包 + `GET /v1/static/{包名}?since=<版本>` 增量 |
| 每天的文档 `day:<日期>` | 评分、睡眠（含阶段图）、负荷、压力、健康监测、提醒、训练（计划 + 完成的组）、饮食合计和条目、喝水、日记、体重 | 今天和过去 14 天全量；更早的按需拉、留 90 天 | 新的同步接口（4.2） |
| 序列 `series:trends` | 趋势 tab 的全部日值序列（约 50 个指标） | 365 天 | 同上，按天增量 |
| 训练 | 月历（90 天）、计划、动作规则（禁用 / 小心）、自定义动作 | 90 天 + 全部规则 | 同上 |
| 饮食 | 快捷记餐模板、食谱（元数据 + 每份营养）、饮食档案、采购清单 | 全量 | 同上 |
| 我 | 个人资料、状态卡、长期记忆（只读副本）、补剂、化验摘要、功能开关 | 全量 | 同上 |
| **不存** | 别人的数据（朋友页、共享查看）、AI 对话全文、心电图波形、反馈、AI 设置里的 key 信息 | — | 只在线 |

估算一个用户 1–2 MB（不含动作库；动作库按 openGym 的规模，gzip 后约 0.5–1 MB，打进包里）。

### 4.2 增量同步：按文档、按版本

页面大多数内容是服务器算出来的视图（评分、建议、趋势），不是用户直接编辑的记录，所以同步单位是「文档」而不是表的行：

- 服务器表 `doc_versions(user_id, doc_key, version, updated_at)`：任何会改变某天视图的写入（导入、`compute-scores`、`hk-derive-run`、记录类接口、管家的写工具）调用一个 `touch(user_id, days)`，把那几天的文档版本加一。同步引擎（#219）已有的脏天队列 `hk_dirty_days` 就是触发点之一。
- `POST /v1/users/me/sync/pull {cursor, have: {doc_key: version}}` → 返回 `cursor` 之后变了的文档（全文，gzip）、删掉的 key、新的 `cursor`。客户端只要 `have` 里没有或者版本更旧的。
- 拉的时机：回到前台、自己的写操作提交成功后、同步引擎一轮上传结束后（服务器回 `dirty_days`，app 只拉那几天）、可选的静默推送（`content-available`，推送已经有了）。
- 服务器重算后版本变了就是「有新数据」，不用比内容；旧接口全部保留，页面没升级也照常用。
- 工作量：服务端约 8–10 人日（`doc_versions`、`touch` 接到各个写入点、`pull`、按文档复用现有视图函数、测试）；页面客户端约 3–4 人日（在 `offline.js` 上扩展）。

### 4.3 写操作离线排队和冲突

沿用 `offline-plan.md` 第 3 节的规则（按顺序发、按组阻塞、失败标「没提交」给用户选），扩大范围：

| 类型 | 幂等 | 冲突 |
|---|---|---|
| 喝水、训练逐组、训练提交、体重 | 已有（`client_id`、整份覆盖、同天同值） | 已有 |
| 快捷记餐、食谱记一份 | 新加 `client_id`（服务器记住 client_id → log_id，重发返回同一个） | 当天已记过同一个模板 → 409，用户选「仍要记」或「丢弃」（现在在线时已经这样） |
| 日记（打勾 / 填数） | 新加 `client_id`；同一天同一项还没发的合并成一条 | 以最后一次编辑为准 |
| 补剂打卡 | 同上 | 同上 |
| 删除 / 撤销 | 只能撤销本机还没发出去的；已提交的撤销要联网 | — |
| 不离线 | Jarvis / AI、朋友页、AI 设置、账号、建食谱 / 模板、化验录入 | — |

写入在本地先改对应的文档（乐观更新），服务器确认后用服务器返回的版本覆盖。

### 4.4 同步引擎（#219）的原生部分怎么接进来

- #219 的 app 端本来就设计成纯 Swift（`ios/Runner/SyncEngine/`：注册表、锚点、读取、上传、后台调度），Flutter 只做配置和界面。所以 A1 / A2 在 1.0.8 的 Flutter app 里做好后，第 3 期换成 SwiftUI 外壳时**原样搬过去**，不用重写。
- 共享三样东西：Keychain 里的账号 token（同一个 Keychain 项）、服务器地址配置、本地数据库。
- 同步引擎一轮上传后服务器回 `dirty_days` → 本地数据层只拉那几天的文档 → 今天 tab 一分钟内更新，不用用户下拉刷新。
- 同步中心（#219 A4）读引擎的状态表，原生页面直接显示；在 Flutter 阶段继续走现在的通道。

---

## 5. 第 3 期起：SwiftUI 原生化

### 5.1 Flutter 的去留：换成纯 SwiftUI

Android 暂停后，Flutter 只剩一层外壳：页面在 WebView 里，健康数据和同步在 Swift 里。保留 Flutter 再往里塞原生页面，等于同时维护 Dart、Swift、JS 三套界面和两层桥接。建议：

- **第 3.0 步（1.1.0）先用 SwiftUI 重写 app 外壳**，替换 Flutter：引导、扫码（`DataScannerViewController` / AVFoundation）、登录（Sign in with Apple 本来就是系统框架）、权限、同步中心、推送、设置链接；中间仍然是同一个 WKWebView 打开今天页，**用户看起来不变**。
  - 同一个 Bundle ID，TestFlight 上原地升级；第一次启动把 Flutter 存的东西迁过来：`shared_preferences`（`NSUserDefaults` 里 `flutter.` 前缀的键）、`flutter_secure_storage` 的 Keychain 项（账号 token、上传 token）、RawSync 的锚点，迁移代码留两个版本后删掉。
  - JS bridge 名字（`Watch2Whoop`）、`watch2whoop://` 链接、`window.Watch2WhoopFeatures` 全部保持，页面不用改。
  - OW iOS SDK 在 #219 下线 OW 之前还要用：它本来就是原生 SDK（Flutter 插件只是包装），SwiftUI 外壳直接调。
  - 工作量约 15–20 人日（Dart 约 6,800 行，大部分是引导和同步页面）。
- 之后每个 tab 单独换成原生（5.2）。
- 仓库：新目录 `apps/ios/`（Xcode 工程，依赖用 Swift Package Manager：本地数据库用 GRDB（MIT），图表用系统的 Swift Charts，不加别的）；`apps/watch2whoop/` 冻结，1.1.0 稳定后归档；`apps.md` 更新平台表。CI 加 macOS 任务（公开仓库免费）跑 `xcodebuild test`。
- 双语：原生界面用和页面一样的 `tr("中文")` 写法，`Localizable.xcstrings` 由脚本从 `i18n-en.js` 生成（同一份翻译），测试检查原生代码里的每个中文都有英文。

### 5.2 按 tab 迁移的顺序

| 顺序 | 版本 | 内容 | 为什么这个顺序 | 估计 |
|---|---|---|---|---|
| 3.0 | 1.1.0 | SwiftUI 外壳 + 本地数据层（GRDB，第 2 期协议的 Swift 客户端）+ 同步引擎 A3 / A4 | 后面所有原生页面都要它 | 15–20 + 8–10 人日 |
| 3.1 | 1.1.0 | **今天**（恢复圈、三块数字、健康监测、负荷目标、睡眠、提醒、训练卡、饮食卡、快捷记餐） | 打开最多、启动卡顿的来源；数据都在 `day:<今天>` 一个文档里 | 10–15 人日 |
| 3.2 | 1.2.0 | **记录**（训练逐组、日记、喝水、体重） | 写操作最多，离线排队的收益最大 | 15–20 人日 |
| 3.3 | 1.2.0 | **趋势**（Swift Charts） | 纯只读，`series:trends` 一个文档 | 8–10 人日 |
| 3.4 | 1.3.0 | **饮食**（食谱、每餐建议、采购） | 依赖 SparkyFitness 的部分多，放后面 | 10–15 人日 |
| 3.5 | 1.3.0 | **我**（资料、状态卡、记忆、补剂、化验、设置、AI 设置、圈子 / 朋友） | 页面多但都是低频设置，最后换收益最小 | 15–20 人日 |
| 3.6 | 1.4.0 | **Jarvis**（SSE 对话、确认卡片、照片 / 视频 / 按住说话） | 最复杂，瓶颈在模型不在界面；WebView 版先预热 | 15–20 人日 |

全部换完约 110–150 人日；每个 tab 换完后 WebView 版再保留两个版本作为回退，之后删掉对应的页面代码（页面本身也只剩 app 一个客户端，见 `apps.md`）。

### 5.3 迁移期间 WebView 和原生页面怎么共存

- **一个常驻的 WKWebView**：启动时在后台预加载今天页（用 3.6 的本机外壳），还没迁移的 tab、弹层、深链接都由它显示；切换时用 JS 调页面自己的路由（`#tab=…`），不重新加载。所以原生 tab 和 Web tab 来回切不会每次重开页面。
- **登录**：原生持有账号 token（Keychain，现在就有）；WebView 的会话 cookie 用一次性登录码换（`POST /v1/web/login-code` 已有），或者原生登录后把 cookie 写进 `WKHTTPCookieStore`。任何一边退出登录都通过 bridge 通知另一边，一起清掉本地数据和队列（队列有没发的先确认，同 `offline-plan.md` 第 4 节）。
- **数据**：原生 tab 读本地数据库；WebView 在它的 tab 迁走之前继续用自己的 IndexedDB。原生写入后发 bridge 事件 `data:changed {days}`，页面丢掉那几天的缓存。
- **开关**：每个原生 tab 一个服务器开关（`native_today`、`native_record` …，`rollout/registry.py`），app 启动和回到前台时读，记住上一次的值；关掉 = 那个 tab 回到 WebView。另有本机保护：原生页面连续两次启动崩溃，自动退回 WebView 并上报。
- **复杂页面**：
  - **讲解弹层**：服务器已经返回结构化 JSON（`explain/metric`、`explain/status`），原生直接渲染；词条走静态包，离线也能看。
  - **图表**：恢复圈、睡眠阶段图、趋势折线、心率区间用 Swift Charts 重画；肌肉图（`muscle-map.js` 的 SVG 数据）先在原生页面里嵌一个小 WebView，之后再换。
  - **Jarvis**：1.4.0 之前一直是 WebView（预热好的那个 WKWebView 弹出来，打开 0 等待）；原生化时用 `URLSession` 的字节流读 SSE，Markdown 用 `AttributedString`，拍照 / 录音用系统框架（按住说话现在就走原生开关 `Watch2WhoopFeatures.mic`）。
  - **朋友 / 圈子**：在线功能，不进本地数据层，原生化放最后（或者一直留 WebView）。

### 5.4 第 2 期先行（1.0.9，负责人 2026-10-27 决定）：Flutter 里先放两个原生视图

在 1.1.0 换掉 Flutter 之前，先在现在的 app 里验证原生视图和页面共存的做法。两个服务器开关，默认关；关着时 app 和 1.0.8 一样（唯一的区别：1.0.9 的 app 会声明 `Watch2WhoopFeatures.nativeUi`，页面按需多取一个 `native-bridge.js` 并回报开关都关着）。

- **怎么叠**：SwiftUI 视图放在 Flutter 窗口上面的一层透明视图里（`ios/Runner/NativeUI.swift`，在 WebView 之上、之后弹出的系统界面之下），只在原生视图所在的位置接触摸，其余穿透给页面。位置由页面报告（CSS 像素 = 点）。
- **协议**（`web/static/native-bridge.js` ↔ `lib/src/native_ui.dart`）：页面 → app `native:`（开关）、`jvn:`（Jarvis 键的位置、能否看见、点一下是不是开菜单）、`tdn:`（今天从分数到 dock 的区域、能否看见）、`tdn-data:`（今天这次画用的数据，固定字段子集）、`jv-camera`；app → 页面 `BodyNative.action / photo`。是否「能看见」用 `elementFromPoint` 判断：弹层、对话面板、登录页、提示条盖住时原生视图自动让开。
- **Jarvis**（`native_jarvis`，还要 `jarvis_orbit`）：键和菜单原生（iOS 26 `.glassEffect(.regular.interactive())` + `GlassEffectContainer` + `glassEffectID`，子键从 Jarvis 键的玻璃里流出；更早的 iOS `.ultraThinMaterial`）；按下压缩回弹、三环、扫描光、呼吸一次，时间线和 `jarvis_dial` 一致。点子键、自己的 AI / 离线 / 录音中的点按都交回页面原来的代码，语音面板、对话仍是页面。**按住说话**和网页键一致：按住 0.35s（环先填满）开始听，手指位置逐帧交给页面（`hold-start / hold-move / hold-end / hold-cancel`），页面用原来的 `holdMove / holdUp` 判断往左上取消、往右上锁定、原地松开识别；识别走 1.0.8 的本机语音（#238），松开后按 `jarvis_voice_direct` 直接发送或先显示文字可改。**状态同步**：页面把 `phase`（在听 / 识别中 / 确认文字 / 发送中 / 回答中）报给 app，原生键换成对应的图标（录音时反色，识别 / 发送 / 回答时图标呼吸），无障碍标签同步。
- **今天**（`native_today`）：分数以下原生（三环 + 恢复 / 睡眠 / 活动 / 负荷目标 / 健康监测），分数以上（同步卡片、横幅）仍是页面。数字由 Dart 按页面的格式算（`todayModel`），颜色用页面的 token。app 存上一次的数据，冷启动先画出来（退出登录清空）。看别人、看过去的日子、睡眠详情、「完整今天页」时退回网页。
- **还不是第 4 节的本地数据层**：数据仍由页面取（已有接口 + `boot` / `cached_first_paint`），1.1.0 起换成同步协议的本地库。

模拟器实测（iPhone 17 Pro Max 模拟器 iOS 26.2，调试包，`W2W_NATIVE_UI=jarvis,today,meter,demo` 自动展开 / 收起、做三种按住说话手势（模拟器里用脚本化的识别器，不要麦克风）、滚动今天，`FrameMeter` 用 CADisplayLink 记每帧间隔，> 1.5 帧算掉帧；模拟器最高 60Hz）：

| 场景 | 帧率 | 掉帧 |
|---|---|---|
| Jarvis 展开，Liquid Glass（浅色 2 轮 + 深色 1 轮，共 15 次） | 55–61 fps | 每轮第一次展开 1 帧 67–84ms（玻璃第一次渲染）；之后 12 次里 7 次 0 帧，5 次 1–2 帧（最长 26–39ms） |
| Jarvis 收起（15 次） | 58–60 fps | 0 |
| Jarvis 展开，`.ultraThinMaterial`（iOS 26 以下的样子，5 次） | 61 fps | 0 |
| 今天滚动（浅色 8 次 + 深色 3 次，0.9s 动画滚到底 / 回顶） | 57–60 fps | 第一次 1 帧 68ms（第一次排版），其余 0 |
| 按住说话（取消 / 锁定 / 松开，3 轮共 5 次手势，含 0.6s 滑动） | 58–62 fps | 每次 0–2 帧（最长 33ms，多在开始听的那一刻） |

真机 120Hz 还要在 ProMotion 机型上确认（`CADisableMinimumFrameDurationOnPhone` 已开，帧计时器按屏幕最高帧率请求）：用调试包加 `meter` 看日志里的 `[frame-meter]`，或 Instruments 的 Animation Hitches。

---

## 6. 发版节奏

| 时间（目标） | 版本 | 内容 | Beta 审核 |
|---|---|---|---|
| 10-25 | 1.0.7 | 已定，不加东西 | 1 次（已排） |
| 10-26 起 | 服务器 | 第 1 期服务端部分：3.1（#257）、3.2、3.3、3.4、3.5；fail2ban 规则；第 2 期服务端协议开始 | 不用 |
| 约 11-08 | **1.0.8**（Flutter，最后一个 Flutter 版本） | 3.6 先开本机外壳（开关）+ 包内副本；#219 A1 / A2（开关）；bridge `data:changed` | 1 次 |
| 约 12-06 | **1.1.0**（SwiftUI） | 外壳替换 Flutter、本地数据层、原生今天（开关）、#219 A3 / A4 | 1 次 |
| 约 1 月中 | 1.2.0 | 原生记录 + 趋势 | 1 次 |
| 约 2 月 | 1.3.0 | 原生饮食 + 我 + 讲解 | 1 次 |
| 之后 | 1.4.0 | 原生 Jarvis；删掉迁完的 WebView 页面 | 1 次 |

- 少打包：一个版本号里攒够内容再发；同一版本号的修复用新 build（TestFlight 一般只有每个新版本号的第一个 build 要过 Beta 审核），`pubspec` / Xcode 的 build 号照常递增（`apps.md` 第 4 节，对外名字只用版本号）。
- 能放服务器的都放服务器：第 1 期大部分、第 2 期的协议和页面客户端都不用发 app。
- 每个版本发布前在 TestFlight 内部测试组装一遍（不要审核），再给外部测试用户。
- TestFlight 的旧 build 在 90 天内都能装回去，这是 app 层面的最后回退（6.1）。

## 7. 风险和回退

| 期 | 出问题时 | 怎么关 / 回退 |
|---|---|---|
| 3.1 静态长缓存 | 页面用到旧文件 | 只有指纹对上才长期缓存、HTML 每次确认，所以旧页面拿不到新文件的长缓存；revert 后下次打开即恢复 |
| 3.2 `boot` | 某块报错 / 变慢 | 每块独立；开关 `boot_api` 关掉 = 接口 404、页面逐个请求（本机记着的开关在下一次启动更新） |
| 3.3–3.5 页面 | 预取浪费流量、显示旧数据、乐观显示后失败 | 预取有上限、已知蜂窝 / 省流量不做；先显示旧数据有「更新中 · 时间」；失败撤回并提示；开关 `tab_prefetch` / `cached_first_paint` / `optimistic_writes` 各自关掉就是以前的行为 |
| 3.6 本机外壳（1.0.8） | 副本和服务器 API 不配套、cookie 没带上 | 开关 `app_shell_first` 关掉 = 1.0.7 行为；页面发现 `WEB_VERSION` 不一致时请 app 重载一次；服务器 API 对旧页面保持兼容 |
| 第 2 期同步 | 文档版本漏改、显示旧数据 | 开关关掉 = 只用旧接口；本地数据库带 schema 版本，对不上就清空重拉；服务器的旧接口一直保留 |
| 1.1.0 外壳替换 | 迁移 Keychain / 设置失败、引导流程出错 | 迁移失败时不删旧数据、显示「重新扫设置二维码」；测试用户可以在 TestFlight 装回 1.0.8；内部测试组先装一周 |
| 原生 tab | 崩溃、数据不对 | 服务器开关关掉那个 tab = 回到 WebView（下次打开或回到前台生效）；本机连续两次崩溃自动回退 |
| 同步引擎 | 后台不同步 | 按 #219 的开关和回退（`apple-sync-engine.md` 第 5 节），OW 链路在切换前一直在 |

## 8. 要负责人决定的点

> 负责人 2026-10-25 的决定：D1–D10 **全部按推荐执行**（纯 SwiftUI；先做服务端提速；1.0.8 只加「先开本机外壳」+ #219 A1 / A2；最低 iOS 17；第 2 期客户端先在页面里做；D3、D5、D6、D7、D10 同推荐），第 1 期马上开工。

| # | 问题 | 推荐（= 决定） |
|---|---|---|
| D1 | 纯 SwiftUI，还是在 Flutter 里加原生页面 | **纯 SwiftUI**：1.1.0 先换外壳（看起来不变），再按 tab 换；Flutter 1.0.8 之后冻结 |
| D2 | 第一期先做服务端提速吗 | **先做**：3.1（#257）、3.2–3.5、fail2ban 规则，不等 1.0.8；预计重开 app 从约 1.7 s 到约 1.0 s（150 ms 往返） |
| D3 | 离线写入的范围 | 第 2 期 = 现有的喝水、体重、训练逐组 + 快捷记餐 / 食谱记一份 + 日记 + 补剂打卡；AI、删除已提交的、朋友、设置不离线 |
| D4 | 1.0.8 带不带原生页面 | **不带**，只带「先开本机外壳」+ #219 A1 / A2，保持 1.0.8 小、好回退 |
| D5 | 手机上存多少、存多久 | 每天的文档 14 天全量 + 90 天按需，趋势 365 天，静态包全量；文件用 iOS 数据保护 `completeUntilFirstUserAuthentication`（后台同步要读）；退出登录清空 |
| D6 | 减少 Beta 审核：同一版本号多个 build 修复 | **接受**：每个版本号只审一次，修复传新 build；要和打包的朋友确认可以多传几次 build |
| D7 | fail2ban 的 401 规则 | 401 不计入（或只计登录类接口），其他只算 403 / 429，阈值调高；ops 仓库改 |
| D8 | 最低 iOS 版本 | 1.1.0 起 **iOS 17**（SwiftUI 的 Observation、Swift Charts 新接口）；现在是 iOS 15，先确认测试用户都在 iOS 17 以上 |
| D9 | 第 2 期的客户端先在页面里做吗 | **先在页面里做**（部署就生效、马上帮到所有人），原生 app 用同一个协议和测试用例；代价是页面客户端将来会删掉（约 3–4 人日） |
| D10 | 动作库（#248）的 AGPL 文字打进 iOS 包 | 跟 #248 一起定；第 2 期的静态包机制先做，内容等许可证结论 |

---

## English summary

**Problem.** Testers find the app slow. Measured against a local HTTP/2 server with synthetic data and 150 ms added round-trip time, the Today page is bound by serial round trips, not rendering: with 0 ms RTT a cold start takes 0.25 s and logging a meal 0.33 s; at 150 ms they take 0.80 s / 0.78 s, at 300 ms 1.55 s / 1.24 s. A real device adds the new connection (DNS + TCP + TLS, ~0.5–0.66 s), parsing 1.15 MB of scripts and styles, and production server time. Re-opening the app costs ~1.5–1.9 s at 150 ms RTT and ~2.9 s at 300 ms.

**Five slowest paths.** (1) Start-up: five serial layers — the page, 38 static files revalidated every time (`no-cache`), `/v1/web/me`, 11 Today endpoints that wait for it, then `/health-history`. (2) Logging a meal: three serial layers and the "logged" tick waits for the POST. (3) First visit to Food / Trends / Me: two serial layers each. (4) Explanation sheets: the sheet opens empty and fetches afterwards. (5) Jarvis: ~0.38 s server preparation before the model starts. Side finding: the public fail2ban jail counts 401s, so signed-out page loads can ban a whole shared IP for an hour; it has fired only once (during this measurement), so it is not the cause of the reported slowness.

**Plan.**
- *Phase 1, no rewrite (mostly server-side, live on deploy):* fingerprinted static URLs with immutable caching plus a `Server-Timing` header (done in #257: warm start 0.79 → 0.64 s at 150 ms RTT); one `boot` endpoint for start-up; parallel tab requests and idle prefetch; optimistic writes; render the last known data first (reusing the offline IndexedDB cache); in app 1.0.8, open the kept page shell first. Expected: ~1.7 s → ~1.0 s server-side only, and ~0.3 s to first (cached) content with 1.0.8.
- *Phase 2, local data layer:* document-based incremental sync (`doc_versions` + `POST /sync/pull`), static bundles (exercise library, glossary) shipped in the app, an offline write queue extended to meals, journal and supplements; the web page is the first client, the native app reuses the protocol; the Swift sync engine (#219) feeds dirty days into it.
- *Phase 3+, SwiftUI:* 1.1.0 replaces the Flutter shell with SwiftUI (same bundle ID, same WebView pages, migrated Keychain / defaults), then tabs move one at a time — Today, Record, Trends, Food, Me, Jarvis — each behind a server kill switch that falls back to the WebView. ~110–150 person-days in total.
- *Releases:* 1.0.8 (last Flutter build, small) → 1.1.0 → 1.2.0 → 1.3.0 → 1.4.0; one Beta review per version number, fixes as new builds.

**Phase 1 status.** 3.1 is live (#257); 3.2–3.5 are implemented behind the flags `boot_api`, `tab_prefetch`, `optimistic_writes` and `cached_first_paint` (off by default). Measured locally at 150 / 300 ms RTT (section 3.9): re-opening the app shows the last data after 0.19 / 0.33 s instead of 0.49 / 0.95 s and fresh data after 0.35 / 0.63 s; start-up sends 1 API request instead of 18–19; tab switches after the idle prefetch take 0 round trips; a quick meal shows as logged at once.

**Decisions for the owner** (section 8, all taken as recommended on 2026-10-25): pure SwiftUI (recommended), server-side speed-ups first (recommended), offline write scope, keeping 1.0.8 small, local storage limits, one review per version, the fail2ban 401 rule, minimum iOS 17 from 1.1.0, web-first client for phase 2, and the exercise-library licence question (#248).
