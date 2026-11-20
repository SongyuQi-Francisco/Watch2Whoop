"""The {user} path helpers (deps.py) and every module's wrapper around them: same answers as before they were merged.

No database: auth.can_view is faked. For each helper: 'me' and the caller's own id give the caller, someone else's id
gives 403 with that module's own text, a write-only helper refuses a read-only login, and resolve_owner follows the
share check and answers 404 for a {user} that is neither 'me' nor a number.
"""
import pytest
from fastapi import HTTPException

from body_engine import api, auth, deps
from body_engine.account import api as account_api
from body_engine.ai import api as ai_api, oauth_api
from body_engine.feedback import api as feedback_api
from body_engine.history import api as history_api
from body_engine.inbox import api as inbox_api
from body_engine.nativesync import api as nativesync_api
from body_engine.notify import api as notify_api
from body_engine.profile import api as profile_api
from body_engine.records import api as records_api
from body_engine.rollout import api as rollout_api
from body_engine.sync import api as sync_api
from body_engine.training import api as training_api

RW = {"id": 7, "token_scopes": ["read", "write"]}
RO = {"id": 7, "token_scopes": ["read"]}

# (helper, writes?, the 403 text for someone else, the 403 text for a read-only login or None)
SELF_HELPERS = [
    (lambda v, u: records_api._self(v, u), True, "写操作只能写给自己", "这个 token 没有 write 权限"),
    (lambda v, u: profile_api._self(v, u), False, "个人资料只能看 / 改自己的", None),
    (lambda v, u: profile_api._self(v, u, True), True, "个人资料只能看 / 改自己的", "这个登录没有 write 权限"),
    (lambda v, u: history_api._self(v, u), False, "只能给自己导入", None),
    (lambda v, u: history_api._self(v, u, True), True, "只能给自己导入", "这个登录没有 write 权限"),
    (lambda v, u: ai_api._self_only(v, u), False, "AI 设置只能管自己的（家人授权不包括 AI 配置）", None),
    (lambda v, u: ai_api._self_only(v, u, write=True), True, "AI 设置只能管自己的（家人授权不包括 AI 配置）",
     "这个登录没有 write 权限"),
    (lambda v, u: notify_api._own(v, u), False, "只能看自己的通知", None),
    (lambda v, u: inbox_api._own(v, u), False, "只能看自己的消息", None),
    (lambda v, u: rollout_api._own(v, u), False, "只能看自己的", None),
    (lambda v, u: feedback_api._own(v, u), False, "反馈记录只能看自己的", None),
    (lambda v, u: oauth_api._own(v, u), False, "AI 连接只能管自己的", None),
    (lambda v, u: sync_api._own(v, u), False, "只能刷新自己的数据", None),
    (lambda v, u: nativesync_api._own(v, u), False, "只能读自己的同步数据", None),
    (lambda v, u: training_api._hevy_self(v, u), False, "只能查看自己的导入", None),
    (lambda v, u: (training_api._self_or_read(v, u), v["id"])[1], False, "只能看自己的", None),
]


@pytest.mark.parametrize("helper,writes,other,ro", SELF_HELPERS)
def test_self_helpers(helper, writes, other, ro):
    assert helper(RW, "me") == 7 and helper(RW, "7") == 7
    for who in ("8", "07", "x", ""):
        with pytest.raises(HTTPException) as e:
            helper(RW, who)
        assert (e.value.status_code, e.value.detail) == (403, other), who
    if writes:
        with pytest.raises(HTTPException) as e:
            helper(RO, "me")
        assert (e.value.status_code, e.value.detail) == (403, ro)
    else:
        assert helper(RO, "me") == 7


def test_require_self_defaults():
    assert deps.require_self(RW, "me") == 7
    with pytest.raises(HTTPException) as e:
        deps.require_self(RO, "me", write=True)
    assert e.value.detail == deps.NO_WRITE


@pytest.fixture
def shares(monkeypatch):
    """viewer 7 may see owner 8's 'sleep' only."""
    monkeypatch.setattr(auth, "can_view", lambda conn, v, o, scope: v == o or (v, o, scope) == (7, 8, "sleep"))


@pytest.mark.parametrize("fn", [api.resolve_owner, records_api._owner, deps.resolve_owner])
def test_resolve_owner(fn, shares):
    assert fn(None, RW, "me", "scores") == 7 and fn(None, RO, "7", "scores") == 7
    assert fn(None, RO, "8", "sleep") == 8
    with pytest.raises(HTTPException) as e:
        fn(None, RW, "8", "scores")
    assert (e.value.status_code, e.value.detail) == (403, "没有查看用户 8 的 scores 的授权")
    with pytest.raises(HTTPException) as e:   # api.resolve_owner used to raise ValueError here (a 500)
        fn(None, RW, "abc", "sleep")
    assert (e.value.status_code, e.value.detail) == (404, "用户写 me 或数字 ID")


def test_parse_user():
    assert deps.parse_user(RW, "me") == 7 and deps.parse_user(RW, "12") == 12
    with pytest.raises(HTTPException) as e:
        deps.parse_user(RW, "me2")
    assert e.value.status_code == 404


def test_no_hand_rolled_user_checks_left():
    """New endpoints use deps (or a module wrapper around it): no `user not in ("me", ...)` / `== "me"` parsing outside deps.py."""
    import pathlib
    import re
    src = pathlib.Path(deps.__file__).parent
    pat = re.compile(r'user not in \("me"|viewer\["id"\] if user == "me"')
    hits = [f"{p.relative_to(src)}:{i}" for p in src.rglob("*.py") if p.name != "deps.py"
            for i, line in enumerate(p.read_text().splitlines(), 1) if pat.search(line)]
    assert hits == []
    assert account_api   # the referral code endpoint is checked through deps too (test_auth_boundary.py)
