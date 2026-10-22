"""Long-term memory of the body butler: the body profile card (key-value) + observation notes; table structure in migrations/060_assistant_memory.sql.

- The profile card is read first in every conversation (get_memory); the butler edits it with update_profile_card, the user edits it on the Today page with PUT .../memory/profile/{key}.
- Observation notes are new facts learned in conversation, with source and date; the user can delete them (DELETE .../memory/observations/{id}).
  kind='recent' notes are current circumstances with an end date (valid_until, user-local, inclusive; default
  memory_params.RECENT_DEFAULT_DAYS): once past it they leave get_memory / the conversation memory / get_briefing but are kept
  for get_memory(history=True) and week reviews (recent_during). Migration 520, issue #112.
- Hygiene, phase 1 (issue #113, migration 660): every entry has topic / confirmed_at / origin / status. Archived entries
  (status=archived) leave the memory and only come back with archived=True (restore_*); add_observation first looks for
  similar notes (similar(), memory_params.SIMILAR_*) and lets the AI rewrite one (update_observation) instead of adding a
  near-duplicate; profile card and note rewrites by the AI are logged with before / after (writelog kinds profile /
  observation) so rollback_write can undo them.
- memory-init generates the initial profile card from existing database data (lab results, exercise rules, supplements, basic info) and body-management.md (transcribed in memory_seed.py);
  it only fills missing keys and never overwrites later changes.
The caller is responsible for authorization (reading needs can_view(scope=memory), writing only for the caller); here we only read / write by user_id.
"""
import difflib
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from .. import queries
from ..records import store as records_store
from . import memory_params as mp
from . import writelog

KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_VALUE = 4000
MAX_OBSERVATION = 1000

# Standard keys: (label, sort). Other keys may be added too, but must come with a label.
STANDARD_KEYS: dict[str, tuple[str, int]] = {
    "basics": ("基本情况", 10),
    "goals": ("目标和进度", 20),
    "health_constraints": ("健康约束", 30),
    "injuries": ("伤病和禁用动作", 40),
    "schedule": ("作息规律", 50),
    "diet_preferences": ("饮食偏好", 60),
    "training_preferences": ("训练偏好和条件", 70),
    "what_works": ("对你有效的做法", 80),
    "what_doesnt": ("无效 / 要避免的做法", 90),
    "current_plan": ("当前训练和饮食计划", 100),
    "supplements": ("补剂", 110),
    "follow_ups": ("复查和待办", 120),
    "communication": ("沟通方式", 5),   # answer length, numbers vs explanations, tone, emoji, reminder frequency, topics not to raise
}
OBS_COLS = ("id, text, created_at, source, evidence, kind, valid_from, valid_until, topic, confirmed_at, origin, status, "
            "archived_at, archived_reason, merged_into")
CARD_COLS = ("key, label, value, updated_at, source, topic, confirmed_at, origin, status, archived_at, archived_reason, "
             "merged_into")
ORIGINS = ("user_said", "ai_inferred", "data")
TOPICS = tuple(STANDARD_KEYS)


def get_memory(conn: psycopg.Connection, user_id: int, history: bool = False, today: date | None = None,
               archived: bool = False) -> dict:
    """Profile card + observation notes (oldest first), active entries only. Recent notes whose end date is before the
    user's local today are left out (counted in expired_recent) unless history=True. archived=True adds `archived`
    ({profile_card, observations}, newest first). Every entry carries its effective topic, confirmed (datetime),
    confirmed_days and stale (memory_params.STALE_DAYS; recent notes are never stale, they expire)."""
    rows = conn.execute(
        f"""SELECT {CARD_COLS} FROM assistant_profile WHERE user_id = %s ORDER BY sort, key""", (user_id,)).fetchall()
    today = today or queries.local_today(conn, user_id)
    now = datetime.now(timezone.utc)
    rows = [_annotate_card(c, now) for c in rows]
    card = [c for c in rows if c["status"] == "active"]
    obs = [_annotate_obs(o, now) for o in conn.execute(
        f"""SELECT {OBS_COLS} FROM assistant_observations
            WHERE user_id = %s ORDER BY created_at, id""", (user_id,)).fetchall()]
    arch = [o for o in obs if o["status"] != "active"]
    obs = [o for o in obs if o["status"] == "active"]
    expired = [o for o in obs if is_expired(o, today)]
    if not history:
        obs = [o for o in obs if not is_expired(o, today)]
    out = {"profile_card": card, "observations": obs, "expired_recent": len(expired)}
    if archived:
        newest = lambda xs: sorted(xs, key=lambda x: x["archived_at"], reverse=True)
        out["archived"] = {"profile_card": newest([c for c in rows if c["status"] != "active"]),
                           "observations": newest(arch)}
    return out


def _stale(topic: str | None, days: int) -> bool:
    return days > mp.STALE_DAYS.get(topic, mp.STALE_DEFAULT_DAYS)


def _annotate_card(c: dict, now: datetime) -> dict:
    confirmed = max(c["confirmed_at"], c["updated_at"])   # writers outside this module (diet profile) only set updated_at
    topic = c["topic"] or topic_for_key(c["key"])
    days = max(0, (now - confirmed).days)
    return {**c, "topic": topic, "confirmed": confirmed, "confirmed_days": days, "stale": _stale(topic, days)}


def _annotate_obs(o: dict, now: datetime) -> dict:
    days = max(0, (now - o["confirmed_at"]).days)
    return {**o, "confirmed": o["confirmed_at"], "confirmed_days": days,
            "stale": o["kind"] != "recent" and _stale(o["topic"], days)}


# ---------------------------------------------------------------- topics and similarity (issue #113)
def topic_for_key(key: str, label: str | None = None, value: str | None = None) -> str | None:
    """Topic of a profile card item: a standard key is its own topic; known other keys and prefixes map to one; else
    inferred from label + value."""
    if key in STANDARD_KEYS:
        return key
    if key in mp.KEY_TOPIC:
        return mp.KEY_TOPIC[key]
    for prefix, topic in mp.KEY_PREFIX_TOPIC.items():
        if key.startswith(prefix):
            return topic
    return infer_topic(f"{label or ''} {value or ''}") if (label or value) else None


def _kw_hit(word: str, text: str) -> bool:
    # ASCII words match at a word start ("eat" not inside "great"), CJK anywhere
    return bool(re.search(r"\b" + re.escape(word), text)) if word.isascii() else word in text


def infer_topic(text: str) -> str | None:
    t = (text or "").lower()
    for topic, words in mp.TOPIC_KEYWORDS:
        if any(_kw_hit(w, t) for w in words):
            return topic
    return None


def check_topic(topic: str | None) -> str | None:
    if topic in (None, ""):
        return None
    if topic not in STANDARD_KEYS:
        raise ValueError(f"topic 写档案卡标准键之一：{', '.join(STANDARD_KEYS)}；拿不准写 null")
    return topic


def check_origin(origin: str | None) -> str | None:
    if origin in (None, ""):
        return None
    if origin not in ORIGINS:
        raise ValueError(f"origin 写 {' / '.join(ORIGINS)} 或 null")
    return origin


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if not unicodedata.category(ch)[0] in "PSZ")


def similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0


def similar(conn: psycopg.Connection, user_id: int, text: str, kind: str = "fact", topic: str | None = None,
            exclude_id: int | None = None) -> list[dict]:
    """Active notes of the same kind (and, with a topic, the same topic or none yet) whose text is at least
    SIMILAR_MIN_RATIO alike, most similar first, at most SIMILAR_MAX."""
    rows = conn.execute(
        f"""SELECT {OBS_COLS} FROM assistant_observations
            WHERE user_id = %s AND status = 'active' AND kind = %s AND id IS DISTINCT FROM %s
              AND (%s::text IS NULL OR topic IS NULL OR topic = %s)""",
        (user_id, kind, exclude_id, topic, topic)).fetchall()
    scored = sorted(((similarity(text, r["text"]), r) for r in rows), key=lambda x: (-x[0], x[1]["id"]))
    return [{**r, "similarity": round(sc, 2)} for sc, r in scored if sc >= mp.SIMILAR_MIN_RATIO][:mp.SIMILAR_MAX]


def is_expired(o: dict, today: date) -> bool:
    return o.get("kind") == "recent" and o["valid_until"] is not None and o["valid_until"] < today


def recent_during(conn: psycopg.Connection, user_id: int, start: date, end: date) -> list[dict]:
    """Recent notes that were in effect on any day of [start, end] (e.g. the week a review looks back on), oldest first."""
    return conn.execute(
        f"""SELECT {OBS_COLS} FROM assistant_observations
            WHERE user_id = %s AND kind = 'recent' AND status = 'active' AND valid_until >= %s AND COALESCE(valid_from, created_at::date) <= %s
            ORDER BY COALESCE(valid_from, created_at::date), id""", (user_id, start, end)).fetchall()


def _check_key(key: str) -> None:
    if not KEY_RE.match(key or ""):
        raise ValueError("键只能是小写字母、数字和下划线，字母开头，例 goals、schedule")


def update_profile(conn: psycopg.Connection, user_id: int, key: str, value: str, label: str | None = None,
                   source: str = "assistant", origin: str | None = None) -> dict:
    """Create or wholly replace one profile card item. Standard keys need no label; new keys must give one.
    The text becomes the truth: structured `data` (diet profile sections, assistant/diet_profile.py) is cleared.
    Writing confirms the item and brings an archived one back. Returns the new row plus `previous` (the old label / value /
    source / updated_at, None for a new item); an AI write logs before / after (writelog kind profile, rollback_write)."""
    _check_key(key)
    origin = check_origin(origin) or ("user_said" if source == "user" else None)
    value = (value or "").strip()
    if not value:
        raise ValueError("value 不能为空（要清掉这一项，写「暂无」或在今天页删掉）")
    if len(value) > MAX_VALUE:
        raise ValueError(f"value 太长（{len(value)} 字），档案卡每项不超过 {MAX_VALUE} 字，写摘要")
    std_label, sort = STANDARD_KEYS.get(key, (None, 200))
    existing = conn.execute(f"SELECT {CARD_COLS}, sort, data FROM assistant_profile WHERE user_id = %s AND key = %s",
                            (user_id, key)).fetchone()
    label = (label or "").strip() or std_label or (existing or {}).get("label")
    if not label:
        raise ValueError(f"{key} 不是标准键（{', '.join(STANDARD_KEYS)}），新键要给 label（中文名）")
    row = conn.execute(
        f"""INSERT INTO assistant_profile (user_id, key, label, value, sort, source, topic, origin)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, key) DO UPDATE SET label = EXCLUDED.label, value = EXCLUDED.value,
              source = EXCLUDED.source, data = NULL, updated_at = now(), confirmed_at = now(),
              topic = EXCLUDED.topic, origin = COALESCE(EXCLUDED.origin, assistant_profile.origin),
              status = 'active', archived_at = NULL, archived_reason = NULL, merged_into = NULL
            RETURNING key, label, value, updated_at, source""",
        (user_id, key, label, value, sort, source, topic_for_key(key, label, value), origin)).fetchone()
    writelog.record(conn, "profile", "update" if existing else "create", key,
                    _card_snapshot(existing) if existing else None, {"label": label, "value": value})
    prev = None if existing is None else {k: existing[k] for k in ("label", "value", "source", "updated_at")}
    return {**row, "previous": prev}


def _card_snapshot(c: dict) -> dict:
    return {k: c[k] for k in ("label", "value", "sort", "source", "data", "topic", "confirmed_at", "origin", "status",
                              "archived_at", "archived_reason", "merged_into")}


def rollback_profile(conn: psycopg.Connection, user_id: int, key: str, before: dict | None, after: dict) -> dict:
    """Undo one AI write of a profile card item (writelog.rollback): only while the item still holds what that write put
    there, so a later edit (by the user or the AI) is never lost."""
    cur = conn.execute("SELECT value FROM assistant_profile WHERE user_id = %s AND key = %s FOR UPDATE",
                       (user_id, key)).fetchone()
    if cur is None or cur["value"] != after["value"]:
        raise ValueError(f"档案卡 {key} 在这次修改之后又改过（或删了），不能自动撤回；用 update_profile_card 手动改回")
    if before is None:
        conn.execute("DELETE FROM assistant_profile WHERE user_id = %s AND key = %s", (user_id, key))
        return {"profile_removed": key}
    conn.execute(
        """UPDATE assistant_profile SET label = %(label)s, value = %(value)s, sort = %(sort)s, source = %(source)s,
             data = %(data)s, topic = %(topic)s, confirmed_at = %(confirmed_at)s, origin = %(origin)s, status = %(status)s,
             archived_at = %(archived_at)s, archived_reason = %(archived_reason)s, merged_into = %(merged_into)s,
             updated_at = now()
           WHERE user_id = %(u)s AND key = %(key)s""",
        {**before, "data": Jsonb(before["data"]) if before.get("data") is not None else None, "u": user_id, "key": key})
    return {"profile_restored": key}


def confirm_profile(conn: psycopg.Connection, user_id: int, key: str) -> dict | None:
    """「还准确」: the item is still right as it is."""
    return conn.execute(
        """UPDATE assistant_profile SET confirmed_at = now() WHERE user_id = %s AND key = %s AND status = 'active'
           RETURNING key, confirmed_at""", (user_id, key)).fetchone()


def restore_profile(conn: psycopg.Connection, user_id: int, key: str) -> dict | None:
    return conn.execute(
        """UPDATE assistant_profile SET status = 'active', archived_at = NULL, archived_reason = NULL, merged_into = NULL,
             confirmed_at = now()
           WHERE user_id = %s AND key = %s AND status = 'archived' RETURNING key, label, value""",
        (user_id, key)).fetchone()


def delete_profile(conn: psycopg.Connection, user_id: int, key: str) -> bool:
    return conn.execute("DELETE FROM assistant_profile WHERE user_id = %s AND key = %s",
                        (user_id, key)).rowcount > 0


def _until(conn: psycopg.Connection, user_id: int, until: str | date | None) -> tuple[date, date] | None:
    """until -> (valid_from, valid_until) of a recent note, or None for a lasting fact. "recent" = the default length."""
    if until is None or until == "":
        return None
    today = queries.local_today(conn, user_id)
    if until == "recent":
        return today, today + timedelta(days=mp.RECENT_DEFAULT_DAYS)
    try:
        end = until if isinstance(until, date) else date.fromisoformat(str(until))
    except ValueError:
        raise ValueError("until 写 YYYY-MM-DD（近况的最后一天）或 recent（默认 "
                         f"{mp.RECENT_DEFAULT_DAYS} 天），长期事实不填")
    if end < today:
        raise ValueError(f"until {end} 已经过去了（今天 {today}），过去的事写日记或事件")
    if end > today + timedelta(days=mp.RECENT_MAX_DAYS):
        raise ValueError(f"近况最长 {mp.RECENT_MAX_DAYS} 天；更长的是长期事实（不填 until）或档案卡")
    return today, end


def add_observation(conn: psycopg.Connection, user_id: int, text: str, evidence: str | None = None,
                    source: str = "assistant", until: str | date | None = None, topic: str | None = None,
                    origin: str | None = None, check_similar: bool = False) -> dict:
    """Record an observation. If the same text was already recorded, do not repeat it (return the old one, duplicate=true;
    restating it confirms it, an archived one stays archived and says so).
    With `until` it is a recent note (kind=recent) ending that day; the same text again as a recent note moves its dates
    (so "still travelling" extends it instead of being dropped as a duplicate of the expired one).
    check_similar (rollout flag memory_hygiene, issue #113): if notes of the same kind and topic are similar, nothing is
    written and they come back as `similar` for the AI to rewrite one (replace_id -> update_observation) or confirm it
    is a new fact (replace_id=0)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")
    if len(text) > MAX_OBSERVATION:
        raise ValueError(f"观察笔记不超过 {MAX_OBSERVATION} 字，写成一句话的事实")
    topic = check_topic(topic) or infer_topic(text)
    origin = check_origin(origin) or ("user_said" if source == "user" else None)
    span = _until(conn, user_id, until)
    kind, vfrom, vuntil = ("recent", *span) if span else ("fact", None, None)
    same = conn.execute(f"""SELECT {OBS_COLS} FROM assistant_observations
                            WHERE user_id = %s AND md5(btrim(text)) = md5(btrim(%s))""", (user_id, text)).fetchone()
    if same is None and check_similar:
        near = similar(conn, user_id, text, kind, topic)
        if near:
            return {"created": False, "similar": [_brief(o) | {"similarity": o["similarity"]} for o in near],
                    "hint": "没有新建：已有相似的笔记。是同一件事就再调一次 add_observation，带 replace_id=那条的 id、"
                            "text 写合并后的完整内容；确实是另一件事，再调一次并带 replace_id=0"}
    if same is None:
        row = conn.execute(
            f"""INSERT INTO assistant_observations (user_id, text, evidence, source, kind, valid_from, valid_until, topic, origin)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, md5(btrim(text))) DO NOTHING
                RETURNING {OBS_COLS}""", (user_id, text, evidence, source, kind, vfrom, vuntil, topic, origin)).fetchone()
        if row is not None:
            return {**row, "duplicate": False}
        same = conn.execute(f"""SELECT {OBS_COLS} FROM assistant_observations
                                WHERE user_id = %s AND md5(btrim(text)) = md5(btrim(%s))""", (user_id, text)).fetchone()
    if same["status"] != "active":
        return {**same, "duplicate": True,
                "hint": "这条以前归档了（原因见 archived_reason），没有恢复；用户确认还对，让他在今天页「我 → 长期记忆」恢复"}
    if span:
        old = conn.execute(
            f"""UPDATE assistant_observations SET kind = 'recent', valid_from = %s, valid_until = %s,
                  evidence = COALESCE(%s, evidence), confirmed_at = now()
                WHERE id = %s RETURNING {OBS_COLS}""", (vfrom, vuntil, evidence, same["id"])).fetchone()
        return {**old, "duplicate": True, "dates_updated": True}
    old = conn.execute(f"UPDATE assistant_observations SET confirmed_at = now() WHERE id = %s RETURNING {OBS_COLS}",
                       (same["id"],)).fetchone()
    return {**old, "duplicate": True}


def _brief(o: dict) -> dict:
    out = {"id": o["id"], "date": f"{o['created_at']:%Y-%m-%d}", "text": o["text"], "topic": o["topic"]}
    return out | ({"until": f"{o['valid_until']}"} if o.get("kind") == "recent" else {})


OBS_SNAPSHOT = ("text", "evidence", "kind", "valid_from", "valid_until", "topic", "origin", "confirmed_at")


def update_observation(conn: psycopg.Connection, user_id: int, observation_id: int, text: str,
                       evidence: str | None = None, until: str | date | None = None, topic: str | None = None,
                       origin: str | None = None) -> dict:
    """Rewrite one active note (e.g. merge a similar one into it): new text, confirmed now. `until` makes / keeps it a
    recent note with those dates; None keeps its kind and dates. Logged with before / after (writelog kind observation)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")
    if len(text) > MAX_OBSERVATION:
        raise ValueError(f"观察笔记不超过 {MAX_OBSERVATION} 字，写成一句话的事实")
    cur = conn.execute(f"""SELECT {OBS_COLS} FROM assistant_observations
                           WHERE id = %s AND user_id = %s AND status = 'active' FOR UPDATE""",
                       (observation_id, user_id)).fetchone()
    if cur is None:
        raise LookupError(f"观察笔记 {observation_id} 不存在（或不是你的、已归档）")
    clash = conn.execute("""SELECT id FROM assistant_observations
                            WHERE user_id = %s AND id <> %s AND md5(btrim(text)) = md5(btrim(%s))""",
                         (user_id, observation_id, text)).fetchone()
    if clash:
        raise ValueError(f"#{clash['id']} 已经是一模一样的文字；保留那条、删掉这条就行")
    span = _until(conn, user_id, until)
    kind, vfrom, vuntil = ("recent", *span) if span else (cur["kind"], cur["valid_from"], cur["valid_until"])
    topic = check_topic(topic) or cur["topic"] or infer_topic(text)
    row = conn.execute(
        f"""UPDATE assistant_observations SET text = %s, evidence = COALESCE(%s, evidence), kind = %s, valid_from = %s,
              valid_until = %s, topic = %s, origin = COALESCE(%s, origin), confirmed_at = now()
            WHERE id = %s RETURNING {OBS_COLS}""",
        (text, evidence, kind, vfrom, vuntil, topic, check_origin(origin), observation_id)).fetchone()
    writelog.record(conn, "observation", "update", observation_id, {k: cur[k] for k in OBS_SNAPSHOT},
                    {k: row[k] for k in OBS_SNAPSHOT})
    return {**row, "previous": cur["text"]}


def rollback_observation(conn: psycopg.Connection, user_id: int, observation_id: int, before: dict, after: dict) -> dict:
    cur = conn.execute("SELECT text FROM assistant_observations WHERE id = %s AND user_id = %s FOR UPDATE",
                       (observation_id, user_id)).fetchone()
    if cur is None or cur["text"] != after["text"]:
        raise ValueError(f"观察笔记 {observation_id} 在这次修改之后又改过（或删了），不能自动撤回")
    conn.execute(
        """UPDATE assistant_observations SET text = %(text)s, evidence = %(evidence)s, kind = %(kind)s,
             valid_from = %(valid_from)s, valid_until = %(valid_until)s, topic = %(topic)s, origin = %(origin)s,
             confirmed_at = %(confirmed_at)s
           WHERE id = %(id)s AND user_id = %(u)s""", {**before, "id": observation_id, "u": user_id})
    return {"observation_restored": observation_id}


def confirm_observation(conn: psycopg.Connection, user_id: int, observation_id: int) -> dict | None:
    """「还准确」: the note is still right as it is."""
    return conn.execute(
        """UPDATE assistant_observations SET confirmed_at = now() WHERE id = %s AND user_id = %s AND status = 'active'
           RETURNING id, confirmed_at""", (observation_id, user_id)).fetchone()


def restore_observation(conn: psycopg.Connection, user_id: int, observation_id: int) -> dict | None:
    return conn.execute(
        """UPDATE assistant_observations SET status = 'active', archived_at = NULL, archived_reason = NULL,
             merged_into = NULL, confirmed_at = now()
           WHERE id = %s AND user_id = %s AND status = 'archived' RETURNING id, text""",
        (observation_id, user_id)).fetchone()


def backfill_topics(conn: psycopg.Connection) -> dict:
    """Data step 660 (db.py): topics of existing entries, inferred from the key / the text; NULL where nothing fits."""
    n_card = n_obs = 0
    for c in conn.execute("SELECT user_id, key, label, value FROM assistant_profile WHERE topic IS NULL").fetchall():
        if (t := topic_for_key(c["key"], c["label"], c["value"])):
            n_card += conn.execute("UPDATE assistant_profile SET topic = %s WHERE user_id = %s AND key = %s",
                                   (t, c["user_id"], c["key"])).rowcount
    for o in conn.execute("SELECT id, text FROM assistant_observations WHERE topic IS NULL").fetchall():
        if (t := infer_topic(o["text"])):
            n_obs += conn.execute("UPDATE assistant_observations SET topic = %s WHERE id = %s", (t, o["id"])).rowcount
    return {"profile": n_card, "observations": n_obs}


def delete_observation(conn: psycopg.Connection, user_id: int, observation_id: int) -> dict | None:
    return conn.execute("DELETE FROM assistant_observations WHERE id = %s AND user_id = %s RETURNING id, text",
                        (observation_id, user_id)).fetchone()


# ---------------------------------------------------------------- initial profile card
def _age(birth: date | None, t: date) -> int | None:
    if birth is None:
        return None
    return t.year - birth.year - ((t.month, t.day) < (birth.month, birth.day))


def _num(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def from_database(conn: psycopg.Connection, user_id: int) -> dict[str, str]:
    """Profile card items derived from existing database data (works for anyone). Items the database has no data for are omitted."""
    out: dict[str, str] = {}
    u = conn.execute("SELECT birth_date, height_cm, sex, timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    if u:
        parts = []
        if (a := _age(u["birth_date"], queries.local_today(conn, user_id))) is not None:
            parts.append(f"{a} 岁（{u['birth_date']} 生）")
        if u["height_cm"]:
            parts.append(f"身高 {u['height_cm']:g}cm")
        parts.append(f"时区 {u['timezone']}")
        out["basics"] = "，".join(parts)

    abnormal = [r for r in records_store.lab_report(conn, user_id) if r["status"] in ("high", "low", "abnormal")]
    if abnormal:
        items = []
        for r in abnormal:
            last = r["latest"]
            val = last["value_text"] or (f"{_num(last['value'])} {last['unit'] or ''}".strip())
            rng = f"，上限 {_num(last['ref_high'])}" if r["status"] == "high" and last["ref_high"] is not None else ""
            items.append(f"{r['test_name']} {val}（{last['collected_on']}{rng}）")
        out["health_constraints"] = "化验异常：" + "；".join(items) + "。最新数值以 get_lab_results 为准。"

    rules = records_store.exercise_rules(conn, user_id)
    if rules["banned"] or rules["caution"]:
        txt = []
        if rules["banned"]:
            txt.append("永久不做：" + "、".join(r["name_zh"] or r["exercise_ref"] for r in rules["banned"]))
        if rules["caution"]:
            txt.append("要小心：" + "；".join(
                f"{r['name_zh'] or r['exercise_ref']}" + (f"（{r['reason']}）" if r.get("reason") else "")
                for r in rules["caution"]))
        out["injuries"] = "。".join(txt) + "。以 get_exercise_rules 为准。"

    supps = records_store.list_supplements(conn, user_id, ["active", "as_needed"])
    if supps:
        out["supplements"] = "；".join(
            f"{s['name']} {s['dose'] or ''}".strip() + ("（按需）" if s["status"] == "as_needed" else "")
            for s in supps) + "。建议和状态以 list_supplements 为准。"
    return out


def init_profile(conn: psycopg.Connection, user_id: int, md_profile: dict[str, str] | None = None) -> dict:
    """Generate the initial profile card: database-derived + md_profile (transcription of body-management.md; if the same key is in both they are joined).
    Only inserts keys that do not exist yet (ON CONFLICT DO NOTHING), never overwrites later changes by the butler or the user. Returns {created: [...], kept: [...]}."""
    db = from_database(conn, user_id)
    md = md_profile or {}
    created, kept = [], []
    for key in sorted(set(db) | set(md), key=lambda k: STANDARD_KEYS.get(k, ("", 200))[1]):
        value = "\n".join(v for v in (md.get(key), db.get(key)) if v)
        label, sort = STANDARD_KEYS.get(key, (key, 200))
        row = conn.execute(
            """INSERT INTO assistant_profile (user_id, key, label, value, sort, source) VALUES (%s, %s, %s, %s, %s, 'seed')
               ON CONFLICT (user_id, key) DO NOTHING RETURNING key""",
            (user_id, key, label, value, sort)).fetchone()
        (created if row else kept).append(key)
    return {"created": created, "kept": kept}
