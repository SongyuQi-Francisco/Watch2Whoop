"""Fake adapter for tests: replies by script and records the key, model, system prompt and history of each call. No network, no cost.

Usage (tests/test_ai.py): FakeAdapter.script = [turn, ...]; monkeypatch ADAPTERS[kind] = lambda: FakeAdapter.
Each turn: {"text": "...", "tool_calls": [{"id", "name", "args"}], "usage": {...}, "stop": "end"}
"""
from .base import Adapter, TurnResult


class FakeAdapter(Adapter):
    kind = "fake"
    script: list[dict] = []
    calls: list[dict] = []

    def stream_turn(self, system, history, tools):
        FakeAdapter.calls.append({"api_key": self.api_key, "model": self.model, "base_url": self.base_url,
                                  "system": system, "history": history, "tools": [t.name for t in tools]})
        turn = FakeAdapter.script.pop(0) if FakeAdapter.script else {"text": "好的。"}
        for piece in (turn.get("text") or "").split("|"):
            if piece:
                yield "text", piece
        calls = turn.get("tool_calls") or []
        yield "result", TurnResult(text=(turn.get("text") or "").replace("|", ""), tool_calls=calls,
                                   native={"fake": True}, usage=turn.get("usage") or {"input_tokens": 1000, "output_tokens": 100},
                                   stop=turn.get("stop") or ("tool_use" if calls else "end"))
