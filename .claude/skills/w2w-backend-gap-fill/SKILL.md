---
name: w2w-backend-gap-fill
description: The iOS-needs-data → backend-adds-fields → iOS-wires-them loop — the app lists "待后端" gaps and shows empty states, a backend session extends existing endpoints with new fields only (no breaking change, migration only if unavoidable), documents them in the PR's first comment with synthetic examples, then the iOS session overlays them with old-server fallback. Use when a UI PR needs data no endpoint returns, or when wiring new server fields into the app. Triggers: 待后端, 补接口, 后端缺口, 接口缺口, 加字段, endpoint, series 接口, 接入后端新字段, wire fields, empty state, 空状态, old server.
---

# Backend gap fill (iOS ↔ Body Engine)

Distilled from #447 → #448 → #447 round 4 (Today L1 series), #449/#451 → #452 → #454 (2.1.0 gaps), #444 (manual
workout entry endpoint). Three sessions, three PRs, one contract.

## 1. iOS side first: never fake data

- Where a design needs data no endpoint returns, show the **existing empty state** and list the gap in the PR body
  under a 「待后端 / Backend gaps」 heading: screen, element, what data, which endpoint would naturally carry it.
- Never compute a stand-in on the phone that the server should own, never draw placeholder numbers.

## 2. Backend session: extend, don't fork

Read the iOS PR's gap list, the design files it points to, `body_engine/README.md`, and the existing implementation
of the endpoints involved before writing anything.

- **Add fields to existing endpoints**; keep every old field and its meaning. Prefer reusing existing pure functions
  (scores/, trends/, training/) over a second algorithm — and if a function has a JS twin, the change goes through the
  algorithm flow (golden tests), not a quick fork.
- No migration unless the data truly has nowhere to live (often it fits an existing `details` JSON). If you need one,
  follow `w2w-new-migration`.
- **Authorization**: `{user}` only via `deps.resolve_owner` / `deps.require_self`; each field is gated by its scope
  (`scores`, `sleep`, `training`, …). When a field is withheld for lack of a share/scope, omit it and say so with
  `"hidden": ["<scope>"]` — never leak it, never error the whole response.
- **Honest absence**: no data → empty value plus a machine-readable `reason` (e.g. `no_scores`, `no_raw_hr`,
  `not_computed`), never an invented number. Consistency invariants are tested (e.g. the hourly strain curve's last
  value equals the day's strain score).
- Product constants (window sizes, counts) go in the module's `params.py` with their source, marked "product setting"
  when that is what they are.
- Writes are idempotent. Health texts: no medical conclusions. Bilingual server texts through `X-Body-Lang`.
- Tests use synthetic data and a throw-away Postgres (or CI). Never read or write the local `body` database on 5433 or
  production.
- Keep the AI tools' payloads unchanged unless the task says otherwise; say so in the PR.

## 3. The interface comment

The backend PR's **first comment** is the interface reference the iOS session will read
(`gh api repos/<owner>/<repo>/issues/<N>/comments --jq '.[0].body'`): per field — endpoint, scope, JSON shape with a
synthetic example, units, time zone semantics, every `reason` value, what "hidden" looks like, what an old row returns.

## 4. iOS wiring session

- Start from the latest `origin/main` after the backend PR is merged and deployed (check with the coordinator).
- Overlay server fields on the phone-computed documents; a field missing on an older server = the existing empty
  state, never a crash and never a fabricated value. Decode with optionals and test both "field present" and "field
  absent" payloads.
- Close the loop in the iOS PR: tick each item of the original gap list, link the backend PR, and list any gap still
  open (e.g. "plan blocks have no start time on the server, so the 18:30 block cannot be drawn").

## 5. Report

PR link + head SHA, required checks, what was not verified, decisions for the coordinator (with a recommendation).
No deploy, no merge from the session.
