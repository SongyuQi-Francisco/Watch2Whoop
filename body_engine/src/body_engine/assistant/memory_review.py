"""Periodic review of the long-term memory (issue #113, phase 2): candidates, the batch that applies a review, the state
behind the briefing reminder and the nightly check. Entry fields and the archive/restore basics are in memory.py.

- The server finds candidates with fixed rules and no AI (candidates()): near-identical notes, entries unconfirmed for longer
  than their topic's memory_params.STALE_DAYS, and entries of one topic side by side for the AI to spot contradictions.
- The user's AI judges and talks to the user (playbook memory_review); apply() writes the outcome back as one batch. Nothing is
  deleted: merged / outdated entries are archived (status=archived, reason, merged_into) and can be restored on the Today page
  or with `restore`; archived entries go for good after memory_params.ARCHIVE_PURGE_DAYS (purge_archived, nightly).
- Guardrails (checked here, not only asked of the AI): an entry whose origin is the user's own words or unknown, and any entry
  of a protected topic (health constraints, injuries), is changed or archived only with user_ok=true on the item; the one
  exception is a merge of a note into another with the very same text. Every change is logged for rollback_write
  (writelog kind memory_state for archive / restore / confirm, profile / observation for rewrites).
- Phase 3 (memory_datacheck.py, owner only): entries are compared with the user's own records (training habits, supplements, body
  weight, injuries). What the data contradicts comes back as `data_mismatch` with the claim and the data, for the AI to put to the
  user (nothing is changed by itself); what it backs is confirmed by the nightly run (confirmed_at, confirm_count). Lasting notes
  confirmed several times, or old and still current, come back as `promote` (merge into the profile card item of their topic).
- memory-review-run (nightly, every user the flag memory_hygiene is on for) confirms what the data backs, refreshes
  memory_review_state and purges; the briefing reads that state (reminder_line).
Authorization is the caller's job: everything here works on the caller's own user_id only.
"""
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import psycopg

from .. import queries
from ..rollout import flags
from . import memory, memory_datacheck, memory_params as mp, writelog

REF_RE = re.compile(r"^(?:n(\d+)|k:([a-z][a-z0-9_]*))$")
ACTIONS = ("merge", "update", "archive", "confirm", "promote", "restore")
STATE_COLS = "status, archived_at, archived_reason, merged_into, confirmed_at, confirm_count"
RULES = (
    "origin 是 user_said 或空的条目、以及健康约束 / 伤病：改、归档、并入前要用户明确同意，才能写 user_ok=true",
    "exact=true 的重复可以直接合并（留 keep）；其余先问用户，一次最多 5 条",
    "data_mismatch 是和记录对不上的：问用户哪个对（记录 / 记忆），不要自己改；promote 先问用户，并入后写这一项的完整内容",
    "归档不删除，用户能在「我 → 长期记忆」恢复，90 天后清理；写错了 rollback_write",
    "结束时一定调 apply_memory_changes（没有要改的就空 items），记下这次整理",
)


# ---------------------------------------------------------------- candidates
def _ref(kind: str, key) -> str:
    return f"n{key}" if kind == "note" else f"k:{key}"


def _cut(text: str, n: int = mp.REVIEW_TEXT_CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rstrip("，、；。 ") + "…"


def _entry(kind: str, e: dict) -> dict:
    key = e["id"] if kind == "note" else e["key"]
    return {"ref": _ref(kind, key), "text": _cut(e["text"] if kind == "note" else e["value"]), "topic": e["topic"],
            "origin": e["origin"], "days": e["confirmed_days"]}


def _duplicates(notes: list[dict]) -> list[dict]:
    """Groups of notes of one kind (and one topic, or one of them without) whose text is at least SIMILAR_MIN_RATIO alike."""
    notes = sorted(notes, key=lambda n: n["id"], reverse=True)[:mp.REVIEW_COMPARE_MAX]
    norms = {n["id"]: memory._norm(n["text"]) for n in notes}
    parent = {n["id"]: n["id"] for n in notes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    t = mp.SIMILAR_MIN_RATIO
    for i, a in enumerate(notes):
        sm = SequenceMatcher(None, autojunk=False)
        sm.set_seq2(norms[a["id"]])
        for b in notes[i + 1:]:
            if a["kind"] != b["kind"] or (a["topic"] and b["topic"] and a["topic"] != b["topic"]):
                continue
            sm.set_seq1(norms[b["id"]])
            if norms[a["id"]] and norms[b["id"]] and sm.real_quick_ratio() >= t and sm.quick_ratio() >= t and sm.ratio() >= t:
                parent[find(a["id"])] = find(b["id"])
    groups: dict[int, list[dict]] = {}
    for n in notes:
        groups.setdefault(find(n["id"]), []).append(n)
    out = []
    for g in groups.values():
        if len(g) < 2:
            continue
        keep = max(g, key=lambda n: (n["confirmed"], n["id"]))
        out.append({"exact": len({norms[n["id"]] for n in g}) == 1, "keep": _ref("note", keep["id"]),
                    "notes": [_entry("note", n) for n in sorted(g, key=lambda n: n["id"])]})
    return sorted(out, key=lambda g: (not g["exact"], g["notes"][0]["ref"]))


def _stale_days(topic) -> int:
    return mp.STALE_DAYS.get(topic, mp.STALE_DEFAULT_DAYS)


def _snoozed(created, confirmed) -> bool:
    """The user (or the AI with him) looked at this entry since it was written, recently: a mismatch is not offered again yet."""
    now = datetime.now(timezone.utc)
    return confirmed > created + timedelta(minutes=1) and now - confirmed < timedelta(days=mp.DATA_SNOOZE_DAYS)


def _data_verdicts(conn, user_id: int, notes: list[dict], cards: list[dict], skip: set[str], today) -> dict[str, tuple]:
    """{ref: (kind, entry, verdict)} for the lasting notes and card items the user's records have something to say about."""
    rows = {_ref("note", n["id"]): ("note", n) for n in notes if n["kind"] != "recent" and _ref("note", n["id"]) not in skip}
    rows |= {_ref("card", c["key"]): ("card", c) for c in cards}
    verdicts = memory_datacheck.check_entries(
        conn, user_id, [(ref, e["text"] if k == "note" else e["value"], e["topic"]) for ref, (k, e) in rows.items()], today)
    return {ref: (*rows[ref], v) for ref, v in verdicts.items()}


def _data_mismatches(verdicts: dict[str, tuple]) -> list[dict]:
    """Entries whose claim the records contradict, unless the user already looked at the entry lately (_snoozed)."""
    out = []
    for kind, e, v in verdicts.values():
        written = e["created_at"] if kind == "note" else e["updated_at"]
        if v["verdict"] == "mismatch" and not _snoozed(written, e["confirmed_at"]):
            out.append({**_entry(kind, e), "check": v["kind"], "claim": v["claim"], "data": v["data"]})
    return sorted(out, key=lambda x: (x["check"], x["ref"]))


def _promotions(notes: list[dict], cards: list[dict], skip: set[str]) -> list[dict]:
    """Lasting notes ready to be merged into the profile card item of their topic: confirmed PROMOTE_MIN_CONFIRMS times or more, or at
    least PROMOTE_MIN_AGE_DAYS old; still current (not stale); not already said by the card item."""
    now = datetime.now(timezone.utc)
    by_key = {c["key"]: c for c in cards}
    out = []
    for n in notes:
        if n["kind"] == "recent" or n["stale"] or not n["topic"] or _ref("note", n["id"]) in skip:
            continue
        age = (now - n["created_at"]).days
        if n["confirm_count"] < mp.PROMOTE_MIN_CONFIRMS and age < mp.PROMOTE_MIN_AGE_DAYS:
            continue
        card = by_key.get(n["topic"])
        if card and memory._norm(n["text"]) in memory._norm(card["value"]):
            continue
        out.append({**_entry("note", n), "into": f"k:{n['topic']}", "confirms": n["confirm_count"], "age": age,
                    "card": _cut(card["value"], mp.PROMOTE_CARD_CHARS) if card else None})
    return sorted(out, key=lambda x: (-x["confirms"], -x["age"], x["ref"]))


def candidates(conn: psycopg.Connection, user_id: int, today=None, with_data: bool = False) -> dict:
    """What a review looks at: duplicates, stale entries, topics with several entries (possible contradictions) and the memory's
    size. `count` is every candidate before the per-group cap (what the nightly check stores).
    with_data (the owner only, never through a share: it quotes weight, training, supplements, injuries) adds `data_mismatch`
    (claims the records contradict, memory_datacheck) and `promote` (notes to merge into their profile card item)."""
    today = today or queries.local_today(conn, user_id)
    m = memory.get_memory(conn, user_id, today=today)
    notes, cards = m["observations"], [c for c in m["profile_card"] if not _managed(c["key"])]
    dups = _duplicates(notes)
    in_dup = {int(n["ref"][1:]) for g in dups for n in g["notes"]}
    mismatch, promote = [], []
    if with_data:
        skip = {_ref("note", i) for i in in_dup}
        mismatch = _data_mismatches(_data_verdicts(conn, user_id, notes, cards, skip, today))
        promote = _promotions(notes, cards, skip | {x["ref"] for x in mismatch})
    flagged = {x["ref"] for x in mismatch}      # listed as a mismatch: not listed again as stale
    stale = []
    for kind, es in (("note", [n for n in notes if n["id"] not in in_dup]), ("card", cards)):
        for e in es:
            if e["stale"] and _ref(kind, e["id"] if kind == "note" else e["key"]) not in flagged:
                lim = _stale_days(e["topic"])
                stale.append({**_entry(kind, e), "limit": lim, "long_stale": e["confirmed_days"] > lim * mp.LONG_STALE_FACTOR,
                              "_over": e["confirmed_days"] / lim})
    stale.sort(key=lambda s: -s["_over"])
    by_topic: dict[str, list[dict]] = {}
    for kind, es in (("note", [n for n in notes if n["kind"] != "recent" and n["id"] not in in_dup]), ("card", cards)):
        for e in es:
            if e["topic"]:
                by_topic.setdefault(e["topic"], []).append(_entry(kind, e))
    topics = sorted(((t, es) for t, es in by_topic.items() if len(es) >= 2), key=lambda x: (-len(x[1]), x[0]))
    facts = [n for n in notes if n["kind"] != "recent"]
    size = {"notes": len(facts), "limit": mp.PROMPT_FACTS_MAX, "card_items": len(cards),
            "near_full": len(facts) >= mp.NEAR_FULL_RATIO * mp.PROMPT_FACTS_MAX}
    out = {"count": len(dups) + len(stale) + len(mismatch) + len(promote), "size": size,
           "duplicates": dups[:mp.REVIEW_MAX_PER_GROUP],
           "stale": [{k: v for k, v in s.items() if k != "_over"} for s in stale[:mp.REVIEW_MAX_PER_GROUP]],
           "same_topic": [{"topic": t, "entries": es[:mp.REVIEW_PER_TOPIC_MAX]} for t, es in topics[:mp.REVIEW_TOPICS_MAX]],
           "expired_recent": m["expired_recent"]}
    if with_data:
        out |= {"data_mismatch": mismatch[:mp.DATA_MAX], "promote": promote[:mp.PROMOTE_MAX]}
    return out


def review(conn: psycopg.Connection, user_id: int, viewer_id: int | None = None) -> dict:
    """The get_memory_review payload: candidates + the review state + the rules. The comparison with the data and the promotion
    candidates only go to the owner's own AI (viewer_id None or the owner), not to someone reading through a memory share."""
    c = candidates(conn, user_id, with_data=viewer_id in (None, user_id))
    st = get_state(conn, user_id)
    return {**c, "last_reviewed": f"{st['last_reviewed_at']:%Y-%m-%d}" if st and st["last_reviewed_at"] else None,
            "rules": list(RULES)}


# ---------------------------------------------------------------- state, reminder, nightly check
def get_state(conn: psycopg.Connection, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM memory_review_state WHERE user_id = %s", (user_id,)).fetchone()


def _save_check(conn, user_id: int, c: dict) -> None:
    conn.execute(
        """INSERT INTO memory_review_state (user_id, candidates, near_full, checked_at) VALUES (%s, %s, %s, now())
           ON CONFLICT (user_id) DO UPDATE SET candidates = EXCLUDED.candidates, near_full = EXCLUDED.near_full,
             checked_at = EXCLUDED.checked_at""", (user_id, c["count"], c["size"]["near_full"]))


def mark_reviewed(conn: psycopg.Connection, user_id: int) -> None:
    c = candidates(conn, user_id, with_data=True)
    _save_check(conn, user_id, c)
    conn.execute("UPDATE memory_review_state SET last_reviewed_at = now() WHERE user_id = %s", (user_id,))


def reminder_line(conn: psycopg.Connection, user_id: int) -> str | None:
    """One line for the briefing when a review is due (memory_params.REVIEW_*); None otherwise or while the flag is off."""
    if not flags.enabled(conn, user_id, "memory_hygiene"):
        return None
    st = get_state(conn, user_id)
    if st is None:
        return None
    last = st["last_reviewed_at"]
    due = last is None or last < datetime.now(timezone.utc) - timedelta(days=mp.REVIEW_INTERVAL_DAYS)
    if not (st["near_full"] or (due and st["candidates"] >= mp.REVIEW_MIN_CANDIDATES)):
        return None
    why = "记忆快满了" if st["near_full"] else f"{st['candidates']} 条待复核"
    return f"记忆该整理了（{why}）：问用户现在要不要整理；要就 get_playbook memory_review，不要就别再提"


def purge_archived(conn: psycopg.Connection, user_id: int) -> dict:
    """Really delete entries archived more than ARCHIVE_PURGE_DAYS ago (idempotent)."""
    n = conn.execute("DELETE FROM assistant_observations WHERE user_id = %s AND status = 'archived' "
                     "AND archived_at < now() - make_interval(days => %s)", (user_id, mp.ARCHIVE_PURGE_DAYS)).rowcount
    k = conn.execute("DELETE FROM assistant_profile WHERE user_id = %s AND status = 'archived' "
                     "AND archived_at < now() - make_interval(days => %s)", (user_id, mp.ARCHIVE_PURGE_DAYS)).rowcount
    return {"observations": n, "profile": k}


def confirm_verified(conn: psycopg.Connection, user_id: int, today=None) -> list[str]:
    """Confirm the lasting notes the user's own records back (memory_datacheck judges the claim true), when their last confirmation
    is at least DATA_CONFIRM_GAP_DAYS old, so a note is confirmed by the data at most monthly. Returns the refs. Card items are not
    confirmed this way: one item holds many statements and one true claim does not make all of them current. Run inside a write-log
    capture (run_all) every change is logged (memory_state) and can be undone with rollback_write."""
    today = today or queries.local_today(conn, user_id)
    due = [n for n in memory.get_memory(conn, user_id, today=today)["observations"]
           if n["kind"] != "recent" and n["confirmed_days"] >= mp.DATA_CONFIRM_GAP_DAYS]
    verdicts = memory_datacheck.check_entries(conn, user_id, [(_ref("note", n["id"]), n["text"], n["topic"]) for n in due], today)
    done = []
    for n in due:
        if (verdicts.get(_ref("note", n["id"])) or {}).get("verdict") == "match":
            _set_state(conn, user_id, "note", n, confirmed_at=conn.execute("SELECT now() AS t").fetchone()["t"],
                       confirm_count=_bumped(n))
            done.append(_ref("note", n["id"]))
    return done


def run_all(conn: psycopg.Connection, only_user: int | None = None) -> list[dict]:
    """memory-review-run: for every user the flag memory_hygiene is on for, confirm the notes the data backs, purge old archived
    entries and refresh the review state. Safe to run any number of times; a user that fails does not stop the others."""
    out = []
    for u in conn.execute("SELECT id FROM users WHERE (%s::int IS NULL OR id = %s) ORDER BY id", (only_user, only_user)).fetchall():
        uid = u["id"]
        if not flags.enabled(conn, uid, "memory_hygiene"):
            continue
        try:
            with conn.transaction():
                purged = purge_archived(conn, uid)
                with writelog.capture(uid, "system", "memory-review-run"):
                    confirmed = confirm_verified(conn, uid)
                c = candidates(conn, uid, with_data=True)
                _save_check(conn, uid, c)
            out.append({"user_id": uid, "candidates": c["count"], "near_full": c["size"]["near_full"], "purged": purged,
                        "confirmed_by_data": len(confirmed)})
        except Exception as e:  # noqa: BLE001
            out.append({"user_id": uid, "error": f"{type(e).__name__}: {e}"})
    return out


# ---------------------------------------------------------------- applying a review
class Skip(Exception):
    """The item changes nothing (already in the wanted state)."""


def _load(conn, user_id: int, ref: str) -> tuple[str, dict]:
    m = REF_RE.match(ref or "")
    if not m:
        raise ValueError(f"ref 写 n12（观察笔记）或 k:goals（档案卡项），收到 {ref!r}")
    if m.group(1):
        row = conn.execute(f"SELECT {memory.OBS_COLS} FROM assistant_observations WHERE id = %s AND user_id = %s FOR UPDATE",
                           (int(m.group(1)), user_id)).fetchone()
        kind = "note"
    else:
        row = conn.execute(f"SELECT {memory.CARD_COLS} FROM assistant_profile WHERE key = %s AND user_id = %s FOR UPDATE",
                           (m.group(2), user_id)).fetchone()
        kind = "card"
    if row is None:
        raise LookupError(f"{ref} 不存在（或不是你的）")
    if kind == "card" and _managed(row["key"]):
        raise ValueError(f"{ref} 由别的功能管理（饮食档案、体型目标、器械），整理不碰它")
    return kind, row


def _managed(key: str) -> bool:
    """Card items other features write and own (the diet profile sections, the physique goal, the equipment list)."""
    return key in mp.KEY_TOPIC or any(key.startswith(p) for p in mp.KEY_PREFIX_TOPIC)


def _protected(kind: str, e: dict) -> bool:
    topic = e["topic"] or (memory.topic_for_key(e["key"], e["label"], e["value"]) if kind == "card" else None)
    return topic in mp.PROTECTED_TOPICS or e["origin"] in mp.USER_ORIGINS or (kind == "card" and e["source"] == "user")


def _need_ok(kind: str, e: dict, item: dict, what: str) -> None:
    if _protected(kind, e) and not item.get("user_ok"):
        raise PermissionError(f"{_ref(kind, e['id'] if kind == 'note' else e['key'])} 是用户自己的话 / 健康约束 / 伤病，"
                              f"{what}要用户明确同意：问过他再带 user_ok=true")


def _snapshot(kind: str, e: dict) -> dict:
    return {k: e[k] for k in ("status", "archived_at", "archived_reason", "merged_into", "confirmed_at", "confirm_count")}


def _bumped(e: dict) -> int:
    """The confirmation counter after confirming `e` now: +1, unless it was confirmed within COUNT_MIN_GAP_DAYS already."""
    old = e["confirmed_at"] < datetime.now(timezone.utc) - timedelta(days=mp.COUNT_MIN_GAP_DAYS)
    return e["confirm_count"] + (1 if old else 0)


def _set_state(conn, user_id: int, kind: str, e: dict, **new) -> None:
    """Change the lifecycle fields of one entry and log before / after (kind memory_state)."""
    ref = _ref(kind, e["id"] if kind == "note" else e["key"])
    fields = {"status": e["status"], "archived_at": e["archived_at"], "archived_reason": e["archived_reason"],
              "merged_into": e["merged_into"], "confirmed_at": e["confirmed_at"], "confirm_count": e["confirm_count"], **new}
    table, where, arg = (("assistant_observations", "id", e["id"]) if kind == "note"
                         else ("assistant_profile", "key", e["key"]))
    row = conn.execute(
        f"""UPDATE {table} SET status = %(status)s, archived_at = %(archived_at)s, archived_reason = %(archived_reason)s,
              merged_into = %(merged_into)s, confirmed_at = %(confirmed_at)s, confirm_count = %(confirm_count)s
            WHERE user_id = %(u)s AND {where} = %(arg)s RETURNING {STATE_COLS}""",
        {**fields, "u": user_id, "arg": arg}).fetchone()
    writelog.record(conn, "memory_state", "update", ref, _snapshot(kind, e), _snapshot(kind, row))   # what changed: before / after status


def _archive(conn, user_id: int, kind: str, e: dict, reason: str, merged_into=None) -> None:
    if e["status"] == "archived":
        raise Skip
    _set_state(conn, user_id, kind, e, status="archived", archived_at=conn.execute("SELECT now() AS t").fetchone()["t"],
               archived_reason=reason, merged_into=merged_into)


def _text_of(kind: str, e: dict) -> str:
    return e["text"] if kind == "note" else e["value"]


def _do(conn, user_id: int, item: dict) -> str:
    action, reason = item.get("action"), (item.get("reason") or "").strip()
    if action not in ACTIONS:
        raise ValueError(f"action 写 {' / '.join(ACTIONS)}")
    if not reason and action != "restore":
        raise ValueError("reason 写一句话原因")
    kind, e = _load(conn, user_id, item.get("ref"))
    if action == "restore":
        if e["status"] == "active":
            raise Skip
        (memory.restore_observation if kind == "note" else memory.restore_profile)(conn, user_id, e["id"] if kind == "note" else e["key"])
        after = _load(conn, user_id, item["ref"])[1]
        writelog.record(conn, "memory_state", "update", item["ref"], _snapshot(kind, e), _snapshot(kind, after))
        return "已恢复"
    if e["status"] == "archived" and action not in ("archive", "merge", "promote"):
        raise ValueError(f"{item['ref']} 已经归档了；要用它先 restore")
    if action == "confirm":
        _set_state(conn, user_id, kind, e, confirmed_at=conn.execute("SELECT now() AS t").fetchone()["t"],
                   confirm_count=_bumped(e))
        return "已确认"
    if action == "archive":
        if e["status"] == "archived":
            raise Skip
        _need_ok(kind, e, item, "归档")
        _archive(conn, user_id, kind, e, reason)
        return "已归档"
    if action == "update":
        _need_ok(kind, e, item, "改")
        text = (item.get("text") or "").strip()
        if not text:
            raise ValueError("update 要给 text（改后的完整内容）")
        if text == _text_of(kind, e).strip():
            raise Skip
        if kind == "note":
            memory.update_observation(conn, user_id, e["id"], text)
        else:
            memory.update_profile(conn, user_id, e["key"], text)
        return "已改写"
    # merge / promote need the entry they go into
    if action == "merge":
        okind, target = _load(conn, user_id, item.get("into"))
        if kind != okind:
            raise ValueError("merge 的 ref 和 into 要同类（都是笔记，或都是档案卡项）")
        tkey = target["id"] if kind == "note" else target["key"]
        if item["ref"] == item["into"]:
            raise ValueError("into 不能是自己")
        if e["status"] == "archived":
            if e["merged_into"] == tkey:
                raise Skip
            raise ValueError(f"{item['ref']} 已经归档了")
        if target["status"] == "archived":
            raise ValueError(f"{item['into']} 已经归档了")
        same = kind == "note" and memory.similarity(e["text"], target["text"]) == 1.0
        text = (item.get("text") or "").strip() or None
        if not same:
            _need_ok(kind, e, item, "并入别的条目")
        if text and text != _text_of(kind, target).strip():
            _need_ok(okind, target, item, "改写")
            if kind == "note":
                memory.update_observation(conn, user_id, target["id"], text)
            else:
                memory.update_profile(conn, user_id, target["key"], text)
        _archive(conn, user_id, kind, e, reason, merged_into=tkey)
        return "已合并"
    # promote: a lasting note becomes part of a profile card item
    if kind != "note":
        raise ValueError("promote 的 ref 要是观察笔记")
    if e["status"] == "archived":
        if e["archived_reason"] == reason:
            raise Skip
        raise ValueError(f"{item['ref']} 已经归档了")
    m = REF_RE.match(item.get("into") or "")
    if not m or not m.group(2):
        raise ValueError("promote 的 into 写档案卡项，例 k:goals")
    text = (item.get("text") or "").strip()
    if not text:
        raise ValueError("promote 要给 text（并入后这一项的完整内容）")
    _need_ok(kind, e, item, "并入档案卡")
    cur = conn.execute("SELECT key, topic, origin, source, label, value FROM assistant_profile WHERE user_id = %s AND key = %s",
                       (user_id, m.group(2))).fetchone()
    if cur:
        _need_ok("card", {**cur, "id": None}, item, "改写这一项")
    memory.update_profile(conn, user_id, m.group(2), text)
    _archive(conn, user_id, "note", e, reason)
    return "已并入档案卡"


def apply(conn: psycopg.Connection, user_id: int, items: list[dict]) -> dict:
    """Apply a review as one batch: each item in its own savepoint and answered on its own (ok / noop / error), so a bad item
    never spoils the rest and running the same batch again changes nothing more. Marks the review as done."""
    if len(items) > mp.APPLY_MAX_ITEMS:
        raise ValueError(f"一次最多 {mp.APPLY_MAX_ITEMS} 项，分批提交")
    results = []
    for i, item in enumerate(items):
        res = {"i": i, "action": item.get("action"), "ref": item.get("ref")}
        try:
            with conn.transaction():
                res |= {"status": "ok", "msg": _do(conn, user_id, item)}
        except Skip:
            res |= {"status": "noop", "msg": "已经是这个状态"}
        except (ValueError, LookupError, PermissionError) as e:
            res |= {"status": "error", "msg": str(e)}
        results.append(res)
    mark_reviewed(conn, user_id)
    return {"results": results, "ok": sum(r["status"] == "ok" for r in results),
            "errors": sum(r["status"] == "error" for r in results)}


def rollback_state(conn: psycopg.Connection, user_id: int, ref: str, before: dict, after: dict) -> dict:
    """Undo one memory_state change (writelog.rollback): only while the entry is still in the state that change left it in."""
    kind, e = _load(conn, user_id, ref)
    if before["status"] != after["status"] and e["status"] != after["status"]:   # a confirm changes no status: always undoable
        raise ValueError(f"{ref} 在这次修改之后又变过（归档 / 恢复过），不能自动撤回；去今天页「我 → 长期记忆」手动处理")
    table, where, arg = ("assistant_observations", "id", e["id"]) if kind == "note" else ("assistant_profile", "key", e["key"])
    conn.execute(
        f"""UPDATE {table} SET status = %(status)s, archived_at = %(archived_at)s, archived_reason = %(archived_reason)s,
              merged_into = %(merged_into)s, confirmed_at = %(confirmed_at)s, confirm_count = COALESCE(%(confirm_count)s, confirm_count)
            WHERE user_id = %(u)s AND {where} = %(arg)s""",
        {**before, "confirm_count": before.get("confirm_count"), "u": user_id, "arg": arg})
    return {"memory_state_restored": ref}
