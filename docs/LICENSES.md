# Licences

[English](#english) · [中文](#中文)

## English
This project is licensed per directory:

| Part | Licence | File |
|---|---|---|
| Everything except the two directories below: Body Engine (`body_engine/`), deployment and ops (`deploy/`), Shortcut receiver (`health_sync/`), AI coach workspace (`assistant/`), Quick Start, tools, docs | **GNU AGPL-3.0** | [`LICENSE`](../LICENSE) |
| iOS app (`apps/watch2whoop/`) | **MIT** | [`apps/watch2whoop/LICENSE`](../apps/watch2whoop/LICENSE) |
| iOS Shortcut and its checker (`shortcuts/`) | **MIT** | [`shortcuts/LICENSE`](../shortcuts/LICENSE) |

What AGPL-3.0 means here: you may use, self-host, modify and share the server freely; if you run a **modified** version as a network service for other people, you must offer them its source code. The app and the Shortcut are MIT so they can be distributed through the App Store / TestFlight and reused anywhere.

Third-party components are **not part of this repository's code** and keep their own licences:
- **Open Wearables** — MIT. Pinned git submodule (`vendor/open-wearables`) and official images.
- **SparkyFitness** — its author's own **non-commercial** licence. It runs as a separate, **unmodified** service from the official images, and this project talks to it only through its HTTP API; none of its code is in this repository. Self-hosting for yourself and your family is fine; any commercial use has to respect (or replace) SparkyFitness, which our licence cannot change.
- **Grafana OSS** — AGPL-3.0, optional, a separate unmodified service; the dashboards and provisioning files here are our own.
- **Jarvis button icon** — "sparkles" from [Tabler Icons](https://tabler.io/icons), MIT.
- PostgreSQL, Redis, SeaweedFS and the Python / Dart libraries are used under their own (permissive or LGPL / AGPL-compatible) licences.

Contributions are accepted under the licence of the directory they change (inbound = outbound, no CLA); see [CONTRIBUTING.md](../CONTRIBUTING.md).

**Trademarks.** This is an independent open-source project, **not affiliated with, endorsed by, sponsored by, or connected to WHOOP, Inc. or Apple Inc.** WHOOP is a trademark of WHOOP, Inc.; Apple Watch, HealthKit and iPhone are trademarks of Apple Inc. The names are used only to describe what the project works with. It is **not a medical device** and does not provide medical advice.

## 中文
按目录分开授权：
- **服务器部分用 GNU AGPL-3.0**（根目录 [`LICENSE`](../LICENSE)）：除下面两个目录以外的全部内容，包括 Body Engine（`body_engine/`）、部署和运维（`deploy/`）、快捷指令接收器（`health_sync/`）、AI 管家工作目录（`assistant/`）、Quick Start、tools、docs。可以自由使用、自托管、修改和分享；如果把**改过的**版本作为网络服务提供给别人用，就必须向这些用户提供源代码。
- **iOS app（`apps/watch2whoop/`）和快捷指令（`shortcuts/`）用 MIT**（各自目录下的 `LICENSE`），方便通过 App Store / TestFlight 分发和在任何地方复用。

第三方组件**不属于本仓库的代码**，保持各自的许可证：Open Wearables 是 MIT（钉版本的 submodule 和官方镜像）；SparkyFitness 是作者自定义的**非商业**许可，作为**未修改**的独立服务（官方镜像）运行，本项目只通过它的 HTTP API 交互，代码不在本仓库——自己和家人自托管没问题，商业用途必须遵守（或替换掉）SparkyFitness 的许可，我们的许可证改变不了这一点；Grafana OSS 是 AGPL-3.0，可选、未修改的独立服务，这里的看板和配置文件是我们自己写的；PostgreSQL、Redis、SeaweedFS 以及 Python / Dart 依赖按各自的许可证使用。

贡献按所改目录的许可证授权（inbound = outbound，不需要签 CLA），见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

**商标。** 本项目是独立的开源项目，与 WHOOP, Inc. 和 Apple Inc. **没有任何关联**，未获其认可或赞助。WHOOP 是 WHOOP, Inc. 的商标；Apple Watch、HealthKit、iPhone 是 Apple Inc. 的商标，这里只用来说明本项目和什么配合使用。本项目**不是医疗器械**，不提供医疗建议；健康问题请咨询医生。
