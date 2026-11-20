# 手机「影子对比」结果上报到服务器：方案（只写方案，未实现）

> 状态：草稿，等负责人选项。来源：2026-11 项目复盘的「多副本漂移」一节。相关：[`ios-native-2.0.md`](ios-native-2.0.md) 第 3.4 节（影子对比的两层）、`packages/openbodyscore-js/README.md`。

## 1. 现在的情况

本机算分（local-first，#337）有两层对比，只有一层服务器看得到：

| 层 | 比什么 | 结果存在哪 | 服务器能看到吗 |
|---|---|---|---|
| **上传抽查** | 手机上传的数据行（`PUT /v1/users/{user}/derived/app`、`scores/app`：daily、sleep、workouts、stress、healthspan、reminders） | 表 `app_compute_checks`（迁移 990，只存 ok / 超差的字段名，不存数值） | 能 |
| **页面对比** | 手机自己算的**页面**（训练日历 / 当天、趋势 series、coach、explain、周报 / 月报、规则）和服务器发来的同一页面文档 | 手机本地 `meta`：`workouts.compare`、`series.compare`、`coach.compare`、`explain.compare`、`reports.compare`、`rules.compare`（`{ok, fields_over, compared, key, at}`，例如 `TrainingLocal.record`） | **不能** |

#444 的漂移就出在第二层：手动训练强度只改了 Python 的 `training/history.py`（页面层），没改 `31_workouts.js` 的 `trainingCalendar` / `trainingDay`。上传的 `workoutDays` 行没变，所以抽查一直是绿的；手机已经记下「不一致」，但只记在手机上。

## 2. 约束（方案都要满足）

- **只传字段路径，不传数值**（第 3.4 节、同意书）：上报内容和 `app_compute_checks` 一样，是 `ok` 和超差的字段名，例如 `days[3].sessions[1].intensity_basis`。数组下标可以去掉，只留 `days[].sessions[].intensity_basis`，这样也不暴露某天练了几次。
- **不增加用户流量**：不为对比单独发请求；或者每台手机每天最多 1 个，几百字节。
- **按用户鉴权**：只能写自己的（`_self(viewer, "me")`、write token）；任何人都读不到别人的。
- **谁能看**：只有管理员，通过 CLI 和 doctor 看汇总（按 `bundle_version × view` 统计失败率）。用户本人不新增界面（同步中心以后可以加「本机计算一致」一行，不在这次范围内）。研究或调参要看数值的，仍然只用用户 1 和合成数据（`native-compare`、`BODY_RESEARCH_USERS`）。
- **有关闭开关和退出条件**（项目规则「每次迁移都有退出 issue」）：全部页面切到 primary、连续 N 周没有失败之后，删掉手机端的页面对比和这条上报。
- **同步代码不吞错**：上报失败也要留状态，下次上传时一起带上（项目规则）；新的上传通道要登记到健康检查里，并写明预期请求数。

## 3. 三个选项

### A. 搭已有上传的车：`derived/app` 请求体加一个可选的 `view_checks`

- 手机在下一次 `PUT derived/app` 时，把上次上报之后**有变化**的页面对比结果（最多 20 条）放进 `view_checks: [{view, ok, compared, fields_over, algo_version, bundle_version, at}]`。服务器写进 `app_compute_checks`：`kind = "view:<页面>"`，`day` = 对比的日期（月历取当月 1 号）。
- 好处：不增加任何请求；不用新表，也不用新接口。
- 坏处：`derived/app` 只在某个 kind 处于 shadow / primary、而且有新的已结算天数时才发。只开了页面包（series、coach、reports）的用户可能很久都不发，结果会迟到甚至一直发不出去。另外，把「页面」和「数据行」两种语义塞进同一个接口，以后删的时候要拆。

### B. 单独一个小接口，每天最多发一次（**推荐**）

- `POST /v1/sync/compute-checks {checks: [...最多 20 条]}`。手机在每轮同步结束时检查：如果有**和上次上报不同**的结果（`view, ok, fields_over, bundle_version` 任何一项变了），或者有还没发出去的失败，就发一次，每天最多一次。结果一直不变的手机，平时一个请求都不会发。
- 存储：沿用 `app_compute_checks`，新加一列 `origin`（`server_spot` 服务器抽查 / `app_view` 手机页面对比），`kind = "view:<页面>"`。保留 90 天，由 nightly 清理。
- 查看：`body-engine native-checks --by bundle`（只给汇总：每个 `bundle_version × view` 的通过数、失败数、最常见的超差字段名）。doctor 规则：某个 bundle 版本、某个页面，在 ≥ 3 个用户上失败，就报 ⚠。
- 开关：rollout flag `app_view_checks`（kill switch，默认对所有人开，按「全员同时上线」的规则）；健康检查登记这条通道，预期每个活跃用户每天 0–1 个请求。
- 好处：和数据上传解耦，每个页面包都能报；流量可以忽略（典型情况下每周不到 1 个请求，每个 < 1 KB）；删的时候只删一个接口、一列、一个开关。
- 坏处：多了一个上传通道（要鉴权、限流、加测试）；需要一次迁移。

### C. 只回传计数：带在已有的 `GET /v1/sync/compute` 请求头里

- 手机给 `GET /v1/sync/compute` 加一个请求头 `X-Body-View-Checks: series=ok;workouts=fail;coach=ok`，服务器按 `(user, view, 日期)` 累加通过 / 失败次数。
- 好处：零新增请求，体积最小，不存字段名。
- 坏处：没有字段名，只知道「不一致」，不知道哪个字段不一致，排查还得回到用户 1 复现。请求头会进 nginx / 反代的访问日志，隐私面反而比请求体大。GET 带写入副作用不干净，也不好做幂等。

## 4. 推荐：B

漂移要尽早被**看到**，而且要能直接定位到字段：#444 那种情况，看到 `view:workouts.day` 在新 bundle 上整体失败、超差字段是 `sessions[].intensity_basis`，就能知道问题在哪个分支。B 是唯一既能定位字段、又不依赖其他上传时机的方案。它的成本是一个接口加一列，流量可以忽略。A 的「搭车」看起来省事，但覆盖不了只开页面包的用户。C 的信息量不够，还会把数据带进访问日志。

**防线的顺序**：第一道是 CI 的 golden（Python 改了某个分支，golden 必须覆盖到，比如这次给 `31_workouts.js` 补的手动训练样例）；第二道是这条上报，它兜住 golden 没想到的真实数据组合。上报不能代替 golden。

## 5. 要实现时的清单（这次不做）

1. 迁移：`app_compute_checks` 加 `origin TEXT NOT NULL DEFAULT 'server_spot'`；索引 `(kind, bundle_version, checked_at)`。
2. 接口 `POST /v1/sync/compute-checks`：write token、只能写自己的、Pydantic `extra="forbid"`，`fields_over` 每条最多 20 个、只收字段路径（正则 `^[a-z_\[\].0-9]+$`，拒收数值）；做负权限测试（别人的 token 写不进；只读 token 被拒）。
3. iOS：`SeriesLocal.compare` 的结果写入时顺便把「待上报」标成 dirty；同步结束时上报；失败了保留 dirty，下一轮再发。文案双语。
4. CLI / doctor 汇总，健康检查登记通道，flag `app_view_checks`。
5. 退出 issue：停止条件（所有页面包都 primary，连续 4 周没有失败）、目标日期、删除清单（接口、`origin` 列里的 `app_view` 行、iOS 的 `*.compare` 和上报、flag）。
