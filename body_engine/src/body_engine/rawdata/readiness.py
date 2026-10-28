"""Before the cut-over (#219 S5b): per user, what is still missing before `sync-mode switch` / `apply` would switch them to
the app's own sync engine, so the deployer sees at a glance who can be switched. `body-engine sync-mode readiness`.

Read only, and cheap (no score recompute): it reads what the gates of rawdata/cutover.py read, from the stored results:
- app version: the newest engine batch within CUTOVER_APP_SEEN (cutover.app_version_gate), ≥ CUTOVER_MIN_APP_VERSION;
- shadow comparison (sync_shadow_summary, the nightly sync-shadow-compare): CUTOVER_CLEAN_DAYS consecutive clean closed
  days, coverage ≥ CUTOVER_COVERAGE, and when the last comparison ran;
- score consistency: the score items the comparison stored (sync_shadow_diffs kind 'score', app-mode recompute vs the
  stored scores) of the last CUTOVER_CHECK_DAYS compared days against the same thresholds as cutover.score_check. The
  gate itself recomputes over its own window; for the exact answer of one user: `sync-mode switch --dry-run --user-id N`;
- consent (nothing is processed before it), the current / desired mode, a pin, an engine-only account (no OW link: only
  the app version counts, cutover.gates).
`switches`: the global state of apple_sync_engine, apple_sync_primary and apple_sync_new_accounts (cutover.new_accounts).
The Markdown carries display names and score differences: the terminal or health_data/reports/ only, never git.
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone

import psycopg

from ..rollout import flags
from . import cutover as CO
from . import engine as EN
from . import params as P
from . import shadow as SH

STATUSES = ("app", "ready", "blocked", "engine_off")
MISSING_ZH = {
    "consent": "还没确认同意书",
    "app_version": "app 版本",
    "no_engine_upload": "最近没有引擎上传",
    "no_shadow": "还没有影子对比结果",
    "streak": "连续一致天数",
    "coverage": "覆盖率",
    "scores": "分数一致性",
    "score_missing": "app 模式算不出分",
    "pinned": "已手动固定在 OW（sync-mode unpin 解除）",
    "stale_compare": "影子对比超过 2 天没跑",
}


def users(conn: psycopg.Connection) -> list[int]:
    """Active users the cut-over concerns: an OW link, engine data, or a sync_mode_state row."""
    return [r["id"] for r in conn.execute(
        """SELECT id FROM users WHERE status = 'active' AND (ow_user_id IS NOT NULL
             OR id IN (SELECT user_id FROM hk_sources) OR id IN (SELECT user_id FROM sync_mode_state))
           ORDER BY id""")]


def score_items(conn: psycopg.Connection, user_id: int, last_day: date | None) -> dict:
    """The comparison's score items of the last CUTOVER_CHECK_DAYS days up to last_day, judged like cutover.score_check:
    {key: {n, mean_abs, max_abs, lost, ok}, ok, days}; ok None = no score items stored yet."""
    if last_day is None:
        return {"ok": None, "days": 0}
    rows = conn.execute("""SELECT date, key, diff, verdict FROM sync_shadow_diffs WHERE user_id = %s AND kind = 'score'
                           AND date BETWEEN %s AND %s""",
                        (user_id, last_day - timedelta(days=P.CUTOVER_CHECK_DAYS - 1), last_day)).fetchall()
    if not rows:
        return {"ok": None, "days": 0}
    out: dict = {"days": len({r["date"] for r in rows})}
    ok = True
    for key, mean_max, max_max in (("recovery", P.CUTOVER_SCORE_MEAN, P.CUTOVER_SCORE_MAX),
                                   ("sleep_score", P.CUTOVER_SCORE_MEAN, P.CUTOVER_SCORE_MAX),
                                   ("strain", P.CUTOVER_STRAIN_MEAN, P.CUTOVER_STRAIN_MAX)):
        ds = [abs(r["diff"]) for r in rows if r["key"] == key and r["diff"] is not None]
        lost = sum(1 for r in rows if r["key"] == key and r["verdict"] == "missing_app")
        m = round(statistics.fmean(ds), 2) if ds else None
        mx = round(max(ds), 2) if ds else None
        good = lost == 0 and (m is None or (m <= mean_max and mx <= max_max))
        out[key] = {"n": len(ds), "mean_abs": m, "max_abs": mx, "lost": lost, "ok": good}
        ok = ok and good
    out["ok"] = ok
    return out


def missing(a: dict) -> list[str]:
    """What blocks the switch of one assessed user (codes of MISSING_ZH), in the order to fix them. Pure."""
    if a["mode"] == "app" or not a["engine"]:
        return []
    out = []
    if a["consent"]:
        out.append("consent")
    if a["pin"] == "ow":
        out.append("pinned")
    av = a["app_version"]
    if not av["ok"]:
        out.append("app_version" if av["app_version"] else "no_engine_upload")
    if a["engine_only"]:
        return out
    s = a["shadow"]
    if s is None or "no_data" in (s.get("blockers") or []):
        out.append("no_shadow")
    else:
        out += [b for b in ("streak", "coverage") if b in (s.get("blockers") or [])]
    sc = a["scores"]
    if sc.get("ok") is False:
        lost = any((sc.get(k) or {}).get("lost") for k in SH.SCORE_KEYS)
        out.append("score_missing" if lost else "scores")
    return out


def assess(conn: psycopg.Connection, user_id: int, now: datetime | None = None) -> dict:
    """One user: mode, the gates' inputs, `missing` and `status` (app / ready / blocked / engine_off)."""
    from ..profile import consent
    now = now or datetime.now(timezone.utc)
    u = conn.execute("SELECT id, display_name, ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()
    st = CO.state(conn, user_id)
    s = SH.summary(conn, user_id)
    if s is not None:
        s["stale"] = s["computed_at"] is None or now - s["computed_at"] > timedelta(days=2)
    a = {"user_id": user_id, "name": u["display_name"], "engine": EN.enabled(conn, user_id),
         "mode": EN.mode(conn, user_id), "desired": CO.desired(conn, user_id), "engine_only": not u["ow_user_id"],
         "pin": st["pin"] if st else None, "consent": consent.needs_consent(conn, user_id),
         "app_version": CO.app_version_gate(conn, user_id), "shadow": s,
         "scores": score_items(conn, user_id, s["last_day"] if s else None),
         "last_attempt": (st["checks"] or {}).get("result") if st else None}
    a["missing"] = missing(a)
    a["warnings"] = ["stale_compare"] if s and s["stale"] and a["mode"] != "app" and not a["engine_only"] else []
    a["status"] = ("engine_off" if not a["engine"] else "app" if a["mode"] == "app"
                   else "blocked" if a["missing"] else "ready")
    return a


def switches(conn: psycopg.Connection) -> dict:
    rows = flags.rows(conn)

    def one(key: str) -> dict:
        r = rows.get(key)
        return {"state": r["state"] if r else "off", "cohort": r["cohort"] if r else None}
    return {"engine": one(EN.FLAG), "primary": one(EN.PRIMARY_FLAG), "primary_cohort": CO.PRIMARY_COHORT,
            "new_accounts": CO.new_accounts(conn), "min_app_version": P.CUTOVER_MIN_APP_VERSION}


def report(conn: psycopg.Connection, user_ids: list[int] | None = None, now: datetime | None = None) -> dict:
    flags.sync(conn)   # declared keys get their (off) rows, so the switches read what `flags list` shows
    conn.commit()
    got = [assess(conn, uid, now) for uid in (user_ids if user_ids is not None else users(conn))]
    counts = {k: sum(1 for a in got if a["status"] == k) for k in STATUSES}
    return {"switches": switches(conn), "counts": counts, "users": got}


# ---------------------------------------------------------------- Chinese Markdown (terminal)
def _pct(x) -> str:
    return "—" if x is None else f"{x:.0%}"


def _scores_cell(sc: dict) -> str:
    if sc.get("ok") is None:
        return "—"
    worst = max(((k, (sc.get(k) or {}).get("max_abs")) for k in SH.SCORE_KEYS), key=lambda x: x[1] or 0)
    tail = f"（最大差 {worst[0]} {worst[1]}）" if worst[1] else ""
    return ("一致" if sc["ok"] else "不一致") + tail


def _missing_zh(a: dict) -> str:
    parts = []
    for m in a["missing"]:
        if m == "app_version":
            parts.append(f"app {a['app_version']['app_version']} < {a['app_version']['min']}")
        elif m == "streak":
            parts.append(f"连续一致 {a['shadow']['clean_streak']}/{P.CUTOVER_CLEAN_DAYS} 天")
        elif m == "coverage":
            parts.append(f"覆盖率 {_pct(a['shadow']['coverage'])} < {_pct(P.CUTOVER_COVERAGE)}")
        else:
            parts.append(MISSING_ZH[m])
    parts += [f"（提醒：{MISSING_ZH[w]}）" for w in a.get("warnings") or []]
    return "；".join(parts)


VERDICT = {"app": "已切换", "ready": "可以切换", "blocked": "还不能切", "engine_off": "引擎没开"}


def markdown(rep: dict) -> str:
    sw = rep["switches"]
    na = sw["new_accounts"]
    pr = sw["primary"]
    primary = pr["state"] + (f"（分组 {pr['cohort']}）" if pr["state"] in ("cohort", "percent") else "")
    out = ["# 切换前体检（自建同步引擎）", "",
           f"- `{EN.FLAG}`：{sw['engine']['state']}；`{EN.PRIMARY_FLAG}`：{primary}"
           f"（`sync-mode switch` 会把人加进分组 {sw['primary_cohort']}）",
           f"- `{EN.NEW_ACCOUNTS_FLAG}`：{na['flag']}，" + ("新开户直接走引擎" if na["on"] else
                                                             "新开户照旧建 OW 用户" + (
                                                                 f"（{EN.FLAG} 不是 all，开关不生效）" if na["reason"] == "engine_not_all" else "")),
           f"- 门槛：app ≥ {sw['min_app_version']}、连续 {P.CUTOVER_CLEAN_DAYS} 天一致、覆盖率 ≥ {_pct(P.CUTOVER_COVERAGE)}、"
           f"分数平均差 ≤ {P.CUTOVER_SCORE_MEAN} / 单日 ≤ {P.CUTOVER_SCORE_MAX}（负荷 {P.CUTOVER_STRAIN_MEAN} / {P.CUTOVER_STRAIN_MAX}）",
           "- " + "，".join(f"{VERDICT[k]} {n} 人" for k, n in rep["counts"].items()), "",
           "| user | 名字 | 模式 | app 版本 | 连续一致 | 覆盖率 | 分数 | 结论 | 还差什么 |",
           "|---|---|---|---|---|---|---|---|---|"]
    for a in rep["users"]:
        s = a["shadow"] or {}
        mode = a["mode"] + ("" if a["desired"] == a["mode"] else f" → {a['desired']}") + (" · 无 OW" if a["engine_only"] else "")
        streak = "—" if a["engine_only"] or not s else f"{s.get('clean_streak', 0)}/{P.CUTOVER_CLEAN_DAYS}"
        cov = "—" if a["engine_only"] else _pct(s.get("coverage"))
        out.append(f"| {a['user_id']} | {a['name']} | {mode} | {a['app_version']['app_version'] or '—'} | {streak} | {cov}"
                   f" | {'—' if a['engine_only'] else _scores_cell(a['scores'])} | {VERDICT[a['status']]} | {_missing_zh(a)} |")
    out += ["", "精确的门槛（会重算分数）：`body-engine sync-mode switch --dry-run --user-id N`；"
                "差异明细：`body-engine sync-shadow-report --user-id N`。", ""]
    if na["flag"] != "all":
        out += [f"打开 `{EN.NEW_ACCOUNTS_FLAG}` 之前：确认 TestFlight 上能装到的 app 都能在没有 OW 账号时同步（引擎 + 同步页不再要求连 OW）。", ""]
    return "\n".join(out)
