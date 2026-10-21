"""Long-term memory of the body butler: the body profile card (key-value) + observation notes; table structure in migrations/060_assistant_memory.sql.

- The profile card is read first in every conversation (get_memory); the butler edits it with update_profile_card, the user edits it on the Today page with PUT .../memory/profile/{key}.
- Observation notes are new facts learned in conversation, with source and date; the user can delete them (DELETE .../memory/observations/{id}).
- memory-init generates the initial profile card from existing database data (lab results, exercise rules, supplements, basic info) and body-management.md (transcribed in memory_seed.py);
  it only fills missing keys and never overwrites later changes.
The caller is responsible for authorization (reading needs can_view(scope=memory), writing only for the caller); here we only read / write by user_id.
"""
import re
from datetime import date

import psycopg

from .. import queries
from ..records import store as records_store

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
}


def get_memory(conn: psycopg.Connection, user_id: int) -> dict:
    card = conn.execute(
        """SELECT key, label, value, updated_at, source FROM assistant_profile
           WHERE user_id = %s ORDER BY sort, key""", (user_id,)).fetchall()
    obs = conn.execute(
        """SELECT id, text, created_at, source, evidence FROM assistant_observations
           WHERE user_id = %s ORDER BY created_at, id""", (user_id,)).fetchall()
    return {"profile_card": card, "observations": obs}


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


def add_observation(conn: psycopg.Connection, user_id: int, text: str, evidence: str | None = None,
                    source: str = "assistant") -> dict:
    """Record an observation. If the same text was already recorded, do not repeat it (return the old one, duplicate=true)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")
    if len(text) > MAX_OBSERVATION:
        raise ValueError(f"观察笔记不超过 {MAX_OBSERVATION} 字，写成一句话的事实")
    row = conn.execute(
        """INSERT INTO assistant_observations (user_id, text, evidence, source) VALUES (%s, %s, %s, %s)
           ON CONFLICT (user_id, md5(btrim(text))) DO NOTHING
           RETURNING id, text, created_at, source, evidence""", (user_id, text, evidence, source)).fetchone()
    if row is not None:
        return {**row, "duplicate": False}
    old = conn.execute(
        """SELECT id, text, created_at, source, evidence FROM assistant_observations
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
