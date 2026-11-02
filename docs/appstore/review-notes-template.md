# App Review 备注模板

> 用法：App Store Connect →「App 审核」→「备注」整段粘贴下面 `---` 之间的英文（审核员读英文），把 `{{…}}` 换成真值。
> **演示账号、登录码、令牌、联系人电话邮箱只放私有仓库（ops）里，绝不写进这个公开文件。** 这里只有占位符。
> 圈子审核（举报 / 屏蔽）写成"另一个 PR 在加"：那个 PR 合并后，核对第 6 节每一句和 app 里的真实行为一致再提交。
> 中文摘要放在文末，给负责人看。

---

```
{{APP_NAME}} — notes for App Review (version {{VERSION}})

1. WHAT THE APP IS
{{APP_NAME}} turns Apple Watch / Health data into daily recovery, sleep and strain scores, lets the user log training,
food, weight and journal, and lets the user optionally talk to an AI ("Jarvis") about their own data. It also has
"circles" (a small friends group with a workout feed and a leaderboard). It is NOT a medical device and makes no
diagnosis or treatment claims; every AI answer is labelled "AI-generated" and links to a "not medical advice" sheet,
and status labels that could be read as a health warning say "not a diagnosis".

2. HOW TO SIGN IN (no password, no payment, no in-app purchase)
The app has no username/password screen. For review please use the demo account below; it holds synthetic data only.
  - Open the app → on the sign-in screen tap "Sign in with a token (advanced)" → server address: {{SERVER_URL}} → account token: {{REVIEW_TOKEN}}
  - Alternative: on the sign-in screen choose "Setup link or activation code" and paste: {{REVIEW_SETUP_LINK}}
The demo account has already accepted the consent form. Real users sign in with Sign in with Apple or an e-mail code.
If the demo account is expired or you cannot sign in, contact {{REVIEW_CONTACT}} and we will renew it within hours.
The demo account is not tied to any real person; its Health-style data is generated.

3. WHY THE APP ASKS FOR THESE PERMISSIONS
- Health (read only): heart rate, resting heart rate, heart rate variability and beat-to-beat intervals, ECG recordings,
  sleep, workouts, steps, energy, respiratory rate, blood oxygen, wrist temperature, VO2 max, weight and similar. They are
  the inputs of the recovery / sleep / strain scores and of the trend charts. Release builds never write to Health.
  Health data is not used for advertising or marketing, is not sold and is not stored in iCloud (guideline 5.1.3).
  ECG waveforms and beat-to-beat data are never sent to any AI.
- Background modes (fetch, processing, remote-notification) and HealthKit background delivery: the Apple Watch writes new
  data to Health during the day; background delivery wakes the app so that data is synced to the user's account and the
  scores are updated without the user opening the app. The background tasks are the sync of new samples, the one-off backfill
  of older history (only on Wi-Fi + power, or when the user allows cellular) and the refresh of scores. The silent push
  (remote-notification) only tells the app that new data is waiting on the server; it carries no content. No location,
  audio or VoIP background mode is used.
- Camera: scanning the setup QR code; taking a photo for the AI or a profile picture, only when the user chooses.
- Microphone and Speech Recognition: only while the user holds the Jarvis button; recognition runs on the iPhone
  (requiresOnDeviceRecognition); for Chinese the recording can also be sent to the user's server for a better transcript and
  is deleted right after.
- Notifications: circle likes / comments and reminders.
- Sign in with Apple: one of the sign-in methods; when the user deletes the account we revoke the Apple authorisation.

4. DOWNLOADED JAVASCRIPT (guideline 2.5.2)
The app calculates its scores with a small JavaScript library ("algorithm bundle"). A copy is built into the app; the
server may offer a newer version of the same calculations. The downloaded file is plain data that is only ever evaluated in
the system JavaScriptCore framework, in a context without DOM, timers, network or any native bridge: it takes numbers in and
returns numbers out (scores, trend values). It cannot add screens or features, change the app's purpose, or reach any
system API. Before use the app checks the file's SHA-256 and an Ed25519 signature against public keys that ship inside the
app (Algorithms/BundleVerifier.swift); a file that fails either check is discarded and the built-in copy keeps running, and
release builds also refuse an unsigned file. The source of the library is public: packages/openbodyscore-js in the project
repository. We read this as the interpreted-code exception in the Developer Program License Agreement (section 3.3.2).

5. AI PROVIDERS (guideline 5.1.2(i))
AI is OFF by default. The user turns it on under Me → AI settings by choosing one of:
  a) their own AI account: their own API key (stored encrypted on the server, only the last 4 characters are shown), or an AI
     client of their own (for example Claude or ChatGPT) connected through MCP with a token the user creates;
  b) the backup AI provided by the service (DeepSeek, servers in China). The app asks for a separate, explicit consent before
     the first use, listing exactly which data fields are sent; it can be withdrawn at any time under Me → AI settings.
Only when the user starts using Jarvis, the part of their data that an answer needs is sent to the AI they chose. Anything
that deletes or undoes a record is confirmed with the user first. The privacy policy (section 4) lists the same facts.
In the demo account the AI is not connected, so Jarvis shows its set-up state; the other screens work with the sample data.

6. CIRCLES / USER-GENERATED CONTENT (guideline 1.2)
Circles are small, invitation-only groups (an invite code or link is needed to join); there is no public feed and no
search for strangers. Members see only what they ticked when joining and can leave any time. In a circle users can post
the automatic workout summary, like and comment. Moderation:
  - Report: posts, comments and member profiles (name / avatar) can be reported from their "⋯" / long-press menu with a
    reason. The reported item disappears for the reporter at once; after reports from several different people it is
    hidden for everyone until the operator reviews it. The operator reviews reports within 24 hours and keeps the item,
    removes it, or removes the member.
  - Block: any member can be blocked from a post, comment or member card; the blocked person's posts, comments, likes and
    leaderboard entries disappear for the blocker on the server, across all circles. Unblock in Circles → Settings → Blocked.
  - Objectionable content: a word filter rejects matching comments, circle names and display names; users accept the
    community rules before creating, joining or commenting in a circle for the first time; the circle owner can remove
    members; comments are plain text, limited to 300 characters.
  - Contact: {{SUPPORT_CONTACT}} (also in the privacy policy, section 10).

7. ACCOUNT DELETION (guideline 5.1.1(v))
Me → Delete account → type to confirm. It deletes the account and all data on the server immediately (old copies in backups
disappear within 6 months), signs the device out, removes the local database and, for Sign in with Apple, revokes the app's
authorisation at Apple. Export of the data: by request through the contact in the privacy policy.

8. OTHER
- No ads, no tracking (no App Tracking Transparency prompt), no third-party analytics SDK, no in-app purchases.
- The app is for people {{MIN_AGE}} and over (stated in the privacy policy and terms of use).
- Privacy policy: {{SERVER_URL}}/legal/privacy · Terms of use: {{SERVER_URL}}/legal/terms · Open-source licences: Me → About.
- The project is open source (MIT for the app); the server is run by {{ENTITY}}; users can also run their own server.
- Medical disclaimer: the scores are estimates for everyday awareness. Their methods and sources are in the open-source code
  (packages/openbodyscore, body_engine scores/params). The app does not diagnose, treat or prevent any disease.
```

---

## 中文摘要（给负责人）

1. **登录**：app 没有账号密码页，审核员没法收邮箱验证码。所以要在私有 ops 仓库里用 `review_demo.py` 建一个**合成数据**的演示账号，把"用 token 登录（高级）"或设置链接填进第 2 节。要不要给审核专门做一个更友好的入口（比如固定验证码的演示账号），是待负责人决定的事（见 `README.md`）。
2. **权限和后台**：第 3 节每条理由都对应 `project.yml` / `Info.plist`；改了权限或后台模式要同步改这里。
3. **下载的 JS**（第 4 节）：这是被拒风险最大的一条。依据是 Apple 开发者计划许可协议 3.3.2 里"解释型代码"的例外，措辞要负责人（最好有法务）确认；JS 包的许可证（`package.json` 现在写 AGPL-3.0-only）和 App Store 的兼容性还没定，见 `README.md`。
4. **AI**（第 5 节）：和 `docs/legal/` 隐私政策第 4 节一致；备用 AI 在中国的服务器，是审核最可能追问的点。
5. **圈子**（第 6 节）：举报 / 屏蔽合并后逐句核对；24 小时内处理是**对 Apple 的承诺**，负责人要确认做得到才保留这句。
6. **占位符**：`APP_NAME`、`VERSION`、`REVIEW_TOKEN`、`REVIEW_SETUP_LINK`、`REVIEW_CONTACT`、`SUPPORT_CONTACT`、`SERVER_URL`、`ENTITY`、`MIN_AGE`。
