"""Fallback AI: users who have not set up their own AI chat on the Today page with the admin's DeepSeek key (docs/ai-assistant-plan.md §9.10).

Who gets it: a user with no default provider in ai_providers and no body butler link (the deployer keeps the butler; admins test with
`body-engine ai-fallback try`). As soon as the user sets a default provider of their own, that one is used instead.

Limits (numbers and sources in fallback_params.py):
- per person per natural month (user's time zone) MONTHLY_QUOTA_CNY, overridable per person (table ai_fallback_quota, CLI ai-fallback quota);
  cost = DeepSeek's CNY price list at the peak price, from the call's cache hit / miss / output tokens, stored in ai_usage.cost_cny;
- before every model call: the worst case of this call (whole prompt as cache miss + max output) must fit in what is left;
- per person per minute / per day model-call limits; short system prompt (system_prompt_lite.md), a subset of tools, smaller max_tokens;
- the DeepSeek account balance is checked (at most every BALANCE_MAX_AGE_S); below BALANCE_STOP_CNY the fallback pauses for everyone.
Privacy: a separate opt-in consent (ai_fallback_consents) before the first use, because the data goes to DeepSeek (servers in China).
Tools only ever read the caller's own data: the `user` argument is removed from the schemas and forced to "me" (no shares).
The key file is read only when needed and never logged or returned.
"""
import dataclasses
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from ..assistant.tools import TOOLS_BY_NAME as ALL_TOOLS
from ..config import REPO_ROOT
from ..i18n import L, is_en
from . import fallback_params as fp
from . import providers

KIND = "system_fallback"
CONSENT_VERSION = "2026-10-22"   # daily metrics and grocery named; 10-20: writes without a confirm step (only delete / undo ask); .2 (10-18): photos, video frames, voice
# Read tools for everything the Today page shows + the everyday write tools. No ECG / raw heart data (get_cardio,
# get_ecg_list: excluded by the consent below), no family data, no recipe editing.
TOOL_NAMES = (
    "get_briefing", "get_profile", "get_scores", "get_sleep", "get_daily_metrics", "get_reminders", "get_sleep_goal",
    "get_strain_coach", "get_sleep_coach", "get_training_calendar", "get_training_day", "get_muscle_load", "get_exercise_rules",
    "search_exercises", "get_exercise", "get_week_plan", "propose_plan", "save_plan", "swap_exercise",
    "reschedule_day", "swap_days", "replan_week", "undo_day_changes",
    "get_training_log", "get_nutrition", "get_weight", "get_lab_results", "list_supplements",
    "list_events", "list_journal_items", "get_journal", "get_memory",
    "log_food", "log_workout", "log_weight", "log_water", "undo_sparky_entry", "log_journal", "add_event",
    "add_observation", "update_profile_card",
    "add_lab_results", "log_metric_snapshot", "add_supplement",   # lab report photos, watch / InBody screenshots, supplement bottles
    "report_feedback", "list_feedback",   # root rules (ai/root_rules.md): writes only the feedback queue
    # The read tool behind every Today page card (issue #58; tests/test_fallback_coverage.py maps cards -> tools):
    # computed metrics, the 饮食 page and 采购.
    "get_healthspan", "get_stress", "get_health_monitor", "get_hr_zones", "get_vo2max", "get_report",
    "get_sleep_details", "get_lab_history", "get_metric_snapshots", "analyze_journal",
    "get_diet_profile", "get_nutrition_goals", "get_meal_templates", "list_recipes", "get_recipe", "suggest_meals",
    "get_shopping_list", "get_weekly_specials", "find_products", "get_price_history",
    "get_diet_constraints", "set_diet_constraint", "end_diet_constraint",   # the user's own diet constraints (#76)
    "get_training_context", "get_week_review_context", "get_playbook",      # scene context + playbooks (#100); no ECG in them
)
CONSENT_POINTS = [
    "托底 AI 是管理员提供的 DeepSeek（深度求索，中国公司），费用管理员出，每人每月 ¥{quota:g} 额度。",
    "你提问时会发给 DeepSeek：你的消息、称呼 / 年龄 / 性别 / 身高 / 时区、长期记忆（档案卡、观察笔记、状态卡），"
    "以及回答需要时读的你的评分、睡眠、训练、饮食、体重、呼吸频率 / 血氧 / 手腕温度等每日指标、化验、补剂、日记、提醒，"
    "还有采购清单、家里存货、已连接的超市、商品价格和购买记录。",
    "你发的照片、截图和视频里抽出的画面也会发给 DeepSeek。",
    "语音在本服务器上转成文字（不发给任何第三方），你确认文字后才发送；录音转完立即删除，不保存。",
    "不发：心电图、逐拍心跳、家人的数据。",
    "DeepSeek 按它自己的用户协议和隐私政策处理这些内容；对话记录存在本服务器，你可以随时删除。",
    "AI 会直接把你说的饮食、训练、体重等写进你的记录（不再逐条确认），删除和撤销之前会先问你。",
    "随时可以在「我 → AI 设置」撤回；撤回后不再发送。也可以不用托底，接入你自己的 AI。",
]
# English translation of CONSENT_POINTS for the page (X-Body-Lang: en). The Chinese list is the authoritative text and
# CONSENT_VERSION is set by hand when the Chinese changes; this list never changes the version (change it together with the Chinese).
CONSENT_POINTS_EN = [
    "The backup AI is DeepSeek (a Chinese company), provided and paid for by the admin, "
    "with ¥{quota:g} per person per month.",
    "When you ask something, DeepSeek receives: your message, your name / age / sex / height / time zone, your long-term "
    "memory (profile card, observation notes, status card), and, when the answer needs them, your scores, sleep, workouts, "
    "food, weight, daily metrics such as respiratory rate / blood oxygen / wrist temperature, lab results, supplements, "
    "journal and reminders, plus shopping lists, what you have at home, the supermarkets you connected, product prices and "
    "purchase records.",
    "Photos, screenshots and frames taken from your videos are sent to DeepSeek too.",
    "Voice is turned into text on this server (never sent to any third party) and is sent only after you confirm the text; "
    "the recording is deleted right after, never stored.",
    "Never sent: ECGs, beat-to-beat heart data, your family's data.",
    "DeepSeek handles this content under its own terms of use and privacy policy; conversations are stored on this server "
    "and you can delete them at any time.",
    "The AI writes the food, workouts, weight etc. you mention straight into your log (no confirmation each time); "
    "it asks before deleting or undoing anything.",
    "You can withdraw at any time in Me → AI settings; after that nothing is sent. You can also skip the backup AI and "
    "connect your own AI.",
]


# ---------------------------------------------------------------- key, provider, tools
def key_path() -> Path:
    default = Path(os.environ.get("BODY_SECRETS_DIR", REPO_ROOT / "secrets")) / fp.KEY_FILE_NAME
    return Path(os.environ.get("BODY_DEEPSEEK_API_KEY_FILE", default))


def read_key() -> str | None:
    try:
        key = key_path().read_text().strip()
    except OSError:
        return None
    return key or None


def provider() -> dict:
    """A provider-shaped dict for chat.py (not a row: provider_id stays NULL in ai_usage / ai_conversations)."""
    return {"id": None, "user_id": None, "kind": KIND, "display_name": display_name(), "model": fp.MODEL,
            "base_url": fp.BASE_URL, "status": "active", "encrypted_secret": None}


def display_name() -> str:
    return L("Jarvis", "Jarvis")   # users never see the provider / model (owner, 2026-10-22); admins: ai-fallback status


def is_fallback(p: dict | None) -> bool:
    return bool(p) and p.get("kind") == KIND


def _own_schema(schema: dict) -> dict:
    props = {k: v for k, v in (schema.get("properties") or {}).items() if k != "user"}
    return {**schema, "properties": props, "required": [r for r in schema.get("required", []) if r != "user"]}


@lru_cache(maxsize=1)
def tools(extra: tuple = ()) -> tuple:
    """The whitelist as own-data-only tools; `extra`: names added behind a rollout flag (ai/web.fallback_extra)."""
    return tuple(dataclasses.replace(ALL_TOOLS[n], input_schema=_own_schema(ALL_TOOLS[n].input_schema))
                 for n in (*TOOL_NAMES, *(n for n in extra if n not in TOOL_NAMES)) if n in ALL_TOOLS)


# ---------------------------------------------------------------- money
def cost_cny(usage: dict) -> float:
    """usage: input_tokens = cache miss, cache_read_tokens = cache hit, output_tokens."""
    return (usage.get("input_tokens", 0) * fp.PRICE_CACHE_MISS_CNY
            + usage.get("cache_read_tokens", 0) * fp.PRICE_CACHE_HIT_CNY
            + usage.get("output_tokens", 0) * fp.PRICE_OUTPUT_CNY) / 1_000_000


def estimate_call_cny(system: str, history: list[dict], tool_list) -> float:
    """Worst case of the next call: the whole prompt as cache miss, plus the maximum output.
    Images count as IMAGE_TOKENS each (their base64 text is not counted)."""
    images, blocks = 0, []
    for m in history:
        for b in m["content"]:
            if b.get("type") == "image":
                images += 1
            else:
                blocks.append(b)
    chars = len(system) + len(json.dumps(blocks, ensure_ascii=False, default=str))
    chars += sum(len(t.description) + len(json.dumps(t.input_schema, ensure_ascii=False)) for t in tool_list)
    tokens = chars / fp.CHARS_PER_TOKEN + images * fp.IMAGE_TOKENS
    return (tokens * fp.PRICE_CACHE_MISS_CNY + fp.MAX_OUTPUT_TOKENS * fp.PRICE_OUTPUT_CNY) / 1_000_000


def quota_cny(conn: psycopg.Connection, user_id: int) -> float:
    row = conn.execute("SELECT monthly_cny FROM ai_fallback_quota WHERE user_id = %s", (user_id,)).fetchone()
    return float(row["monthly_cny"]) if row else fp.MONTHLY_QUOTA_CNY


def set_quota(conn: psycopg.Connection, user_id: int, cny: float | None, note: str | None = None) -> None:
    """cny=None: back to the default."""
    if cny is None:
        conn.execute("DELETE FROM ai_fallback_quota WHERE user_id = %s", (user_id,))
        return
    if not 0 <= cny <= 1000:
        raise ValueError(L("额度要在 0–1000 元之间", "The quota must be between ¥0 and ¥1000"))
    conn.execute("""INSERT INTO ai_fallback_quota (user_id, monthly_cny, note) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET monthly_cny = EXCLUDED.monthly_cny, note = EXCLUDED.note,
                    updated_at = now()""", (user_id, cny, note))


def month_spent_cny(conn: psycopg.Connection, user_id: int) -> float:
    """What the admin paid for this person this month: fallback model calls + server web searches (ai/web, issue #150)."""
    return float(conn.execute("""SELECT coalesce(sum(cost_cny), 0) AS c FROM ai_usage
                                 WHERE user_id = %s AND (kind = %s OR purpose IN ('web_search', 'web_fetch')) AND at >= %s""",
                              (user_id, KIND, providers.month_start(conn, user_id))).fetchone()["c"])


def reset_on(conn: psycopg.Connection, user_id: int) -> date:
    start = providers.month_start(conn, user_id)
    return (start + timedelta(days=32)).replace(day=1).date()


def _calls_since(conn, user_id: int, since: datetime) -> int:
    return conn.execute("""SELECT count(*) AS n FROM ai_usage WHERE user_id = %s AND kind = %s AND at >= %s
                           AND purpose NOT IN ('web_search', 'web_fetch')""",   # model calls only (not the web tools)
                        (user_id, KIND, since)).fetchone()["n"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def calls_today(conn, user_id: int) -> int:
    """Model calls since the user's local midnight (their time zone): the RATE_PER_DAY counter."""
    tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"] or "UTC"
    start = _now().astimezone(ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    return _calls_since(conn, user_id, start)


# ---------------------------------------------------------------- consent
def consent(conn: psycopg.Connection, user_id: int) -> dict | None:
    """The current (not revoked, current version) consent, or None. The owner (the admin who pays for it) needs none."""
    u = conn.execute("SELECT role FROM users WHERE id = %s", (user_id,)).fetchone()
    if u and u["role"] == "owner":
        return {"version": CONSENT_VERSION, "accepted_at": None, "implicit": True}
    return conn.execute("""SELECT version, accepted_at FROM ai_fallback_consents
                           WHERE user_id = %s AND revoked_at IS NULL AND version = %s
                           ORDER BY accepted_at DESC LIMIT 1""", (user_id, CONSENT_VERSION)).fetchone()


def accept_consent(conn: psycopg.Connection, user_id: int, version: str, user_agent: str | None = None) -> None:
    if version != CONSENT_VERSION:
        raise ValueError(L("Jarvis 的说明更新了，请刷新后再看一遍", "Jarvis's terms were updated; reload and read them again"))
    if consent(conn, user_id) is None:
        conn.execute("INSERT INTO ai_fallback_consents (user_id, version, user_agent) VALUES (%s, %s, %s)",
                     (user_id, version, (user_agent or "")[:300] or None))


def revoke_consent(conn: psycopg.Connection, user_id: int) -> int:
    return conn.execute("""UPDATE ai_fallback_consents SET revoked_at = now()
                           WHERE user_id = %s AND revoked_at IS NULL""", (user_id,)).rowcount


def consent_points(conn: psycopg.Connection, user_id: int) -> list[str]:
    """In the request's language (English is a translation; the version stays the Chinese text's CONSENT_VERSION)."""
    q = quota_cny(conn, user_id)
    return [p.format(quota=q) for p in (CONSENT_POINTS_EN if is_en() else CONSENT_POINTS)]


# ---------------------------------------------------------------- global state and balance
def state(conn: psycopg.Connection) -> dict:
    return conn.execute("SELECT * FROM ai_fallback_state WHERE id = 1").fetchone() or {"enabled": False}


def set_enabled(conn: psycopg.Connection, enabled: bool) -> None:
    conn.execute("UPDATE ai_fallback_state SET enabled = %s, updated_at = now() WHERE id = 1", (enabled,))


def fetch_balance(key: str, timeout: float = 8) -> float:
    """DeepSeek GET /user/balance (free). Returns the CNY total balance."""
    req = urllib.request.Request(fp.BASE_URL + "/user/balance",
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
        data = json.loads(resp.read())
    infos = [i for i in data.get("balance_infos") or [] if i.get("currency") == "CNY"]
    if not infos:
        raise ValueError("余额接口没有返回人民币余额")  # admin only (CLI / doctor)
    return float(infos[0]["total_balance"])


def refresh_balance(conn: psycopg.Connection, force: bool = False) -> dict:
    """Re-check the balance when the last check is older than BALANCE_MAX_AGE_S (or force). Failures are recorded, not raised."""
    st = state(conn)
    at = st.get("balance_checked_at")
    if not force and at is not None and (datetime.now(at.tzinfo) - at).total_seconds() < fp.BALANCE_MAX_AGE_S:
        return st
    key = read_key()
    if key is None:
        return st
    try:
        bal, err = fetch_balance(key), None
    except urllib.error.HTTPError as e:
        bal, err = None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001  network / format: keep the old balance, note the error
        bal, err = None, type(e).__name__
    if bal is not None:
        conn.execute("""UPDATE ai_fallback_state SET balance_cny = %s, balance_checked_at = now(), balance_error = NULL,
                        updated_at = now() WHERE id = 1""", (bal,))
    else:
        conn.execute("""UPDATE ai_fallback_state SET balance_checked_at = now(), balance_error = %s, updated_at = now()
                        WHERE id = 1""", (err,))
    conn.commit()
    return state(conn)


def paused_reason(conn: psycopg.Connection, refresh: bool = True) -> tuple[str, str] | None:
    """Reasons that stop the fallback for everyone: (code, message) or None."""
    if read_key() is None:
        return "fallback_off", L("Jarvis 还没开通", "Jarvis is not set up yet")
    st = refresh_balance(conn) if refresh else state(conn)
    if not st.get("enabled"):
        return "fallback_off", L("Jarvis 已被管理员暂停", "The admin has paused Jarvis")
    if st.get("balance_error") == "HTTP 401":
        return "fallback_paused", L("Jarvis 暂停了，管理员已经在处理",
                                    "Jarvis is paused; the admin is on it")
    bal = st.get("balance_cny")
    if bal is not None and float(bal) < fp.BALANCE_STOP_CNY:
        return "fallback_paused", L("Jarvis 暂停了，管理员已经在处理",
                                    "Jarvis is paused; the admin is on it")
    return None


# ---------------------------------------------------------------- per-user availability
def summary(conn: psycopg.Connection, user_id: int, refresh: bool = False) -> dict:
    """For the Today page (/v1/web/me ai, AI settings): short numbers + whether it can be used now and why not."""
    from ..profile import consent as main_consent
    spent, quota = month_spent_cny(conn, user_id), quota_cny(conn, user_id)
    c = consent(conn, user_id)
    out = {"mode": "fallback", "kind": KIND, "display_name": display_name(), "model": fp.MODEL,
           "status": "active", "vision": fp.VISION, "spent_cny": round(spent, 4), "quota_cny": quota,
           "remaining_cny": round(max(quota - spent, 0), 4), "reset_on": reset_on(conn, user_id).isoformat(),
           "remaining_pct": max(0, min(100, round(100 * (quota - spent) / quota))) if quota else 0,   # what users see
           "consent": c is not None, "consent_version": CONSENT_VERSION,
           "consented_at": c.get("accepted_at") if c else None}
    reason = paused_reason(conn, refresh)
    if reason is None and main_consent.needs_consent(conn, user_id):
        reason = ("needs_consent", L("先在今天页同意健康数据使用同意书", "First accept the health data consent on the Today page"))
    if reason is None and quota <= 0:
        reason = ("fallback_off", L("你的账号没有 Jarvis 额度", "Your account has no Jarvis allowance"))
    if reason is None and spent >= quota:
        reason = ("quota", L(f"Jarvis 本月的额度用完了，{out['reset_on'][5:]} 重置",
                             f"Jarvis has used up this month's allowance; it resets on {out['reset_on']}"))
    if reason is None and c is None:
        reason = ("fallback_consent", L("第一次用 Jarvis 要先同意", "Agree to the terms before using Jarvis for the first time"))
    out["available"] = reason is None
    out["reason"], out["message"] = reason if reason else (None, None)
    return out


def require_available(conn: psycopg.Connection, user_id: int, need_quota: bool = True) -> None:
    """Before starting a fallback chat. need_quota=False (confirming a write already proposed): only consent / pause count."""
    from .chat import ChatError
    s = summary(conn, user_id, refresh=True)
    if s["available"] or (not need_quota and s["reason"] == "quota"):
        return
    status = 402 if s["reason"] == "quota" else (403 if s["reason"] in ("fallback_consent", "needs_consent") else 409)
    raise ChatError(s["reason"], s["message"], status=status, **_quota_extra(s))


def _quota_extra(s: dict) -> dict:
    return {"spent_cny": s["spent_cny"], "quota_cny": s["quota_cny"], "remaining_cny": s["remaining_cny"],
            "remaining_pct": s["remaining_pct"], "reset_on": s["reset_on"]}


def precheck(conn: psycopg.Connection, user_id: int, system: str, history: list[dict], tool_list=None) -> tuple[float, float]:
    """Before each model call: monthly quota, worst-case cost of this call, per-minute / per-day limits. Returns (spent, quota).
    tool_list: the tools this call sends (core + page groups, ai/chat.py); None = the whole whitelist."""
    from .chat import ChatError
    s = summary(conn, user_id)
    if s["reason"] in ("fallback_off", "fallback_paused"):
        raise ChatError(s["reason"], s["message"], status=409)
    spent, quota = s["spent_cny"], s["quota_cny"]
    extra = _quota_extra(s)
    if spent >= quota:
        raise ChatError("quota", L(f"Jarvis 本月的额度用完了，{s['reset_on'][5:]} 重置；"
                                   "也可以在「我 → AI 设置」接入自己的 AI",
                                   f"Jarvis has used up this month's allowance; it resets on {s['reset_on']}. "
                                   "You can also connect your own AI in Me → AI settings"), status=402, **extra)
    if estimate_call_cny(system, history, tools() if tool_list is None else tool_list) > quota - spent:
        raise ChatError("quota", L(f"Jarvis 本月剩下的额度不够再问一轮，{s['reset_on'][5:]} 重置；"
                                   "也可以在「我 → AI 设置」接入自己的 AI",
                                   f"What is left of Jarvis's allowance this month is not enough for another round; "
                                   f"it resets on {s['reset_on']}. You can also connect your own AI in Me → AI settings"),
                        status=402, **extra)
    if _calls_since(conn, user_id, _now() - timedelta(minutes=1)) >= fp.RATE_PER_MINUTE:
        raise ChatError("rate_limit", L("问得太快了，歇一分钟再问", "Too many questions at once; wait a minute"), status=429)
    if calls_today(conn, user_id) >= fp.RATE_PER_DAY:
        raise ChatError("rate_limit", L("今天问得太多了，明天再来", "That's all the questions for today; come back tomorrow"), status=429)
    return spent, quota


# ---------------------------------------------------------------- admin
def admin_status(conn: psycopg.Connection, refresh: bool = True) -> dict:
    """For body-engine ai-fallback status and the Ali doctor: global switch, balance, this month per user."""
    st = refresh_balance(conn, force=refresh) if read_key() else state(conn)
    users = []
    for u in conn.execute("SELECT id, display_name, role FROM users ORDER BY id"):
        spent, quota = month_spent_cny(conn, u["id"]), quota_cny(conn, u["id"])
        n = conn.execute("SELECT count(*) AS n, coalesce(sum(input_tokens + cache_read_tokens), 0) AS i, "
                         "coalesce(sum(output_tokens), 0) AS o FROM ai_usage WHERE user_id = %s AND kind = %s AND at >= %s",
                         (u["id"], KIND, providers.month_start(conn, u["id"]))).fetchone()
        from .choice import effective
        e = effective(conn, u["id"])
        users.append({"user_id": u["id"], "name": u["display_name"], "role": u["role"], "spent_cny": round(spent, 4),
                      "quota_cny": quota, "calls": n["n"], "input_tokens": int(n["i"]), "output_tokens": int(n["o"]),
                      "consent": consent(conn, u["id"]) is not None,
                      "uses": e["provider"]["kind"] if e["source"] == "provider" else e["source"]})
    eligible = [u for u in users if u["uses"] == "fallback" and u["quota_cny"] > 0]
    reason = paused_reason(conn, refresh=False)
    bal = st.get("balance_cny")
    return {"key": read_key() is not None, "enabled": bool(st.get("enabled")),
            "available": reason is None, "reason": reason[1] if reason else None,
            "balance_cny": float(bal) if bal is not None else None, "balance_checked_at": st.get("balance_checked_at"),
            "balance_error": st.get("balance_error"),
            "month_spent_cny": round(sum(u["spent_cny"] for u in users), 4),
            "month_remaining_cny": round(sum(max(u["quota_cny"] - u["spent_cny"], 0) for u in eligible), 4),
            "eligible_users": len(eligible), "stop_cny": fp.BALANCE_STOP_CNY, "warn_cny": fp.BALANCE_WARN_CNY,
            "topup_url": fp.TOPUP_URL, "model": fp.MODEL, "price_source": fp.PRICE_SOURCE, "users": users}
