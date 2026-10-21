"""The fallback AI can read everything the Today page shows (issue #58).

Two maps, both checked against the source so they cannot go stale:
- every /v1/users/<user>/… endpoint the page's scripts (web/static/*.js) call -> the read tool with the same data, or
  a reason it needs none (writes, settings, account, static texts);
- every explanation key (explain/glossary*.py: the ⓘ sheets on cards and trend charts) -> the read tool with that number.
A new card, endpoint or explanation fails here until it is mapped; a mapped tool must be in fallback.TOOL_NAMES.
Pure: no database.
"""
import re
from pathlib import Path

from body_engine.ai import fallback
from body_engine.explain import glossary

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"

# endpoint (user segment and ${…} parts normalised to {x}, query dropped) -> fallback read tool(s)
READ = {
    "today": ["get_briefing"],
    "days": ["get_scores"],
    "trends": ["get_scores", "get_daily_metrics", "get_sleep_details", "get_stress", "get_healthspan", "get_vo2max"],
    "vo2max": ["get_vo2max"],
    "healthspan": ["get_healthspan"],
    "strain-coach": ["get_strain_coach"],
    "stress": ["get_stress"],
    "health-monitor": ["get_health_monitor"],
    "sleep-coach": ["get_sleep_coach"],
    "sleep/nights": ["get_sleep"],
    "sleep/details": ["get_sleep_details"],
    "goals/sleep": ["get_sleep_goal"],
    "reports": ["get_report"],
    "events": ["list_events"],
    "journal": ["get_journal"],
    "journal/items": ["list_journal_items"],
    "journal/frequent": ["list_journal_items", "get_journal"],
    "journal/analysis": ["analyze_journal"],
    "labs/summary": ["get_lab_results"],
    "labs/{x}/history": ["get_lab_history"],
    "supplements": ["list_supplements"],
    "memory": ["get_memory"],
    "profile": ["get_profile"],
    "training/calendar": ["get_training_calendar"],
    "training/day/{x}": ["get_training_day", "get_hr_zones"],
    "training/week": ["get_week_plan"],
    "training/exercises": ["search_exercises"],
    "training/exercises/{x}": ["get_exercise"],
    "diet-profile": ["get_diet_profile", "get_nutrition_goals"],
    "meal-templates": ["get_meal_templates"],
    "recipes": ["list_recipes"],
    "recipes/{x}": ["get_recipe"],
    "recipes/meal-plan": ["suggest_meals"],
    "grocery/list": ["get_shopping_list"],
    "grocery/products": ["find_products"],
    "grocery/specials": ["get_weekly_specials"],
    "grocery/store-products": ["get_price_history"],          # 「价格记录」 (prices from the user's own AI)
    "grocery/store-products/{x}": ["get_price_history"],
    "feedback": ["list_feedback"],
}
# endpoints with no data for the AI to read
NO_DATA = {
    "grocery": "prefix of the grocery helper (grocery.js base()); the calls are grocery/*",
    "explain/{x}/{x}": "the ⓘ texts themselves: static glossary + the numbers mapped below",
    "training/sparky": "connection state of the training log app",
    "training/sparky/connect": "write: connect the training log app",
    "training/plan": "write: save / change the week plan (save_plan, reschedule_day)",
    "training/plan/swap": "write: swap an exercise (swap_exercise)",
    "journal/{x}/{x}": "write: one journal answer (log_journal)",
    "meal-logs/{x}": "write: undo a quick meal",
    "meal-templates/{x}/log": "write: log a quick meal",
    "recipes/{x}/log": "write: log a recipe",
    "nutrition/entries/{x}": "write: delete a food entry (undo_sparky_entry)",
    "reminders/{x}": "write: dismiss a reminder",
    "supplements/{x}": "write: edit a supplement",
    "memory/observations/{x}": "write: delete an observation",
    "grocery/stores": "setting: which supermarkets are connected",
    "grocery/list/items/{x}": "write: tick an item",
    "feedback/{x}": "write: +1 / withdraw feedback",
    "feedback-mode": "setting",
    "ai-choice": "setting", "ai-fallback": "setting", "ai-fallback/consent": "setting",
    "ai-providers": "setting", "ai-providers/{x}": "setting", "ai-tokens": "setting", "ai-tokens/{x}": "setting",
    "consent": "account", "intro": "account", "invite-code": "account",
    "health-history": "upload of a Health export", "sync/refresh": "sync control", "sync/status": "sync control",
}

# explanation key -> fallback read tool(s) carrying that number
EXPLAIN = {
    "recovery": ["get_scores"], "hrv": ["get_scores"], "resting_hr": ["get_scores"], "sleep_score": ["get_scores"],
    "sleep_debt": ["get_scores"], "sleep_need": ["get_scores"], "sleep_performance": ["get_scores"],
    "strain": ["get_scores"], "training_load": ["get_scores"], "acwr": ["get_scores"], "muscle_load": ["get_scores"],
    "recovery_vs_strain": ["get_scores"],
    "sleep_hours": ["get_sleep"], "bedtime": ["get_sleep"], "restorative": ["get_sleep"],
    "sleep_efficiency": ["get_sleep_details"], "sleep_consistency": ["get_sleep_details"],
    "awakenings": ["get_sleep_details"],
    "resp_rate": ["get_health_monitor"], "spo2": ["get_health_monitor"], "wrist_temp": ["get_health_monitor"],
    "steps": ["get_daily_metrics"], "calories": ["get_daily_metrics"], "daylight": ["get_daily_metrics"],
    "aerobic_min": ["get_hr_zones"], "vo2max": ["get_vo2max"], "weight": ["get_weight"],
    "stress": ["get_stress"], "body_age": ["get_healthspan"], "behavior": ["analyze_journal"],
    "uric_acid": ["get_lab_history"], "alt": ["get_lab_history"], "triglycerides": ["get_lab_history"],
    "goal_weight": ["get_memory"], "phase": ["get_memory"], "red_meat_week": ["get_memory"], "to_goal": ["get_memory"],
    "training_week": ["get_memory"],
}
# Trends-tab status cards computed only inside /trends: no read tool has them yet, for any AI (follow-up to #58).
NO_TOOL_YET = {"illness", "recovery_index", "sleep_perf_whoop", "strain_combined", "resilience", "hrv_status",
               "training_status", "body_battery", "recovery_time", "chronotype"}
# Excluded from the fallback by its consent text (no ECG / beat-to-beat data): only reachable with the user's own AI.
CONSENT_EXCLUDED = {"hr_recovery": "get_cardio"}

TOKEN = r"(?:[A-Za-z0-9_/-]|\$\{[^}]*\})*"


def _norm(raw: str) -> str:
    p = re.sub(r"\$\{[^}]*\}", "{x}", raw)
    p = re.sub(r"(?<!/)\{x\}", "", p)   # ${…} glued to a word is a query builder, not a path segment
    return p.strip("/")


def page_endpoints() -> set[str]:
    found = set()
    for f in STATIC.glob("*.js"):
        src = f.read_text()
        for m in re.finditer(r"/v1/users/(?:me|\$\{[^}]*\})/(" + TOKEN + ")", src):
            found.add(_norm(m.group(1)))
        # path helpers: const base = () => `/v1/users/${…}/grocery`; used as `${base()}/list`
        for name, path in re.findall(r"const (\w+) = \([^)]*\) => `/v1/users/\$\{[^}]*\}/([A-Za-z0-9_/-]+)", src):
            for m in re.finditer(r"\$\{" + name + r"\([^)]*\)\}(" + TOKEN + ")", src):
                found.add(_norm(path + m.group(1)))
    return {p for p in found if p}


def test_endpoint_scan_finds_the_cards():
    found = page_endpoints()
    assert {"healthspan", "stress", "health-monitor", "reports", "grocery/specials", "recipes/meal-plan",
            "labs/{x}/history"} <= found, found


def test_every_page_endpoint_is_mapped():
    found = page_endpoints()
    unmapped = found - READ.keys() - NO_DATA.keys()
    assert not unmapped, f"Today page endpoints without a fallback read tool (add to READ or NO_DATA): {sorted(unmapped)}"
    stale = (READ.keys() | NO_DATA.keys()) - found
    assert not stale, f"mapped endpoints the page no longer calls: {sorted(stale)}"


def test_every_explained_metric_is_mapped():
    keys = set(glossary.METRICS) | set(glossary.STATUS) | set(glossary.LABS)
    covered = EXPLAIN.keys() | NO_TOOL_YET | CONSENT_EXCLUDED.keys()
    assert not keys - covered, f"explained metrics without a read tool: {sorted(keys - covered)}"
    assert not covered - keys, f"mapped keys that are no longer explained: {sorted(covered - keys)}"


def test_mapped_tools_are_in_the_fallback_set():
    allowed = set(fallback.TOOL_NAMES)
    missing = {t for tools in (*READ.values(), *EXPLAIN.values()) for t in tools} - allowed
    assert not missing, f"add to fallback.TOOL_NAMES: {sorted(missing)}"
    assert not set(CONSENT_EXCLUDED.values()) & allowed   # the consent says no ECG / beat-to-beat data
    assert {t.name for t in fallback.tools()} == allowed   # every name is a real tool
