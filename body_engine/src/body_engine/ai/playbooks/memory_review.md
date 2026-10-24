# 整理长期记忆
> 找出重复、过时和可能矛盾的记忆，和用户确认后一次整理好；归档不删除，随时能恢复。

<!-- AI-facing playbook (issue #113 phase 2). Served as the MCP prompt "memory_review" and by get_playbook; keep tool names exact
     (tests/test_playbooks.py checks every `tool_name` exists). The briefing's memory_review line is what reminds the AI. -->

## 步骤
1. 调 `get_memory_review`：只返回候选——`duplicates`（重复）、`stale`（太久没确认）、`same_topic`（同一主题的几条，看有没有矛盾）、`size`（记忆大小）、`rules`。`rules` 必须照做。
2. 能直接处理的先处理：`exact=true` 的重复，合并到 `keep`（`merge`，`reason` 写「重复」）。
3. 剩下的按主题问用户，一次最多 5 条，每条说清是什么、你建议怎么办（还准确 / 改成什么 / 归档），让用户选：
   - 重复但不完全一样：问合并成哪句话（`merge` 带 `text`）；
   - `stale`：问还准不准——准就 `confirm`，变了就 `update`，不再适用就 `archive`；`long_stale=true` 的优先建议归档；
   - `same_topic` 里互相矛盾的：问以哪个为准，另一条 `archive`；
   - 反复出现、已经是稳定规律的笔记，可以 `promote` 并入对应的档案卡项。
4. 用户答完，把所有结果放进一次 `apply_memory_changes`（`items`，每项都写 `reason`）。用户明确同意改的条目，该项带 `user_ok=true`。整批只会弹一张确认卡。
5. 看返回的每项结果：`error` 的告诉用户原因并改正后再提交；`noop` 说明已经是那个状态。
6. 汇报：合并了几条、归档了几条（用户能在「我 → 长期记忆」的已归档里恢复）、确认了几条、还剩什么没处理。用户什么都不想改，也调一次 `apply_memory_changes`（`items` 为空），这次整理就记下了。

## 什么时候先问用户
- `origin` 是 `user_said` 或空的条目，改、归档、并入之前问用户；服务器会拒绝没有 `user_ok` 的项。
- 健康约束、伤病只有用户明确说了才能改。
- 一字不差的重复可以直接合并，其余都问。拿不准的先留着，下次再整理。

## 护栏
- 只整理这个用户自己的记忆，看不到也不碰别人的；不写别人的。
- 不删除：归档就是不再进对话、但保留原文；写错了用 rollback_write 撤回。
- 不编造：没有依据的内容不写进记忆，不凭推断把用户的话改掉。
- 不做医学诊断；这个场景不用联网。
