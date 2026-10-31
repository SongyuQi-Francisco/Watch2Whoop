"""Circle community rules (App Store guideline 1.2): accepted once per version before creating or joining a circle or writing a
comment, while the rollout flag params.RULES_FLAG is on for the person (off: nothing is asked, as before).

The text is community_rules.md / community_rules.en.md in this package, identical character for character to
docs/legal/community-rules.md / .en.md (tests check); its 「版本：」 / "Version:" line equals params.RULES_VERSION.
Acceptances are kept per version with the time (table circle_rules_acceptances, migration 1100); a new version asks again.
A circle activation code used at sign-up joins its circle only after the rules are accepted (codes._ready).
"""
from __future__ import annotations

from importlib import resources

import psycopg

from ..i18n import L
from ..rollout import flags
from . import params as P

FILES = {"zh": "community_rules.md", "en": "community_rules.en.md"}
STATUS = 428   # Precondition Required: the app shows the rules and sends the request again after "accept"


def text(language: str = "zh") -> str:
    return resources.files("body_engine.friends").joinpath(FILES["en" if language == "en" else "zh"]).read_text(encoding="utf-8")


def required(conn: psycopg.Connection, uid: int) -> bool:
    return flags.enabled(conn, uid, P.RULES_FLAG)


def accepted_at(conn: psycopg.Connection, uid: int, version: str = P.RULES_VERSION):
    row = conn.execute("SELECT accepted_at FROM circle_rules_acceptances WHERE user_id = %s AND version = %s",
                       (uid, version)).fetchone()
    return row and row["accepted_at"]


def ok(conn: psycopg.Connection, uid: int) -> bool:
    """May act in circles as far as the rules go: not asked (flag off) or this version accepted."""
    return not required(conn, uid) or accepted_at(conn, uid) is not None


def state(conn: psycopg.Connection, uid: int) -> dict:
    at = accepted_at(conn, uid)
    return {"required": required(conn, uid), "accepted": at is not None, "accepted_at": at, "version": P.RULES_VERSION}


def accept(conn: psycopg.Connection, uid: int, version: str) -> dict:
    """Record the acceptance of `version` (must be the current one: the app showed that text)."""
    from .store import FriendsError
    if version != P.RULES_VERSION:
        raise FriendsError(409, L("社区规则已更新，请重新阅读", "The community rules were updated; please read them again"))
    conn.execute("""INSERT INTO circle_rules_acceptances (user_id, version) VALUES (%s, %s)
                    ON CONFLICT (user_id, version) DO NOTHING""", (uid, version))
    return state(conn, uid)


def require(conn: psycopg.Connection, uid: int) -> None:
    from .store import FriendsError
    if not ok(conn, uid):
        raise FriendsError(STATUS, L("请先同意圈子社区规则", "Please accept the circle community rules first"))
