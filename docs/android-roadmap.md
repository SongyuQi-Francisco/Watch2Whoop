# 安卓端 roadmap：手机、手表、数据来源与分期

> 对应 issue #253（华为）和安卓第一版 PR #261 之后的下一步。**只做调研和排期，不含代码改动。**
> 调研日期 2026-10-25。资料只用厂商官方文档、监管机构原文、开源项目官方文档；聚合服务和媒体对厂商的描述单独标「二手」，只当线索。每个结论后面附来源链接，核实不了的标「待查」，不猜。
> 工作量单位「人日」是估算，不是承诺。本文不是法律意见，第 3 节的法规部分请负责人找法务对原文再核一遍。
> 项目约定：公开仓库里不写个人信息、域名、健康数值。

## 0. 摘要

**现状**：安卓 APK（PR #261，Flutter，`minSdk 29` = Android 10）只有登录、今天页和 AI 对话，**没有任何手表数据同步**。恢复分必须有 HRV，睡眠分必须有睡眠分期，所以「安卓能用」的真正门槛是手表数据。

**核心发现（按重要程度）**

1. **海外 / 有 Google 服务的手机，路线是通的，而且不需要资质**：Health Connect（HC）有评分需要的全部记录类型（HRV 只有 RMSSD 一种口径），现有 OW Flutter SDK 已经支持 HC 和 Samsung Health（`docs/apps.md`）。Pixel Watch / Fitbit、Garmin、Polar、Oura、Whoop 官方能给 **HRV + 睡眠分期**；Samsung Galaxy Watch 经 HC 能给睡眠分期，**官方列表里没有 HRV**，只能做到「没有恢复分」的降级（第 2 节）。
2. **大陆是难点，因为大陆主流品牌（华为、荣耀、小米、OPPO、vivo、Amazfit）都没有查到「官方开放、免资质」的 HRV 路线**；而 HC 在没有 Google 服务的大陆 ROM 上是否存在，官方文档**没写**（HC 官方前置条件是装有 Google Play services），要真机实测（第 1 节）。
3. **方案 D（聚合服务）对「华为高阶数据」目前没有证据能解决问题**：Terra 明文要求客户**自己**向华为申请凭据（等于资质问题原样回来）；Sahha 对华为只走苹果健康、睡眠只有总时长；Spike 矩阵里华为有睡眠分期但没有 HRV、连接方式没写；Thryve 页面没写 HRV / 分期、也没说是否自有华为资质。**在这些服务给出书面答复之前，不应把 D 当作确定可行的路线**（第 3 节）。
4. **大陆「免资质」的现实备选是 Gadgetbridge 导出文件**：它直连手表（华为 / 荣耀 / 小米 / Amazfit / Garmin 等），官方 wiki 写明华为 HarmonyOS 5+ 的手表能同步睡眠分期，数据库里有华为 HRV 表；自动导出 SQLite，我们的 app 只读导出文件（不链接 AGPL 代码）。成熟度和 HRV 口径都要拿真机导出核对（第 2.3 节）。**建议 P1 先做一个只读导出文件的原型验证它**。
5. **出境合规的几个数字和常见说法不同**：标准合同备案期限官方原文是**生效后 10 个工作日**；敏感个人信息**不适用**「累计不满 10 万人」的豁免，累计不满 1 万人敏感个人信息走「标准合同 / 认证」，达到 1 万人要走安全评估（第 3.3 节）。
6. **Fitbit 旧 Web API 官方时间表：2026-09-30 结束支持，2026-11-02 关停**，必须迁到 Google Health API（OAuth 验证 + 每年 CASA 安全评估），OW 路线图里已有 Google Health provider。

**建议分期**

| 期 | 目标 | 手机 | 设备 |
|---|---|---|---|
| **P0** | 海外 / 有 GMS 的安卓手机出完整恢复分 + 睡眠分 | Pixel、三星、其他有 Play 的机型（Android 10+） | Pixel Watch / Fitbit（HC）、Garmin / Polar / Oura / Whoop（OW 云端 provider）；三星手表降级（无恢复分） |
| **P1** | 大陆无资质路线：华为 / 荣耀 / 小米 / Amazfit 用户拿到 HRV + 睡眠分期 | 华为 HarmonyOS 4.x、荣耀、小米 / 红米、OPPO、vivo（侧载 APK） | Gadgetbridge 导出文件（原型 → 正式）、华为隐私中心数据副本（先验证内容）、HC（真机实测后）、BLE 心率带 |
| **P2** | 按验证结果决定：方案 D、有资质的合作主体、鸿蒙 NEXT 壳、Wear OS、Samsung 官方 SDK | 按需 | 按需 |

详见第 4 节；负责人要办的事见第 5 节。

---

## 1. 手机：品牌、系统与 Health Connect

### 1.1 先说 Health Connect 本身（官方）

| 事实 | 来源 |
|---|---|
| HC 要求 Android 9（API 28）及以上，**且设备装有 Google Play services**；页面更新 2026-01-19 | [availability](https://developer.android.com/health-and-fitness/health-connect/availability) |
| Android 14+：HC 是系统框架的一部分；Android 13 及以下：Play 商店里的独立 app（包名 `com.google.android.apps.healthdata`） | [availability](https://developer.android.com/health-and-fitness/health-connect/availability)、[get-started](https://developer.android.com/health-and-fitness/health-connect/get-started)（更新 2026-10-21） |
| 我们的 `minSdk 29`（Android 10）在 HC 支持范围内；Android 10–13 的用户要自己从 Play 装 HC app | 同上 |
| 不支持工作资料（Work Profile）/ 多用户 profile | [Android 帮助](https://support.google.com/android/answer/12201227) |
| **官方页面没有任何一页写明哪些厂商 / 地区的 ROM 带不带 HC**，也没写大陆 ROM 能不能用 | 以上页面均无此说明 → 待查 |
| 历史数据：默认只能读首次授权前 30 天；更早需要 `READ_HEALTH_DATA_HISTORY`（API 35 / Android 14 要装 Extensions 13）；后台读取需要 `READ_HEALTH_DATA_IN_BACKGROUND`（同样 API 35 / Ext 13），用 WorkManager 周期任务。Android 13 及以下 HC app 是否提供这两项：待查 | [read-data](https://developer.android.com/health-and-fitness/health-connect/read-data)（更新 2026-10-09）、[HealthPermissions](https://developer.android.com/reference/android/health/connect/HealthPermissions) |
| 上架 Google Play 要在 Play Console 填 Health apps 声明（逐类型写理由），按包名白名单。**非 Play 分发（侧载、国内市场）的 app 能否获得 HC 生产权限：官方没写，待查（要真机实测）** | [declare-access](https://developer.android.com/health-and-fitness/guides/health-connect/publish/declare-access)（更新 2026-03-10）、[Play 政策](https://support.google.com/googleplay/android-developer/answer/9888170) |

评分需要的记录类型 HC 都有（[data-types](https://developer.android.com/health-and-fitness/health-connect/data-types)，更新 2026-10-21）：

| 评分输入 | HC 记录类型 | 备注 |
|---|---|---|
| HRV | `HeartRateVariabilityRmssdRecord` | **只有 RMSSD**（毫秒，瞬时点），没有 SDNN 类型；权限 API 34 起 |
| 睡眠分期 | `SleepSessionRecord`（`stages`） | API 34 起 |
| 心率明细 / 静息心率 | `HeartRateRecord` / `RestingHeartRateRecord` | `HeartRateRecord` 的平台版本未逐条核，待查 |
| 血氧 / 呼吸 | `OxygenSaturationRecord` / `RespiratoryRateRecord` | API 34 起 |
| 体温 | `SkinTemperatureRecord` | 相对基线的 deltas，要 feature flag，**API 35（Ext 13）**；[说明](https://developer.android.com/health-and-fitness/health-connect/features/skin-temperature) |
| VO₂max / 训练 | `Vo2MaxRecord` / `ExerciseSessionRecord` | VO₂max API 34 起 |

**对评分的含义**：HC 路线的 HRV 口径固定是 RMSSD，服务器要新增口径（如 `rmssd_hc`，和 Apple 的 SDNN、`rmssd_huawei` 一样每个用户整段历史只用一种口径，换表重建基线，参考 `docs/huawei-health-plan.md` 第 5 节）。HC 里「有没有数据」取决于厂商 app 写不写，不取决于 HC 能不能装。

### 1.2 逐品牌（大陆 / 海外）

> 「待查」= 没有一手来源。媒体报道、聚合服务和第三方博客写的内容单独标「二手」。

| 品牌 / 系统 | 市场 | GMS | Health Connect | 侧载 APK | 最低系统 / 备注 |
|---|---|---|---|---|---|
| **华为 HarmonyOS 4.x 及更早** | 大陆（海外 EMUI 另算） | 待查（未找到华为官方原文；华为机型通常无 GMS 属常识，非本次核实） | 待查，未见官方说明；二手（聚合服务 Sahha）称华为运动健康不写 HC（[Sahha](https://sahha.ai/integrations/huawei-health/)） | 能装（华为开发者社区文章：普通 HarmonyOS 到 4.3 借助兼容层可运行 Android 应用；**社区文章，官方原文待查**：[文章](https://developer.huawei.com/consumer/cn/blog/topic/03199548836169090)） | 与 minSdk 29 的对应关系待查 |
| **华为 HarmonyOS NEXT（5.0 起）** | 大陆 | — | — | **不能装 APK**（社区文章 + 媒体报道：[Caixin](https://www.caixinglobal.com/2024-01-19/huaweis-latest-operating-system-completely-breaks-with-android-102158167.html)、[9to5Google](https://9to5google.com/2024/06/24/huawei-harmonyos-next-android-apps-late-2024/)；**华为官方原文待查**）。需单独做 ArkTS 壳，见 `docs/huawei-health-plan.md` 第 7 节 | 近两三年新机基本都已是 NEXT（[支持设备清单](https://developer.huawei.com/consumer/cn/doc/doccenter-release-notes/support-device)） |
| **荣耀 MagicOS** | 海外 / 大陆 | 海外站列出部分机型预装 GMS（[荣耀马来西亚站](https://www.honor.com/my/topic/honor-gms/)，无页面日期）；**大陆版待查** | 待查；二手：Honor 手表 App 只写苹果健康、不写 HC（[Sahha](https://sahha.ai/integrations/honor-health/)） | 待查 | 待查 |
| **小米 / 红米 HyperOS** | 大陆 + 国际 | 国际版带 GMS（常识，未找到小米官方页）；大陆版：二手，媒体评测称依赖 Google 服务的功能不工作，Play 可经 GetApps 装上（[GSMArena](https://m.gsmarena.com/xiaomi_17_pro_max-review-2895p4.php)）；官方说明待查 | 小米国际站手表页宣称参与 HC 开发、Mi Fitness 数据可经 HC 传（[页面](https://www.mi.com/global/product/xiaomi-watch-5/)，抓取受限只见搜索摘要）；**大陆版待查** | 待查 | 待查 |
| **OPPO / 一加 / realme** | 大陆 + 海外 | 待查 | OPPO 海外商店页写「OHealth 支持 Google Health Connect」（[OPPO Watch X3](https://www.oppostore.com.au/products/oppo-watch-x3)，无页面日期）；大陆版与写入的数据类型待查 | 待查 | 待查 |
| **vivo / iQOO** | 大陆 + 海外 | 待查 | 未找到任何官方资料，待查 | 待查 | 待查 |
| **三星 One UI** | 海外为主 | 带 GMS（常识，非本次核实） | **有，双向**：Samsung Health 自 6.22.5（2022-10）起与 HC 同步（[三星博客](https://developer.samsung.com/health/blog/en/accessing-samsung-health-data-through-health-connect)，2025-02-18；[FAQ](https://developer.samsung.com/health/health-connect-faq.html)）；同步范围见 2.2 | 待查 | Samsung Health Data SDK 要求 Android 10+ 和 Samsung Health 6.30.2+（[文档](https://developer.samsung.com/health/data/overview.html)） |
| **Pixel** | 海外 | 有 | Android 14+ 内置；Google Health（原 Fitbit）app 能**写入** HC：HRV、心率、静息心率、皮肤温度、呼吸率、睡眠会话与分期，**不写血氧**（[Fitbit 帮助](https://support.google.com/fitbit/answer/14506680)，无页面日期） | 待查 | Android 14+ |

**结论**

- **P0 手机**：Pixel、三星、其他带 Play 的机型，Android 10+（minSdk 29 已覆盖；Android 10–13 用户需要自己装 HC app，Android 14 以下的历史 / 后台读取能力待查）。
- **大陆手机一律不能假设有 HC**。进入 P1 前必须在每个品牌各一台真机上测三件事：HC 有没有、非 Play 签名的包能不能拿到权限、厂商健康 app 往 HC 写了什么类型。
- 华为 NEXT 机型不在本文的安卓路线内（ArkTS 壳另议，见 `docs/huawei-health-plan.md` 第 7 节）。

---

## 2. 手表和外设

### 2.1 总表：谁能给出评分最低输入（HRV + 睡眠分期）

符号：✓ 官方确认；△ 部分 / 二手 / 口径待核；✗ 官方明确没有；? 待查。

| 品牌 | 主要路线 | HRV（口径） | 睡眠分期 | 心率明细 | 静息心率 | 血氧 | 呼吸 | 体温 | VO₂max | 训练 | 资质 / 费用 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **华为** | Health Kit 云 / 端 | ✓（RMSSD，仅部分手表；高阶） | ✓（高阶） | ✓（高阶） | ✓（高阶） | ✓（高阶） | △（仅睡眠统计） | ✓（高阶） | ✓（高阶） | ✓（基础） | 高阶需企业实缴 ≥ 500 万、成立 ≥ 1 年（见 `huawei-health-plan.md` 第 3 节；本次未能从官方页面重新核实，沿用负责人已有结论）；费用待查 |
| **荣耀** | 无官方开放 API（待查）；二手：只写苹果健康、不写 HC | ? | △（二手：安卓上拿不到分期） | △ | △ | ? | ? | ? | ? | △ | 待查 |
| **小米 / 红米** | 二手：Mi Fitness 写 HC；无官方开放 API；Gadgetbridge 支持 | △（二手：HC 列表里没有） | △（二手：HC 有深 / 浅 / REM） | △ | △ | △ | ? | ? | △ | △ | 官方文档未查到；聚合服务 Junction 把小米列为 planned |
| **Amazfit / Zepp** | Zepp → HC 单向写入（二手）；官方云 API 未见（Terra 与 Sahha 说法冲突） | ? | △（二手：HC 有深 / 浅 / REM） | △ | △ | △ | △ | ? | ? | △ | 待查；老机型（Bip 3、GTR 3、GTS 2）不支持 HC 同步（二手） |
| **OPPO / 一加** | OHealth → HC（海外页写明） | ? | ? | ? | ? | ? | ? | ? | ? | ? | 待查 |
| **vivo** | 未找到 | ? | ? | ? | ? | ? | ? | ? | ? | ? | 待查 |
| **三星 Galaxy Watch** | HC（免审批）/ Health Data SDK（需 partner，**当前不接受申请**）/ Health Sensor SDK（原始 IBI，同样需 partner） | ✗（SDK 与 HC 官方列表均没有；Sensor SDK 可拿 IBI 自算） | ✓（经 HC，2025-02 博客） | ✓ | ? | ✓ | ✗ | ✓（仅 SDK） | ✓（经 HC，随运动） | ✓ | HC 免费；SDK 需合作申请，流程页写 Partner Apps Program 当前不接受申请（[流程](https://developer.samsung.com/health/data/process.html)） |
| **Garmin** | Connect Developer Program（云 push） | △（二手：夜间 RMSSD 日汇总） | △（二手：有） | ✓（概要页列出，粒度待查） | ✓ | ✓ | ✓ | ? | ? | ✓ | Personal（自己的数据）/ Business Starter（申请表）/ Enterprise（定制）；无公开价，审批标准待查（[概览](https://developer.garmin.com/gc-developer-program/overview/)） |
| **Polar** | AccessLink API | ✓（Nightly Recharge，口径待核；二手标 RMSSD） | ✓ | ✓（5 分钟间隔） | ? | ✓ | ✓ | ✓ | ? | ✓ | 任何 Polar Flow 用户可建 API client（[AccessLink](https://www.polar.com/accesslink-api/)）；商用条款与费用页面未见，待查 |
| **Coros** | 无公开开发者 API（官方 GitHub 有 MCP 连接器，面向 AI 助手，不是批量同步管道） | △（二手 / MCP：有） | ✗（二手：只有总时长） | △ | △ | ? | ? | ? | ? | ✓ | 待查（[COROS MCP](https://github.com/coroslab/COROS-MCP)） |
| **Suunto** | API Zone | ✗（只有训练 FIT 和样本，无睡眠、HRV、全天心率） | ✗ | 仅训练内 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | 仅向做公开服务的公司开放、不提供个人用途；不收使用费（[API Zone](https://apizone.suunto.com/)） |
| **Oura** | API v2（OAuth2） | ✓（`average_hrv`，口径待核） | ✓（5 分钟序列） | ✓ | ✓ | ✓ | △ | △ | △ | ✓ | **用户必须有有效 Membership 才能经 API 拿数据**（[官方支持页](https://support.ouraring.com/hc/en-us/articles/4415266939155-The-Oura-API)）；v2 应用用户数限制待查 |
| **Whoop** | Developer Platform（OAuth2） | ✓（`hrv_rmssd_milli`） | ✓（仅阶段汇总，无时间轴） | ✗（官方 API 无逐点心率，只有平均 / 最大 / 区间时长） | ✓ | ✓ | ✓ | ✓ | ? | ✓ | 分档审批：Sandbox 10 人 → Build 100 → Growth 1000 → Scale 5000 → Production；限流 100 次 / 分钟（[审批](https://developer.whoop.com/docs/developing/app-approval)、[API](https://developer.whoop.com/api)）；是否要求有效会员待查 |
| **Fitbit / Pixel Watch** | Google Health API（旧 Fitbit Web API 2026-11-02 关停）；或 HC | ✓（RMSSD，毫秒） | ✓（LIGHT / DEEP / REM / AWAKE） | ✓ | ✓ | ✓ | ✓ | ✓（睡眠期相对基线） | △ | ✓ | OAuth 验证 + **每年 CASA 第三方安全评估**（评估机构费用页面写 $500–$4,500）；未验证应用最多 100 用户（[验证](https://developers.google.com/health/app-verification)、[迁移](https://developers.google.com/health/migration)） |
| **BLE 心率带**（Polar H10 等） | 标准 BLE 心率服务，手机直连 | 自算（RR 间期 → RMSSD） | ✗ | ✓ | 自算 | ✗ | ✗ | ✗ | ✗ | 训练内 | 无资质；见 2.4 |

来源（上表各行的官方页）：Garmin / Polar / Oura / Whoop / Suunto / Samsung 见表内链接；Fitbit / Google Health 数据类型页 [vitals](https://developers.google.com/health/data-types/vitals)、[sleep](https://developers.google.com/health/data-types/sleep)（均更新 2026-09-22），时间表 [release notes](https://developers.google.com/health/release-notes)；Coros、小米、Amazfit 等「△」的来源是聚合服务和第三方博客（见第 3 节与 [FitMesh](https://www.fitmesh.fit/en/sync/xiaomi-mi-band)），**不是官方**。

> 说明：Open Wearables（我们已在用）官方仓库列出的云端 provider 有 Garmin、Oura、Whoop、Suunto、Polar、Ultrahuman、Strava、Fitbit、Withings、Google Health，SDK 型有 Apple Health、Samsung Health、Health Connect；**没有华为、小米、Zepp**；官方路线图 1.0 预计 2026-10 底（[仓库](https://github.com/the-momentum/open-wearables)、[路线图](https://openwearables.io/docs/roadmap)）。所以云端 provider 这一类，我们大部分不用重写，只要在服务器上开通各家的开发者应用。

### 2.2 三星：HC 里到底有什么

三星官方的 HC 同步映射表（[博客，2025-02-18](https://developer.samsung.com/health/blog/en/accessing-samsung-health-data-through-health-connect)）列出：心率、运动、VO₂max、血氧、睡眠会话 / 睡眠阶段；**HRV、静息心率、呼吸率、皮肤温度不在表里**，也没写「不同步」，所以按官方文档只能说「没有 HRV」，实际行为要真机看。同时三星称 HC 不支持 Wear OS 设备的数据直通，且同步范围随 Samsung Health 版本变化（同页、[FAQ](https://developer.samsung.com/health/health-connect-faq.html)）。结论：**三星手表在 P0 只能做睡眠分、strain、训练，恢复分缺 HRV**；要 HRV 只能走 Health Sensor SDK 读原始 IBI，但需要 partner 申请，且 Partner Apps Program 目前不接受申请（[流程页](https://developer.samsung.com/health/sensor/process.html)）。

### 2.3 五条路线的评估

| 路线 | 适用 | 能拿什么 | 门槛 | 对「数据只留自己服务器」 | 评价 |
|---|---|---|---|---|---|
| **Health Connect**（手机端读） | 有 GMS 的手机上，厂商 app 写入 HC 的设备 | 取决于厂商 app 写什么；Pixel / Fitbit 有 HRV（RMSSD）+ 分期；三星有分期无 HRV | 免资质；Play 上架要 Health apps 声明 | ✓ 手机 → 我们服务器 | **P0 主路线**。大陆 ROM 可用性待查 |
| **厂商云 API**（服务器端拉） | Garmin、Polar、Oura、Whoop、Fitbit / Google Health | 见 2.1 | 各家申请（Garmin 商务申请、Whoop 分档审批、Google CASA、Oura 要用户会员） | ✓ 厂商云 → 我们服务器（用户授权） | **P0**，OW 已有大部分 provider |
| **苹果健康兜底** | 手表配 iPhone（Garmin、Polar、Oura、华为 Health iOS 等写入苹果健康） | 取决于厂商 iOS app 写什么；华为官方只声明体重和运动，其余待查（`huawei-health-plan.md` 第 1.3 节） | 免资质 | ✓ | 已有（iOS app）；对安卓手机用户无关 |
| **用户手动导出再导入** | 华为（隐私中心数据副本）、小米（账号隐私页）、Zepp（数据可携请求） | 华为：见下；小米 / Zepp：格式与字段待查 | 免资质 | ✓ | 一次性历史导入，不是持续同步；先验证内容 |
| **Gadgetbridge**（直连手表） | 华为 / 荣耀、小米、Amazfit、Garmin 等 | 见下 | 免资质，但 AGPL、需用户装另一个 app、配对方式有限制 | ✓ 数据只在手机和我们服务器 | **P1 大陆主路线候选，要原型验证** |

**华为隐私中心「获取您的数据副本」**（[官方步骤](https://consumer.huawei.com/cn/support/content/zh-cn16044986/)，无页面日期）：入口为设置 → 华为账号 → 隐私中心 → 管理您的数据 → 获取您的数据副本；约 7 个工作日后下载；在线工具只覆盖最近约一年。官方页面**没写**运动健康是否可勾选、含哪些字段、文件格式。华为运动健康的隐私声明（[2020-11-15](https://consumer.huawei.com/minisite/cloudservice/ai-life-china-healthkit/CN/b0/latest/ai-life-healthkit-china-zh-cn.htm)）列出的设备数据类型有体重、体脂、日常心率、VO₂max、步数、睡眠数据与状态、血氧、呼吸率、运动心率等，**没有提 HRV，睡眠分期也未明确**。一条 2019 年的社区线索称副本是 zip 内含 JSON，未提 HRV / 分期（[GitHub issue](https://github.com/aricooperdavis/Huawei-TCX-Converter/issues/19)，**只当线索**）。运动健康 App 里官方只写了运动轨迹导出（[官方页](https://consumer.huawei.com/cn/support/content/zh-cn15893332/)），不涉及心率、睡眠。**结论：先让负责人用自己的账号申请一份副本，看里面有没有 HRV 和睡眠分期，再决定要不要做导入器。**

**Gadgetbridge**（[官网](https://gadgetbridge.org/)，AGPLv3，源码在 [Codeberg](https://codeberg.org/Freeyourgadget/Gadgetbridge)；当前稳定版 0.95.1，2026-10-09，[releases](https://codeberg.org/Freeyourgadget/Gadgetbridge/releases)；经 F-Droid / IzzyOnDroid 分发）：

- **型号**（[华为 / 荣耀页](https://gadgetbridge.org/gadgets/wearables/huawei-honor/)）：华为 Band 2–11、Watch 3–5、Watch D2、GT 全系列到 GT 7 Pro、Fit 系列、Ultimate 系列，荣耀 Band 3–6、Magic Watch 系列、Watch 4/5/6 等都在「大部分支持」，多个新机型标 experimental；小米 Mi Band 1–8「高度支持」、Band 9/10、Redmi Watch 5 等「大部分支持」（[小米页](https://gadgetbridge.org/gadgets/wearables/xiaomi/)）；Amazfit 多数「高度支持」，但**要先用 Zepp 系 app 配对并提取 auth key**（[Amazfit 页](https://gadgetbridge.org/gadgets/wearables/amazfit/)）；Garmin Enduro / Fenix 6–8 等「大部分支持」（[Garmin 页](https://gadgetbridge.org/gadgets/wearables/garmin/)）。
- **华为数据范围**（[wiki](https://gadgetbridge.org/basics/topics/huawei-honor/)）：步数、卡路里、睡眠可用；心率、静息心率、SpO₂、压力、体温、HRV **仅部分设备**；**睡眠分期（TruSleep）要求手表升级到 HarmonyOS 5+ 且 Gadgetbridge ≥ 0.87.0**，更老的 TruSleep 设备只同步入睡 / 醒来时间。
- **数据库里有什么**（读 `GBDaoGenerator.java`，[源码](https://codeberg.org/Freeyourgadget/Gadgetbridge/raw/branch/master/GBDaoGenerator/src/nodomain/freeyourgadget/gadgetbridge/daogen/GBDaoGenerator.java)）：华为有 `HuaweiHrvValueSample`（时间 + 整数值）、`HuaweiSleepStageSample`、`HuaweiSleepStatsSample`（含睡眠分、`avgHrv` 及基线、血氧、呼吸率）；小米有 `XiaomiSleepStageSample`，**没看到小米 HRV 实体**；Garmin 有 HRV 值 / 汇总和睡眠分期；Amazfit（Huami）睡眠阶段在 byte 数组里，怎么解待查，没看到 HRV 实体。**表名是按命名规则推断的，HRV 值的口径（RMSSD 还是别的）和 stage 数值含义待查，必须拿真实导出库核对。**
- **导出方式**：手动导出 zip（SQLite + GPX + 偏好）；自动导出（设置 → Automations）可选 ZIP / Database / GPX / FIT；Intent 广播可触发导出（`TRIGGER_EXPORT` 等，动作名在不同版本页面里有两种写法，以实际版本为准）；文件默认在 app 的 `Android/data` 下，新版 Android 对访问有限制，需要用户在自动导出里选一个我们 app 读得到的目录（[数据管理](https://gadgetbridge.org/internals/development/data-management/)、[自动导出](https://gadgetbridge.org/internals/automations/auto-export/)、[Intent](https://gadgetbridge.org/internals/automations/intents/)）。
- **Gadgetbridge → Health Connect**：官方有 HC 集成页，写入步数、心率、SpO₂、**HRV**、体温、呼吸率、静息心率、**睡眠会话**、VO₂max 和运动；页面只写了「睡眠会话」，**是否含分期待查**（[HC 集成](https://gadgetbridge.org/basics/integrations/health-connect/)）。这条链路的前提是手机有 HC（又回到 GMS 问题）。
- **AGPL 依据**（只给依据，不下结论）：AGPLv3 第 5(c)、13 条，及 [GNU 对独立程序间通信的说法](https://www.gnu.org/licenses/gpl-faq.html)：管道、socket、命令行参数是常用于两个独立程序之间的通信；同一可执行文件或共享地址空间链接则几乎肯定算合并。**我们的做法是只读它导出的文件（SQLite / FIT / GPX），不引入它的代码、不链接它的库**，是否合规请法务把关（[AGPLv3](https://www.gnu.org/licenses/agpl-3.0.html)）。
- **风险**：①配对方式（华为 / 荣耀手表同时配华为运动健康 app 和 Gadgetbridge 是否冲突、用户要不要解绑）——本次**没有查到**，待查；②数据库 schema 随版本变，需要按 schema 版本解析并在版本不认识时拒绝（schema 版本 158 为本次所读）；③用户要装第二个 app 并保持后台连接；④华为新机型多为 experimental；⑤最低 Android 版本待查。

### 2.4 BLE 心率带（Polar H10 一类）

- 标准 GATT 心率服务（[蓝牙 SIG](https://www.bluetooth.com/specifications/specs/heart-rate-service-1-0/)）可带 RR 间期，**RR 单位（1/1024 秒）和 flag 位的一手规范文本我没读到，待查**，实现时以 SIG 规范为准。
- **能确认通过标准路径给出 RR 的只有 Polar H10 一条证据链**：Polar 官方 SDK 示例有 `rrsMs`，同行评审对 25 人与 12 导联 ECG 对比的静息 RR 偏差约 ±0.2 ms（[Sensors 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9459793/)）；Garmin HRM-Dual / Pro Plus、Wahoo TICKR、Coospo 的官方规格页要么只写「HRV 支持」要么什么都没写，是否真发 RR 必须实测。
- **Polar BLE SDK**（[仓库](https://github.com/polarofficial/polar-ble-sdk)）：自定义 Polar SDK License（非开源；免费、可用于商业软件，授权范围限于「Polar 产品和你的软件之间传输数据」，对非 Polar 设备是否授权没写明；[许可证](https://raw.githubusercontent.com/polarofficial/polar-ble-sdk/master/Polar_SDK_License.txt)）；Android **minSdk 33**，高于我们的 29，所以要么不引入，要么只在 Android 13+ 启用。H10 的心率和 RR 走标准 GATT 即可，不依赖这个 SDK。
- **Flutter BLE 库**：`flutter_blue_plus` 是 FlutterBluePlus License，**商用需购买许可**（[pub.dev](https://pub.dev/packages/flutter_blue_plus)）；`flutter_reactive_ble` 是 BSD-3-Clause（[pub.dev](https://pub.dev/packages/flutter_reactive_ble)）。安卓没有内置后台支持，夜间持续连接要前台服务。
- **现实限制**：H10 官方规格写训练记录最长 30 小时、CR2025 约 400 小时（[Polar](https://www.polar.com/en/products/accessories/h10_heart_rate_sensor)），夜间能否离线记录再拉取、睡眠舒适度是推演不是事实。心率带**没有睡眠分期**，只能补 HRV 和心率，睡眠分要另想办法（手机体动？）——待设计。

---

## 3. 方案 D：第三方聚合服务

### 3.1 逐家

| 服务 | 华为高阶数据 | 其他大陆品牌 | 公开价格 | 数据存放 | 角色 / DPA |
|---|---|---|---|---|---|
| **Terra** | 官方集成页说走 Huawei Health Kit，数据类别 Activity / Body / Daily / Sleep，**HRV、血氧、睡眠分期页面均未提**；**明文要求客户自己向华为申请开发者审批并提供凭据**；只拿到约每分钟一个点的心率（[集成页](https://tryterra.co/integrations/huawei)、[凭据说明](https://docs.tryterra.co/faq/help-topics/connecting/regional-and-account-restrictions/huawei-dedicated-credentials-required)、[心率说明](https://docs.tryterra.co/help-center/help-topics/data-api-sdk/provider-coverage-and-per-provider-data-availability/huawei-hr-downsampled-upstream.md)） | Amazfit：Terra 用自己的凭据轮询，类别 Activity / Daily / Sleep，未提 HRV（[页面](https://tryterra.co/integrations/amazfit)）；小米未见 | Quick Start $499/月（年付 $399），含 10 万 credits，每个活跃授权 200 credits/月；Enterprise 无公开价（[定价](https://tryterra.co/pricing)） | 默认 EU 集群（英国 + 荷兰处理），可选独立 US 集群；**无中国区**（[区域](https://tryterra.co/blog/health-data-residency)，2026-09-21） | 客户 controller，Terra processor；可能转美国；EU SCC（[DPA](https://tryterra.co/dpa)，2026-09-24） |
| **Thryve** | 有「Huawei Health Kit 连接」，数据：步数、热量、心率、睡眠、vitals；**未提 HRV / 分期 / 血氧，未说是否自有华为资质**（[连接页](https://www.thryve.health/features/connections/huawei-health-kit)） | 列表有 Zepp & Amazfit；未列小米 / 荣耀 / OPPO / vivo（[列表](https://www.thryve.health/features/connections)） | Start Up €499/月，Scale Up €1,000/月（[定价](https://www.thryve.health/pricing)） | 官方 FAQ：全部托管在德国 | 计划表有 data processing agreement，含在哪档不明 |
| **Rook** | 目录列为「Huawei Health（Indirect）」，维度未显示；「Indirect」的准确定义待查（[数据源](https://docs.tryrook.io/data-sources/)） | Mi Fitness、Zepp 同为 Indirect（疑为经手机 HC / 苹果健康读取，推断，待查） | Core $399/月（≤ 750 活跃用户）起（[定价](https://www.tryrook.io/pricing)） | 页面未写；Enterprise 可专用基础设施 | 签 BAA，DPA 未见 |
| **Junction（原 Vital）** | 已读页未见华为 | 小米为 planned；Zepp 未见 | Launch $300/月（≤ 500 连接用户）（[定价](https://www.junction.com/pricing)） | 团队选 US 或 EU（比利时），不可改（[区域](https://docs.junction.com/api-details/regions.md)） | DPA 页未找到 |
| **Spike** | 提供商矩阵有 Huawei：**睡眠分期有（深 / 浅 / REM / 清醒），HRV 无**，连接方式没写（[矩阵](https://docs.spikeapi.com/technical-references/provider_matrix.md)） | 小米、Zepp 未列 | Sandbox 起 $450/月（[定价](https://www.spikeapi.com/pricing)） | 页面未写 | 页面未写 |
| **Sahha** | **只在 iOS 走苹果健康，睡眠只有总时长，安卓上没有**（[页面](https://sahha.ai/integrations/huawei-health/)） | 小米：HC 读深 / 浅 / REM、心率；Amazfit：分期、心率、呼吸、血氧；荣耀：只有总睡眠 | Starter $299/月年付，含 1,000 用户（[定价](https://www.sahha.ai/pricing)） | 页面未写 | 各档含「DPA GDPR」 |
| **Validic** | 页面动态加载，清单未读到，待查 | 待查 | 无公开价 | 页面未写 | 待查 |
| **Open Wearables**（已在用） | 无 | 无（路线图也没有） | 免费，MIT，自托管 | 自托管 | — |

### 3.2 对「华为高阶数据」的结论

在所有读过的页面里，**没有任何一家声称自己拥有华为企业资质、并代客户提供华为的心率明细、HRV、睡眠分期或血氧**：Terra 要客户自备凭据（资质问题原样回到我们身上）；Sahha 在安卓上拿不到；Spike 有分期无 HRV；Thryve 没说。**这个结论只基于公开页面，没有联系销售**。因此：

1. **方案 D 目前不能保证「拿到华为高阶数据」**，在负责人要求「必须拿到高阶数据」的前提下，D 也有可能走不通。
2. 要最终确认，需要向 Thryve、Spike、Rook、Validic（以及 Terra 确认是否有自己的华为资质）发一封书面询问，问清楚下面 5 个问题（第 5 节附清单），拿到**书面**答复再做决定。
3. 就算某家有，也要接受它的成本：月费 $300–$1,000 级，数据出境（第 3.3 节），数据落在它们的服务器（EU / 德国 / 美国），和「数据只留在自己的服务器」冲突。

### 3.3 大陆用户数据经聚合服务出境：《个人信息保护法》要满足什么

> 以下是对官方文本的概括，不是法律意见；条文以原文为准，标「需法务」的请负责人找法务确认。

**健康数据是敏感个人信息**（PIPL 第 28 条列举医疗健康）。适用的要求：

| 条款 | 要求 | 来源 |
|---|---|---|
| 第 28、29 条 | 处理敏感个人信息要有特定目的和充分必要性，**取得单独同意** | [PIPL 全文（国家网信办转载）](https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm) |
| 第 38 条 | 向境外提供，须具备之一：通过国家网信部门安全评估；专业机构保护认证；与境外接收方订立**标准合同**；法律法规规定的其他条件 | 同上 |
| 第 39 条 | 须告知个人：境外接收方名称、联系方式、处理目的与方式、个人信息种类、行使权利的方式与程序，并取得**单独同意** | 同上 |
| 第 40 条 | 关键信息基础设施运营者和达到规定数量的处理者，应境内存储，确需出境须通过安全评估（具体人数门槛在本次读到的原文里没找到，待查 / 需法务） | 同上 |
| 第 55、56 条 | 处理敏感个人信息、向境外提供个人信息，**事前做个人信息保护影响评估**，报告和记录至少保存 3 年 | 同上 |

**数量档（以 2024-03-22 起施行的《促进和规范数据跨境流动规定》为准，按「当年 1 月 1 日起累计向境外提供」计）**（[规定原文](https://www.cac.gov.cn/2024-03/22/c_1712776611775634.htm)、[官方答问](https://www.cac.gov.cn/2024-03/22/c_1712776612187994.htm)）：

| 情形 | 要求 |
|---|---|
| 非敏感个人信息，累计不满 10 万人 | 免于安评 / 标准合同 / 认证（第 5 条） |
| 非敏感个人信息，10 万人以上不满 100 万人 | 标准合同或认证（第 8 条） |
| **敏感个人信息，不满 1 万人** | **标准合同或认证**（第 8 条）；**不适用第 5 条的 10 万人豁免** |
| 敏感个人信息 **1 万人以上**；或非敏感 100 万人以上；或涉及重要数据 | **数据出境安全评估**（第 7 条） |
| 任何数量 | 仍须履行告知、**单独同意**、影响评估等义务（第 10 条） |

> 与常见说法不同的两点（官方原文为准）：①标准合同备案期限是**合同生效之日起 10 个工作日**内向省级网信部门备案，同时提交保护影响评估报告（[标准合同办法第 7 条](https://www.cac.gov.cn/2023-02/24/c_1678884830036813.htm)，施行日 2023-06-01），不是 30 个工作日；②没有查到「累计不满 1 万人个人信息出境一律豁免」的条款，1 万人这条线是针对**敏感个人信息**的安评 / 标准合同分界。

**重要数据**：《网络数据安全管理条例》（国务院令第 790 号，2025-01-01 施行）规定重要数据目录由国家统筹、各地区各部门制定（[原文](https://www.gov.cn/zhengce/content/202409/content_6977766.htm)）。**没有找到官方把个人可穿戴 / 健身健康数据列入重要数据目录，也没有找到明确说「不是」的文件**，不推断；卫健领域重要数据目录是否发布、2018 年 / 2022 年 / 2026-02 的卫健类办法是否适用于个人健康 app，均待查 / 需法务。

**把这些条件落到我们身上**（对「方案 D 的大陆用户」）：

1. 每个大陆用户连接前做**单独同意**弹窗，写明境外接收方名称、联系方式、数据种类、目的、行使权利的方式（第 39 条）；与 #249 的内地版同意书统一处理；
2. 与聚合服务签**标准合同**，做**个人信息保护影响评估**，合同生效后 10 个工作日内备案（我们的出境人数每年累计 < 1 万人时走这条；≥ 1 万人要安全评估）；
3. 聚合服务必须是**境外接收方并愿意签标准合同**；Terra 的 DPA 用的是 EU SCC，**不是**中国标准合同，是否愿意另签待问；
4. 无论如何，**最小化**：只拉评分需要的类型，关闭它们的数据保留（Sahha 默认保留 30 / 120 天；其他家保留策略待查）。

**数据原则怎么取舍**：项目原则是「数据只留在自己的服务器」。方案 D 一定会让大陆用户的健康数据**先经过一个境外 processor**（EU / 德国 / 美国），并可能被缓存。这是一个**需要负责人明确接受的例外**：建议若走 D，只对**自愿**选择「华为 / 小米等没有其他路线的品牌」的大陆用户开放，默认关闭；其他路线（HC、Gadgetbridge、云 API）无此问题。

### 3.4 其它可选项：自己拿资质

`docs/huawei-health-plan.md` 第 3 节写明：事业单位 / 政府机构免实缴资本和年限要求。与有资质的主体合作（由对方做华为开发者主体）的可行性**本次没有核实**，需要先向华为问清服务端应用是否可由合作主体代为注册、用户数据归属与合规责任怎么划分；同样只能列入待查。

---

## 4. 分期 roadmap

> 前置工作（所有期共用，已在 PR #261 / `docs/apps.md` 中提到）：安卓 APK 清单里为了第一版去掉了 OW SDK 自带的 HC `READ_*` 权限和通知权限，**接手表数据时要加回对应权限行**（#261 交接）；服务器侧 Body Engine 的 `import-ow`、来源优先级（`importers/common.py resolve_daily`）、睡眠 / 训练导入器目前只在 Apple 来源上测过，开放给安卓用户前要用一个安卓测试用户端到端跑通并和手机对比几天（`docs/apps.md`）。

### P0：有 Google 服务的安卓手机（Pixel / 三星 / 其他带 Play 的机型）

| 项 | 内容 | 估算 |
|---|---|---|
| 设备 | Pixel Watch / Fitbit（HC，HRV + 分期）；Garmin、Polar、Oura、Whoop（OW 云端 provider，**与手机系统无关**，iOS 用户同样受益）；三星 Galaxy Watch（HC：分期有、HRV 无 → 无恢复分，今天页要明示原因） | — |
| 手机 | Android 10+（minSdk 29）；Android 10–13 需要用户自己装 HC | — |
| App | 恢复 HC 权限与 OW SDK 同步（SDK 已支持 HC / Samsung Health，`getAvailableProviders()` / `setProvider()`）；`getSdkStatus()` 检查与引导安装；历史读取权限和后台读取的降级 | 5–8 人日 |
| 服务器 | HC 口径 `rmssd_hc`；安卓来源端到端验证（HC 来源去重、三星 / Pixel 的睡眠分期映射）；各云 provider 的开发者应用在服务器上开通并配置 | 8–12 人日 + 各家审批时间 |
| 分发 | Google Play 内部 / 封闭测试，需要 Health apps 声明（逐类型写理由，隐私政策与应用内披露一致），或签名 APK 侧载 | 声明材料 1–2 人日 |
| 门槛 / 风险 | Garmin 商务申请（时长待查）；Whoop 先在 Sandbox 10 人内用；Google Health API 的 OAuth 验证 + 每年 CASA（$500–$4,500）；Oura 用户需有会员；HC 在 Android 13 以下的后台 / 历史限制 | — |

合计代码约 **13–20 人日**（估算）。**P0 不依赖华为，也不卡资质**，是最快能给「海外 + Pixel / 三星用户」完整体验的一期。

### P1：大陆手机（华为 HarmonyOS 4.x、荣耀、小米 / 红米、OPPO、vivo），无资质路线

| 步骤 | 内容 | 估算 |
|---|---|---|
| **P1-0 真机验证（先做）** | 每个品牌一台真机测：①HC 有没有、非 Play 签名包能否拿到权限；②厂商健康 app 往 HC 写什么（含 HRV、分期）；③负责人申请一份华为隐私中心数据副本，看有没有 HRV / 分期；④装 Gadgetbridge 导出一份 SQLite，核对表名、HRV 口径、stage 含义；⑤华为 / 荣耀手表同时配两个 app 的冲突情况 | 3–5 人日 + 负责人等待（副本约 7 个工作日） |
| P1-A Gadgetbridge 导出读取 | app 引导用户装 Gadgetbridge、选自动导出目录；app 读 SQLite（只读文件）→ 上传 → 服务器按 schema 版本解析成 `ext` 原始样本 → 日值 / 睡眠 / 训练（口径 `rmssd_gb_huawei` 等，先单设备族：华为）| 15–25 人日 |
| P1-B 华为数据副本导入器 | **仅当 P1-0 验证副本含 HRV 和睡眠分期**时做，一次性历史导入 | 3–5 人日 |
| P1-C HC 路线 | 仅对 P1-0 验证过「有 HC 且有 HRV + 分期」的品牌开放（小米 / OPPO 国际版是候选，大陆版待查） | 与 P0 共用，增量 2–3 人日 |
| P1-D BLE 心率带 | 补 HRV 和心率，不补睡眠分期；Polar H10 为首选；需要前台服务与夜间连接策略 | 10–15 人日 |
| 分发 | 侧载 APK（HarmonyOS 4.x / 其他大陆安卓）；国内应用市场上架要备案等，周期按月（见 `huawei-health-plan.md` 第 7.4 节），**第一版不做** | — |
| 门槛 / 风险 | Gadgetbridge 配对方式与 schema 变动、AGPL 边界（法务）、用户操作负担高、新机型 experimental；大陆 ROM 的后台保活 | — |

**P1 的判断点**：P1-0 验证后，如果 Gadgetbridge 对华为 / 小米能稳定拿到 HRV + 分期，则大陆无资质路线成立，**不需要方案 D**；否则进入 P2 的方案 D 询价。

### P2：按验证结果决定

| 项 | 触发条件 | 内容 | 估算 |
|---|---|---|---|
| 方案 D | P1 验证失败，且某家聚合服务**书面**确认能给华为 HRV + 分期 | 接聚合服务、服务器 connector、出境合规（单独同意、标准合同、影响评估、备案）；月费 $300–$1,000 | 15–25 人日 + 法务与合同时间 |
| 有资质的合作主体 | 负责人能找到满足条件的主体 | 按 `huawei-health-plan.md` 实施（6 周审批 + 28–34 人日） | 见该文档 |
| 鸿蒙 NEXT ArkTS 壳 | 家人手机是 NEXT | 见 `huawei-health-plan.md` 第 7 节 | 8–12 人日 |
| 三星官方 SDK | Partner Apps Program 重新开放 | 为 Galaxy Watch 拿 HRV | 待查 |
| Wear OS app | 需要 Pixel Watch / Galaxy Watch 原始数据 | `docs/apps.md` 已规划 | 待估 |
| Coros、Suunto、其他 | 官方 API 或需求出现 | Coros 无公开 API；Suunto 缺睡眠 / HRV，不够 | — |

---

## 5. 要负责人去办的事

| 编号 | 事项 | 为什么 | 什么时候 |
|---|---|---|---|
| **T1** | 先决定接受什么：**P0 先做**，大陆先走 P1 的 Gadgetbridge 原型而不是直接买聚合服务 | 公开资料里没有聚合服务能给华为高阶数据的证据（第 3.2 节） | 现在 |
| **T2** | 用自己的华为账号申请一份「获取您的数据副本」（设置 → 华为账号 → 隐私中心 → 管理您的数据），收到后只告诉我们**有没有 HRV 和睡眠分期、文件格式**，不要把文件本身发进公开仓库 | 约 7 个工作日；决定要不要做 P1-B | 现在 |
| **T3** | 准备手机 / 手表清单：家人和测试用户各用什么手机（品牌、系统版本）和手表（型号）；至少借到大陆小米、华为 4.x、OPPO 或 vivo 各一台能测 HC 的真机 | P1-0 | 现在 |
| **T4** | 装 Gadgetbridge（F-Droid / IzzyOnDroid），配对自己的华为 / 荣耀 / 小米 / Amazfit 手表（若有），导出一份数据库交给开发会话核对表结构 | P1-0 | 与 T3 同步 |
| **T5** | 向聚合服务发书面询问（Thryve、Spike、Rook、Validic；Terra 问是否有自己的华为资质）：①华为数据是否包括心率明细、HRV、睡眠分期、血氧；②由谁持有华为资质，客户是否需要自己的华为凭据；③能否在中国大陆用户数据上签中国《个人信息出境标准合同》；④数据保留期与能否关闭保留；⑤价格与最少用户数。**没有书面答复之前不签约、不付款** | 第 3 节 | P1-0 之后，如果 Gadgetbridge 不成立 |
| **T6** | 法务确认：①大陆用户数据经境外聚合服务的单独同意文案与标准合同路径；②预计每年出境人数是否会超过 1 万人（超过要安全评估）；③健康数据是否可能被认定为重要数据；④AGPL：只读 Gadgetbridge 导出文件的做法 | 第 3.3 节 | 走 D 之前 |
| **T7** | 向华为提工单（沿用 `huawei-health-plan.md` D1）：是否允许合作主体代为注册；高阶数据实缴资本门槛的最新官方原文（本次没能从页面核实） | 第 3.4 节 | 可选 |
| **T8** | Garmin Business 申请（用途、公司、数据处理、集成计划）；Google Health API 的 OAuth 验证与 CASA 评估预算（$500–$4,500 / 年）；确认愿意为 Oura 用户要求会员这个前置条件 | P0 | P0 开发期间 |
| **T9** | 决定：Play 上架（需要 Play Console 账号、Health apps 声明）还是只侧载；大陆分发是否只侧载 | P0 / P1 分发 | P0 前 |

---

## 6. 待查清单（本次核实不了的）

1. 华为官方对 HarmonyOS 4.x 能装 APK、NEXT 不能装 APK 的**官方原文**（只有社区文章和媒体报道）；华为机型 GMS 的官方说明。
2. 华为 / 荣耀 / 小米大陆版 / OPPO / vivo 大陆 ROM 的 GMS 与 Health Connect 官方说明；Android 14+ 内置 HC 在无 Google Play services 的 ROM 上是否存在 / 能否更新。
3. 非 Play 分发的 app 能否获得 HC 生产权限；Google 的 HC 政策对不经 Play 分发的应用是否仍约束；「在不兼容的设备或平台之间同步数据」禁止项是否覆盖「用户自传服务器」。
4. HC：`HeartRateRecord`、`ExerciseSessionRecord` 的 API 级别；Android 13 及以下 HC app 是否提供历史 / 后台 / 皮肤温度；feature flag 的精确门槛。
5. 大陆厂商手表（华为、小米大陆、OPPO、vivo、荣耀）往 HC 写入哪些类型的官方说明；Zepp / Amazfit 是否写 HRV、是否有官方云 API。
6. 华为隐私中心数据副本：运动健康是否可勾选、字段、格式、粒度；运动健康 App 内是否有全量导出；小米 Mi Fitness 导出格式；Zepp 数据可携副本格式。
7. Gadgetbridge：最低 Android 版本；配对与华为运动健康 app 是否冲突；华为 HRV 值口径、stage 数值含义、真实表名；HC 同步是否含睡眠分期；Huami 睡眠 byte 数组怎么解；心率带清单。
8. 三星：Samsung Health 是否向 HC 同步 HRV / 静息心率 / 呼吸 / 皮肤温度；Partner Apps Program 何时重开。
9. Garmin Health API 官方字段与口径（HRV 摘要口径、分期、血氧 / 呼吸 / 体温 / VO₂max）、审批标准与时长、费用；Polar Nightly Recharge HRV 官方口径、AccessLink 商用条款；Oura v2 字段口径、应用用户数限制、PAT 下线日期；Whoop 是否要求有效会员；Pixel Watch 经 HC 写入类型；Google Health API 的 VO₂max 与 OAuth 审核耗时。
10. 蓝牙 SIG 心率特征的 RR 单位与 flag 位原文；Polar H10 官方 RR 精度白皮书、H9 规格；H10 夜间离线记录；其他心率带是否真发 RR；`flutter_blue_plus` 商业许可价格与适用性。
11. 聚合服务：Rook「Indirect」定义；Rook / Junction / Spike / Validic / Sahha 的数据区域与 DPA；Validic 完整设备清单；各家是否有自己的华为资质（**需要联系销售才能确认**）；Terra 是否愿意签中国标准合同。
12. 法规：《规定》第 6、9 条细节；PIPL 第 40 条「达到规定数量」的人数；《标准合同办法》与《规定》第 13 条衔接后哪些条文仍有效；个人可穿戴健康数据是否属重要数据、卫健类办法是否适用。
13. 华为高阶数据「实缴注册资本 ≥ 500 万」本次未能从官方页面重新核实，沿用 `huawei-health-plan.md` 第 3 节（中国站资质说明页）；华为开发者协议对「第三方再转接」的明文限制未读到。

---

## 7. 引用清单

- Health Connect：[availability](https://developer.android.com/health-and-fitness/health-connect/availability)、[get-started](https://developer.android.com/health-and-fitness/health-connect/get-started)、[data-types](https://developer.android.com/health-and-fitness/health-connect/data-types)、[read-data](https://developer.android.com/health-and-fitness/health-connect/read-data)、[HealthPermissions](https://developer.android.com/reference/android/health/connect/HealthPermissions)、[declare-access](https://developer.android.com/health-and-fitness/guides/health-connect/publish/declare-access)、[Play 政策](https://support.google.com/googleplay/android-developer/answer/9888170)
- 三星：[HC 博客](https://developer.samsung.com/health/blog/en/accessing-samsung-health-data-through-health-connect)、[HC FAQ](https://developer.samsung.com/health/health-connect-faq.html)、[Health Data SDK](https://developer.samsung.com/health/data/overview.html)、[流程](https://developer.samsung.com/health/data/process.html)、[Sensor SDK](https://developer.samsung.com/health/sensor/overview.html)
- 云 API：[Garmin](https://developer.garmin.com/gc-developer-program/overview/)、[Polar](https://www.polar.com/accesslink-api/)、[Oura](https://support.ouraring.com/hc/en-us/articles/4415266939155-The-Oura-API)、[Whoop](https://developer.whoop.com/api)、[Suunto](https://apizone.suunto.com/)、[Google Health API 迁移](https://developers.google.com/health/migration)、[Open Wearables](https://github.com/the-momentum/open-wearables)
- 华为：[隐私中心副本步骤](https://consumer.huawei.com/cn/support/content/zh-cn16044986/)、[运动健康数据声明](https://consumer.huawei.com/minisite/cloudservice/ai-life-china-healthkit/CN/b0/latest/ai-life-healthkit-china-zh-cn.htm)；其余见 [`huawei-health-plan.md`](huawei-health-plan.md)
- Gadgetbridge：[官网](https://gadgetbridge.org/)、[华为 / 荣耀](https://gadgetbridge.org/basics/topics/huawei-honor/)、[自动导出](https://gadgetbridge.org/internals/automations/auto-export/)、[HC 集成](https://gadgetbridge.org/basics/integrations/health-connect/)、[AGPLv3](https://www.gnu.org/licenses/agpl-3.0.html)
- 聚合服务：[Terra](https://tryterra.co/pricing)、[Thryve](https://www.thryve.health/pricing)、[Rook](https://www.tryrook.io/pricing)、[Junction](https://www.junction.com/pricing)、[Spike](https://docs.spikeapi.com/technical-references/provider_matrix.md)、[Sahha](https://www.sahha.ai/pricing)
- 法规：[PIPL](https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm)、[标准合同办法](https://www.cac.gov.cn/2023-02/24/c_1678884830036813.htm)、[促进和规范数据跨境流动规定](https://www.cac.gov.cn/2024-03/22/c_1712776611775634.htm)、[网络数据安全管理条例](https://www.gov.cn/zhengce/content/202409/content_6977766.htm)
- 项目内：[`huawei-health-plan.md`](huawei-health-plan.md)（#253）、[`apps.md`](apps.md)、[`multi-region.md`](multi-region.md)（#249）、[`scoring-methodology.md`](scoring-methodology.md)

---

## English summary

**Goal.** Plan the Android client beyond the first APK (login + Today page + AI chat only): which phones, which watches and data routes, what an aggregator (option D) can and cannot do, and phases P0–P2. Research only; no code.

**Findings.** (1) Outside mainland China the route is open and needs no qualification: Health Connect has every record type we need (HRV only as RMSSD), the Open Wearables Flutter SDK already supports Health Connect and Samsung Health, and OW has Garmin / Polar / Oura / Whoop / Fitbit-Google Health cloud providers. Pixel Watch / Fitbit give HRV + sleep stages; Samsung's official Health Connect mapping has sleep stages but no HRV, so Galaxy Watch users get no recovery score. (2) Health Connect requires Google Play services; whether it exists on mainland ROMs and whether a sideloaded package can obtain production permissions is undocumented — to be tested on real devices. (3) No aggregator page we read says it holds Huawei's enterprise qualification: Terra requires customers to bring their own Huawei credentials, Sahha has no Huawei stages on Android, Spike lists Huawei stages without HRV, Thryve is silent; this rests on public pages only, written confirmation from sales is needed. (4) The no-qualification candidate for mainland is Gadgetbridge (AGPL — our app only reads its exported SQLite/FIT files): Huawei HarmonyOS 5+ watches sync sleep stages and the database has HRV tables; format, pairing conflicts and HRV semantics must be verified on real devices first. (5) PIPL: health data is sensitive personal information (separate consent, Arts. 28/29/39); cross-border transfer needs a standard contract + PIA + filing within 10 working days when under 10,000 sensitive-data subjects per year, a security assessment above that; the "under 100,000 people" exemption does not apply to sensitive data. (6) Option D conflicts with "data stays on our server" and must be an explicit, opt-in exception.

**Phases.** P0: Android 10+ phones with Google services (Pixel, Samsung, others) + cloud providers (~13–20 person-days). P1: mainland phones via real-device checks, Gadgetbridge export reader, Huawei data-copy import (only if it contains HRV and stages), BLE chest strap (~30–50 person-days). P2: aggregator, partner entity with Huawei qualification, HarmonyOS NEXT shell, Samsung SDK, Wear OS — decided by P1 results. Owner to-dos are in section 5, unverified items in section 6.
