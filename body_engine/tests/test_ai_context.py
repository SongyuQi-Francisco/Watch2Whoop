"""Context management of the in-page chat (ai/context.py, issue #53): where the history is cut, what the fold call sees.
Pure functions, no database (the chat-level tests are in test_ai.py and test_ai_fallback.py)."""
from body_engine.ai import context


def msg(role, *blocks):
    return {"role": role, "content": list(blocks)}


def text(t):
    return {"type": "text", "text": t}


def turn(i, tool=False):
    """One user turn: question, (tool call + result,) answer."""
    out = [msg("user", text(f"问{i}"))]
    if tool:
        out += [msg("assistant", {"type": "tool_call", "id": f"c{i}", "name": "get_profile", "args": {"user": "me"}}),
                msg("tool", {"type": "tool_result", "id": f"c{i}", "name": "get_profile", "content": "x" * 5000,
                             "is_error": False})]
    return out + [msg("assistant", text(f"答{i}"))]


def test_fits_means_no_fold():
    h = turn(0) + turn(1) + [msg("user", text("问2"))]
    assert context.fold_point(h, fallback=False) is None


def test_cuts_only_at_turn_starts_and_keeps_the_latest_turn(monkeypatch):
    monkeypatch.setattr(context, "MAX_MESSAGES", 10)
    monkeypatch.setattr(context, "KEEP_TURNS", 3)
    h = [m for i in range(6) for m in turn(i, tool=i % 2 == 0)] + [msg("user", text("问6"))]
    cut = context.fold_point(h, fallback=False)
    assert cut is not None and h[cut]["role"] == "user"
    tail = h[cut:]
    assert len(tail) <= 10 * context.TAIL_SHARE and tail[-1]["content"][0]["text"] == "问6"
    # every tool call in the tail has its result in the tail
    calls = {b["id"] for m in tail for b in m["content"] if b["type"] == "tool_call"}
    results = {b["id"] for m in tail for b in m["content"] if b["type"] == "tool_result"}
    assert calls == results


def test_one_huge_turn_is_kept_whole(monkeypatch):
    monkeypatch.setattr(context, "MAX_CHARS", 3000)
    h = turn(0) + turn(1, tool=True)   # the last turn alone is over the budget
    assert context.fold_point(h, fallback=False) == 2
    assert context.fold_point(turn(0, tool=True), fallback=False) is None   # nothing before it to fold


def test_fallback_has_its_own_smaller_budget(monkeypatch):
    from body_engine.ai import fallback_params as fp
    h = [m for i in range(12) for m in turn(i)] + [msg("user", text("问12"))]   # 25 messages
    assert context.fold_point(h, fallback=False) is None
    monkeypatch.setattr(fp, "CONTEXT_MAX_MESSAGES", 20)
    cut = context.fold_point(h, fallback=True)
    assert cut is not None and len(h) - cut <= fp.KEEP_TURNS * 2


def test_images_count_but_are_not_resent_in_the_transcript():
    img = {"type": "image", "media_type": "image/jpeg", "data": "A" * 100_000}
    m = [msg("user", text("这是早餐"), img), msg("assistant", text("看起来不错"))]
    assert context.size(m) < 10_000   # base64 is not measured, a fixed cost per image is
    t = context.transcript(m)
    assert "[发了一张图片]" in t and "AAAA" not in t


def test_fold_request_merges_the_old_summary_and_clips_tool_results():
    req = context.fold_request("- 用户想减到 80kg", turn(0, tool=True))
    assert len(req) == 1 and req[0]["role"] == "user"
    body = req[0]["content"][0]["text"]
    assert body.startswith("之前的摘要：\n- 用户想减到 80kg") and "get_profile" in body
    assert "x" * (context.TOOL_RESULT_CHARS + 1) not in body
    assert "之前的摘要" not in context.fold_request(None, turn(0))[0]["content"][0]["text"]


def test_with_summary():
    assert context.with_summary("SYS", None) == "SYS"
    s = context.with_summary("SYS", "- 要点")
    assert s.startswith("SYS\n\n") and s.endswith("- 要点") and context.SUMMARY_HEADER in s
