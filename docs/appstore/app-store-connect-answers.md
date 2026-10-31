# App Store Connect 问卷答案（草稿，负责人确认后再填）

> 这份是给负责人照着填 App Store Connect 的底稿；不是法律意见。所有"和代码对得上"的依据都列在每一节后面，问卷的措辞以填写当天 App Store Connect 里的为准（Apple 2025 年改过年龄分级问卷，下面按新问卷写）。
> 依赖 PR #381（隐私和合规，`PrivacyInfo.xcprivacy`、隐私政策和使用条款）：合并前，第 3 节的数据类型以该 PR 的 `apps/ios/Watch2Whoop/PrivacyInfo.xcprivacy` 为准；合并后以 main 上的文件为准，两边不一致时先改文件、再改这里。
> 文中 `{{APP_NAME}}` = 上架名（还没定，名字里不能有 WHOOP，商标）。

## 1. 基本信息

| 项 | 答案 | 说明 |
|---|---|---|
| 平台 | iPhone（`TARGETED_DEVICE_FAMILY: "1"`，只有 iPhone，没有 iPad / Mac / Vision 版） | 不勾"iPad 上运行 iPhone 版"以外的选项，由负责人定要不要允许 iPhone 版在 iPad / Apple 芯片 Mac 上运行 |
| 最低系统 | iOS 16.0 | `project.yml` |
| 主要类别 | **健康与健身（Health & Fitness）** | |
| 次要类别 | 生活（Lifestyle） | 可不填；不要选"医疗（Medical）"：选了会被按医疗 app 审（准则 1.4.1），而我们明确不是医疗器械 |
| 价格 | 免费，无 App 内购买，无广告 | app 里目前没有任何购买入口；以后如果在 app 里卖数字服务或额度，必须走 App 内购买（准则 3.1.1，见 README 的待定项） |
| 出口合规 | 只用系统 HTTPS，`ITSAppUsesNonExemptEncryption = false`（已在 `project.yml`） | |
| 内容版权 | 不含第三方版权内容 | 开源许可在 app「我 → 关于 → 开源许可」（PR #381） |
| 隐私政策网址 | `{{SERVER_URL}}/legal/privacy`（`render_listing.py` 会按 `SERVER_URL` 渲染 `privacy_url.txt`） | 公网 nginx 要放行 `/legal/`，见 PR #381 |
| 使用条款 | 用自定义 EULA，填 `{{SERVER_URL}}/legal/terms` | |
| 支持网址 / 营销网址 | 待定（见 README） | 支持页必须能打开并有联系方式（5.1 / 1.5） |

## 2. 年龄分级（新问卷：4+ / 9+ / 13+ / 16+ / 18+）

按 App Store Connect 的分组列出，每项给"答案 + 为什么"。

**应用内控制与能力**

| 问题 | 答案 | 依据 |
|---|---|---|
| 家长控制（Parental Controls） | 否 | 没有 |
| 年龄验证（Age Assurance） | 否 | 最低年龄只写在隐私政策 / 使用条款里（参数 `BODY_LEGAL_MIN_AGE`，默认 16），app 里不验证年龄 |
| 不受限制的网页访问（Unrestricted Web Access） | 否 | 没有 WebView（C2）；链接只有隐私政策 / 条款 / 开源许可，用系统浏览器打开 |
| 用户生成内容（User-Generated Content） | **是** | 圈子里的动态、评论、头像、昵称；见下面的"准则 1.2" |
| 消息与聊天（Messaging and Chat） | **是**（保守回答） | 圈子成员之间可以评论、点赞；没有私聊。如果负责人认为"评论不算聊天"可以改成否，分级结果通常不变 |
| 广告（Advertising） | 否 | 没有广告 |

**医疗与健康**

| 问题 | 答案 | 依据 |
|---|---|---|
| 健康或保健主题（Health or Wellness Topics） | **是** | 整个 app 是健康记录和保健 |
| 医疗或治疗信息（Medical or Treatment Information） | **无**（若 Apple 要求考虑 AI 的回答，选"偶尔"） | app 本身不含诊断、治疗内容；Jarvis 的回答是 AI 生成的，每条带"AI 生成"标签和"不是医疗建议"弹层（PR #381）。化验记录是用户自己的数据，app 只画趋势 |

**内容类（全部"无"）**：卡通 / 奇幻暴力、写实暴力、长时间的血腥暴力、亵渎或粗俗幽默、成人 / 暗示性主题、恐怖 / 惊悚主题、酒精 / 烟草 / 毒品、性内容或裸体、露骨性内容、模拟赌博、竞赛（Contests）、赌博（Gambling）。

- 圈子里有"排行榜"，不是竞赛（没有奖品，没有赌注）：竞赛一项答"否"。
- Jarvis 可以连用户自己的通用 AI（Claude / ChatGPT 等），理论上能说出任何内容。我们的设定把它限制在身体管理话题，但 Apple 可能追问；回答"无"的前提是 Jarvis 的系统提示不变。

**预期结果**：UGC + 健康主题，预计 **13+**（以 App Store Connect 算出的为准）。隐私政策写了最低年龄 16，**建议在"年龄分级"页用高于计算值的覆盖选项选 16+**，让商店分级和政策一致（负责人决定）。

**准则 1.2（用户生成内容）要求的四件事**，评审会逐项问：

| 要求 | 状态 |
|---|---|
| 过滤不良内容的方法 | 待核对（圈子审核由另一个 PR 处理，合并后写进 `review-notes-template.md` 第 6 节） |
| 举报功能，并及时处理 | 评论的"举报"已有（`FeedViews.swift`）；动态 / 成员的举报和"屏蔽"在另一个 PR |
| 屏蔽滥用用户 | 另一个 PR |
| 公布联系方式 | 隐私政策第 10 节（`BODY_LEGAL_CONTACT`，负责人填） |

## 3. App 隐私（营养标签）

依据：`apps/ios/Watch2Whoop/PrivacyInfo.xcprivacy`（PR #381）、隐私政策第 1、2、4 节、`Resources/healthkit_types.json`。
**"是否追踪"全部是"否"**：没有广告、没有第三方分析 SDK、不读 IDFA，`NSPrivacyTracking = false`，不需要 App Tracking Transparency 弹窗。
下面每一类都是：**收集 = 是；关联到用户身份 = 是**（所有数据都在账号下）；**用于追踪 = 否**。

| Apple 的数据类型 | 对应什么 | 用途（勾选） |
|---|---|---|
| 健康与健身 → **健康** | HealthKit 读到的睡眠、心率、HRV 和逐拍间隔、静息心率、血氧、呼吸频率、手腕温度、心电图、VO₂ max、体重 / 体脂、血压、血糖、体温等；用户自己记的化验、补剂、身体事件、出生日期 / 性别 / 身高 | App 功能 |
| 健康与健身 → **健身** | 体能训练、步数、距离、活动能量、锻炼 / 站立分钟、手动记的训练组 | App 功能 |
| 联系信息 → **姓名** | 称呼 / 昵称；用 Apple 登录时用户选择分享的名字 | App 功能 |
| 联系信息 → **电子邮件地址** | 邮箱验证码登录；Apple 登录时分享的邮箱（可能是私密转发地址） | App 功能 |
| 标识符 → **用户 ID** | 账号 ID、Apple 的用户标识（加密保存的刷新令牌，用来在删除账号时撤销授权）、设备 token | App 功能 |
| 用户内容 → **照片或视频** | 用户主动发给 AI 的照片 / 视频帧、头像 | App 功能 |
| 用户内容 → **音频数据** | 按住说话时的录音，发到服务器转成文字，转完即删、不保存 | App 功能 |
| 用户内容 → **其他用户内容** | 饮食、日记、圈子动态和评论、与 AI 的对话、长期记忆 | App 功能 |
| 用户内容 → **客户支持** | 用户（或 AI）提交的问题和建议 | App 功能 |
| 使用数据 → **产品交互** | 用了哪些功能和页面（不含记录的内容和健康数值），用于了解功能是否好用 | **分析** |
| 诊断 → **其他诊断数据** | app 版本、iOS 版本、设备型号、同步状态（最近同步时间、低电量模式等） | App 功能 |

**不收集**：位置、通讯录、浏览 / 搜索历史、购买记录、财务信息、敏感信息（种族、宗教等）、设备 ID（IDFA / IDFV）、广告数据、崩溃日志（没有崩溃上报 SDK）。如果以后加了 Crashlytics 之类，要回来改。

**第三方（AI）怎么答**
- 用户自己的 AI（自己的 API key，或者 Claude / ChatGPT 等客户端通过 MCP 连接）：只有用户开始用 Jarvis 时，回答需要的那部分数据才发给用户选的公司。在隐私标签里已经按"健康 / 其他用户内容"声明为收集；在问卷的"数据是否与第三方共享"里，按 Apple 的定义这属于**应用用户自己选择的服务**，仍建议如实写"AI 回答需要的数据会发给用户自己选的 AI 提供商"，写在隐私政策第 4 节（PR #381 已写）。
- 备用 AI（DeepSeek，服务器在中国）：第一次使用前单独征得同意（`ai_fallback_consents`）。这是准则 5.1.2(i)（2025 年 11 月起）要求的"向第三方 AI 共享个人数据前明确披露并取得许可"。审核备注里要单独写一段（`review-notes-template.md` 第 5 节）。
- 心电图波形和逐拍心跳**从不**发给任何 AI（隐私政策第 4 节）。

**HealthKit 的特别规则（准则 5.1.3）**：健康数据不用于广告 / 营销，不卖给数据经纪人，不存 iCloud；隐私政策第 2 节已写。

**Required-reason API**（`PrivacyInfo.xcprivacy`）：UserDefaults（`CA92.1`，只读写自己的设置）、文件时间戳（`C617.1`，`LocalStorage.swift` 读自己库文件的大小）。没用到磁盘空间 / 开机时间 / 系统启动时间 API。GRDB 自带隐私清单。

## 4. HealthKit 读取的类型

注册表是 `apps/ios/Watch2Whoop/Resources/healthkit_types.json`（59 项，是单一来源；这里不重复列表，避免两边不一致）。大类：

- 睡眠、体能训练、心率 / 静息心率 / 步行平均心率；HRV（SDNN、RMSSD）、逐拍心跳序列、心电图（`HKElectrocardiogram`）。
- 呼吸频率、血氧、睡眠手腕温度、体温、VO₂ max。
- 体重、体脂、去脂体重、BMI、身高；血压、血糖；饮水、正念。
- 步数、距离（步行 / 跑步 / 骑行 / 游泳）、爬楼层、活动 / 静息能量、锻炼 / 站立分钟、日照时间、体力消耗。
- 步态与跑步指标、骑行功率、水温、环境 / 耳机音量。

只读：Release 版不写入"健康"（`NSHealthUpdateUsageDescription` 的文字只给模拟器里的 Debug 版）；entitlement 有 `healthkit` 和 `healthkit.background-delivery`，`healthkit.access` 是空数组（不读临床健康记录）。

## 5. 其他 App Store Connect 项

| 项 | 答案 |
|---|---|
| Sign in with Apple | 有（和邮箱验证码并列）；删除账号时撤销 Apple 授权（PR #381，准则 5.1.1(v)） |
| 账号删除 | app 内「我 → 删除账号」，删除全部数据（备份在 6 个月内轮换掉） |
| 推送通知 | 有：圈子的点赞 / 评论、提醒；后台静默推送（`remote-notification`）只通知 app 去拉新数据 |
| 后台模式 | `fetch`、`processing`、`remote-notification`，加 HealthKit 后台送达；理由见 `review-notes-template.md` 第 3 节 |
| 内容分发权利 / 第三方内容 | 无 |
| 版本发布方式 | 手动发布（建议，等 TestFlight 验收后再放出） |
