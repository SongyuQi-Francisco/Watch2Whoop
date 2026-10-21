# 灰度发布和 A/B 测试

新功能先部署到正式环境、默认关着，只对内测分组打开，在线验收通过再全量打开；不通过就关掉（或回滚代码）。A/B 测试给同一批人稳定地分到不同的版本，统计各版本的人数和曝光。

代码在 `body_engine/src/body_engine/rollout/`，迁移 `530_rollout.sql`。**代码里不写任何具体用户**：内测名单在数据库（`user_cohorts`），开关状态也在数据库（`feature_flags`），都用命令改，每次修改都写进审计日志（`rollout_audit`）。

## 1. 概念

| 名词 | 是什么 | 在哪 |
|---|---|---|
| 功能开关 | 一个 key（例如 `new_food_page`）+ 状态 | key 在代码 `rollout/registry.py` 里声明，状态在表 `feature_flags` |
| 状态 | `off` 谁都关；`cohort` 只对分组打开；`percent` 按百分比 + 分组；`all` 全开 | `flags set` |
| 分组 | 一组用户，例如 `beta`（内测） | 表 `user_cohorts`，`cohort add / remove / list` |
| 同意书最低版本 | 开关可以要求用户先同意某个版本的同意书 | `flags set --min-consent` |
| 同意书基础版本 | 所有人都必须确认的版本；不设置时就是 `consent.md` 里的版本（和以前一样） | `flags consent-base` |
| 实验 | A/B 测试：变体和比例、可选分组、起止时间 | 表 `experiments`，`experiments start / stop` |
| 曝光 | 谁、哪天（用户本地日期）、看到了哪个变体、几次；不记内容 | 表 `experiment_exposures` |

### 判断规则
`flags.enabled(conn, user_id, key)` 按顺序判断：

1. `all` → 开；
2. `percent` → 用户的桶号（`sha256("flag:" + key + ":" + user_id)`，0–100，同一个人、同一个 key 永远不变，不同 key 互相独立）小于百分比 → 开；
3. `cohort` 或 `percent` → 用户在开关的分组里（默认 `beta`）→ 开（所以放量期间内测用户一直在里面）；
4. 其余 → 关。

命中以后，如果开关有 `min_consent_version`、用户还没同意到那个版本，功能**仍然关着**，今天页的同意书弹层会请他确认；确认后立刻打开。

开关状态和分组成员每个进程缓存 30 秒（`CACHE_TTL_S`）：用命令改了以后，正在跑的服务最多 30 秒后生效。

### 同意书跟着开关走
用户要确认的版本 = max(基础版本, 他命中的开关要求的版本)，但不会比 `consent.md` 本身更新（`profile/consent.required_version`）。版本按「日期 + 序号」比较（`2026-10-18.10` 在 `.9` 之后）。

- 没设基础版本、也没有开关要求时，和以前完全一样：同意过旧版本的人会被请求重新确认。
- 新同意书先只给内测用户：把基础版本固定在旧版本，新版本挂在开关上，开关对 `beta` 打开 → 只有内测用户被请求确认；开关改成 `all` → 所有人都被请求确认。
- 纸质同意（`paper`）不会在 app 里被要求重新确认。

## 2. 写一个带开关的新功能（开发者）

1. 在 `rollout/registry.py` 的 `FLAGS` 里加 key 和一句说明。没声明的 key，`flags.enabled` 会直接报错（打错字在测试里就能发现）。
2. **接口**：被开关管的接口加依赖，关着时返回 404（不只是隐藏入口）：
   ```python
   from ..rollout import flags

   @router.get("/v1/users/{user}/new-thing", dependencies=[Depends(flags.require("new_thing"))])
   ```
   其他后端代码用 `flags.enabled(conn, user_id, "new_thing")`，或者 `flags.guard(...)`（关着时抛 `FlagOff`）。AI 工具的处理函数里也用 `guard`。
3. **今天页**：`/v1/web/me` 里有 `flags`（这个人打开的 key）和 `experiments`（{实验 key: 变体}）。页面上用 `window.BodyFlags.on("new_thing")` 决定画不画入口。app 原生页用 `GET /v1/users/me/flags` 拿同样的数据。
4. **实验**：页面渲染时调 `window.BodyFlags.variant("exp_key")`。它返回变体（`null` = 不在实验里，画原来的页面），每次页面加载记一次曝光（`POST /v1/users/me/experiments/{key}/exposure`，变体由服务器算，客户端传什么都不存）。后端代码用 `experiments.variant(conn, user_id, key, expose=True)`。
5. 测试里用 `monkeypatch.setitem(registry.FLAGS, ...)` 声明测试用的 key（见 `tests/test_rollout.py`）。
6. 功能全量稳定后，在后续 PR 里删掉开关判断和 `FLAGS` 里的 key。

## 3. 上线新功能的标准流程（管理员）

命令在服务器上通过 engine 包装脚本运行（和其他 `body-engine` 命令一样）。`--by 名字` 记进审计日志。

1. **部署**：新代码合并、部署。新开关的数据库行第一次被列出或设置时才会创建，状态是 `off`，所以部署本身不会打开任何东西。
   ```bash
   body-engine flags list
   ```
2. **确认内测名单**（只需要做一次，以后按需增减）：
   ```bash
   body-engine cohort add --user-id 1 --user-id 2 --cohort beta --by 管理员
   body-engine cohort list --cohort beta
   ```
3. **对内测打开**：
   ```bash
   body-engine flags set new_thing --state cohort --cohort beta --by 管理员
   ```
   如果这个功能带新同意书（`consent.md` 已更新到新版本 V2，旧版本是 V1）：**部署前**先固定基础版本，再在开关上要求新版本：
   ```bash
   body-engine flags consent-base --set V1 --by 管理员      # 部署新同意书之前做，其他人不会被打扰
   body-engine flags set new_thing --state cohort --min-consent V2 --by 管理员
   ```
4. **在线验收**：内测用户在正式环境里用；需要的话打开对应的实验看分布。
5. **结果**：
   - 通过 → 全量（或者先放一部分）：
     ```bash
     body-engine flags set new_thing --state percent --percent 20 --by 管理员   # 可选：先放 20%，内测用户一直在里面
     body-engine flags set new_thing --state all --by 管理员
     body-engine flags consent-base --clear --by 管理员                        # 新同意书全量以后，基础版本回到默认
     ```
   - 不通过 → 关掉，接口立即 404、入口消失（缓存最多 30 秒）：
     ```bash
     body-engine flags set new_thing --state off --by 管理员
     ```
     问题严重时再回滚代码。开关关着的时候回滚代码不影响任何人。
6. **审计**：`body-engine flags audit` 看最近谁改了什么（改前 / 改后）。

## 4. A/B 测试

```bash
body-engine experiments start home_layout --variants control:50,cards:50 --cohort beta --days 14 --description "首页卡片布局" --by 管理员
body-engine experiments results home_layout     # 每个变体的人数、曝光次数
body-engine experiments stop home_layout --by 管理员
body-engine experiments list
```

- 分桶：`sha256("exp:" + key + ":" + user_id)` 映射到 0–100，再按变体的比例切开；同一个人在同一个实验里永远是同一个变体，不同实验互相独立，也和开关的桶无关。
- 一个实验已经有曝光以后，不能再改它的变体或比例（会把人重新分组）：换一个新 key。
- 只统计人数和曝光。指标对比（例如活跃天数）的接口留好了：`experiments.METRICS` 里注册 `fn(conn, key, {变体: [user_id…]}) -> {变体: 数值}`，`results` 会一起算出来。等活跃度统计（`account/stats.py`）接进来再加。

## 5. 表

| 表 | 内容 |
|---|---|
| `feature_flags` | key、说明、状态、分组、百分比、需要的最低同意书版本、时间、谁改的 |
| `user_cohorts` | user_id、分组、加入时间、谁加的 |
| `experiments` | key、说明、变体和比例（JSON）、分组、状态 running / stopped、起止时间 |
| `experiment_exposures` | 实验、用户、本地日期、变体、次数、首次 / 最近时间 |
| `rollout_settings` | 例如 `consent_base_version` |
| `rollout_audit` | 每次修改：时间、谁、动作、对象、改前、改后 |

都不是健康数据，没有授权给 `grafana_ro`。
