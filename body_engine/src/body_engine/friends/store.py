"""Circles: create, invite, join, change what you share, leave; and the shares they create (docs/friends-social-plan.md §3.2).

Sharing model: a member picks scopes per circle (params.CIRCLE_SCOPES, never ecg). Between two people, the owner → viewer
grant from circles is the union of the scopes the owner picked in every circle both are in. It lives in
shares.circle_scopes, apart from the hand-made shares.scopes, and is recomputed (sync_user / sync_pair) inside the same
transaction as every membership change, so leaving takes effect at once and never touches a hand-made grant.

Every function acts for `uid` (the caller); only the circle's owner may invite, reset, rename, remove or dissolve.
Personal circles (owner decision 2026-10-22, plan A): everyone has one circle of their own (ensure_personal). Signing up
with someone's referral code brings an invite to that person's personal circle only, never to a club or any other circle
they made or are merely a member of (#269: no personal circle = no circle invite, only the referral itself is recorded),
so friends of friends stay invisible to each other.
Errors are FriendsError(status, Chinese message); the API turns them into HTTP errors (English in errors_en.py).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

import psycopg

from .. import auth, i18n, wordfilter
from ..avatars import urls as avatar_store
from ..profile import consent
from ..web import login_codes
from . import blocks, params as P, remarks, trail

CODE_LENGTH = 10


class FriendsError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _sha(code: str) -> str:
    return hashlib.sha256(login_codes.normalize(code).encode()).hexdigest()


def _pretty(code: str) -> str:
    return f"{code[:5]}-{code[5:]}"


def clean_scopes(scopes) -> list[str]:
    """Validate a member's choice: only circle scopes, never ecg; keeps the order of params.CIRCLE_SCOPES."""
    chosen = set(scopes or ())
    if chosen & set(P.NEVER_SHARED):
        raise FriendsError(400, "心电图不能通过圈子共享")
    unknown = sorted(chosen - set(P.CIRCLE_SCOPES))
    if unknown:
        raise FriendsError(400, f"不认识的共享范围：{', '.join(unknown)}")
    return [s for s in P.CIRCLE_SCOPES if s in chosen]


def _clean_name(name: str | None) -> str:
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= P.NAME_MAX:
        raise FriendsError(400, f"圈子名称 1–{P.NAME_MAX} 字")
    if wordfilter.find(name) is not None:   # App Store 1.2: the configured word list (wordfilter.py)
        raise FriendsError(wordfilter.STATUS, str(wordfilter.Blocked()))
    return name


def _require_consent(conn: psycopg.Connection, uid: int) -> None:
    if not consent.agreed_at_least(conn, uid, P.CONSENT_VERSION):
        raise FriendsError(409, "先在今天页重新确认同意书（新增了「圈子共享」一节）")


def _require_may_join(conn: psycopg.Connection, uid: int) -> None:
    """Creating or joining a circle: the consent, not banned from circles, the community rules accepted (flag circle_rules)."""
    _require_consent(conn, uid)
    from . import moderation, rules
    moderation.require_allowed(conn, uid)
    rules.require(conn, uid)


def _circle_for_member(conn, circle_id: int, uid: int, lock: bool = False) -> dict:
    row = conn.execute(
        "SELECT c.* FROM circles c JOIN circle_members m ON m.circle_id = c.id AND m.user_id = %s WHERE c.id = %s"
        + (" FOR UPDATE OF c" if lock else ""), (uid, circle_id)).fetchone()
    if row is None:
        raise FriendsError(404, "没有这个圈子（或你不在里面）")
    return row


def _circle_for_owner(conn, circle_id: int, uid: int) -> dict:
    row = _circle_for_member(conn, circle_id, uid, lock=True)
    if row["owner_id"] != uid:
        raise FriendsError(403, "只有圈主能这样做")
    return row


def _count_circles(conn, uid: int) -> int:
    return conn.execute("SELECT count(*) AS n FROM circle_members WHERE user_id = %s", (uid,)).fetchone()["n"]


def max_members(circle: dict) -> int:
    """The circle's own cap (circles.max_members, set for club circles by the admin) or params.MAX_MEMBERS."""
    return circle.get("max_members") or P.MAX_MEMBERS


def members_and_waiting(conn, circle_id: int) -> int:
    """Members plus people who used one of the circle's activation codes and wait for their consent (their place is kept)."""
    return conn.execute(
        """SELECT (SELECT count(*) FROM circle_members WHERE circle_id = %(c)s)
                + (SELECT count(*) FROM circle_code_uses u JOIN circle_codes k ON k.id = u.code_id
                   WHERE k.circle_id = %(c)s AND u.joined_at IS NULL AND u.user_id IS NOT NULL) AS n""",
        {"c": circle_id}).fetchone()["n"]


def avatar_url(circle: dict) -> str | None:
    """Where the page loads the circle's avatar (members only, friends/api.py); the image id makes the URL change with it."""
    img = circle.get("avatar_image_id")
    return f"/v1/friends/circles/{circle['id']}/avatar?v={img}" if img else None


# ---------------------------------------------------------------- shares sync
def circle_scopes(conn, owner_id: int, viewer_id: int) -> list[str]:
    """Union of what `owner_id` picked in every circle `viewer_id` is also in."""
    row = conn.execute(
        """SELECT coalesce(array_agg(DISTINCT x), '{}') AS s FROM circle_members mo
           JOIN circle_members mv ON mv.circle_id = mo.circle_id AND mv.user_id = %s
           CROSS JOIN LATERAL unnest(mo.scopes) x WHERE mo.user_id = %s""", (viewer_id, owner_id)).fetchone()
    return [s for s in P.CIRCLE_SCOPES if s in set(row["s"])]


def _sync_one(conn, owner_id: int, viewer_id: int) -> None:
    # a block between the two (either way, friends/moderation.py) pauses the circle sharing; unblocking restores it
    scopes = [] if blocks.pair_blocked(conn, owner_id, viewer_id) else circle_scopes(conn, owner_id, viewer_id)
    if scopes:
        conn.execute("""INSERT INTO shares (owner_id, viewer_id, scopes, circle_scopes) VALUES (%s, %s, '{}', %s)
                        ON CONFLICT (owner_id, viewer_id) DO UPDATE SET circle_scopes = EXCLUDED.circle_scopes""",
                     (owner_id, viewer_id, scopes))
    else:
        conn.execute("UPDATE shares SET circle_scopes = '{}' WHERE owner_id = %s AND viewer_id = %s AND circle_scopes <> '{}'",
                     (owner_id, viewer_id))
    # a row left with nothing in it (no hand-made grant, no circle) goes away
    conn.execute("DELETE FROM shares WHERE owner_id = %s AND viewer_id = %s AND scopes = '{}' AND circle_scopes = '{}'",
                 (owner_id, viewer_id))


def sync_pair(conn, a: int, b: int) -> None:
    if a != b:
        _sync_one(conn, a, b)
        _sync_one(conn, b, a)


def sync_user(conn, uid: int, extra: tuple[int, ...] = ()) -> None:
    """Recompute circle grants between `uid` and everyone they share a circle with or had a circle grant with."""
    peers = {r["user_id"] for r in conn.execute(
        """SELECT DISTINCT o.user_id FROM circle_members m JOIN circle_members o ON o.circle_id = m.circle_id
           WHERE m.user_id = %s AND o.user_id <> %s""", (uid, uid))}
    peers |= {r["peer"] for r in conn.execute(
        """SELECT CASE WHEN owner_id = %s THEN viewer_id ELSE owner_id END AS peer FROM shares
           WHERE (owner_id = %s OR viewer_id = %s) AND circle_scopes <> '{}'""", (uid, uid, uid))}
    peers |= set(extra)
    for p in sorted(peers - {uid}):
        sync_pair(conn, uid, p)


# ---------------------------------------------------------------- circles
def create(conn: psycopg.Connection, uid: int, name: str, scopes=P.DEFAULT_SCOPES) -> dict:
    """New circle with the caller as owner and first member; returns {circle, invite} (the code only this once; valid params.INVITE_DEFAULT_DAYS)."""
    _require_may_join(conn, uid)
    name, scopes = _clean_name(name), clean_scopes(scopes)
    if _count_circles(conn, uid) >= P.MAX_CIRCLES_PER_USER:
        raise FriendsError(409, f"最多加入 {P.MAX_CIRCLES_PER_USER} 个圈子")
    cid = conn.execute("INSERT INTO circles (name, owner_id) VALUES (%s, %s) RETURNING id", (name, uid)).fetchone()["id"]
    conn.execute("INSERT INTO circle_members (circle_id, user_id, scopes, join_via) VALUES (%s, %s, %s, 'created')",
                 (cid, uid, scopes))
    invite = mint_invite(conn, uid, cid)
    return {"circle": circle_view(conn, uid, cid), "invite": invite}


def invite_days(days: int | None, permanent: bool = False) -> int | None:
    """Validated expiry of a new code in days; None = never expires. No choice = params.INVITE_DEFAULT_DAYS."""
    if permanent:
        if days is not None:
            raise FriendsError(400, "有效期和「永久有效」只能选一个")
        return None
    if days is None:
        return P.INVITE_DEFAULT_DAYS
    if isinstance(days, bool) or days not in P.INVITE_DAYS_CHOICES:
        raise FriendsError(400, "邀请有效期只能是 " + " / ".join(map(str, P.INVITE_DAYS_CHOICES)) + " 天或永久有效")
    return days


def mint_invite(conn: psycopg.Connection, uid: int, circle_id: int, reset: bool = False,
                days: int | None = None, permanent: bool = False) -> dict:
    """A new code for the circle (owner only), valid `days` (params.INVITE_DAYS_CHOICES, default INVITE_DEFAULT_DAYS) or for
    good (`permanent`: expires_at stays NULL). reset=True first revokes every live code (old links stop at once).
    At most params.MAX_LIVE_CODES live codes: the oldest goes. The plaintext exists only in this return value."""
    days = invite_days(days, permanent)
    _circle_for_owner(conn, circle_id, uid)
    if reset:
        conn.execute("""UPDATE circle_invites SET revoked_at = now() WHERE circle_id = %s AND code_sha256 IS NOT NULL
                        AND revoked_at IS NULL""", (circle_id,))
    conn.execute(
        """UPDATE circle_invites SET revoked_at = now() WHERE id IN (
             SELECT id FROM circle_invites WHERE circle_id = %s AND code_sha256 IS NOT NULL AND revoked_at IS NULL
               AND (expires_at IS NULL OR expires_at > now()) ORDER BY created_at DESC, id DESC OFFSET %s)""",
        (circle_id, P.MAX_LIVE_CODES - 1))
    while True:
        code = "".join(secrets.choice(login_codes.ALPHABET) for _ in range(CODE_LENGTH))
        row = conn.execute(
            """INSERT INTO circle_invites (circle_id, created_by, code_sha256, expires_at)
               VALUES (%s, %s, %s, CASE WHEN %s::int IS NULL THEN NULL ELSE now() + make_interval(days => %s::int) END)
               ON CONFLICT (code_sha256) DO NOTHING
               RETURNING id, expires_at""", (circle_id, uid, _sha(code), days, days)).fetchone()
        if row:
            return {"id": row["id"], "code": _pretty(code), "expires_at": row["expires_at"]}


def revoke_invite(conn: psycopg.Connection, uid: int, circle_id: int, invite_id: int) -> None:
    """Owner: stop one live code early (a code that was already used keeps its members). 404 when it is not a live code of this circle."""
    _circle_for_owner(conn, circle_id, uid)
    if conn.execute(
            """UPDATE circle_invites SET revoked_at = now() WHERE id = %s AND circle_id = %s AND code_sha256 IS NOT NULL
               AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now()) RETURNING id""",
            (invite_id, circle_id)).fetchone() is None:
        raise FriendsError(404, "这个邀请已失效")


def _live_code(conn, code: str) -> dict | None:
    if len(login_codes.normalize(code or "")) != CODE_LENGTH:
        return None
    return conn.execute(
        """SELECT i.id, i.circle_id, i.created_by FROM circle_invites i WHERE i.code_sha256 = %s AND i.revoked_at IS NULL
           AND (i.expires_at IS NULL OR i.expires_at > now())""", (_sha(code),)).fetchone()


def preview(conn: psycopg.Connection, code: str) -> dict | None:
    """What a code opens (before choosing what to share): circle name, owner's name, member count. None = bad code.
    A circle activation code (friends/codes.py) also gives the circle's default scopes; 410 when it is used up / off."""
    from . import codes
    hit = codes.preview(conn, code)
    if hit is not None:
        return hit
    inv = _live_code(conn, code)
    if inv is None:
        return None
    row = conn.execute(
        """SELECT c.id, c.name, c.owner_id, u.display_name AS owner_name,
                  (SELECT count(*) FROM circle_members m WHERE m.circle_id = c.id) AS members
           FROM circles c JOIN users u ON u.id = c.owner_id WHERE c.id = %s""", (inv["circle_id"],)).fetchone()
    if row["owner_id"] in blocks.masked(conn, 0, [row["owner_id"]]):
        row["owner_name"] = blocks.hidden_name()
    row.pop("owner_id")
    return row


def join(conn: psycopg.Connection, uid: int, scopes, code: str | None = None, invite_id: int | None = None) -> dict:
    """Join with a code, or accept an invite addressed to the caller. Already a member: nothing changes.
    Returns the circle view; LookupError when the code / invite is not valid (the API counts it as a failure)."""
    _require_may_join(conn, uid)
    scopes = clean_scopes(scopes)
    if code is not None and invite_id is None:
        from . import codes
        hit = codes.redeem(conn, uid, code, scopes)
        if hit is not None:
            return {**circle_view(conn, uid, hit["circle_id"]), **({"already": True} if hit["already"] else {})}
    if invite_id is not None:
        inv = conn.execute("""SELECT id, circle_id, created_by FROM circle_invites WHERE id = %s AND invitee_user_id = %s
                              AND revoked_at IS NULL AND expires_at > now()""", (invite_id, uid)).fetchone()
    else:
        inv = _live_code(conn, code or "")
    if inv is None:
        raise LookupError("邀请码无效或已过期")
    cid = inv["circle_id"]
    circle = conn.execute("SELECT id, max_members FROM circles WHERE id = %s FOR UPDATE", (cid,)).fetchone()
    if conn.execute("SELECT 1 FROM circle_members WHERE circle_id = %s AND user_id = %s", (cid, uid)).fetchone():
        return {**circle_view(conn, uid, cid), "already": True}
    if _count_circles(conn, uid) >= P.MAX_CIRCLES_PER_USER:
        raise FriendsError(409, f"最多加入 {P.MAX_CIRCLES_PER_USER} 个圈子")
    if members_and_waiting(conn, cid) >= max_members(circle):
        raise FriendsError(409, f"这个圈子已满（最多 {max_members(circle)} 人）")
    # an addressed invite only comes from a referral sign-up (referral_invites); its maker is the referral code's owner
    conn.execute("""INSERT INTO circle_members (circle_id, user_id, scopes, join_via, invite_id, invited_by)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                 (cid, uid, scopes, "referral" if invite_id is not None else "invite_code", inv["id"], inv["created_by"]))
    if invite_id is not None:   # an addressed invite is used up
        conn.execute("UPDATE circle_invites SET revoked_at = now(), used_count = used_count + 1 WHERE id = %s", (inv["id"],))
    else:
        conn.execute("UPDATE circle_invites SET used_count = used_count + 1 WHERE id = %s", (inv["id"],))
    # any other open invite to the same circle for this user is moot now
    conn.execute("""UPDATE circle_invites SET revoked_at = now() WHERE circle_id = %s AND invitee_user_id = %s
                    AND revoked_at IS NULL""", (cid, uid))
    sync_user(conn, uid)
    return circle_view(conn, uid, cid)


def set_scopes(conn: psycopg.Connection, uid: int, circle_id: int, scopes) -> dict:
    _circle_for_member(conn, circle_id, uid, lock=True)
    conn.execute("UPDATE circle_members SET scopes = %s WHERE circle_id = %s AND user_id = %s",
                 (clean_scopes(scopes), circle_id, uid))
    sync_user(conn, uid)
    return circle_view(conn, uid, circle_id)


def rename(conn: psycopg.Connection, uid: int, circle_id: int, name: str) -> dict:
    _circle_for_owner(conn, circle_id, uid)
    conn.execute("UPDATE circles SET name = %s WHERE id = %s", (_clean_name(name), circle_id))
    return circle_view(conn, uid, circle_id)


def _members(conn, circle_id: int) -> list[int]:
    return [r["user_id"] for r in conn.execute(
        "SELECT user_id FROM circle_members WHERE circle_id = %s ORDER BY joined_at, user_id", (circle_id,))]


def _delete_circle(conn, circle_id: int) -> None:
    """Delete a circle and its logo (the image row would stay behind otherwise: circles only point at it)."""
    row = conn.execute("DELETE FROM circles WHERE id = %s RETURNING avatar_image_id", (circle_id,)).fetchone()
    if row and row["avatar_image_id"]:
        conn.execute("DELETE FROM images WHERE id = %s", (row["avatar_image_id"],))


def _drop_member(conn, circle_id: int, member_id: int) -> None:
    """Take a member out; an owner who leaves hands the circle to the longest-standing member, the last one dissolves it."""
    circle = conn.execute("SELECT * FROM circles WHERE id = %s FOR UPDATE", (circle_id,)).fetchone()
    conn.execute("DELETE FROM circle_members WHERE circle_id = %s AND user_id = %s", (circle_id, member_id))
    conn.execute("DELETE FROM circle_member_prefs WHERE circle_id = %s AND user_id = %s", (circle_id, member_id))
    rest = _members(conn, circle_id)
    if not rest:
        _delete_circle(conn, circle_id)
    elif circle["owner_id"] == member_id:
        conn.execute("UPDATE circles SET owner_id = %s WHERE id = %s", (rest[0], circle_id))
    sync_user(conn, member_id)


def leave(conn: psycopg.Connection, uid: int, circle_id: int) -> None:
    _circle_for_member(conn, circle_id, uid, lock=True)
    _drop_member(conn, circle_id, uid)


def remove_member(conn: psycopg.Connection, uid: int, circle_id: int, member_id: int) -> None:
    _circle_for_owner(conn, circle_id, uid)
    if member_id == uid:
        raise FriendsError(400, "不能移除自己，用退出")
    if member_id not in _members(conn, circle_id):
        raise FriendsError(404, "这个人不在圈子里")
    _drop_member(conn, circle_id, member_id)


def dissolve(conn: psycopg.Connection, uid: int, circle_id: int) -> None:
    _circle_for_owner(conn, circle_id, uid)
    members = _members(conn, circle_id)
    _delete_circle(conn, circle_id)
    for m in members:
        sync_user(conn, m)


def decline(conn: psycopg.Connection, uid: int, invite_id: int) -> bool:
    return conn.execute("""UPDATE circle_invites SET revoked_at = now() WHERE id = %s AND invitee_user_id = %s
                           AND revoked_at IS NULL RETURNING id""", (invite_id, uid)).fetchone() is not None


def decline_all(conn: psycopg.Connection, uid: int) -> int:
    """「全部忽略」 (#269 P2): every open invite addressed to the caller; returns how many."""
    return len(conn.execute("""UPDATE circle_invites SET revoked_at = now() WHERE invitee_user_id = %s
                               AND revoked_at IS NULL RETURNING id""", (uid,)).fetchall())


def leave_all(conn: psycopg.Connection, uid: int) -> int:
    """Account deletion: leave every circle first, so circles the person owned pass on instead of vanishing for the rest."""
    ids = [r["circle_id"] for r in conn.execute("SELECT circle_id FROM circle_members WHERE user_id = %s", (uid,))]
    for cid in ids:
        _drop_member(conn, cid, uid)
    return len(ids)


# ---------------------------------------------------------------- personal circles and referrals
def _personal_name(conn, uid: int) -> str:
    """「Ann 的圈子」 / "Ann's circle" in the owner's language (their profile choice, else the request's)."""
    u = conn.execute("SELECT display_name, language FROM users WHERE id = %s", (uid,)).fetchone()
    en = (u["language"] or i18n.lang()) == "en"
    who = " ".join((u["display_name"] or "").split())
    if not who:
        return "My circle" if en else "我的圈子"
    tail = "'s circle" if en else " 的圈子"
    return who[:P.NAME_MAX - len(tail)] + tail


def home_circle(conn: psycopg.Connection, uid: int) -> int | None:
    """The person's own personal circle, else the oldest one they own (ensure_personal: an owner gets no second circle).
    Not where a referral invites to: that is personal_circle() only."""
    row = conn.execute("SELECT id FROM circles WHERE owner_id = %s ORDER BY personal DESC, created_at, id LIMIT 1",
                       (uid,)).fetchone()
    return row["id"] if row else None


def ensure_personal(conn: psycopg.Connection, uid: int) -> int | None:
    """Make the person's personal circle the first time circles are used: needs the `friends` flag on for them and the
    circle consent, else nothing happens (and it is tried again next time). Someone who already owns a circle made by
    hand gets none and keeps theirs as it is. Settled once (users.personal_circle_at): a dissolved one is not remade.
    Returns home_circle()."""
    u = conn.execute("SELECT personal_circle_at FROM users WHERE id = %s", (uid,)).fetchone()
    if u is None or blocks.banned(conn, uid):
        return None
    if u["personal_circle_at"] is None and auth.circles_on(conn, uid) \
            and consent.agreed_at_least(conn, uid, P.CONSENT_VERSION):
        u = conn.execute("SELECT personal_circle_at FROM users WHERE id = %s FOR UPDATE", (uid,)).fetchone()
        if u["personal_circle_at"] is None:
            if home_circle(conn, uid) is None:
                if _count_circles(conn, uid) >= P.MAX_CIRCLES_PER_USER:
                    return None   # no room now; tried again later
                cid = conn.execute("INSERT INTO circles (name, owner_id, personal) VALUES (%s, %s, true) RETURNING id",
                                   (_personal_name(conn, uid), uid)).fetchone()["id"]
                conn.execute("INSERT INTO circle_members (circle_id, user_id, scopes, join_via) VALUES (%s, %s, %s, 'created')",
                             (cid, uid, list(P.DEFAULT_SCOPES)))
            conn.execute("UPDATE users SET personal_circle_at = now() WHERE id = %s", (uid,))
    return home_circle(conn, uid)


def personal_circle(conn: psycopg.Connection, uid: int) -> int | None:
    """The circle made for the person as their personal one (circles.personal), None when they have none."""
    row = conn.execute("SELECT id FROM circles WHERE owner_id = %s AND personal ORDER BY created_at, id LIMIT 1",
                       (uid,)).fetchone()
    return row["id"] if row else None


def referral_invites(conn: psycopg.Connection, inviter_id: int, invitee_id: int) -> int | None:
    """#57 hook: someone signed up with `inviter_id`'s referral code → one invite to the inviter's personal circle (made
    now if circles are on for the inviter), valid params.REFERRAL_INVITE_DAYS. A referral code is the person's own, not a
    circle's: an inviter without a personal circle (they owned a hand-made or club circle first) sends no circle invite at
    all, never one to that circle (#269). Made once per circle and person: a declined or expired invite is not renewed.
    Nothing is shared until the invitee accepts and picks scopes. Returns the circle's id (recorded in
    invite_redemptions.circle_id) or None (no personal circle, or circles off for the inviter: referral_offer() catches up later)."""
    ensure_personal(conn, inviter_id)
    cid = personal_circle(conn, inviter_id)
    if cid is None:
        return None
    if not conn.execute("""SELECT 1 FROM circle_invites WHERE circle_id = %s AND invitee_user_id = %s
                           UNION ALL SELECT 1 FROM circle_members WHERE circle_id = %s AND user_id = %s""",
                        (cid, invitee_id, cid, invitee_id)).fetchone():
        conn.execute("""INSERT INTO circle_invites (circle_id, created_by, invitee_user_id, expires_at)
                        VALUES (%s, %s, %s, now() + make_interval(days => %s))""",
                     (cid, inviter_id, invitee_id, P.REFERRAL_INVITE_DAYS))
    return cid


def referral_offer(conn: psycopg.Connection, uid: int) -> dict | None:
    """The 「加入 XX 的圈子」 step of onboarding (and the Friends page invite list): if `uid` signed up with a referral code,
    the open invite to the inviter's personal circle. Catches up a sign-up made while circles were off for either of them
    (the invite is made now). None = nothing to offer, also whenever circles are off for the inviter (no error, nothing
    about the inviter is shown). Also makes the caller's own personal circle."""
    ensure_personal(conn, uid)
    red = conn.execute(
        """SELECT r.id, r.inviter_user_id, r.circle_id FROM invite_redemptions r JOIN users u ON u.id = r.inviter_user_id
           WHERE r.user_id = %s AND u.status = 'active' ORDER BY r.redeemed_at DESC LIMIT 1""", (uid,)).fetchone()
    if red is None or not auth.circles_on(conn, red["inviter_user_id"]) \
            or not consent.agreed_at_least(conn, uid, P.CONSENT_VERSION):
        return None
    cid = referral_invites(conn, red["inviter_user_id"], uid)
    if cid is None:
        return None
    if red["circle_id"] is None:
        conn.execute("UPDATE invite_redemptions SET circle_id = %s WHERE id = %s", (cid, red["id"]))
    return conn.execute(
        """SELECT i.id AS invite_id, i.expires_at, c.id AS circle_id, c.name, u.display_name AS inviter_name,
                  (SELECT count(*) FROM circle_members m WHERE m.circle_id = c.id) AS members
           FROM circle_invites i JOIN circles c ON c.id = i.circle_id LEFT JOIN users u ON u.id = c.owner_id
           WHERE i.circle_id = %s AND i.invitee_user_id = %s AND i.revoked_at IS NULL AND i.expires_at > now()
             AND NOT EXISTS (SELECT 1 FROM circle_members m WHERE m.circle_id = c.id AND m.user_id = %s)
           ORDER BY i.created_at DESC LIMIT 1""", (cid, uid, uid)).fetchone()


# ---------------------------------------------------------------- views
def circle_view(conn, uid: int, circle_id: int) -> dict:
    c = _circle_for_member(conn, circle_id, uid)
    members = conn.execute(
        """SELECT u.id, u.display_name, m.joined_at, m.scopes FROM circle_members m JOIN users u ON u.id = m.user_id
           WHERE m.circle_id = %s ORDER BY m.joined_at, u.id""", (circle_id,)).fetchall()
    avatars = avatar_store.urls_for(conn, uid, [m["id"] for m in members])
    nick = remarks.for_viewer(conn, uid, [m["id"] for m in members])
    blocks.mask_rows(conn, uid, members)
    mine = blocks.blocked_by(conn, uid)   # still listed (to leave or remove them), marked 「已屏蔽」
    # how each member came in (who invited, how): the owner only, behind the circle_join_trail flag
    joins = trail.for_owner(conn, uid, circle_id) if c["owner_id"] == uid else {}
    out = {"id": c["id"], "name": c["name"], "owner_id": c["owner_id"], "is_owner": c["owner_id"] == uid,
           "personal": c["personal"], "created_at": c["created_at"], "avatar": avatar_url(c),
           "max_members": max_members(c),
           "my_scopes": next(m["scopes"] for m in members if m["id"] == uid),
           # other members' picks are not shown here: what they share with you is in /v1/web/me → viewable
           "members": [{"id": m["id"], "display_name": m["display_name"], "joined_at": m["joined_at"],
                        "owner": m["id"] == c["owner_id"], "me": m["id"] == uid, "avatar": avatars.get(m["id"]),
                        "remark": nick.get(m["id"]), **({"blocked": True} if m["id"] in mine else {}),
                        **({"join": joins[m["id"]]} if m["id"] in joins else {})}
                       for m in members]}
    from . import prefs   # (prefs imports this module)
    if prefs.on(conn, uid):   # my own pin / mute / archive and the last activity, for the circles sheet (#269 P2)
        mine = prefs.of(conn, uid).get(circle_id) or {}
        out["prefs"] = {k: bool(mine.get(k)) for k in ("pinned", "muted", "archived")}
        out["recent_at"] = prefs.recent(conn, [circle_id]).get(circle_id)
    if out["is_owner"]:
        codes = conn.execute("""SELECT id, created_at, expires_at, used_count FROM circle_invites WHERE circle_id = %s
                                AND code_sha256 IS NOT NULL AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())
                                ORDER BY created_at DESC, id DESC""", (circle_id,)).fetchall()
        # `codes`: the live codes (no plaintext is kept), expires_at null = never expires; `until`: when the last one
        # ends, null when there is none or one of them never expires
        until = None if any(c["expires_at"] is None for c in codes) else max((c["expires_at"] for c in codes), default=None)
        out["invites"] = {"live": len(codes), "codes": codes, "until": until}
    return out


def overview(conn: psycopg.Connection, uid: int) -> dict:
    """The caller's circles, invites addressed to them, the scope choices and whether consent allows circles yet."""
    ids = [r["circle_id"] for r in conn.execute(
        """SELECT m.circle_id FROM circle_members m JOIN circles c ON c.id = m.circle_id WHERE m.user_id = %s
           ORDER BY c.created_at, c.id""", (uid,))]
    invites = conn.execute(
        """SELECT i.id, i.expires_at, c.id AS circle_id, c.name, u.display_name AS from_name, i.created_by AS from_id,
                  (SELECT count(*) FROM circle_members x WHERE x.circle_id = c.id) AS members,
                  coalesce(c.default_scopes, %s) AS default_scopes
           FROM circle_invites i JOIN circles c ON c.id = i.circle_id LEFT JOIN users u ON u.id = i.created_by
           WHERE i.invitee_user_id = %s AND i.revoked_at IS NULL AND i.expires_at > now()
             AND NOT EXISTS (SELECT 1 FROM circle_members m WHERE m.circle_id = i.circle_id AND m.user_id = %s)
           ORDER BY i.created_at""", (list(P.DEFAULT_SCOPES), uid, uid)).fetchall()
    hide = blocks.masked(conn, uid, [i["from_id"] for i in invites])
    for i in invites:
        if i.pop("from_id") in hide:
            i["from_name"] = blocks.hidden_name()
    from . import prefs, rules
    return {"circles": [circle_view(conn, uid, cid) for cid in ids], "invites": invites,
            "prefs_on": prefs.on(conn, uid), "max_pinned": P.MAX_PINNED, "search_from": P.SEARCH_FROM,
            "scopes": list(P.CIRCLE_SCOPES), "default_scopes": list(P.DEFAULT_SCOPES),
            "consent_ok": consent.agreed_at_least(conn, uid, P.CONSENT_VERSION),
            # community rules (flag circle_rules): required / accepted / version; banned = an admin barred me from circles
            "rules": rules.state(conn, uid), "banned": blocks.banned(conn, uid),
            "blocked_count": len(blocks.blocked_by(conn, uid)), "report_reasons": list(P.REPORT_REASONS),
            "limits": {"circles": P.MAX_CIRCLES_PER_USER, "members": P.MAX_MEMBERS, "invite_days": list(P.INVITE_DAYS_CHOICES),
                       "invite_default_days": P.INVITE_DEFAULT_DAYS},
            "now": datetime.now().astimezone()}
