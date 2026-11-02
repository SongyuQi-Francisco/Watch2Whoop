# iOS 原生 1.0.9：发布前全量回归（#340 N9）

> 2026-10-29，iPhone 17 模拟器（iOS 26.2），Debug 构建，签名 `FAKETEAMID`（Keychain 和 Flutter 版的模拟器构建一致）。
> 服务器：本地 Body Engine（当前分支）+ 一次性 Postgres 17 容器（库 `body_test_n9`），用完删掉；没有碰任何真实的库。
> 数据：全部是合成的。手机上的「健康」数据由 Debug 版的 `DebugHealthSeeder` 写入（21 天，`W2WTestData=1`），朋友的数据由 `quickstart/demo_data.py` 生成（40 天）。用户都是虚构的（`tester-*@example.com`）。
> 只能在真机上验证的项目单独列在 [`ios-native-1.0.9-device-checklist.md`](ios-native-1.0.9-device-checklist.md)。

## 1. 结果

| 统计 | 数量 |
|---|---|
| 通过 | 17 步 |
| 修复后通过 | 2 步（第 7、14 步；共修了 5 个 bug，见第 3 节） |
| 部分（新账号没有模板 / 计划，剩下的交给真机） | 1 步（第 9d 步） |
| 只能真机 | 41 项，分 8 类（真机清单 A–H） |
| 观察到、没改（第 4 节） | 6 项 |

单元测试：`Watch2WhoopTests` 132 个全部通过（新增 3 个）；Release 构建（模拟器、真机 `generic/platform=iOS`，不签名）通过；服务器 `tests/test_native_sync.py` 17 个通过。

## 2. 每一步

开关：`apple_sync_engine`、`apple_sync_new_accounts`、`native_sync`、`friends`、`circle_feed`、`circles_by_circle`、`water`、`muscle_load`、`training_day_v2`、`sleep_detail`、`data_status`、`sync_center`、`notifications`、`jarvis_*`、`own_food_*`、`records_v2` 都是 `all`（上线时的状态）。新账号用 `user-add` + `provision-run` 开户，所以一开就是 `app` 同步模式、没有 OW 账号。

| # | 步骤 | 结果 | 说明 |
|---|---|---|---|
| 1 | 全新安装 → 设置链接登录 | 通过 | `W2W_DEV_SETUP=watch2whoop://setup?today=…&login=<登录码>`，和相机扫码走同一条路：`POST /v1/web/login-code` → `/v1/web/me` → 打开本地库。相机扫码本身只能真机 |
| 2 | 同意书 | 通过 | 三个新账号都弹出、勾选后「同意并继续」写进 `users.consent_version`。另见第 4 节 O1 |
| 3 | 资料引导 | 通过 | 「保存」写进身高（180），「以后再说」只问一次。另见 O2 |
| 4 | 健康权限 | 通过 | 一个系统弹窗问全部类型 |
| 5 | 首次同步 14 天 | 通过 | 引擎上传 10-15 → 10-29 共 2,778 条（心率 2,014 条，正好 14 天的量），服务器 `hk-derive-run` 派生出 15 天日汇总、28 段睡眠、7 次训练 |
| 6 | 今天三环 | 通过 | 睡眠分 / 恢复（基线不够 14 晚，显示「还在建立你的基线」）/ 昨天负荷并排；核心指标、健康监测、负荷目标都有数 |
| 7 | 本机算分并上传，服务器停算 | **修复后通过** | 修之前每个新用户的抽查都失败（B1、B3、B4）。修之后：手机从评分起点全量算 14 天，上传，抽查当天全部字段一致，`app-scores status` = `active`、`trusted`、计划 `{"mode": "app", "days": []}`，`sync-refresh-run` 不再给他算分；另外用服务器的 Python 逐天重算 13 天对比，全部一致 |
| 8 | 趋势 | 通过 | 1 月范围、HRV / 静息心率图、比上期 |
| 9a | 记体重 | 通过 | 弹层记 72.5 kg（早上空腹），`POST /weight` 200，列表马上出现 |
| 9b | 喝水 | 通过 | +250 ml，`POST /water` 200；合计里包含「健康」里的 330 ml |
| 9c | 日记 | 通过 | 点「喝了咖啡」，`PUT /journal/<日期>/caffeine` 200 |
| 9d | 饮食 / 训练 | 部分 | 新账号没有快捷记餐模板、没有训练计划：只验了「今天吃了」「喝水」「食谱」和肌肉负荷图（来自手表训练）、训练日历。记餐 / 逐组记录由 N5 的单元测试覆盖，真机清单里让内测用户各记一次 |
| 10 | Jarvis 对话（假 SSE） | 通过 | `scripts/fake_jarvis_server.py`：流式文字、Markdown、HRV 指标卡、训练卡、快捷按钮、额度 |
| 11 | 圈子：加入、点赞、评论 | 通过 | 邀请链接 `watch2whoop://circle?code=…` 打开加入页，`POST /friends/join` 200；给朋友的训练帖点赞 `PUT …/like` 200，评论 `POST …/comments` 201（带 `client_id`） |
| 12 | 我 / 设置 | 通过 | 我、数据状态（12 类正常、12 类没有）、同步中心入口、语言、版本 1.0.9 |
| 13 | 退出登录 | 通过 | `DELETE /push-devices`、`POST /account/logout`，本地库目录删掉，回到登录页 |
| 14 | 已登录时打开别人的设置链接 | **修复后通过** | B5：正显示同意书 / 资料 / 健康权限这一步时，确认框弹不出来，链接被吞。修之后先问「用这个设置信息重新登录？」，「取消」后那一步回来 |
| 15 | 删掉重装 → 恢复 | 通过 | 重装、设置链接登录、不给健康权限：全量恢复拉回 14 天分数（origin app）、132 条日汇总、14 晚睡眠、2,350 条原始样本；今天页三环和指标和重装前一样 |
| 16 | 从 Flutter 1.0.8 覆盖升级 | 通过 | 装 `app-ios-v1.0.8` 的 Flutter 模拟器构建，邮箱验证码登录（账号 token 进 Keychain），再覆盖装原生版：不用重新登录，直接进今天页，引擎接着上传（12 批）。升级后资料引导会再问一次（O5）。1.0.8 本身在这个账号上显示「服务器出错（409）」，见 O4 |
| 17 | Release 构建 | 通过 | `-configuration Release`，模拟器和 `generic/platform=iOS` 都能编译（Debug 才有的开发入口都在 `#if DEBUG` 里） |

N8 已经做过、这次没有重复的：Apple 登录、邮箱登录（第 16 步在 1.0.8 里用过）、激活码、同步中心的补全历史和重新同步、Jarvis 的语音 / 拍照（模拟器没有麦克风和相机，见真机清单）。

## 3. 修了的 bug

**B1 本机算分读了服务器没有的「健康」历史**（`HealthKitScoringReader`、`HealthSyncService.uploadedSince`）
- 现象：新用户第一次上传分数，抽查就不一致（CTL 625 vs 491、ATL 486 vs 475、恢复 67 vs 空），服务器接手算分，「服务器停算」永远做不到。
- 原因：首次同步只传最近 14 天（引擎的 `daysBack`），手机算分却从「健康」读最近 60 天。手机多了 7 天心率和 HRV，CTL 的起点、HRV 基线都和服务器不一样。真实用户的「健康」里通常有几个月甚至几年的历史，所以每个新用户都会这样。
- 修法：算分只读服务器已经有的那一段：首次同步窗口的起点，或者补全历史已经补到的位置（`[cursor, to)` 都在服务器上）。补得更早以后自动重读（重置锚点）。更早的天照旧靠服务器存的分数行做种子。

**B2 首次同步和补全历史之间有一段空档**（`SyncSettings.pinFirstWindow`）
- 原因：引擎第一次读的是「现在 − 14 天」（精确到秒），补全历史的终点 `backfillTo` 却是「那天 0 点 − 14 天」，并且是第一次点补全历史时才定下来。当天就补历史的话，中间那几个小时两边都不传。
- 修法：账号连上时就把首次同步窗口定下来，比引擎的起点晚一小时（两边重叠一点，服务器按 UUID 去重），不再有空档。1.0.8 已经开始补的人沿用原来的值。

**B3 服务器还没有评分起点时就上传，抽查形同虚设**（`ScoringSync`）
- 现象：新用户第一轮同步刚传完、服务器还没派生出日汇总时，手机就上传了一天（全是空值）。服务器那时没有评分起点，抽查直接算「一致」，这个用户立刻变成「由 app 算」，而且这一行还会当下一轮的种子。
- 修法：服务器还没有起点（`/v1/sync/scoring` 的 `scoring_origin` 为空）时只在手机上算、给页面显示，不上传；有了起点以后，先删掉手机上起点之前的行，从起点重算。

**B4 新用户头几周，手机、服务器和抽查三方的 CTL / ATL 起点不一样**（`ScoringSync`、服务器 `nativesync/appscores.py`）
- 原因：CTL / ATL 的起点是「前 14 个有 TRIMP 的天的平均值」。新用户不满 14 天时，这个平均值取决于算到哪一天：手机算到今天，服务器的每小时任务也算到今天，抽查却只算到被抽查的那天；手机后面几轮又用前一轮的行做种子，把当时（天数更少时）的起点一路带下去。
- 修法：
  - 手机：只要从评分起点到今天的全部原始数据都在手机的 60 天窗口里（新用户的头两个月），每一轮都从起点全量算，不用种子，和服务器每小时任务的算法一模一样。超出窗口后起点早已固定（14 天以后），再改用种子。
  - 服务器：抽查也算到今天（`server_rows(…, end=today)`），和每小时任务、手机一致。
- 验证：同一个用户修之前 13 天里 13 天不一致，修之后 13 天全部一致（`appscores.diff`，容差 `CHECK_TOLERANCE`）。

**B5 已登录时打开设置链接，被引导步骤挡住**（`SignedInFlows`）
- 原因：「用这个设置信息重新登录？」的确认框挂在 tab 页上；正显示同意书 / 资料 / 健康权限这一步（全屏）时，SwiftUI 弹不出来，设置链接就这样丢了，用户什么提示也看不到。
- 修法：设置链接到了以后，那一步先让开，等用户回答；选「取消」后那一步再回来。

另外加了一个**只在模拟器 Debug 版**里有的工具：`SIMCTL_CHILD_W2W_DEV_SEED_HEALTH=21` 往模拟器的「健康」写合成数据（Flutter 版有同样的工具，原生版之前没有，所以首次同步、本机算分这一段在模拟器里一直没法端到端跑）。

## 4. 观察到、这次没改

| # | 现象 | 建议 |
|---|---|---|
| O1 | 同意书遮罩有 1 次没出现（最早两次启动之一，当时 Debug 写数据的「健康」授权请求同时在弹）；之后 10 次启动、3 个新账号都正常，没能复现 | 真机清单第 A3 项观察；正式版没有那个写数据请求 |
| O2 | 资料页的身高 / 目标体重是数字键盘，没有「完成」键，「保存」被键盘挡住，要先往上滑 | 小改动（`scrollDismissesKeyboard` 或键盘工具条），留给 1.0.10 |
| O3 | 「我」页往下滑以后，顶部的头像行滑到状态栏下面，没有背景遮挡 | 视觉小问题，留给 1.0.10 |
| O4 | 1.0.8（Flutter）登录一个 `app` 模式、没有 OW 账号的新账号，会显示「服务器出错（409）」，同步连不上 | 这是契约第 7.1 节已知的缺口，所以 `apple_sync_new_accounts` 只能在 TestFlight 上只剩 1.0.9 之后打开（`ios-native-app.md` 第 12 节） |
| O5 | 从 1.0.8 升级后，资料引导会再问一次（「只问一次」的标记是原生版的键） | 每个账号最多再问一次，可以「以后再说」，不改 |
| O6 | 服务器抽查时，如果这个用户还没有评分起点，`server_rows` 返回空，抽查算「一致」 | 手机这边已经不在没有起点时上传（B3）；服务器侧要不要改成「没比较不算通过」，留给负责人定 |

## 5. 怎么重跑

1. `df -h /` 至少 15G。起一个一次性的 Postgres（不要用 5433 上的库）：`docker run -d --name n9-pg -e POSTGRES_USER=body -e POSTGRES_PASSWORD=… -e POSTGRES_DB=body_test_n9 -p 127.0.0.1:55439:5432 postgres:17.11-alpine`，`BODY_DB_PORT=55439 BODY_DB_NAME=body_test_n9 body-engine migrate`。
2. 按第 2 节打开开关；`user-add --email … ` + `provision-run` 开户；`login-code --user-id N` 发登录码。服务器 `BODY_MAIL_MODE=console` 时邮箱验证码写在日志里。
3. 模拟器构建：`CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual W2W_TEAM_ID=FAKETEAMID`，派生目录用独有的名字。
4. 第一次启动加 `SIMCTL_CHILD_W2W_DEV_SEED_HEALTH=21`，点「全部打开」「允许」；之后用 `SIMCTL_CHILD_W2W_DEV_SETUP=watch2whoop://setup?today=http://127.0.0.1:<端口>/app/&login=<码>` 登录。
5. 每次上传后跑一次 `body-engine hk-derive-run`（生产上是每分钟的定时任务），再看 `body-engine app-scores status --user-id N`。
6. 做完删掉容器、模拟器、派生目录。

这次是用一个临时的 XCUITest 驱动（不进仓库）按脚本点界面、截图、导出界面树；结论都对照了服务器日志和两边的数据库。
