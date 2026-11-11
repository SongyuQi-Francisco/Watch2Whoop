#!/usr/bin/env python3
"""A fake Body Engine for Jarvis demos in the simulator (#340 N6): no database, no paid AI, made-up numbers only.

    python3 apps/ios/scripts/fake_jarvis_server.py            # http://127.0.0.1:18766
    SIMCTL_CHILD_W2W_DEV_SERVER=http://127.0.0.1:18766 SIMCTL_CHILD_W2W_DEV_TOKEN=demo \
      SIMCTL_CHILD_W2W_JARVIS_DEMO=dial xcrun simctl launch --console-pty <device> <bundle id>

It answers the endpoints the native Jarvis uses with the same shapes as body_engine/src/body_engine/ai (the chat's SSE
events, cards, a pending delete, the AI switcher, the fallback AI's terms, voice correction). Every other path is 404,
so the rest of the app shows its empty states. Standard library only.
"""
import json
import os
import pathlib
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18766
# Seconds /transcribe and /voice-fix take (W2W_FAKE_ASR_DELAY=2.5 stands in for the server's recogniser; default 0)
ASR_DELAY = float(os.environ.get("W2W_FAKE_ASR_DELAY") or 0)
# W2W_FAKE_ECHO=1: every answer names its question and comes after tool rounds (report_feedback, show_card), so two answers in
# a row can be told apart (the 2.0.1 repeated-reply check). W2W_FAKE_CUT=<n>: the next chat stream stops after n events
# without its last one, as a dropped connection; the app picks it up with GET /chat/{id}/events?after=.
ECHO = os.environ.get("W2W_FAKE_ECHO") == "1"
CUT = {"left": int(os.environ.get("W2W_FAKE_CUT") or 0)}
# conversation id -> the events of its latest turn (body_engine ai/runs.py keeps only that one per conversation)
RUNS = {}
STATE = {"choice": "fallback", "consent": True, "conversations": []}
# The cockpit's flows (2.0.1): the day's food log, two quick meals, today's session (made-up numbers; the nickname is 小林)
FOOD = {"entries": [{"id": "e1", "time": "08:10", "meal_type": "breakfast", "food": "燕麦碗", "quantity": 1, "unit": "份",
                     "calories": 420, "protein_g": 18, "carbs_g": 62, "fat_g": 11}], "next": 2, "logs": {}}
TEMPLATES = [{"id": 3, "name": "鸡胸饭", "meal": "lunch", "uses_30d": 12, "en": "Chicken rice",
              "totals": {"calories": 570, "protein_g": 45, "carbs_g": 66, "fat_g": 14}},
             {"id": 4, "name": "燕麦碗", "meal": "breakfast", "uses_30d": 8, "en": "Oat bowl",
              "totals": {"calories": 420, "protein_g": 18, "carbs_g": 62, "fat_g": 11}}]
MANUAL = {"next": 300, "by_key": {}}   # 补记: client_id -> workout id
MANUAL_TYPES = [("basketball", "篮球", "Basketball"), ("swim", "游泳", "Swimming"), ("incline_walk", "爬坡", "Incline walk"),
                ("run", "跑步", "Running"), ("cycling", "骑车", "Cycling"), ("other", "其他", "Other")]
GOAL = {"calories": 2300, "protein_g": 60, "carbs_g": 100, "fat_g": 35}
SETS = {"bench": [{"reps": 8, "weight_kg": 60, "done": True}] * 4 + [{"reps": 8, "weight_kg": 60, "done": False}],
        "row": [{"reps": 10, "weight_kg": 50, "done": False}] * 4, "press": [{"reps": 10, "weight_kg": 20, "done": False}] * 4,
        "curl": [{"reps": 12, "weight_kg": 12, "done": False}] * 5}


def nutrition(zh):
    tot = {k: sum(e.get(k) or 0 for e in FOOD["entries"]) for k in GOAL}
    return {"goal": GOAL, "intake": tot, "entries": FOOD["entries"], "logged": bool(FOOD["entries"])}


def add_entry(name, meal, totals):
    eid = "e%d" % FOOD["next"]
    FOOD["next"] += 1
    FOOD["entries"].append({"id": eid, "time": "12:30", "meal_type": meal, "food": name, "quantity": 1, "unit": "份", **totals})
    return eid


def templates(zh):
    today_logs = {tid: lid for lid, (tid, _) in FOOD["logs"].items()}
    return [{"id": t["id"], "name": t["name"] if zh else t["en"], "meal": t["meal"], "uses_30d": t["uses_30d"], "totals": t["totals"],
             "items": [{"food": t["name"] if zh else t["en"], "grams": 350, **t["totals"]}],
             **({"today_log": {"log_id": today_logs[t["id"]]}} if t["id"] in today_logs else {})} for t in TEMPLATES]


def session(zh):
    ex = [("bench", "卧推", "Bench press", 60), ("row", "杠铃划船", "Barbell row", 50), ("press", "哑铃推举", "Dumbbell press", 20),
          ("curl", "哑铃弯举", "Dumbbell curl", 12)]
    return {"sessions": [{"name": "上肢 A" if zh else "Upper A", "exercises": [
        {"slug": slug, "name_zh": n, "name_en": en, "status": "active", "modality": "weight_reps",
         "target": {"sets": len(SETS[slug]), "reps": SETS[slug][0]["reps"]}, "load": {"kg": kg}} for slug, n, en, kg in ex]}],
        "can_log": True, "log": {"status": "draft", "exercises": [{"slug": k, "sets": v} for k, v in SETS.items()]}}


def week(zh):
    """/training/week: a 4-day plan around today (Mon..Sun), the built-in splits on offer."""
    import datetime
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    names = {1: ("上肢 A", "Upper A"), 2: ("下肢 A", "Lower A"), 4: ("上肢 B", "Upper B"), 5: ("下肢 B", "Lower B")}
    sets = {1: 18, 2: 16, 4: 17, 5: 16}
    days = []
    for k in range(7):
        d = monday + datetime.timedelta(days=k)
        dow = k + 1
        if dow in names:
            n = names[dow][0 if zh else 1]
            days.append({"date": d.isoformat(), "planned": [{"name": n, "day_of_week": dow, "exercises": [
                {"slug": "x%d" % i, "name": "动作 %d" % i, "status": "active", "sets_text": "%d × 8" % s}
                for i, s in enumerate([4, 4, 4, 3, 3][: 5 if sets[dow] > 16 else 4] + ([2] if sets[dow] == 18 else []))]}]})
        else:
            days.append({"date": d.isoformat(), "rest_day": True, "planned": []})
    def split(t, zhn, en, dows):
        return {"split_type": t, "name": zhn, "name_en": en, "summary": "", "days": [{"day_of_week": x, "name": "D%d" % x, "exercise_count": 5} for x in dows]}
    return {"days": days, "plan": {"name": "上下肢四分化" if zh else "Upper / lower, 4 days", "split_type": "4_day_upper_lower"},
            "available_splits": [split("3_day_full_body", "全身三分化", "Full body, 3 days", [1, 3, 5]),
                                 split("4_day_upper_lower", "上下肢四分化", "Upper / lower, 4 days", [1, 2, 4, 5]),
                                 split("5_day_ppl", "推拉腿五分化", "Push / pull / legs, 5 days", [1, 2, 3, 4, 5])]}


def cockpit_answer(msg, zh):
    """The cockpit's voice flows: a meal or a set is logged (the server side of log_food / the session log), then a card."""
    if "鸡胸饭" in msg or "chicken" in msg.lower():
        t = TEMPLATES[0]
        add_entry(t["name"] if zh else t["en"], "lunch", t["totals"])
        text = "已记到午餐：鸡胸饭 570 kcal。" if zh else "Logged to lunch: chicken rice, 570 kcal."
        return [("tool", {"name": "log_food", "status": "running"}), ("tool", {"name": "log_food", "status": "done"}),
                ("card", {"card": {"v": 1, "type": "meal", "date": days(1)[0], "logged": True, "totals": nutrition(zh)["intake"],
                                   "goal": GOAL, "entries": FOOD["entries"], "actions": [{"kind": "open", "page": "food"}]}, "call_id": 501}),
                ("text", {"delta": text})]
    if "卧推" in msg or "bench" in msg.lower():
        i = next((k for k, x in enumerate(SETS["bench"]) if not x["done"]), None)
        if i is not None:
            SETS["bench"] = SETS["bench"][:i] + [{"reps": 8, "weight_kg": 60, "done": True}] + SETS["bench"][i + 1:]
        text = "记好了：卧推 60 kg × 8。" if zh else "Logged: bench press 60 kg × 8."
        return [("tool", {"name": "log_training_set", "status": "running"}), ("tool", {"name": "log_training_set", "status": "done"}),
                ("text", {"delta": text})]
    return None



def ai():
    if STATE["choice"] == "fallback":
        return {"mode": "fallback", "display_name": "Jarvis", "consent": STATE["consent"], "consent_version": "demo-1",
                "available": STATE["consent"], "reason": None if STATE["consent"] else "fallback_consent",
                "remaining_pct": 82, "quota_cny": 3, "spent_cny": 0.54, "vision": True}
    return {"mode": "api", "kind": "anthropic_api", "display_name": "My API key", "model": "demo-model", "vision": True}


def options():
    return [{"key": "fallback", "label": "Jarvis", "kind": "fallback", "available": True, "note": "82%",
             "consent": STATE["consent"], "current": STATE["choice"] == "fallback"},
            {"key": "p:7", "label": "My API key", "kind": "api", "available": True, "note": "demo-model",
             "current": STATE["choice"] == "p:7"}]


def days(n):
    t = time.localtime()
    base = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 12, 0, 0, 0, 0, -1))
    return [time.strftime("%Y-%m-%d", time.localtime(base - 86400 * (n - 1 - i))) for i in range(n)]


def cards(zh):
    d = days(14)
    hrv = [52, 55, 49, 58, 61, 57, None, 54, 60, 63, 59, 62, 66, 64]
    return [
        {"v": 1, "type": "metric", "metric": "hrv", "unit": "ms", "digits": 0, "dates": d, "values": hrv, "latest": 64, "avg": 58,
         "actions": [{"kind": "open", "page": "trends"}, {"kind": "send", "key": "explain_trend"}]},
        {"v": 1, "type": "training", "date": d[-1], "is_today": True, "plan": "Push / Pull / Legs" if not zh else "推 / 拉 / 腿",
         "sessions": ["Legs" if not zh else "腿"], "rest_day": False, "readiness": {"tier": "green", "label": "Ready" if not zh else "可以练"},
         "exercises": [{"name_zh": "深蹲", "name_en": "Back squat", "target": "4×6"}, {"name_zh": "罗马尼亚硬拉", "name_en": "Romanian deadlift", "target": "3×8"},
                       {"name_zh": "腿举", "name_en": "Leg press", "target": "3×12"}],
         "log": None, "actions": [{"kind": "open", "page": "record.training"}, {"kind": "send", "key": "adjust_training"}]},
    ]


def echo_answer(msg, zh):
    q = msg.replace("［语音］", "").replace("[voice] ", "")[:24]
    text = (f"关于「{q}」：今天恢复 64%，可以按计划练。" if zh else f"About \u201c{q}\u201d: recovery is 64%, train as planned.")
    out = [("tool", {"name": "get_briefing", "status": "running"}), ("tool", {"name": "get_briefing", "status": "done"}),
           ("tool", {"name": "report_feedback", "status": "running"}), ("tool", {"name": "report_feedback", "status": "done"}),
           ("card", {"card": cards(zh)[0], "call_id": 300})]
    for i in range(0, len(text), 6):
        out.append(("text", {"delta": text[i:i + 6]}))
    return out


def answer(msg, zh):
    if msg.strip() == "cards":   # W2W_JARVIS_DEMO=cards: one card of every server type (the unit tests' fixture)
        fixture = pathlib.Path(__file__).resolve().parent.parent / "Watch2WhoopTests" / "Fixtures" / "jarvis_cards.json"
        return [("card", {"card": c, "call_id": 200 + i}) for i, c in enumerate(json.loads(fixture.read_text()))]
    c = cockpit_answer(msg, zh)
    if c:
        return c
    if any(w in msg for w in ("删", "delete", "undo", "撤销")):
        return [("text", {"delta": "要删掉的是今天 12:30 记的那条午饭。" if zh else "That is the lunch logged at 12:30 today."}),
                ("confirm", {"conversation_id": 1, "calls": [{"call_id": 41, "tool": "undo_meal_log", "args": {"id": 9},
                                                             "summary": "撤销一次快捷记餐：{\"id\": 9}" if zh else "Undo a quick meal log: {\"id\": 9}"}]})]
    text = ("**可以练**，恢复在正常范围。\n\n"
            "- HRV 最近 14 天在上升，今天 **64 ms**，高于均值 58\n"
            "- 昨晚睡了 7:42，比需求少 10 分钟\n\n"
            "建议：按计划练腿，深蹲最后一组留 2 次余量。") if zh else (
            "**Good to train**; recovery is in range.\n\n"
            "- HRV has risen over 14 days: **64 ms** today, above the 58 average\n"
            "- You slept 7:42, 10 minutes short of your need\n\n"
            "Plan: legs as planned, leave 2 reps in reserve on the last squat set.")
    out = [("tool", {"name": "get_briefing", "status": "running"}), ("tool", {"name": "get_briefing", "status": "done"})]
    for i in range(0, len(text), 6):
        out.append(("text", {"delta": text[i:i + 6]}))
    out += [("card", {"card": c, "call_id": 100 + i}) for i, c in enumerate(cards(zh))]
    out.append(("card", {"card": {"v": 1, "type": "actions", "actions": [
        {"kind": "send", "label": "换成上肢" if zh else "Upper body instead", "text": "今天换成练上肢" if zh else "Switch today to upper body"},
        {"kind": "send", "label": "推迟一天" if zh else "Move to tomorrow", "text": "腿推迟到明天" if zh else "Move legs to tomorrow"}]}}))
    return out


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.command, self.path))

    def body(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw) if raw and self.headers.get("content-type", "").startswith("application/json") else {}
        except ValueError:
            return {}

    def json(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def sse(self, events, delay=0.03, first=1, cut=0):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        for seq, (ev, data) in enumerate(events, first):
            if cut and seq > cut:
                break   # a dropped connection: no last event
            self.wfile.write(f"id: {seq}\nevent: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            time.sleep(delay)
        self.close_connection = True

    @property
    def zh(self):
        return (self.headers.get("x-body-lang") or "zh") != "en"

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/v1/web/me":
            extra = [f for f in os.environ.get("W2W_FAKE_FLAGS", "").split(",") if f]   # e.g. friends: food moves into 记录
            return self.json({"me": {"id": 1, "display_name": "Demo"}, "flags": ["water", "jarvis_voice_direct", *extra], "ai": ai()})
        if p == "/v1/users/me/ai-choice":
            return self.json({"options": options(), "ai": ai()})
        if p == "/v1/users/me/ai-fallback":
            pts = ["你发给 Jarvis 的文字、照片和需要用到的健康数据会发给模型服务商处理。", "每月有额度，用完下个月重置。", "随时可以在「AI 设置」里撤回。"] \
                if self.zh else ["What you send to Jarvis and the health data it needs go to the model provider.",
                                 "There is a monthly allowance; it resets next month.", "You can withdraw any time in AI settings."]
            return self.json({"points": pts, "consent_version": "demo-1", "points_translated": not self.zh, "consent": STATE["consent"]})
        if p.startswith("/v1/assistant/chat/") and p.endswith("/events"):   # resume a turn after `after`
            cid = int(p.split("/")[4])
            after = int((self.path.split("after=")[1:] or ["0"])[0].split("&")[0])
            if cid not in RUNS:
                return self.sse([("resync", {"conversation_id": cid})])
            return self.sse(RUNS[cid][after:], first=after + 1)
        if p == "/v1/assistant/conversations":
            return self.json([{"id": 1, "title": "今天适合练吗" if self.zh else "Should I train today", "state": "idle",
                               "updated_at": days(1)[0] + "T08:30:00+10:00"},
                              {"id": 2, "title": "复盘这一周" if self.zh else "Review this week", "state": "idle",
                               "updated_at": days(3)[0] + "T21:05:00+10:00"}])
        if p.startswith("/v1/assistant/conversations/"):
            return self.json({"id": 2, "title": "", "state": "idle", "pending": [], "messages": [
                {"id": 1, "role": "user", "text": "复盘这一周" if self.zh else "Review this week", "images": 0, "tools": [], "cards": []},
                {"id": 2, "role": "assistant", "text": "这周练了 4 次，平均恢复 68%。" if self.zh else "4 sessions this week, recovery averaged 68%.",
                 "images": 0, "tools": [], "cards": [cards(self.zh)[0]]}]})
        if p == "/v1/users/me/meal-templates":
            return self.json(templates(self.zh))
        if p == "/v1/users/me/today":
            d = (self.path.split("date=")[1:] or [days(1)[0]])[0].split("&")[0]
            return self.json({"date": d, "user": {"timezone": "Australia/Sydney"}, "nutrition": nutrition(self.zh),
                              "scores": {"today": {"recovery": 82, "sleep_score": 88, "hrv": 64}, "yesterday": {"strain": 2.8},
                                         "recovery_zone": "green"}})
        if p == "/v1/users/me/training/manual-workouts/options":
            lv = [("light", "轻", "Light"), ("moderate", "中", "Moderate"), ("hard", "强", "Hard")]
            return self.json({"types": [{"type": t, "name": zh, "name_en": en} for t, zh, en in MANUAL_TYPES],
                              "intensities": [{"key": k, "label": zh if self.zh else en} for k, zh, en in lv],
                              "max_duration_min": 600, "max_past_days": 7})
        if p == "/v1/users/me/training/week":
            return self.json(week(self.zh))
        if p == "/v1/users/me/training/session":
            return self.json(session(self.zh))
        return self.json({"detail": "not here"}, 404)

    def do_PUT(self):
        if self.path.startswith("/v1/users/me/training/session/"):   # the session draft (undo of a set)
            for e in self.body().get("exercises", []):
                if e.get("slug") in SETS:
                    SETS[e["slug"]] = e.get("sets", SETS[e["slug"]])
            return self.json(session(self.zh))
        if self.path.startswith("/v1/users/me/ai-choice"):
            STATE["choice"] = self.body().get("key", "fallback")
            return self.json({"options": options(), "ai": ai(), "assistant_url": None})
        return self.json({"detail": "not here"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0]
        b = self.body()
        if p == "/v1/assistant/chat":
            if STATE["choice"] == "fallback" and not STATE["consent"]:
                return self.json({"detail": {"code": "fallback_consent", "message": "first agree"}}, 403)
            msg = b.get("message", "")
            ev = [("conversation", {"id": 1, "provider": "fallback", "model": "demo", "display_name": "Jarvis",
                                    "quota": {"spent_cny": 0.54, "quota_cny": 3, "remaining_cny": 2.46}})]
            ev += echo_answer(msg, self.zh) if ECHO else answer(msg, self.zh)
            ev += [("usage", {"currency": "CNY", "remaining_cny": 2.4, "quota_cny": 3, "month_cost_cny": 0.6})]
            if ev[-2][0] != "confirm":
                ev.append(("done", {"conversation_id": 1}))
            RUNS[1] = ev
            cut, CUT["left"] = CUT["left"], 0
            return self.sse(ev, cut=cut)
        if p == "/v1/assistant/chat/confirm":
            ok = b.get("approve")
            return self.sse([("tool", {"name": "undo_meal_log", "status": "done" if ok else "rejected", "call_id": 41}),
                             ("text", {"delta": ("好，已经撤销。" if ok else "好，没有删。") if self.zh else ("Done, undone." if ok else "OK, kept it.")}),
                             ("done", {"conversation_id": 1})])
        if p.startswith("/v1/users/me/meal-templates/") and p.endswith("/log"):
            tid = int(p.split("/")[5])
            if tid in [x for x, _ in FOOD["logs"].values()] and not b.get("allow_duplicates"):
                return self.json({"detail": {"code": "duplicate", "message": "already logged today"}}, 409)
            t = next(x for x in TEMPLATES if x["id"] == tid)
            lid = 900 + FOOD["next"]
            FOOD["logs"][lid] = (tid, add_entry(t["name"] if self.zh else t["en"], t["meal"], t["totals"]))
            return self.json({"log_id": lid, "entry_ids": [FOOD["logs"][lid][1]]})
        if p == "/v1/users/me/training/manual-workouts":
            key = b.get("client_id") or str(MANUAL["next"])
            created = key not in MANUAL["by_key"]
            if created:
                MANUAL["by_key"][key] = MANUAL["next"]
                MANUAL["next"] += 1
            name = next((zh if self.zh else en) for t, zh, en in MANUAL_TYPES if t == b.get("type"))
            day = b.get("date") or (b.get("start") or "")[:10]
            # W2W_FAKE_DUPLICATE=1: the watch already has it (duplicate_of)
            dup = {"id": 12, "source": "apple", "name": name, "date": day, "duration_min": 62} if os.environ.get("W2W_FAKE_DUPLICATE") else None
            return self.json({"created": created, "workout": {"id": MANUAL["by_key"][key], "name": name, "date": day,
                                                                 "duration_min": b.get("duration_min")}, "duplicate_of": dup})
        if p == "/v1/users/me/ai-fallback/consent":
            STATE["consent"] = True
            return self.json({"consent": True})
        if p == "/v1/assistant/voice-fix":
            time.sleep(ASR_DELAY)
            return self.json({"text": b.get("text", ""), "fixed": []})
        if p == "/v1/assistant/transcribe":   # the WAV was already read by body() above
            time.sleep(ASR_DELAY)
            return self.json({"text": "今天睡得不错，练腿四十分钟", "seconds": 3})
        return self.json({"detail": "not here"}, 404)

    def do_DELETE(self):
        p = self.path.split("?")[0]
        if p.startswith("/v1/users/me/training/manual-workouts/"):
            return self.json({"deleted": True})
        if p.startswith("/v1/users/me/meal-logs/"):
            _, eid = FOOD["logs"].pop(int(p.rsplit("/", 1)[1]), (None, None))
            FOOD["entries"] = [e for e in FOOD["entries"] if e["id"] != eid]
            return self.json({"deleted": True})
        if p.startswith("/v1/users/me/nutrition/entries/"):
            eid = p.rsplit("/", 1)[1]
            FOOD["entries"] = [e for e in FOOD["entries"] if e["id"] != eid]
            return self.json({"deleted": True})
        if not self.path.startswith("/v1/assistant/conversations/"):
            return self.json({"detail": "not here"}, 404)
        self.send_response(204)
        self.send_header("content-length", "0")
        self.end_headers()


if __name__ == "__main__":
    print(f"fake Jarvis server on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
