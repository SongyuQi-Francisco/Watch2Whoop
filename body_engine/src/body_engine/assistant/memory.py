"""Long-term memory of the body butler: the body profile card (key-value) + observation notes; table structure in migrations/060_assistant_memory.sql.

- The profile card is read first in every conversation (get_memory); the butler edits it with update_profile_card, the user edits it on the Today page with PUT .../memory/profile/{key}.
- Observation notes are new facts learned in conversation, with source and date; the user can delete them (DELETE .../memory/observations/{id}).
  kind='recent' notes are current circumstances with an end date (valid_until, user-local, inclusive; default
  memory_params.RECENT_DEFAULT_DAYS): once past it they leave get_memory / the conversation memory / get_briefing but are kept
  for get_memory(history=True) and week reviews (recent_during). Migration 520, issue #112.
- memory-init generates the initial profile card from existing database data (lab results, exercise rules, supplements, basic info) and body-management.md (transcribed in memory_seed.py);
  it only fills missing keys and never overwrites later changes.
The caller is responsible for authorization (reading needs can_view(scope=memory), writing only for the caller); here we only read / write by user_id.
"""
import re
from datetime import date, timedelta

import psycopg

from .. import queries
from ..records import store as records_store
from . import memory_params as mp

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
OBS_COLS = "id, text, created_at, source, evidence, kind, valid_from, valid_until"


def get_memory(conn: psycopg.Connection, user_id: int, history: bool = False, today: date | None = None) -> dict:
    """Profile card + observation notes (oldest first). Recent notes whose end date is before the user's local today are
    left out (counted in expired_recent) unless history=True."""
    card = conn.execute(
        """SELECT key, label, value, updated_at, source FROM assistant_profile
           WHERE user_id = %s ORDER BY sort, key""", (user_id,)).fetchall()
    today = today or queries.local_today(conn, user_id)
    obs = conn.execute(
        f"""SELECT {OBS_COLS} FROM assistant_observations
            WHERE user_id = %s ORDER BY created_at, id""", (user_id,)).fetchall()
    expired = [o for o in obs if is_expired(o, today)]
    if not history:
        obs = [o for o in obs if not is_expired(o, today)]
    return {"profile_card": card, "observations": obs, "expired_recent": len(expired)}


def is_expired(o: dict, today: date) -> bool:
    return o.get("kind") == "recent" and o["valid_until"] is not None and o["valid_until"] < today


def recent_during(conn: psycopg.Connection, user_id: int, start: date, end: date) -> list[dict]:
    """Recent notes that were in effect on any day of [start, end] (e.g. the week a review looks back on), oldest first."""
    return conn.execute(
        f"""SELECT {OBS_COLS} FROM assistant_observations
            WHERE user_id = %s AND kind = 'recent' AND valid_until >= %s AND COALESCE(valid_from, created_at::date) <= %s
            ORDER BY COALESCE(valid_from, created_at::date), id""", (user_id, start, end)).fetchall()


def _check_key(key: str) -> None:
    if not KEY_RE.match(key or ""):
        raise ValueError("键只能是小写字母、数字和下划线，字母开头，例 goals、schedule")


def update_profile(conn: psycopg.Connection, user_id: int, key: str, value: str, label: str | None = None,
                   source: str = "assistant") -> dict:
    """Create or wholly replace one profile card item. Standard keys need no label; new keys must give one.
    The text becomes the truth: structured `data` (diet profile sections, assistant/diet_profile.py) is cleared."""
    _check_key(key)
    value = (value or "").strip()
    if not value:
        raise ValueError("value 不能为空（要清掉这一项，写「暂无」或在今天页删掉）")
    if len(value) > MAX_VALUE:
        raise ValueError(f"value 太长（{len(value)} 字），档案卡每项不超过 {MAX_VALUE} 字，写摘要")
    std_label, sort = STANDARD_KEYS.get(key, (None, 200))
    existing = conn.execute("SELECT label FROM assistant_profile WHERE user_id = %s AND key = %s",
                            (user_id, key)).fetchone()
    label = (label or "").strip() or std_label or (existing or {}).get("label")
    if not label:
        raise ValueError(f"{key} 不是标准键（{', '.join(STANDARD_KEYS)}），新键要给 label（中文名）")
    return conn.execute(
        """INSERT INTO assistant_profile (user_id, key, label, value, sort, source) VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (user_id, key) DO UPDATE SET label = EXCLUDED.label, value = EXCLUDED.value,
             source = EXCLUDED.source, data = NULL, updated_at = now()
           RETURNING key, label, value, updated_at, source""",
        (user_id, key, label, value, sort, source)).fetchone()


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
                    source: str = "assistant", until: str | date | None = None) -> dict:
    """Record an observation. If the same text was already recorded, do not repeat it (return the old one, duplicate=true).
    With `until` it is a recent note (kind=recent) ending that day; the same text again as a recent note moves its dates
    (so "still travelling" extends it instead of being dropped as a duplicate of the expired one)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")
    if len(text) > MAX_OBSERVATION:
        raise ValueError(f"观察笔记不超过 {MAX_OBSERVATION} 字，写成一句话的事实")
    span = _until(conn, user_id, until)
    kind, vfrom, vuntil = ("recent", *span) if span else ("fact", None, None)
    row = conn.execute(
        f"""INSERT INTO assistant_observations (user_id, text, evidence, source, kind, valid_from, valid_until)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, md5(btrim(text))) DO NOTHING
            RETURNING {OBS_COLS}""", (user_id, text, evidence, source, kind, vfrom, vuntil)).fetchone()
    if row is not None:
        return {**row, "duplicate": False}
    if span:
        old = conn.execute(
            f"""UPDATE assistant_observations SET kind = 'recent', valid_from = %s, valid_until = %s,
                  evidence = COALESCE(%s, evidence)
                WHERE user_id = %s AND md5(btrim(text)) = md5(btrim(%s)) RETURNING {OBS_COLS}""",
            (vfrom, vuntil, evidence, user_id, text)).fetchone()
        return {**old, "duplicate": True, "dates_updated": True}
    old = conn.execute(
        f"""SELECT {OBS_COLS} FROM assistant_observations
            WHERE user_id = %s AND md5(btrim(text)) = md5(btrim(%s))""", (user_id, text)).fetchone()
    return {**old, "duplicate": True}


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
