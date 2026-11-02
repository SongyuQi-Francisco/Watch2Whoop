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
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18766
STATE = {"choice": "fallback", "consent": True, "conversations": []}


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


def answer(msg, zh):
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

    def sse(self, events, delay=0.03):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        for seq, (ev, data) in enumerate(events, 1):
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
            return self.json({"me": {"id": 1, "display_name": "Demo"}, "flags": ["water", "jarvis_voice_direct"], "ai": ai()})
        if p == "/v1/users/me/ai-choice":
            return self.json({"options": options(), "ai": ai()})
        if p == "/v1/users/me/ai-fallback":
            pts = ["你发给 Jarvis 的文字、照片和需要用到的健康数据会发给模型服务商处理。", "每月有额度，用完下个月重置。", "随时可以在「AI 设置」里撤回。"] \
                if self.zh else ["What you send to Jarvis and the health data it needs go to the model provider.",
                                 "There is a monthly allowance; it resets next month.", "You can withdraw any time in AI settings."]
            return self.json({"points": pts, "consent_version": "demo-1", "points_translated": not self.zh, "consent": STATE["consent"]})
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
            return self.json([{"id": 3, "name": "燕麦酸奶碗" if self.zh else "Oat yoghurt bowl", "uses_30d": 12},
                              {"id": 4, "name": "鸡胸沙拉" if self.zh else "Chicken salad", "uses_30d": 8}])
        return self.json({"detail": "not here"}, 404)

    def do_PUT(self):
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
            ev += answer(msg, self.zh)
            ev += [("usage", {"currency": "CNY", "remaining_cny": 2.4, "quota_cny": 3, "month_cost_cny": 0.6})]
            if ev[-2][0] != "confirm":
                ev.append(("done", {"conversation_id": 1}))
            return self.sse(ev)
        if p == "/v1/assistant/chat/confirm":
            ok = b.get("approve")
            return self.sse([("tool", {"name": "undo_meal_log", "status": "done" if ok else "rejected", "call_id": 41}),
                             ("text", {"delta": ("好，已经撤销。" if ok else "好，没有删。") if self.zh else ("Done, undone." if ok else "OK, kept it.")}),
                             ("done", {"conversation_id": 1})])
        if p == "/v1/users/me/ai-fallback/consent":
            STATE["consent"] = True
            return self.json({"consent": True})
        if p == "/v1/assistant/voice-fix":
            return self.json({"text": b.get("text", ""), "fixed": []})
        if p == "/v1/assistant/transcribe":
            self.rfile.read(int(self.headers.get("content-length") or 0))
            return self.json({"text": "今天睡得不错，练腿四十分钟", "seconds": 3})
        return self.json({"detail": "not here"}, 404)

    def do_DELETE(self):
        if not self.path.startswith("/v1/assistant/conversations/"):
            return self.json({"detail": "not here"}, 404)
        self.send_response(204)
        self.send_header("content-length", "0")
        self.end_headers()


if __name__ == "__main__":
    print(f"fake Jarvis server on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
