# 自助注册 + 内测激活码

> 状态：服务器和今天页已实现（2026-10-20，`body_engine/src/body_engine/account/`，迁移 `230_self_signup.sql`）；iOS app 另一个 PR。
> 方案最初在 PR #8 里讨论，这份文档取代它。

## 为什么

以前每个账号都要部署者在服务器上开（管理员开户脚本），再把个人激活链接发给本人；链接里又带一次性登录码和同步邀请码，码太多、容易丢（issue #12）。人一多，管理员就成了瓶颈。

现在：**用户自己登录**（邮箱验证码或「通过 Apple 登录」），内测阶段**凭激活码开通**；管理员只需要**批量生成激活码**往外发。用户从头到尾只碰到**一个码：激活码**。

## 用户流程

1. **登录**：app 首屏「通过 Apple 登录」或「用邮箱登录」（邮箱收 6 位验证码，不设密码）。今天页（网页）目前只有邮箱登录（见下面「网页版通过 Apple 登录」）。
2. **输入激活码**：账号刚建好是 `pending`（待开通），只显示一页「输入激活码」，其他接口一律 403 `account_pending`，不读、不存任何健康数据。
3. **开通**：激活码有效 → 账号变 `active` → 服务器在后台自动开通：同步账号（Open Wearables）、训练 / 饮食记录账号（SparkyFitness）、默认日记项、管家的初版档案卡 → 同意书 → 个人资料 → 授权健康 → 正常使用。
4. app 登录后**静默**向服务器要一个新的 OW 邀请码（`POST /v1/account/ow-invitation`）并兑换，用户看不到；今天页用一次性登录码在 app 内自动登录（`POST /v1/account/web-login-code`）。
5. 以后换手机：用同一个 Apple ID / 邮箱登录，不需要激活码，也不需要管理员。

同一个人先用邮箱、后用 Apple 登录（Apple 给的邮箱已验证且相同）会进同一个账号；用了 Apple「隐藏邮件地址」则是另一个账号。

## 管理员

服务器上（容器里）：

```
body-engine invite-code batch --count 50 --label 小红书-1020 [--days 30] [--max-uses 1]   # 打印 CSV（明文只出现这一次，库里只存 sha256）
body-engine invite-code list [--batch N]          # 每批：码数、已用 / 总次数、还能用的码；--batch N：每个码谁用的、什么时候
body-engine invite-code revoke --batch N | --code-id N
body-engine provision-run [--user-id N]           # 补完没开通完的（每小时跑一次；也清理 7 天没开通的 pending 账号和过期验证码）
body-engine mail-test --to you@example.com        # 用当前配置发一封测试邮件
```

部署者自己的 Mac 上有一个私有的管理命令（不在公开仓库）通过 ssh 调上面这些，把明文码写进本机 `secrets/` 下权限 600 的 CSV。

- 一个码默认只能用 1 次；`--max-uses 20` = 一码 20 人（比如发在群里）。整批可以设有效期、可以整批或单个停用；已经开通的账号不受影响。
- 管理员开户（开户脚本）+ 个人激活链接（`/app/activate.html#<码>`）**继续保留**，给不方便自己注册的家人。

## 数据和接口

| 表 | 作用 |
|---|---|
| `users.status` | `pending`（已登录未开通）/ `active` / `disabled`；已有账号都是 `active` |
| `user_identities` | 登录身份：`provider`（apple / email）、`subject`（Apple 的 sub / 规范化后的邮箱）、`email`、`verified_at`；一个用户可以有多个 |
| `email_login_codes` | 邮箱验证码：只存 sha256(salt + 码)，10 分钟、最多错 5 次，新码作废旧码 |
| `invite_batches` / `invite_codes` | 激活码批次（标签、有效期、每码次数）和码（`code_sha256`、末 3 位 `hint`、`uses` / `max_uses`） |
| `invite_redemptions` | 谁在什么时候用了哪个码（账号删除后 user_id 置空，次数保留） |
| `user_provisioning` | 开通任务状态（每步 done / error、重试次数）+ 这个人的 SparkyFitness 登录和两把 key（用 AI 主密钥 AES-256-GCM 加密，绑定 user_id） |

接口（`account/api.py`）：

| 接口 | 说明 |
|---|---|
| `GET /v1/auth/methods` | 服务器开了哪些登录方式（`email` / `apple`） |
| `POST /v1/auth/email/start` `{email, lang}` | 发验证码（不管账号是否存在，回答都一样） |
| `POST /v1/auth/email/verify` `{email, code, client, device}` | 验证 → 会话：`client=web` 设今天页 cookie，`client=app` 返回这台设备的 Bearer token |
| `POST /v1/auth/apple` `{identity_token, nonce, client, device, given_name, family_name}` | 原生 iOS 的 Apple identity token → 同上 |
| `GET /v1/account` | 状态、登录身份、开通进度（pending 也能调） |
| `POST /v1/account/redeem-invite` `{code}` | pending → active，后台开通 |
| `POST /v1/account/ow-invitation` | 给自己的同步账号要一个新的 OW 邀请码（app 静默兑换） |
| `POST /v1/account/web-login-code` | 10 分钟一次性登录码（app 让内嵌今天页登录） |
| `POST /v1/account/logout` | 吊销这台设备的 token |
| `DELETE /v1/account` `{confirm: "DELETE"}` | 删除账号和全部数据 |

限流：验证码 / Apple 登录 / 激活码兑换失败都计入现有的每 IP 登录失败限制（`web/login_limit.py`，15 分钟 10 次）；发验证码每个邮箱每小时 5 封、60 秒 1 封，每个 IP 每小时 20 封。

### 开通任务（`account/provision.py`，幂等）

| 步骤 | 做什么 |
|---|---|
| ow | 用 Body Engine 已有的 OW API key 建 OW user（`external_user_id = w2w-u<id>`，先查有没有）→ `users.ow_user_id` |
| sparky | SparkyFitness 账号，登录名是**合成的** `w2w-u<id>@users.watch2whoop.invalid`（不用本人邮箱，可用 `BODY_SPARKY_EMAIL_DOMAIN` 改）、随机密码；生成读写 + 只读 key，加密存库（不再写 secrets 文件、不用重建容器） |
| journal | 默认日记项 |
| memory | 管家初版档案卡和状态卡（不含部署者本人的数据） |

兑换后马上在后台跑一次；失败的步骤由每小时的 `provision-run` 重试（最多 48 次）。失败不影响账号开通，只是对应功能晚一点出现。

**SparkyFitness 的注册开关**：SparkyFitness 关注册时（`SPARKY_FITNESS_DISABLE_SIGNUP=true`）连管理员 API 也建不了用户，所以自动开通要求**打开注册**（`SF_DISABLE_SIGNUP=false`）。SparkyFitness 的端口只绑 127.0.0.1、只经 tailnet 访问，打开注册只对你自己的设备可见；不愿意打开的话，sparky 这一步会一直失败重试，你可以临时打开注册跑一次 `provision-run` 再关上。

### 删除账号（App Store 审核指南 5.1.1(v)）

今天页「我」→「删除账号」（app 里也是这一页），输入「删除」确认 → `DELETE /v1/account`：

1. SparkyFitness 用户（用管理员的读写 key，`BODY_SPARKY_ADMIN_KEY_FILE`，默认 `secrets/sparkyfitness_api_key_owner_rw`，挂进容器）
2. Open Wearables 用户（OW API key）
3. Body Engine 里所有带 user_id / 外键指向这个用户的行，然后用户本身
4. 上传过的文件 `health_data/imports/u<id>`、`archive/u<id>`

外部系统删除失败不会挡住 Body Engine 的删除：日志里记 ERROR（带外部 id），管理员手动补删。部署者账号（owner）不能这样删。在 app 里删除后页面打开 `watch2whoop://account-deleted`，app 停止同步、清掉登录、回到首屏。

## 发邮件（部署者要决定和提供的）

发信器和服务商无关：SMTP（STARTTLS 587 或 SSL 465）+ 一个 `console` 开发模式（不发信，验证码只出现在日志里，测试和 Quick Start 用）。**不要用 25 端口**：阿里云等云厂商默认封出方向 25，所以不建议自建 Postfix 直接投递。

推荐（都有免费额度，验证码这种量够用；以各家官网当前条款为准）：

| 选项 | 免费额度（大致） | SMTP | 要做的 |
|---|---|---|---|
| **Brevo**（原 Sendinblue） | 每天 300 封 | `smtp-relay.brevo.com:587`，用户名 = 控制台里的 SMTP 登录名，密码 = SMTP key | 注册 → 验证发件域名（DNS 加它给的 TXT / DKIM） |
| **Resend** | 每月 3000 封、每天 100 封 | `smtp.resend.com:465`（SSL）或 587，用户名 `resend`，密码 = API key | 注册 → 添加域名（DNS 加它给的 DKIM TXT、`send` 子域的 MX + SPF TXT） |
| 邮箱 SMTP（如 Gmail） | 个人邮箱的发送上限 | `smtp.gmail.com:587`，密码 = **应用专用密码**（要先开两步验证） | 不用 DNS；发件人只能是这个邮箱，容易进垃圾箱，适合人很少的时候 |

**DNS 记录**（以发件域 `example.com` 为例，具体值照服务商控制台给的填）：

- SPF：`TXT  @（或服务商要求的子域）  "v=spf1 include:<服务商给的> ~all"`（已有 SPF 就把 include 合进去，一个域只能有一条 SPF）
- DKIM：服务商给的 `TXT` 或 `CNAME`（如 `resend._domainkey`、`brevo._domainkey`）
- 服务商的域名验证记录（Brevo 的 `brevo-code` TXT；Resend 的 `send` 子域 MX）
- DMARC（建议）：`TXT  _dmarc  "v=DMARC1; p=none; rua=mailto:dmarc@example.com"`

**服务器要加的**（Ali 上 body-engine 容器的环境变量 + docker secret）：

| 名字 | 值 |
|---|---|
| `secrets/smtp_password`（文件，600） | SMTP 密码 / API key，挂成 docker secret |
| `BODY_MAIL_MODE` | `smtp` |
| `BODY_MAIL_FROM` | `Watch2Whoop <no-reply@你的发件域>`（必须是服务商验证过的发件人） |
| `BODY_SMTP_HOST` / `BODY_SMTP_PORT` / `BODY_SMTP_SECURITY` | 如 `smtp-relay.brevo.com` / `587` / `starttls`，或 `smtp.resend.com` / `465` / `ssl` |
| `BODY_SMTP_USER` | SMTP 登录名 |
| `BODY_SMTP_PASSWORD_FILE` | `/run/secrets/smtp_password` |

配好后 `body-engine mail-test --to 你的邮箱` 试一封。没配（`BODY_MAIL_MODE` 不设）时邮箱登录返回 503，今天页不显示邮箱登录。

## 通过 Apple 登录

- **原生 iOS（已实现）**：app 拿到 Apple 的 identity token 发给服务器；服务器用 Apple 公开的 JWKS（https://appleid.apple.com/auth/keys，缓存 1 小时，遇到不认识的 kid 再取）验 RS256 签名、`iss`、`aud`（= `BODY_APPLE_AUDIENCES`，app 的 Bundle ID，多个用逗号）、过期时间和 nonce（app 发原始 nonce，token 里是它的 sha256）。**不需要任何 Apple 私钥或 secret**。
- 编译 app 的人要在 Apple Developer 账号里给这个 App ID 打开「Sign In with Apple」能力（步骤在 `apps/watch2whoop/README.md`），需要新的 TestFlight 版本。
- **网页版通过 Apple 登录（以后）**：要在 Apple Developer 里建一个 Services ID、配回调域名、用 Sign in with Apple 的私钥签 client secret，走 OAuth 回调；现在今天页只有邮箱登录。
- 审核：app 里能注册就必须能删除账号（5.1.1(v)，已做）；提供第三方登录时要同时提供「通过 Apple 登录」（4.8，已做）。

## 部署步骤（Ali）

1. 合并后 `deploy/ali/push.sh code`，重建 body-engine（迁移 230 自动跑）。
2. 加上面「发邮件」的环境变量和 `smtp_password` secret；`BODY_APPLE_AUDIENCES=<Bundle ID>`；`BODY_SPARKY_ADMIN_KEY_FILE`（挂部署者的 SparkyFitness 读写 key）；确认 `BODY_OW_INVITE_SIGNING_FILE`、`BODY_OW_PUBLIC_URL`、`BODY_AI_MASTER_KEY_FILE` 已配（激活链接已经在用）。
3. SparkyFitness 打开注册（`SF_DISABLE_SIGNUP=false`，端口保持只对本机 / tailnet）。
4. 每小时的任务里加 `body-engine provision-run`。
5. 公网 nginx 放行 `/v1/auth/` 和 `/v1/account`（如果它不是整段转发 `/v1/`）。
6. `body-engine mail-test`，再用一个新邮箱走一遍：登录 → 激活码 → 开通 → 删除账号。

## 要注意的

- **防滥用**：注册和兑换都走登录失败限流；`pending` 账号 7 天不开通自动删除（`provision-run`）；以后可以加总人数上限。
- **成本**：每个新开通的人都会用到托底 AI 额度和服务器内存；发码前心里有数。
- **隐私**：同意书之前不处理健康数据（和现在一样）；邮箱只用来登录，不传给 SparkyFitness / OW。
- 公开发布时可以把激活码这一步改成可选（开关），还没做。

## 待定

- 开通后是否自动送托底 AI，还是要本人在「AI 设置」里手动开（现在：和以前一样，没配自己 AI 的人第一次用前单独同意）。
- 管理页：在今天页「我」里给部署者加「激活码」管理（现在用命令行）。
- 已有账号在「我」里绑定 Apple ID / 邮箱（现在：管理员开的账号还用激活链接登录）。
