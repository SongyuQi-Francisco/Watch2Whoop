"""The Open Wearables refresh-token patch (deploy/open-wearables/patches/refresh_token_service.py): a phone that lost a
refresh answer gets the unused successor again instead of 401 forever; a stale token whose successor was used stays
rejected; developer tokens, explicit revocation and the grace window behave as upstream.

The patch imports OW's own modules (SQLAlchemy models, settings), which Body Engine does not have; they are replaced by
small in-memory fakes here. The real check against the official OW 0.9.0 image (its own token / auth tests, 189 of them,
plus the replay cases) is described in deploy/open-wearables/README.md. The pinned upstream file is compared by hash when the
vendor submodule is checked out, so an OW upgrade cannot silently keep mounting a patch of the old file.
"""
import hashlib
import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PATCH = REPO / "deploy/open-wearables/patches/refresh_token_service.py"
UPSTREAM = REPO / "vendor/open-wearables/backend/app/services/refresh_token_service.py"
UPSTREAM_SHA256 = "073525671cc60fe62be2db0e0a60adc0b0965741499a3412347154547ec44865"   # OW 0.9.0
UTC = timezone.utc


# ---------------------------------------------------------------- fakes for the OW modules the patch imports
class Col:
    """A model attribute: comparisons return predicates over rows, like SQLAlchemy column expressions."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, v):
        return lambda r: getattr(r, self.name) == v

    def __ge__(self, v):
        return lambda r: getattr(r, self.name) is not None and getattr(r, self.name) >= v

    def __le__(self, v):
        return lambda r: getattr(r, self.name) is not None and getattr(r, self.name) <= v

    def is_(self, v):
        return lambda r: getattr(r, self.name) is v

    def desc(self):
        return (self.name, True)

    __hash__ = object.__hash__


class TokenType(StrEnum):
    SDK = "sdk"
    DEVELOPER = "developer"


class RefreshToken:
    id = Col("id")
    token_type = Col("token_type")
    user_id = Col("user_id")
    app_id = Col("app_id")
    developer_id = Col("developer_id")
    created_at = Col("created_at")
    revoked_at = Col("revoked_at")

    def __init__(self, **kw):
        self.last_used_at = None
        for k, v in kw.items():
            setattr(self, k, v)


class Query:
    def __init__(self, model):
        self.preds, self.order, self.n = [], None, None

    def where(self, *preds):
        self.preds += preds
        return self

    def order_by(self, col):
        self.order = col if isinstance(col, tuple) else (col.name, False)
        return self

    def limit(self, n):
        self.n = n
        return self


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class DB:
    def __init__(self):
        self.rows = []

    def execute(self, q):
        rows = [r for r in self.rows if all(p(r) for p in q.preds)]
        if q.order:
            rows.sort(key=lambda r: getattr(r, q.order[0]), reverse=q.order[1])
        return Result(rows[: q.n] if q.n else rows)


class Clock:
    """Strictly increasing timestamps, so a successor is always created after its predecessor was revoked."""
    t = datetime(2026, 10, 21, 0, 26, 54, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        cls.t += timedelta(milliseconds=5)
        return cls.t


class Repo:
    def create(self, db, token):
        db.rows.append(token)
        return token

    def get_valid_token(self, db, token_id):
        return next((r for r in db.rows if r.id == token_id and r.revoked_at is None), None)

    def revoke_token(self, db, token):
        token.revoked_at = Clock.now()
        return token


class HTTPException(Exception):
    def __init__(self, status_code, detail=None, headers=None):
        self.status_code = status_code


class TokenResponse:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
def svc(monkeypatch):
    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, m)

    mod("fastapi", HTTPException=HTTPException,
        status=types.SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_400_BAD_REQUEST=400, HTTP_404_NOT_FOUND=404))
    mod("sqlalchemy", select=Query)
    mod("app.config", settings=types.SimpleNamespace(access_token_expire_minutes=1440))
    mod("app.database", DbSession=object)
    mod("app.models", RefreshToken=RefreshToken)
    mod("app.repositories.refresh_token_repository", refresh_token_repository=Repo())
    mod("app.schemas.auth", TokenResponse=TokenResponse, TokenType=TokenType)
    mod("app.services.sdk_token_service", create_sdk_user_token=lambda app_id, user_id: f"jwt:{user_id}:{Clock.t}")
    mod("app.utils.security", create_access_token=lambda subject: f"dev-jwt:{subject}:{Clock.t}")
    monkeypatch.setenv("W2W_REFRESH_REPLAY_GRACE_HOURS", "720")
    spec = importlib.util.spec_from_file_location("w2w_ow_refresh_patch", PATCH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "datetime", Clock)
    return m, m.RefreshTokenService(log=__import__("logging").getLogger("test")), DB()


def refresh(s, db, tok):
    return s.refresh_token(db, tok).refresh_token


def rejected(s, db, tok):
    with pytest.raises(HTTPException) as e:
        s.refresh_token(db, tok)
    return e.value.status_code == 401


def test_lost_answer_is_answered_again_with_the_same_successor(svc):
    m, s, db = svc
    t0 = s.create_sdk_refresh_token(db, "u1", "app")
    t1 = refresh(s, db, t0)                    # the phone never received this answer
    assert refresh(s, db, t0) == t1            # it retries with t0: same successor, no new rotation
    assert refresh(s, db, t0) == t1            # idempotent
    assert sum(r.revoked_at is None for r in db.rows) == 1
    t2 = refresh(s, db, t1)                    # the phone has t1 now and rotates normally
    assert t2 not in (t0, t1)
    assert rejected(s, db, t0)                 # t1 was used: t0 is stale


def test_stale_token_never_gets_a_later_rotation(svc):
    m, s, db = svc
    a0 = s.create_sdk_refresh_token(db, "u1", "app")
    a1 = refresh(s, db, a0)
    refresh(s, db, a1)                         # within the successor window of a0's revocation
    assert rejected(s, db, a0)


def test_explicit_revocation_unknown_tokens_and_the_grace_window(svc):
    m, s, db = svc
    t = s.create_sdk_refresh_token(db, "u1", "app")
    s.revoke_token(db, t)                      # /token/revoke: no successor
    assert rejected(s, db, t)
    assert rejected(s, db, "rt-" + "0" * 32)
    o = s.create_sdk_refresh_token(db, "u2", "app")
    refresh(s, db, o)
    Clock.t += m.REPLAY_GRACE + timedelta(minutes=1)
    assert rejected(s, db, o)


def test_other_users_apps_and_developer_tokens_are_not_replayed(svc):
    m, s, db = svc
    d0 = s.create_developer_refresh_token(db, "dev1")
    refresh(s, db, d0)
    assert rejected(s, db, d0)                 # developer tokens: upstream behaviour
    x0 = s.create_sdk_refresh_token(db, "u1", "app-a")
    s.repo.revoke_token(db, db.rows[-1])       # revoked without a rotation...
    s.create_sdk_refresh_token(db, "u2", "app-a")   # ...and the next token belongs to someone else
    s.create_sdk_refresh_token(db, "u1", "app-b")   # or to another app of the same user
    assert rejected(s, db, x0)


def test_switched_off(svc, monkeypatch):
    m, s, db = svc
    monkeypatch.setattr(m, "REPLAY_GRACE", timedelta(0))
    t0 = s.create_sdk_refresh_token(db, "u1", "app")
    refresh(s, db, t0)
    assert rejected(s, db, t0)


def test_compose_mounts_the_patch_over_both_copies_read_only():
    text = (REPO / "deploy/open-wearables/compose.override.yaml").read_text()
    for target in ("/root_project/app/services/refresh_token_service.py",
                   "/opt/venv/lib/python3.13/site-packages/app/services/refresh_token_service.py"):
        assert f"patches/refresh_token_service.py:{target}:ro" in text
    assert 'ACCESS_TOKEN_EXPIRE_MINUTES: "1440"' in text
    assert "themomentum/open-wearables-backend:0.9.0@" in text   # the patch is for this version


def test_patch_is_based_on_the_pinned_upstream_file():
    assert UPSTREAM_SHA256 in PATCH.read_text()
    if not UPSTREAM.exists():
        pytest.skip("vendor/open-wearables is not checked out")
    assert hashlib.sha256(UPSTREAM.read_bytes()).hexdigest() == UPSTREAM_SHA256, (
        "Open Wearables changed refresh_token_service.py: re-apply the patch to the new file (or drop it), then update "
        "the hash here and in the patch header")
