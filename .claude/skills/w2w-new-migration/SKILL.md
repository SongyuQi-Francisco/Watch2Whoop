---
name: w2w-new-migration
description: Add a Body Engine database migration correctly — pick the number by NUMERIC maximum (never `ls | tail`, which sorts as strings and has caused two collisions), per-user tables with user_id, idempotent SQL, the grafana_ro grant, the exit issue, and re-checking the number right before merge. Triggers: 迁移, migration, 新表, 加列, migrations/NNN, 迁移编号, 撞号, schema change, ALTER TABLE, grafana_ro.
---

# New database migration

Files: `body_engine/src/body_engine/migrations/NNN_name.sql`. `migrate()` applies them in **numeric** order
(`db.migration_key`), each once, recorded in `schema_migrations` by file name.

## 1. Pick the number

```bash
ls body_engine/src/body_engine/migrations | sed -n 's/^\([0-9]\+\)_.*/\1/p' | sort -n | tail -1
```

Take the next free step after that maximum (numbers go up in tens, e.g. 1210 → 1220). **Never `ls | tail`**: string
sorting puts `995_…` after `1210_…`, which is how #463 and #481 both picked wrong numbers. Also check open PRs for a
number someone else already claimed:

```bash
gh pr list --state open --json number,files --jq '.[] | select(any(.files[]; .path|test("migrations/"))) | .number'
```

Re-check right before merge: if a PR with the same number merged first, rename yours (the merge record on each PR lists
its migration numbers).

## 2. Write it

- Every new table has `user_id` (FK to users) and is read/written only through per-user authorization.
- Idempotent DDL (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, guarded backfills), so a re-run or a
  partially applied deploy is safe.
- `GRANT SELECT ON <table> TO grafana_ro;` if Grafana reads it — and only counts / views for sensitive text.
- Prefer no migration at all when the data fits an existing column (`details` JSON) — see `w2w-backend-gap-fill`.
- Health thresholds never go into SQL defaults; they live in `params.py`.

## 3. Around it

- **Exit issue** (CLAUDE.md conventions): stop condition, target date, deletion checklist — for every migration that
  starts a dual-write, a rollout flag or a platform switch.
- Tests run the migrations on a throw-away Postgres (or CI). Never the local `body` database on 5433, never production.
- PR body: the migration number and what it does, whether it is safe to apply before the code deploys, and what the
  coordinator must run after merge. Docs that cite the number (plans, module READMEs) change in the same PR.
