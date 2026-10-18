# iPhone 快捷指令同步规范（唯一规范）

> 2026-10-17 定，2026-10-18 改（快捷指令 1.1：累计型改传原始样本；来源选择规则见第 5 节）。Open Wearables 的 iPhone app 恢复之前，日常健康数据全部靠 iPhone 快捷指令「Export Health Data」上传到服务器（2026-10-18 起是 Ali）。
> 改快捷指令、接收器（`health_sync/health_server.py`）或导入器（`body_engine/src/body_engine/importers/shortcut.py`）都以这里为准，三处一起改。
> 原快捷指令的备份：iPhone 上的「Export Health Data 备份 2026-10-17」（rows-v0 格式、局域网地址、心率按小时分组）。
> 开源版本（导入问题填地址和 token、不含私人信息）：`shortcuts/Watch2Whoop Health Sync.shortcut`，导入、权限、自动化、改版流程见 `shortcuts/README.md`。

## 1. 上传
| 项 | 值 |
|---|---|
| 地址（主，2026-10-18 起） | `POST https://body.example.com/upload?name=health`（Ali 公网入口，nginx 限流 + fail2ban，手机不用开 Tailscale） |
| 地址（旧，过渡期） | `POST https://your-server.your-tailnet.ts.net:8446/upload?name=health`（已停用：2026-10-18 iPhone 改成新地址，upload-bridge 已卸） |
| 地址（兼容） | `POST http://your-mac.local:8765/upload?name=health`（局域网，旧快捷指令用） |
| 请求头 | `X-Upload-Token: <token>`（或 `Authorization: Bearer <token>`）。token 按人发：`python3 health_sync/health_server.py token-create --user-id N --name iphone-xxx`，明文在 `secrets/health_upload_token_<name>`，库里只存 sha256（`secrets/health_upload_tokens.json`） |
| 请求体 | 「获取 URL 内容」→ 方法 POST、请求体「文件」= 合并后的文本 |
| 没带 token | 过渡期仍收（算 user 1），`health_data/server.log` 记「没带 token」；`HEALTH_SYNC_REQUIRE_TOKEN=1` 以后拒收（401） |
| token 不对 | 401，不存 |
| 存档 | user 1：`health_data/archive/health_YYYY-MM-DD_HHMMSS.txt`（和 `health_latest.txt`）；其他用户：`archive/u<id>/`。存完回 `saved health N bytes`，后台跑 `body-engine import-shortcut` |

接收器监听 `0.0.0.0:8765`（局域网）和 `127.0.0.1:8767`（`tailscale serve --bg --https=8446 http://127.0.0.1:8767`）。本机 `127.0.0.1:8765` 被别的项目占着，测试用局域网 IP 或 8767。

## 2. 数据清单
「查找健康样本」每类一个动作，结果「添加到变量」`HS`。**范围故意重叠**（2 天 / 7 天）：某次没跑成，下一次会补上；导入是幂等的，重复的样本只存一条。

| 类型（快捷指令里的名字） | 范围 | 分组 | 单位 | 进哪里 | 用途 |
|---|---|---|---|---|---|
| Sleep | 7 天 | 无 | — | sleep_sessions（按间隔 > 60 分钟切段，归到醒来 − 4h 那天） | 睡眠分、睡眠阶段 |
| Heart Rate | 2 天 | **无**（2026-10-17 改：原来按小时，是总和不能用） | count/min | daily_metrics.heart_rate（avg/min/max）；评分的原始心率 | 负荷（TRIMP / strain）、夜间静息心率、训练推断 |
| Heart Rate Variability | 7 天 | 无 | ms | daily_metrics；评分 | 恢复分（SDNN；iPhone 快捷指令没有 RMSSD 类型） |
| Resting Heart Rate | 7 天 | 无 | count/min | daily_metrics | 恢复分的退路 |
| Respiratory Rate | 7 天 | 无 | count/min | daily_metrics；评分 | 恢复分 |
| Oxygen Saturation | 7 天 | 无 | % | daily_metrics；评分 | 异常标记 |
| Wrist Temperature（Sleeping Wrist Temperature） | 7 天 | 无 | degC | daily_metrics；评分 | 异常标记 |
| Walking Heart Rate Average | 7 天 | 无 | count/min | daily_metrics | 趋势 |
| VO2 Max（Cardio Fitness） | 7 天 | 无 | mL/min·kg | daily_metrics | 趋势 |
| Weight（Body Mass） | 7 天 | 无 | kg | daily_metrics.body_mass | 体重（SparkyFitness 是体重的主来源） |
| Steps | 2 天 | **无**（2026-10-18 改：原来按小时，iPhone 和手表直接相加、步数约翻倍） | count | daily_metrics.step_count（同一天取最大的单一设备合计，第 5 节） | 活动 |
| Active Calories（Active Energy） | 2 天 | **无**（2026-10-18 改，同上） | kJ（导入转 kcal） | daily_metrics.active_energy_burned | **每日卡路里消耗** = 活动 + 静息 |
| Resting Calories（Resting Energy） | 2 天 | **无**（2026-10-18 改，同上） | kJ | daily_metrics.basal_energy_burned | 同上 |
| Exercise Time | 2 天 | **无**（2026-10-17 改：原来按小时） | min | daily_metrics.apple_exercise_time；训练推断 | 锻炼分钟、训练时段 |

还没加、要你先在 iPhone 上授权的（第一次读会弹健康权限，**只能你自己点「允许」**；授权后照第 8 节加）：
- **Stand Minutes**（站立分钟）：导入器已经认（`apple_stand_time`，按小时分组、累计型）。
- **Body Fat Percentage / Lean Body Mass**（体脂 / 去脂体重）：导入器已经认（`body_fat_percentage` 0–1 自动转 %、`lean_body_mass`）。体重秤或手动记了才有数据。

读不到的：
- **体能训练（Workouts）**：快捷指令的「查找健康样本」没有体能训练这个类型（只有「开始 / 结束 / 记录体能训练」，没有查询），所以拿不到手表记录的单次训练（类型、时长、消耗、平均 / 最高心率）。改为服务端用锻炼分钟 + 原始心率**推断**（第 4 节）。OW 的 iPhone app 恢复后以 OW 的训练记录为准。
- **HRV RMSSD**：快捷指令类型列表里只有 Heart Rate Variability（SDNN）。
- Apple 的 Readiness / Sleep Score：HealthKit 没有这两个类型。

## 3. 文本格式 cols-v1（按列）
快捷指令里不用「重复」逐条拼（原始心率每次 1,500 条以上，逐条拼要十几分钟、打开微信时的自动化根本跑不完），而是一个「文本」动作直接取 `HS` 的属性列表（一次几秒）：

```
#format=cols-v1
@@type
<HS 的「类型」>
@@start
<HS 的「开始日期」，日期格式 ISO 8601、打开「ISO 8601 时间」>
@@end
<HS 的「结束日期」，同上>
@@value
<HS（值）>
@@unit
<HS 的「单位」>
@@source
<HS 的「来源」>
```
每个变量展开成「一行一个值」。上传样例：
```
#format=cols-v1
@@type
Sleep
…
Heart Rate
@@start
2026-10-10T02:54:27+11:00
…
@@value
Core
…
67
@@unit
count/min
…
@@source
<手表的名字>
…
```
- 第 i 行的类型 / 开始 / 结束 / 值是同一个样本；这四段行数必须相等，不等则整个文件不用（`shortcut_uploads.notes.cols_mismatch`）。
- **快捷指令会跳过空值**：睡眠没有单位，睡眠和按小时分组的样本没有来源，所以 `@@unit`、`@@source` 两段会比 `@@type` 短（1.1 起只有睡眠没来源）。导入器按「整类缺」补齐：先试「睡眠没单位」「睡眠 + 按小时分组的类型没来源」（1.0 的布局），再试「只有睡眠没来源」等组合，最后找唯一的整类组合；对不上就整段留空并记 `unit_misaligned` / `source_misaligned`。累计型没来源、但同一个钟点里有两条以上的，是来源段丢了的原始样本，不当按小时分组（记 `sum_source_missing`，各设备只能直接相加）。
- 段尾空行忽略。值是数字就进 `value`，否则（睡眠阶段 Core / Deep / REM / Awake / Asleep / In Bed）进 `value_text`。
- 日期必须带时区；不带时区或结束早于开始的行丢掉。

### 旧格式 rows-v0（兼容，2026-10-17 以前）
每行 `Type|Start|End|Value|Unit|Source`，用「重复」逐条拼。导入器继续认（备份快捷指令就是这个格式）。旧格式的心率是按小时求和的：一个文件里 ≥ 95% 的心率行开始 = 结束、且都在 25–230 才当原始读数，否则整份文件的心率不用（`hr_hourly_sum`）。

### 样本属性（agg）
- `raw`：原始样本。不分组的类型都是；心率有来源（或形状符合上面的规则）才算。
- `hour`：快捷指令按小时分组后的合计（没有来源）。累计型（步数、活动 / 静息能量、站立分钟，快捷指令 1.0 和更早）是各来源直接相加（手表、手机同时记时偏高，步数约翻倍），日值带 `flags.hourly_sum`，只在没有干净来源时才用（第 5 节）。按小时分组的心率不用。

## 4. 训练行（Workout）
训练模块（`training/shortcut_workouts.py`，训练历史会话）读这个格式，一行一次训练：
```
Workout|开始|结束|类型|时长秒|活动能量kcal|平均心率|最高心率|来源
```
- 开始 / 结束 ISO 8601 带时区；类型用 HealthKit 的训练类型名（英文或中文），认不出的原样保留；时长秒、能量、心率可以空。
- 快捷指令目前**写不出**这一行（见第 2 节「读不到的」）。服务端 `importers/shortcut.infer_workouts` 推断后用同一格式交给训练模块：
  - 不分组的锻炼分钟样本连成段（间隔 ≤ 5 分钟），累计 ≥ 10 分钟；段内原始心率 ≥ 5 条、平均 ≥ 95 → 一次训练。
  - 类型 `Other`，来源 `快捷指令推断（锻炼分钟 + 心率）`；活动能量按和训练时段重叠的比例从活动能量样本里摊（原始样本取训练时段里合计最大的单一设备；按小时分组的只能整段摊）。
  - 训练模块按 `开始（UTC）|类型` 去重；同一次训练 OW 有记录时 OW 优先。
- 将来能在快捷指令里直接拿到训练（例如 iOS 新增类型）时，直接在合并文本里加这种行（rows-v0 写法，一行一条），导入器原样交给训练模块。

## 5. 导入（Mac）
`body-engine import-shortcut --user-id N [--archive-dir …] [--all]`：
1. 只读还没导入过的上传文件（表 `shortcut_uploads` 记录每个文件：格式、字节、样本数、各类条数、notes）；`--all` 全部重导。
2. 样本写 `shortcut_samples`，主键 (user_id, type, start_at, source)：同一个样本在多次上传里只存一条，以较新的上传为准；按小时分组的样本在这一小时里会「长大」（开始时间也可能变），同一小时的旧样本删掉。
3. 用表里覆盖日期范围内的样本重算这几天的日值（source = shortcut，写 `daily_metric_sources`，再按下面的规则选出 `daily_metrics`）、`sleep_sessions`（优先级最低，不覆盖 OW / export.xml）、推断训练 → `workouts`。
   - **按用户时区切天**（`users.timezone`，Australia/Sydney 夏令时 +11、冬令时 +10），样本按开始时间归天。
   - **累计型**（步数、活动 / 静息能量、锻炼 / 站立分钟）：某天有原始样本时，按来源（设备）分别求和，**取最大的那个设备**，和 export.xml 的规则（`apple_zip` / `parse_export.py`）一致。为什么：iPhone 和手表会同时记同一段走路，直接相加步数约翻倍（2026-10-18 快捷指令按小时合计 10,327 步，「健康」显示约 5,000）；取单一设备永远不会重复算，只会漏掉另一台设备单独记到的部分（例如手表充电时手机记的步数，通常几个百分点）。
   - 按小时分组的旧样本（快捷指令 1.0，没有来源）：某类从第一条原始样本所在的整点起，之后的按小时样本一律不用（新快捷指令接手了）；之前的照算，这部分和原始部分相加，日值带 `flags.hourly_sum`（偏高）。只有按小时样本的旧日子整天都是 `hourly_sum`。
   - kJ 转 kcal（÷ 4.184）。
4. **来源选择**（`importers/common.py resolve_daily`，各来源的日值都在 `daily_metric_sources`）：
   - 累计型：在「完整、去重」的来源里取**最大**（各来源都已经按设备去重，完整的来源之间只差几个百分点，明显偏小的是漏了一段）。下面两种是「降级」的，只在没有干净来源时才用（也取最大），选中时把原因写进 `daily_metrics.flags`：
     - `hourly_sum`：快捷指令按小时合计（偏高）。
     - `incomplete`：这个来源这天的**能量**只同步了一部分（2026-10-12～16 OW iOS SDK 0.14 的问题）：基础代谢不到这个人前 60 天中位数的一半（要有 ≥ 7 天），或全天活动能量比当天训练记录的活动能量少 20 kcal 以上（= 训练日历的 `daily_incomplete`）。只作用于活动 + 基础能量；只判断今天以前的日子（今天对所有来源都不全）。步数等没有可靠的参照，去重后的来源一律当完整。
   - 非累计型（心率、HRV、体重……）和睡眠：优先级不变，ow > apple_export > shortcut。
   - 改了规则后重算：`body-engine import-shortcut --user-id N --rebuild-since YYYY-MM-DD`（从表里重算快捷指令的日值 / 睡眠 / 推断训练，再重新选出这段时间每天的全部指标；幂等）。
5. 评分（`compute-scores`）从 `shortcut_samples` 读原始心率 / HRV / 呼吸 / 血氧 / 手腕温度和睡眠阶段（和 OW 的样本按开始时间去重，OW 优先）。

## 6. 自动化（iPhone「快捷指令」→「自动化」）
| 触发 | 设置 |
|---|---|
| 打开 App：微信 | 立即运行，不通知（原有） |
| 起床（睡眠 → 起床时） | 立即运行 |
| 每天 12:00 | 立即运行 |
| 每天 18:00 | 立即运行 |
| 每天 22:00 | 立即运行 |

**限制：iPhone 锁屏时 HealthKit 数据是加密的，读不到。** 定时触发正好在锁屏时，快捷指令会读到空或报错。缓解：
- 范围重叠（2 天 / 7 天）：漏掉的那次由下一次补上，数据不会丢，只是晚到。
- 打开微信的自动化最可靠（手机一定是解锁的），一天会跑很多次。
- 起床自动化在关闭闹钟 / 睡眠模式结束时触发，那时手机通常刚解锁。
- doctor 每小时检查「每类数据最近一条」，超过 26 小时提示（`deploy/ops/doctor.sh`）。

## 7. 数据量
- 2026-10-17 实测（cols-v1，最近 2 天心率不分组）：2,408 条样本、232 KB，快捷指令运行约 5 秒（含上传）；原始心率 1,503 条。
- 训练多的日子心率约 1,500–2,100 条 / 天 → 单次上传最多约 4,500 条、450 KB；接收器上限 50 MB。
- 2026-10-18 起步数、活动 / 静息能量也传原始样本（按 export.xml 10-01～10-13 实测，手表 + iPhone 每天：步数 25–100 条、活动能量 10–800 条、静息能量 30–800 条，训练日最多）：2 天范围多约 300–3,400 条，平常约 1,400 条（+140 KB），最多约 +330 KB → 单次上传一般 3,800 条 / 370 KB，最多约 8,000 条 / 800 KB，快捷指令仍是几秒。
- 存档每次一份，按每天 15 次算约 3 MB / 天、1 GB / 年（`health_data/` 不进 git；`archive/` 在每晚备份里）。以后需要可以把旧存档 gzip（导入器认 `health_*.txt.gz`）。

## 8. 改快捷指令的步骤（在 iPhone 上）
新增一类：在任意一个「查找健康样本」的图标上点 → 拷贝 → 在某个「添加到变量 HS」的图标上点 → 在下方粘贴 → 粘贴后它会变成「过滤 HS」，点 `HS` → 清除变量，变回「查找健康样本」→ 改类型、范围、分组 → 后面再加一个「添加到变量 HS」。合并文本不用改。

去掉按小时分组（1.0 → 1.1，2026-10-18；Steps、Active Calories、Resting Calories 三个动作各做一遍）：打开快捷指令 → 找到类型是「步数」的「查找健康样本」→ 点动作右下角的展开箭头（「显示更多」）→「分组方式」（Group By）从「小时」改成「无」（改完后「填充缺失」那一行会消失）→ 范围保持「过去 2 天」。「活动能量」「静息能量」同样操作。改完手动运行一次：服务器上 `shortcut_uploads` 里这次上传的 notes 应该是空的，`shortcut_samples` 里这三类出现 `agg = raw`、带手表 / iPhone 名字的样本。也可以直接导入 `shortcuts/Watch2Whoop Health Sync.shortcut`（1.1）替换，但要重新填两个导入问题、重设自动化。
