# App Store 上架材料

原生 iOS app（`apps/ios/`，2.0.0）上架 App Store 要用的文字、问卷答案、截图脚本和审核备注。**上架名还没定**（名字里不能有 WHOOP，商标）：所有文字里名字都是 `{{APP_NAME}}`，脚本里是环境变量 `APP_NAME`，名字定了重新生成一次就行。

## 文件

| 文件 | 内容 |
|---|---|
| `listing/zh-Hans/`、`listing/en-US/` | 商店文字，一个字段一个 `.txt`（fastlane `deliver` 的目录结构）：`name`、`subtitle`、`promotional_text`、`description`、`keywords`、`release_notes`、`privacy_url` |
| `render_listing.py` | 检查字数限制并把名字 / 服务器地址填进去 |
| `app-store-connect-answers.md` | 类别、年龄分级、App 隐私（营养标签）、HealthKit 类型、其他项的答案 |
| `review-notes-template.md` | 给 App Review 的备注模板（英文）+ 中文摘要 |
| `../../apps/ios/scripts/appstore_screenshots.sh` | 截图流水线（模拟器 + 合成数据），配套 `appstore_demo_server.py`、`appstore_seed_circles.py` |

## 名字定了以后（三条命令）

```bash
export APP_NAME="<最终名字>"  SERVER_URL="https://<公网域名>"
python3 docs/appstore/render_listing.py --write          # 检查字数 + 商标 / 医疗词，渲染到 docs/appstore/out/<语言>/（不进 git）
apps/ios/scripts/appstore_screenshots.sh                  # 截图 → apps/ios/build/appstore-screenshots/（不进 git）
```

`render_listing.py` 没设 `APP_NAME` 时按名字 30 个字符的最坏情况计数；设了的话名字里有 WHOOP、超过 30 个字符会报错。检查项：各字段字数上限（名称 30、副标题 30、宣传文字 170、描述 4000、关键词 100、更新说明 4000）、文字里出现竞品商标（WHOOP、Oura、Garmin、Fitbit 等）、医疗词（诊断、治疗、疾病等，只允许出现在否定句里，如"不是医疗器械"）、关键词里逗号旁有空格 / 重复 / 含 apple。当前各字段字数（名字按 30 个字符的最坏情况）：

| 字段 | 中文 | English | 上限 |
|---|---|---|---|
| 副标题 | 17 | 29 | 30 |
| 宣传文字 | 85 | 149 | 170 |
| 描述 | 960 | 2432 | 4000 |
| 关键词 | 66 | 100 | 100 |
| 更新说明（2.0.0） | 189 | 532 | 4000 |

文字的写法：定位是"你的身体数据层"——Apple Watch / 健康的数据变成恢复、睡眠、负荷；Jarvis（AI）可选且默认关闭；圈子；数据归用户。不提 WHOOP；不做医疗宣称（描述最后一段写明不是医疗器械、不用于诊断治疗）。更新说明来自 `apps/ios/CHANGELOG.md` 的 2.0.0 一节，只挑用户能感觉到的，不写开关名和技术词。

## 截图流水线

```bash
apps/ios/scripts/appstore_screenshots.sh                         # 6.9" iPhone、浅色、中文 + 英文
LOCALES=zh SCREENS="01-today 05-jarvis" apps/ios/scripts/appstore_screenshots.sh
WITH_65=1 apps/ios/scripts/appstore_screenshots.sh               # 另外生成 6.5" 一套（由 6.9" 缩放）
```

- **尺寸假设**：iPhone app 必须提供 6.9"（iPhone 17 Pro Max 模拟器，1320×2868）；6.5"（1284×2778）只在没传 6.9" 时才需要，Apple 会把 6.9" 缩小给小屏，所以默认不做，`WITH_65=1` 时由 6.9" 缩放得到（iOS 26 没有 6.5" 的模拟器）。app 只有 iPhone 版，不需要 iPad 截图。上传前以 App Store Connect 当天的要求为准。
- **只用合成数据**：`appstore_demo_server.py` 起一个一次性的 Postgres 容器（`w2w-appstore-shots-db`，端口 55433，不是真库的 5433），用 `quickstart/demo_data.py` 生成 60 天合成的健康数据导入，`demo_sparky.py` 加饮食 / 力量训练 / 体重，再造三个虚构成员（Ann、Ben、Cat）和一个圈子 `Sunrise Club`，用 uvicorn 在 127.0.0.1:18199 提供服务。不读仓库的 `secrets/`，不连任何线上服务器；结束后容器和临时目录（`/tmp/w2w-appstore-shots`）、模拟器状态栏覆盖、派生数据（`/tmp/w2w-appstore-dd`，`KEEP_DD=1` 保留）都会删掉。
- **合成数据的几个细节**：时间假装成当天 10:00（午夜后跑的话昨晚的睡眠还没有，三环会是空的）；服务器的上线开关（rollout flags）按发布后"所有人都开"的状态打开 UI 相关的那批（`appstore_demo_server.py` 的 `UI_FLAGS`），否则会显示旧版布局（比如睡眠详情页是关着的）；`quickstart/demo_sparky.py` 自己不提交事务，脚本给它加了 autocommit。
- **不改 app 代码**：用的全是 Debug 版已有的启动环境变量（`W2W_DEV_SERVER` / `W2W_DEV_TOKEN` / `W2W_DEV_LANG` / `W2W_DEV_TAB` / `W2W_DEV_SLEEP` / `W2W_DEV_RECORD` / `W2W_DEV_CIRCLES` / `W2W_JARVIS_DEMO`，见 `apps/ios/README.md`）；Release 版里这些开关不存在。
- **Jarvis 一屏**用 `fake_jarvis_server.py`（不调任何 AI，回答是写死的示例），其余屏用真的 Body Engine。
- 状态栏统一成 9:41、满电、满格；浅色模式；语言用启动参数 `-AppleLanguages`，加上 `W2W_DEV_LANG`。
- 屏幕顺序（按故事排）：01 今天（三环）、02 睡眠、03 趋势、04 训练、05 Jarvis、06 圈子动态、07 排行榜、08 饮食、09 日记。上传时 Apple 最多 10 张，可以删减；每张图上要不要加说明文字、要不要套手机边框（做图工具）由负责人定，脚本只出原始截图。
- 截图里显示的 app 名字是 `W2W_DISPLAY_NAME`；设了 `APP_NAME` 只覆盖这次构建的显示名。
- 需要：Xcode 26 + XcodeGen 2.46.0、Docker（起 Postgres）、`uv`、`python3`；磁盘剩余 < 15G 会拒绝构建（`docs/ios-native-app.md` 第 10 节）。

**2026-11-01 在本机跑过一次**（iPhone 17 Pro Max 模拟器 iOS 26.2，构建 + 起服务约 8 分钟，截图约 4 分钟）：中英文各 9 张 1320×2868 的 PNG 全部生成，内容是合成数据——今天（三环）、睡眠详情、趋势、训练（肌肉负荷）、Jarvis（假服务器）、圈子动态、排行榜、饮食、日记。已知的小问题：圈子动态那一张的页眉偶尔压到状态栏（首次进入朋友 tab 时的布局抖动，没有改 app 代码，需要的话手动重拍或等 app 修）；Jarvis 一张的回答是写死的示例；饮食页只有今天的早餐、喝水 0。

## 清单：已经好了 / 等负责人

**已经好了**

- [x] 中英文商店文字（副标题、宣传文字、描述、关键词、2.0.0 更新说明），字数检查通过，无竞品商标，无医疗宣称。
- [x] 类别、年龄分级、App 隐私、HealthKit 类型的答案（`app-store-connect-answers.md`）。
- [x] App Review 备注模板（`review-notes-template.md`）。
- [x] 截图流水线（合成数据）。

**依赖别的 PR**（合并后回来核对）

- [ ] PR #381（隐私和合规）：隐私政策 / 使用条款网页、`PrivacyInfo.xcprivacy`、删除账号时撤销 Apple 授权。`app-store-connect-answers.md` 第 3 节照它写的，合并后核对一次。
- [ ] 圈子审核（举报 / 屏蔽，另一个 PR）：合并后核对 `review-notes-template.md` 第 6 节和 `app-store-connect-answers.md` 第 2 节的准则 1.2 四项。
- [ ] 登录页 / 引导 / 账号相关的另外几个 PR：合并后重跑截图（登录页不在截图里，但"我"页等可能变），核对审核备注第 2 节的登录步骤。

**等负责人决定**

- [ ] **上架名**（不能含 WHOOP）：定了之后设 `APP_NAME`，再设 `BODY_LEGAL_APP_NAME`（隐私政策里的名字），Bundle ID 不变；`W2W_DISPLAY_NAME` 改成新名字。商店名字不能被占用，先在 App Store Connect 里预占。
- [ ] **开发者账号 / 主体**：用个人还是公司主体上架（决定"卖家"名、`BODY_LEGAL_ENTITY`、中国大陆区要不要上）。App Store 的健康类 app 需要隐私政策网址、支持网址，主体和联系方式要对得上。
- [ ] **上架地区**：中国大陆区需要 App 备案（ICP），备用 AI 在中国的服务器、Jarvis 可能还牵涉生成式 AI 服务的备案，要和有资质的人确认；其他地区看当地对健康数据的要求（欧盟 / 英国 GDPR、澳大利亚隐私法等）。没确认前先只选能确认的地区。
- [ ] **JS 算法包的许可证**：`packages/openbodyscore-js/package.json` 现在写的是 AGPL-3.0-only，一般认为 App Store 的条款和 AGPL / GPL 不兼容，要改成 MIT / Apache-2.0 之类，或者另做授权；`review-notes-template.md` 第 4 节和 `licenses.json` 都依赖这个决定（PR #381 写了同样的问题）。
- [ ] **下载 JS 的风险**：准则 2.5.2 允许的前提是只做计算、不改变 app 用途、不绕过签名；备注第 4 节是这么写的，措辞请负责人（最好有法务）确认。被拒的话退路是只用 app 内置的算法包、更新算法就发新版本。
- [ ] **App 内购买（IAP）**：app 里现在没有购买入口；将来如果在 app 里卖额度 / 订阅，必须走 IAP（准则 3.1.1），或者留在网页端、app 里不出现任何价格和购买入口。要不要做、怎么做先定。
- [ ] **审核演示账号**：app 没有账号密码，审核员收不到邮箱验证码。要在私有 ops 里建一个合成数据的演示账号（令牌或设置链接，长期有效），并考虑是否专门做一个对审核更友好的入口。令牌只放私有仓库。
- [ ] **支持网址 / 营销网址 / 联系方式**：隐私政策第 10 节（`BODY_LEGAL_CONTACT`）、App Store Connect 的支持网址（必须能打开），以及圈子举报的受理人和"24 小时内处理"这个承诺能不能做到。
- [ ] **年龄分级**：计算结果预计 13+，隐私政策写的最低年龄是 16，要不要用覆盖选项选 16+（`app-store-connect-answers.md` 第 2 节）。
- [ ] **截图的呈现**：要不要加说明文字 / 手机边框、要几张、哪几张放最前；英文版要不要上（商店本地化是否只做中英）。
- [ ] **隐私标签里的"产品交互 → 分析"**：对应按用户的使用监测（负责人 2026-10 的决定）；确认标签和隐私政策"使用情况"一条一致。
- [ ] **发布方式**：手动发布；TestFlight 验收完成后才提交审核（`docs/ios-native-app.md` 第 12 节的上线顺序）。

## 参考

- App Store Connect 帮助：截图规格、年龄分级问卷、App 隐私、App 审核准则（5.1.1 数据收集和删除账号、5.1.2(i) 向第三方 AI 共享个人数据、5.1.3 健康数据、1.2 用户生成内容、1.4.1 医疗、2.5.2 下载代码、3.1.1 内购）。
- 项目内：`docs/ios-native-app.md`（架构、算法包签名第 6 节、上线顺序第 12 节）、`apps/ios/CHANGELOG.md`、`docs/legal/`（PR #381 合并后）。
