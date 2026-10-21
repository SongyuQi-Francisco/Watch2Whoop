<!-- Project name appears only in this title and in the clone commands below (git clone …/Watch2Whoop.git), so a rename only touches those lines. -->
# Watch2Whoop

**Your body's data layer for AI.** A self-hosted scaffold any AI (Claude, ChatGPT, Codex, Gemini, DeepSeek …) reads and writes through MCP. Your data stays; the AI is swappable.

**给 AI 用的身体数据层。** 自托管的脚手架，任何 AI（Claude、ChatGPT、Codex、Gemini、DeepSeek……）都能通过 MCP 读写。数据留在你手里，AI 随时可以换。

<p align="center"><img src="docs/images/readme/hero.png" alt="Today, trends, sleep and profile screens with synthetic demo data" width="100%"></p>

[English](#english) · [中文](#中文) · [Quick Start](#quick-start) · [License](#license)

> [!IMPORTANT]
> Not affiliated with WHOOP, Inc. or Apple Inc.; their names are trademarks, used only to say what this works with. Not a medical device, no medical advice. Status: **alpha**, Apple Watch first.
>
> 与 WHOOP、Apple 无关（名称是它们的商标，只用来说明配合什么使用）。不是医疗器械，不提供医疗建议。状态：**alpha**，先支持 Apple Watch。

---

## English

> [!NOTE]
> **You need an AI.** There are no input forms: you log by talking to your own AI (a subscription through MCP, or an API key). Hosting for family without an AI? Configure a fallback API key on the server.

### Why
- **Your data, accumulated** — watch data, food, training, labs, journal and the AI's notes, structured on your own server.
- **Bring any AI, swap any time** — tools, memory and history live in your database, not in the model.
- **No forms** — send text, a screenshot or a photo; the AI logs it.
- **Real numbers** — recovery, strain, sleep debt, body age and stress computed from raw watch data against your own baselines.

Features, architecture and WHOOP comparison: [docs/README.md](docs/README.md) · Tester handbook: [English](docs/tester-handbook.en.md) · [中文](docs/tester-handbook.md)

### Quick Start
Needs Docker (Compose v2) and `python3` ≥ 3.9. Runs locally with 60 days of synthetic data.
```bash
git clone https://github.com/<owner>/Watch2Whoop.git
cd Watch2Whoop
./quickstart.sh            # prints a login link for the Today page
```
Ports, options and commands: [quickstart/README.md](quickstart/README.md). Real self-hosting: [docs/README.md#self-hosting](docs/README.md#self-hosting).

### Repository
| Path | What |
|---|---|
| [`body_engine/`](body_engine/README.md) | Server: scores, records, rules, AI tools, MCP, Today page |
| [`apps/watch2whoop/`](apps/watch2whoop/README.md) | iOS app (Flutter) |
| [`shortcuts/`](shortcuts/README.md) | iOS Shortcut sync (no app needed) |
| [`quickstart/`](quickstart/README.md) | One-command local demo |
| `deploy/`, `assistant/` | Deployment, ops, AI coach workspace |
| [`docs/`](docs/README.md) | Design documents |
| [`docs/admin-guide.md`](docs/admin-guide.md) | Admin guide (Chinese): accounts, access links, AI, backups, troubleshooting |

### Roadmap
- [x] Apple Watch: recovery, strain, sleep, body age, stress, coaches, reports
- [x] One-command local Quick Start
- [x] **Training module v2**: bilingual exercise library, 3 / 4 / 5-day splits, weekly plans, readiness autoregulation and safe exercise swap, built by talking to your AI ([design](docs/training-v2-plan.md))
- [ ] iOS app on TestFlight
- [ ] English docs and UI
- [ ] One-command Linux VPS installer
- [ ] RMSSD-based recovery (iOS 27 / Open Wearables 0.10)
- [ ] **Other watches**: Garmin, Oura, Polar, Huawei, Xiaomi, Android Health Connect
- [ ] Shopping list, cycle tracking, more languages
- [x] **Self-service sign-up** (e-mail code or Sign in with Apple) gated by admin-issued invite codes during the beta ([design](docs/self-signup-plan.md))
- [x] **Training module v2** (first version): exercise library, 3 / 4 / 5-day splits, weekly plans, day-level changes, built by talking to your AI ([plan](docs/training-v2-plan.md))
- [ ] *In progress*: **Friends page**: family / friend circles, today + 7-day status of everyone you share with, leaderboards against your own baseline ([plan](docs/friends-social-plan.md))

### Contributing
Every change goes through a pull request from your own branch; CI must pass and a maintainer reviews. Want to bring this to **your** watch? Start with [CONTRIBUTING.md](CONTRIBUTING.md).

### License
| Part | Licence |
|---|---|
| Server and everything else | [AGPL-3.0](LICENSE) |
| iOS app `apps/watch2whoop/` | [MIT](apps/watch2whoop/LICENSE) |
| iOS Shortcut `shortcuts/` | [MIT](shortcuts/LICENSE) |

Third-party services (Open Wearables, SparkyFitness, Grafana) keep their own licences: [docs/LICENSES.md](docs/LICENSES.md).

---

## 中文

> [!NOTE]
> **需要一个 AI。** 没有输入表单，记录靠跟你自己的 AI 对话（通过 MCP 用订阅，或用 API key）。给没有 AI 的家人用？在服务器上配一个托底 API key。

### 为什么
- **数据沉淀**：手表数据、饮食、训练、化验、日记和 AI 的笔记，结构化存在你自己的服务器上。
- **AI 随便换**：工具、记忆和历史在你的数据库里，不在模型里。
- **没有表单**：发文字、截图或照片，AI 帮你记。
- **真实的数字**：恢复、负荷、睡眠债、身体年龄、压力，用手表原始数据和你自己的基线算。

功能、架构和 WHOOP 对照：[docs/README.md](docs/README.md) · 测试用户手册：[中文](docs/tester-handbook.md) · [English](docs/tester-handbook.en.md)

### 快速开始
需要 Docker（Compose v2）和 `python3` ≥ 3.9。在本机用 60 天合成数据跑起来。
```bash
git clone https://github.com/<owner>/Watch2Whoop.git
cd Watch2Whoop
./quickstart.sh            # 最后打印今天页的登录链接
```
端口、参数和命令：[quickstart/README.md](quickstart/README.md)。正式自托管：[docs/README.md#self-hosting](docs/README.md#self-hosting)。

### 目录
| 路径 | 内容 |
|---|---|
| [`body_engine/`](body_engine/README.md) | 服务器：评分、记录、规则、AI 工具、MCP、今天页 |
| [`apps/watch2whoop/`](apps/watch2whoop/README.md) | iOS app（Flutter） |
| [`shortcuts/`](shortcuts/README.md) | iOS 快捷指令同步（不用装 app） |
| [`quickstart/`](quickstart/README.md) | 一条命令的本地演示 |
| `deploy/`、`assistant/` | 部署、运维、AI 管家工作目录 |
| [`docs/`](docs/README.md) | 设计文档 |
| [`docs/admin-guide.md`](docs/admin-guide.md) | 管理员手册：开户、发链接、AI、备份、排错 |

### 路线图
- [x] Apple Watch：恢复、负荷、睡眠、身体年龄、压力、教练、报告
- [x] 一条命令的本地快速开始
- [x] **训练模块 v2**：双语动作库、3 / 4 / 5 分化、每周计划、恢复分自调节与同模式安全换动作，跟 AI 对话来建（[方案](docs/training-v2-plan.md)）
- [ ] iOS app 上 TestFlight
- [ ] 英文文档和界面
- [ ] Linux 云服务器一键安装
- [ ] 基于 RMSSD 的恢复分（iOS 27 / Open Wearables 0.10）
- [ ] **其他手表**：Garmin、Oura、Polar、华为、小米、安卓 Health Connect
- [ ] 采购清单、生理周期、更多语言
- [x] **自助注册**（邮箱验证码或通过 Apple 登录），内测期凭管理员批量发的激活码开通（[设计](docs/self-signup-plan.md)）
- [x] **训练模块 v2**（第一版）：动作库、3 / 4 / 5 分化、每周计划、按天调整，跟 AI 对话来建（[方案](docs/training-v2-plan.md)）
- [ ] *开发中*：**朋友页**：家庭 / 好友圈子，一屏看到共享的人今天和最近 7 天的状态，和自己基线比的排行榜（[方案](docs/friends-social-plan.md)）

### 参与贡献
所有改动都从你自己的分支提 Pull Request，CI 通过、维护者审核后合并。想把本项目带到**你的**手表上？先看 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 许可证
| 部分 | 许可证 |
|---|---|
| 服务器和其余全部 | [AGPL-3.0](LICENSE) |
| iOS app `apps/watch2whoop/` | [MIT](apps/watch2whoop/LICENSE) |
| 快捷指令 `shortcuts/` | [MIT](shortcuts/LICENSE) |

第三方服务（Open Wearables、SparkyFitness、Grafana）保持各自的许可证：[docs/LICENSES.md](docs/LICENSES.md)。
