# Watch2Whoop 管理员手册

给**自己部署服务器的人**看：开户、发链接、AI、导入、备份、排错。只用仓库里公开的命令；每条命令的完整参数用 `--help` 看。
用户自己怎么用 app：[tester-handbook.md](tester-handbook.md)。

## 先选：Quick Start 还是正式部署

| | Quick Start | 正式部署 |
|---|---|---|
| 用途 | 本机看效果，合成数据 | 自己和家人的真实数据 |
| 命令 | `./quickstart.sh`（`login / status / logs / down / reset`） | 按 [docs/README.md#self-hosting](README.md#self-hosting) 逐个服务部署 |
| 访问 | `http://localhost:18100` | Tailscale 或你自己的 HTTPS 域名（下文用 `https://body.example.com`） |
| 备份、定时任务 | 没有 | `deploy/ops/`（见「备份和体检」） |

**Quick Start 的数据不要当真数据用**，`reset` 会全部删掉。

## 怎么跑 body-engine 命令

下文写的 `body-engine …` 按你的环境换成：

```bash
cd body_engine && uv run body-engine …                 # 开发机，直连 127.0.0.1:5433
docker compose exec body-engine body-engine …          # 仓库根目录，进正在跑的容器（compose 项目名 body）
```

- 查用户 id：`body-engine user-list`。
- **明文只显示一次**：token、登录码、激活码打印出来后库里只存 sha256，丢了只能重发。

## 开户

```bash
body-engine user-add --name 妈妈                       # 打印 user id；--role 默认 member，--timezone 默认 Australia/Sydney
body-engine user-add --name 妈妈 --timezone Asia/Shanghai --birth-date 1970-05-01 --height-cm 160 --sex F
body-engine journal-init --user-id 3                   # 默认日记项（可选，已有的不动）
```

- 资料（称呼、生日、身高、目标体重）可以留空，**本人第一次登录后在「个人资料」里填**。
- **同意之前不处理数据**：member 在 app 里确认同意书（[family-consent.md](family-consent.md)）之前，`import-ow / compute-scores / import-apple-zip` 等命令会打印 `skip:` 跳过。owner 不受限制。

## 发访问方式

三种，按场景选一种：

| 方式 | 命令 | 有效期 | 适合 |
|---|---|---|---|
| **激活链接**（推荐） | `body-engine activation-code create --user-id 3 --label 妈妈` | 吊销前一直有效（`--days N` 设上限） | 换手机、重装 app 都用同一条 |
| 一次性设置链接 / 二维码 | `python3 deploy/open-wearables/invite.py …` | 用一次就失效 | 当面扫码 |
| 一次性登录码 | `body-engine login-code --user-id 3 --label 妈妈的iPhone` | 默认 72 小时（`--hours`，最多 2160） | 只登录今天页 |

### 激活链接

```bash
body-engine activation-code create --user-id 3 --label 妈妈   # 打印码 + https://body.example.com/app/activate.html#<码>
body-engine activation-code list [--user-id 3]                # 不显示明文；看 active、uses、last_used_at
body-engine activation-code revoke --id 7                     # 停用；已经设置好的手机不受影响
```

- 链接前缀来自容器环境变量 `BODY_PUBLIC_URL`；没设时打印 `<BODY_PUBLIC_URL>`，自己补上。
- 每次打开现发一次性登录码 +（配了签名文件时）OW 同步邀请码。签名文件：`python3 deploy/open-wearables/invite.py --export-signing-file secrets/ow_invite_signing.json`，只读挂进 body-engine 容器，设 `BODY_OW_INVITE_SIGNING_FILE`（见 `compose.yaml` 注释）。**这个文件是 OW 的主密钥。** 没配也能激活，只是 app 会在同步那一步要邀请码。

### 一次性设置链接（invite.py）

```bash
# 新家人：先在 OW 建用户，再发 OW 邀请码 + 今天页登录码，存二维码
python3 deploy/open-wearables/invite.py --create-user 妈妈 --body-user-id 3 --label 妈妈的iPhone \
    --today-url https://body.example.com --ow-url https://sync.example.com --qr /tmp/mom.png
# 已有 OW 用户
python3 deploy/open-wearables/invite.py --ow-user-id <UUID> --body-user-id 3 --today-url … --ow-url … --copy
```

- `--body-cmd` 指定怎么跑 body-engine（默认 `docker exec -i body-body-engine-1 body-engine`；开发机 `"uv run --directory body_engine body-engine"`）。
- `--qr` 需要 `qrencode`；`--copy` 放进 Mac 剪贴板。
- Body Engine 和 OW 的关联：`body-engine ow-link --user-id 3 [--ow-user-id <UUID>]`。

### API token（脚本、快捷指令、旧方式）

```bash
body-engine token-create --user-id 3 --name iphone-shortcut [--scopes read,write]
body-engine token-list --user-id 3
body-engine token-revoke --user-id 3 --name iphone-shortcut   # 或 --all：全部下线（含 AI token、没用过的登录码、激活码）
```

快捷指令上传用**单独的上传 token**：`python3 health_sync/health_server.py token-create --user-id 3 --name iphone-mom`（明文写进 `secrets/health_upload_token_<name>`）、`token-list`、`token-revoke --name …`。

## 家人互看（shares）

没有命令行，直接写表 `shares`（A 授权 B 看哪些内容，可设过期）：

```sql
-- 用户 3 允许用户 1 看睡眠、评分、指标、训练，一年后过期
INSERT INTO shares (owner_id, viewer_id, scopes, expires_at)
VALUES (3, 1, '{sleep,scores,metrics,training}', now() + interval '1 year')
ON CONFLICT (owner_id, viewer_id) DO UPDATE SET scopes = EXCLUDED.scopes, expires_at = EXCLUDED.expires_at;
DELETE FROM shares WHERE owner_id = 3 AND viewer_id = 1;      -- 取消
```

- 进库：`docker compose exec body-db psql -U body -d body`。
- 常用 scope：`scores sleep metrics training weight nutrition journal labs supplements events exercises reminders memory`；心电图要单独的 `ecg`。
- **AI 不在共享范围内**：AI 设置、key、对话只能本人看。
- **先征得被看的人同意。**

## AI

### AI token（远程 MCP）

用户一般在今天页「我 → AI 设置」自己生成。管理员代办：

```bash
body-engine ai-token-create --user-id 3 --client claude-code [--read-only]   # 只能用在 /mcp
body-engine ai-token-list --user-id 3
body-engine ai-token-revoke --user-id 3 --token-id 12                        # 立即生效
body-engine ai-key-init                                                      # 首次部署：生成 secrets/ai_master_key（已有不动）
```

**`secrets/ai_master_key` 丢了，所有人存的 API key 都解不开**，要进备份。

### 托底 AI（Jarvis，DeepSeek）

没配自己 AI 的用户用管理员的 DeepSeek key（`secrets/deepseek_api_key`），每人每月有额度。

```bash
body-engine ai-fallback status                 # 现查 DeepSeek 余额 + 本月每人花费 + 开关；--no-refresh 不查余额，--json 机器读
body-engine ai-fallback quota --user-id 3 --cny 5      # 改某人月额度（0 = 不给用）；--default 恢复默认；--note 备注
body-engine ai-fallback disable / enable       # 全体关 / 开
body-engine ai-fallback try --user-id 3 --message "今天恢复怎么样"   # 真跑一轮（只读，计入该用户额度）
```

- **余额低于停用线全体自动暂停**，`status` 第二行显示余额、停用线、告警线；充值后下次查余额时恢复。
- 第一次用前用户要在 app 里单独同意。

## 导入健康历史

用户在 app「我 → 导入健康历史」自己上传，后台队列处理（`body-engine history-import-run`，定时任务每分钟或每小时跑一次）。管理员代导：

```bash
body-engine import-apple-zip --user-id 3 --file /path/export.zip    # 流式解析，不解压；--no-scores 不重算评分
body-engine import-raw --user-id 3 --file /path/export.zip          # 只导 HRV 逐拍 + 心电图
```

文件要在命令能读到的地方（容器里跑要先 `docker cp` 进去）。

## 重算评分

```bash
body-engine compute-scores --user-id 3                       # 全量（从开始戴表那天算起，约 20–30 秒）
body-engine compute-scores --user-id 3 --start 2026-10-01    # 只写这天以后
body-engine import-ow --user-id 3 [--start 日期 | --full]    # 先从 OW 重拉
body-engine import-shortcut --user-id 3 --rebuild-since 2026-10-01   # 快捷指令数据按新规则重算
body-engine evaluate-rules --user-id 3                       # 规则提醒
```

全部幂等，可以重复跑。

## 备份和体检（deploy/ops）

```bash
deploy/ops/doctor.sh                       # 全部服务一张表 ✓ / ⚠ / ✗；--json
deploy/ops/ops.sh hourly | nightly         # 手动跑一次定时任务
deploy/ops/ops.sh backup [--drill]         # pg_dump 三个库 + secrets/ + 快捷指令原始上传 → restic
deploy/ops/ops.sh restore-drill            # 恢复演练：临时容器里还原、逐表核对行数，不碰线上库（建议每季度）
deploy/ops/ops.sh restore-extract --target ~/restore [--snapshot ID]   # 真恢复第一步：把快照解出来
deploy/ops/ops.sh watchdog --pause 30      # 维护前暂停看门狗；--pause 0 恢复
deploy/ops/install.sh                      # 装定时任务（macOS LaunchAgent）；--uninstall 卸载
```

- 日志 `health_data/ops/<任务>-日期.log`，最近一次体检 `health_data/ops/doctor-latest.txt`。
- **覆盖线上库之前先恢复到新库名验证。** 完整步骤见 [deploy/ops/README.md](../deploy/ops/README.md)。

## 密钥在哪

全部在 `secrets/`（不进 git，每个文件一个密钥，权限 600），清单和生成方法见 [secrets/README.md](../secrets/README.md)。最要紧的：

| 文件 | 丢了会怎样 |
|---|---|
| `restic_password` | 备份打不开。**另存一份到密码管理器** |
| `ai_master_key` | 用户存的 AI key 全部失效 |
| `body_db_password`、`open_wearables.env`、`sparkyfitness.env` | 服务连不上库 |
| `ow_invite_signing.json` | 激活链接不带同步邀请码（泄露 = OW 全部数据泄露） |
| `deepseek_api_key` | 托底 AI 停用 |

## 常见问题

### 激活链接显示「激活码无效或已停用」

- `activation-code list --user-id N`：`active` 是 false = 已吊销或过期 → `create` 一条新的。
- 链接被聊天软件截断（`#` 后面的码不全）→ 让对方复制完整链接。
- 同一网络失败太多次会暂时被限制（提示「失败次数太多」），等一会再试。

### app 登录失败 / 「登录码无效、已用过或已过期」

登录码只能用一次。重新打开激活链接即可；没有激活链接就 `login-code` 或 `invite.py` 再发。要让某台手机下线：`token-list` 找到 `app:<标签>` 或 `activation:<标签>`，`token-revoke --name …`。

### 同步不更新

1. `doctor.sh` 看 OW 和定时任务。
2. 用户是不是还没同意（命令输出 `skip:`）。
3. 手动 `import-ow --user-id N` + `compute-scores --user-id N --start <几天前>`。
4. 手机端：app 保持打开、手机解锁再同步一次（锁屏读不到健康数据）。

### 快捷指令上传失败

- 401：上传 token 不对或被吊销 → `health_server.py token-list`，重发。
- 上传成功但没数据：看 `health_data/import.log`；会员没同意时会跳过。
- 格式问题以 [shortcut-sync-spec.md](shortcut-sync-spec.md) 为准。

### 身体管家（Claude Code Remote Control）掉线 / 登录过期

`python3 deploy/assistant/butler.py status` 看状态：`needs_login` = 登录过期，在跑管家的机器上用管家那个系统用户重新 `claude` 登录；`needs_trust` = 没信任 `assistant/` 目录。会话地址变了要更新 `.env` 的 `BODY_ASSISTANT_URL`。详见 [deploy/assistant/README.md](../deploy/assistant/README.md)。

## 内测码（自助注册上线后）

自助注册的人用 Apple / 邮箱登录后要输一个内测码才能开通。

```bash
body-engine invite-code create --label 群-1020 --uses 20 [--days 30]   # 一个码，最多开 20 个账号；明文只打印这一次
body-engine invite-code batch --count 50 --label 渠道-1020 [--days 30] [--max-uses 1]   # 一批一次性码（CSV）
body-engine invite-code list [--batch N]   # 已用 / 剩余次数；每次使用绑定的身份（Apple / 邮箱，打码显示）和时间
body-engine invite-code revoke --batch N | --code-id N
```

一个人只能用一次：每次兑换都记下这个账号的登录身份（只存哈希和打码提示），删号重注册也不能再用码开新账号。

### 用户邀请码（推荐）

服务器设了 `BODY_REFERRALS_ENABLED=1` 后，每个已开通的用户在「我」→「邀请好友」里有自己的邀请码（复制 / 分享链接 `…/app/?invite=码`，链接打开后注册流程会自动填好）。好友用它注册就能开通，不用管理员发码。默认关闭：不设这个变量，入口和接口都隐藏，已有的用户邀请码也不能用。

- 规则在 `body_engine/src/body_engine/account/params.py`：每人最多邀请 `REFERRAL_MAX_USES`（10）人，每个码 24 小时内最多 `REFERRAL_DAILY_REDEEMS`（3）次；不过期。
- 邀请人被停用或删号，码立即失效；管理员可以停掉某个人的码（停掉后不会自动发新码）。
- 每次通过邀请码开通都记下邀请人，并在 `invite_rewards` 记一条待发奖励（奖励是什么还没定，只记账）。

```bash
body-engine invite-code list --kind referral   # 谁邀请了谁：每个用户的码、已用 / 剩余，每次使用的人、身份（打码）、时间
body-engine invite-code revoke --code-id N     # 停掉某个人的邀请码（N 从上面的列表 --batch 看）
```
