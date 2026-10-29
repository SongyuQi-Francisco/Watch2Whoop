# 静态数据包 / Static data bundle（#248 第 2 项）

**English summary.** The exercise library and other rarely-changing data (labels, glossary, split templates) as one versioned JSON
package. The app ships the full file as an asset, reads it locally, and updates by version number with
`GET /v1/static-bundle?since=<version>`. Nothing personal is in it. Flag `static_bundle` gates the endpoints; the pack command is not gated.
How the app reads the package is decided by the native-app plan (#247); this PR changes no app code.

## 内容（模块）
| 模块 | 内容 | 更新方式 |
|---|---|---|
| `exercises` | 共享动作库 `exercise_catalog` 的全部行（精选 + free-exercise-db + openGym 文字），**不含**每人的自定义动作和 `exercise_meta`（禁用 / 小心 / 个人提示） | 行级增量：`upsert`（rev 比客户端版本新的行）+ `delete`（被删除的 slug） |
| `taxonomy` | 肌群 / 器械 / 部位 / 运动模式 / 关节压力的中英文标签、肌群到部位的归属、默认「小心」动作 slug、导入来源列表 | 整个模块替换 |
| `glossary` | 讲解词条（指标、化验、状态项、情况），中英文 | 整个模块替换 |
| `split_templates` | 内置的 3 / 4 / 5 天训练分化模板 | 整个模块替换 |
| `sport_muscles` | 每种训练类型练到的肌群（主 / 次）和阻力系数，没列出的类型用全身低强度兜底（`training/sport_muscles.py`；2.0.0 M1c 肌肉负荷图的运动层，算法包 `src/31_workouts.js` 带同一张表并做黄金测试） | 整个模块替换 |

动作行是紧凑格式：缺的键 = 空 / null。`name_zh` / `cues_zh` / `instructions_zh` 只放中文；还没有中文的行（导入的）只有 `name_en` / `cues_en` / `instructions_en`，app 回退用英文。每行带 `source`、`license`、`text_sources[{source, license, ref}]`、`rev`；`images` 是库里的相对路径（演示图不在包里，也不打进 app）。署名清单在包头 `attribution`，完整文本在仓库的 `THIRD_PARTY_NOTICES.md`。

## 版本号
- 全局一个递增计数器（迁移 730 的序列 `static_data_rev_seq`）。动作行的 `rev` 由触发器在**内容真的变了**时设成新值（重复 `seed-exercises` 不改内容就不变）；删除的行记进 `static_tombstones`（再次出现就清掉）；代码里的几个模块（taxonomy、glossary、split_templates、sport_muscles）在 `static_modules` 记内容哈希和变化时的 rev（部署后第一次请求时对比，变了就取新 rev）。
- 包版本 `version` = 所有 rev 里最大的，只增不减。所以「自 N 以来变了什么」是精确的：`rev > N` 的行 + `rev > N` 的删除记录 + `rev > N` 的模块。
- `since` 没给 / 0 / 比服务器的版本还大（服务器回滚过）→ 返回全量，`full: true`。

## 接口（开关 `static_bundle`，关着 404；需要登录，任何 token / cookie）
- `GET /v1/static-bundle/manifest`：`{format, version, modules{名字: {rev, count | hash}}, attribution, notices, flags}`，几百字节，app 用 `version` 和本地比。
- `GET /v1/static-bundle[?since=N]`：全量或增量文档；客户端接受 gzip 就返回 gzip（`Content-Encoding: gzip`）；`ETag` = 版本 + since + 开关，`If-None-Match` 命中返回 304；响应头 `X-Static-Bundle-Version`。
- `flags`：app 要在本地副本上遵守的开关。`exercise_opengym: false` = 隐藏 `source` 在 `taxonomy.imported_sources` 里的动作（openGym 导入的应急开关；这些行仍在包里，版本轴不动）。
- `format` 是包的布局版本（现在 1）；app 遇到比自己认识的更大的就不要用这个包。

## 打包命令
```
cd body_engine
uv run body-engine static-bundle-pack --out /tmp/bundle [--since N]
# /tmp/bundle/bundle-v<版本>.json.gz            全量，给 app 当安装包里的资源
# /tmp/bundle/delta-v<N>-to-v<版本>.json.gz     --since 时的增量
# /tmp/bundle/manifest.json                     版本、模块、署名，每个文件的大小 / 解压后大小 / sha256
```
同样的数据得到同样的字节（gzip mtime 固定，JSON 键排序）。打包出来的 `flags` 是上线状态（`exercise_opengym: true`）。要在数据库里装好动作库（`seed-exercises`、`exercises-import-opengym`）再打包；命令不受开关控制。

## 大小（精选 100 + free-exercise-db 777 + openGym 5,391 = 6,268 个动作）
全量 `bundle-v6271.json.gz` 约 890 KiB（解压 5.6 MiB）：exercises 约 5.6 MiB，glossary 54 KiB，split_templates 5 KiB，taxonomy 2 KiB。测试限制全量压缩后 < 3 MiB。

## 待决定
- 把 openGym 的 AGPL-3.0 文字打进 iOS 安装包：openGym 的 App Store 额外许可只适用于它自己的 app，我们不依赖；这一项在 #248 里还没定，定之前不要把包放进 app 构建。
- app 怎么读这个包（SQLite / 文件 / 内存）由 #247 的方案定。
