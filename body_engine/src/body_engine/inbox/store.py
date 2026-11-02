"""The inbox (issue #359 P1): one list merged read-only from the tables that already hold the messages.

Item ids and where each comes from (those tables stay as they are):
  notice:<id>        admin_notices addressed to the person, not withdrawn, not expired
  whatsnew:<week>    whats_new_sends: every week a message was due, also when the person switched its push off (that switch is
                     about the push; the text comes from whatsnew/entries.py)
  invite:<id>        circle_invites addressed to the person (invitee_user_id) that can still be accepted
  notification:<id>  notifications of the category circle (❤️, comments)

Left out on every read, so a client never has to know the rules (friends/blocks.py is the one source for them):
- invites whose maker or circle owner the person blocked or was blocked by, and interactions from such a person;
- interactions from someone whose profile moderation hid or removed, who was banned from circles, or whose account is gone;
- a comment notification whose comment was deleted, or is hidden for this person by a report or by moderation;
- everything about circles while the `friends` flag is off for the person; invites also for someone banned from circles.

Read state lives where it already lived (notifications.read_at, admin_notices.seen_at), so the app's badge and the old lists
agree with the inbox; only what has no column of its own (invites, what's-new weeks without a notification) and "hidden" for
every kind are in inbox_item_state. Reading or hiding only ever touches items in the person's own list.

Invite cards show what a stranger to the circle may know: its name and picture, the owner's name, how many members, what the
person would share. Member names, the inviter's id and who else was invited or declined are never in them. Accepting goes
through friends/store.join with the circle's default scopes, the ones the card listed.
"""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from .. import auth, queries
from ..friends import blocks, params as FP, rules, store as friends
from ..friends.feed import COMMENT
from ..i18n import L, is_en
from ..images import store as images
from ..notices import store as notices
from ..notify import store as notify_store
from ..profile import consent
from ..rollout import flags
from ..whatsnew import entries as E, params as WP, service as whatsnew
from . import params as P

_CURSOR_TIME = "%Y%m%dT%H%M%S%f"


class InboxError(Exception):
    """A refusal with a code the app acts on; the API turns it into {code, message, …extra}."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.extra = status, code, extra


def _invite_gone() -> InboxError:
    # the same answer for an invite that never existed, someone else's, an expired, withdrawn or blocked one
    return InboxError(404, "invite_gone", L("这个邀请已失效", "This invite is no longer valid"))


def _circles_reach(conn, uid: int) -> bool:
    """The `friends` flag reaches this person. Not flags.enabled: that flag may also ask for the circle consent, and someone
    without that consent must see the card and get consent_required, not a dead invite."""
    return flags.targeted(conn, uid, auth.CIRCLES_FLAG)


def _pick(row: dict, field: str, en: bool) -> str | None:
    return (row[f"{field}_en"] if en else None) or row[field]


# ---------------------------------------------------------------- the four sources
def _notices(conn, uid: int) -> list[dict]:
    en = is_en()
    rows = conn.execute(
        """SELECT id, title, body, link, link_label, title_en, body_en, link_label_en, created_at, seen_at FROM admin_notices
           WHERE user_id = %s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())
             AND created_at > now() - make_interval(days => %s)
           ORDER BY created_at DESC, id DESC LIMIT %s""", (uid, P.KEEP_DAYS, P.SOURCE_MAX)).fetchall()
    return [{"id": f"notice:{r['id']}", "type": "notice", "at": r["created_at"], "title": _pick(r, "title", en),
             "body": _pick(r, "body", en), "url": r["link"], "link_label": _pick(r, "link_label", en),
             "_read": r["seen_at"] is not None, "_src": ("notice", r["id"])} for r in rows]


def _whatsnew(conn, uid: int) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM (SELECT s.week_start, s.through, s.created_at, n.id AS notification_id, n.read_at,
                                 lag(s.through) OVER (ORDER BY s.week_start) AS previous
                          FROM whats_new_sends s LEFT JOIN notifications n ON n.id = s.notification_id AND n.user_id = s.user_id
                          WHERE s.user_id = %s) x
           WHERE x.created_at > now() - make_interval(days => %s)""", (uid, P.KEEP_DAYS)).fetchall()
    if not rows:
        return []
    created = conn.execute("SELECT created_at FROM users WHERE id = %s", (uid,)).fetchone()["created_at"]
    created = created.astimezone(queries.user_tz(conn, uid)).date()
    out = []
    for r in rows:
        entries = E.between(whatsnew.floor_day(r["previous"], created, r["through"]), r["through"])
        if not entries:   # the entries of that week were taken out of the file
            continue
        title, body = whatsnew.message(entries)
        nid = r["notification_id"]
        out.append({"id": f"whatsnew:{r['week_start'].isoformat()}", "type": "whatsnew", "at": r["created_at"],
                    "title": L(title["zh"], title["en"]), "body": L(body["zh"], body["en"]), "url": WP.URL,
                    "entries": [{"date": e.day.isoformat(), "text": L(e.zh, e.en), "url": e.url} for e in entries],
                    "_read": r["read_at"] is not None, "_src": ("notification", nid) if nid else ("state", None)})
    return out


def card_scopes(circle: dict) -> list[str]:
    """What accepting shares: the circle's default scopes, the same rule as a circle activation code (friends/codes.py).
    The one list both the card and the join use, so the card can never say less than what happens."""
    chosen = set(circle["default_scopes"] or FP.DEFAULT_SCOPES)
    return [s for s in FP.CIRCLE_SCOPES if s in chosen]


def _open_invites(conn, uid: int, invite_id: int | None = None) -> list[dict]:
    """Invites addressed to the person that can be accepted right now."""
    if not _circles_reach(conn, uid) or blocks.banned(conn, uid):
        return []
    rows = conn.execute(
        f"""SELECT i.id, i.created_at, i.expires_at, c.name, c.owner_id, c.avatar_image_id, c.default_scopes,
                   u.display_name AS owner_name, (SELECT count(*) FROM circle_members x WHERE x.circle_id = c.id) AS members
            FROM circle_invites i JOIN circles c ON c.id = i.circle_id LEFT JOIN users u ON u.id = c.owner_id
            WHERE i.invitee_user_id = %(u)s AND i.revoked_at IS NULL AND i.expires_at > now()
              AND i.created_at > now() - make_interval(days => %(d)s)
              AND (%(i)s::int IS NULL OR i.id = %(i)s::int)
              AND NOT EXISTS (SELECT 1 FROM circle_members m WHERE m.circle_id = i.circle_id AND m.user_id = %(u)s)
              AND {blocks.not_blocked('%(u)s', 'c.owner_id')}
              AND (i.created_by IS NULL OR {blocks.not_blocked('%(u)s', 'i.created_by')})
            ORDER BY i.created_at DESC, i.id DESC LIMIT %(n)s""",
        {"u": uid, "d": P.KEEP_DAYS, "i": invite_id, "n": P.SOURCE_MAX}).fetchall()
    blocks.mask_rows(conn, uid, rows, id_key="owner_id", name_key="owner_name")
    return rows


def _card_body(scopes: list[str]) -> str:
    if not scopes:
        return L("加入后不共享任何内容。可以随时修改或退出。", "You won't share anything. You can change this or leave any time.")
    zh = "、".join(P.SCOPE_LABELS[s][0] for s in scopes)
    en = ", ".join(P.SCOPE_LABELS[s][1] for s in scopes).lower()
    return L(f"加入后圈子成员能看到你的：{zh}。可以随时修改或退出。",
             f"Members will see your: {en}. You can change this or leave any time.")


def _invites(conn, uid: int) -> list[dict]:
    rows = _open_invites(conn, uid)
    if not rows:
        return []
    consent_ok, rules_ok = consent.agreed_at_least(conn, uid, FP.CONSENT_VERSION), rules.ok(conn, uid)
    out = []
    for r in rows:
        scopes = card_scopes(r)
        owner = r["owner_name"] or L("圈主", "The owner")
        avatar = f"/v1/users/me/inbox/invites/{r['id']}/avatar?v={r['avatar_image_id']}" if r["avatar_image_id"] else None
        out.append({
            "id": f"invite:{r['id']}", "type": "circle_invite", "at": r["created_at"],
            "title": L(f"{owner} 邀请你加入「{r['name']}」", f"{owner} invited you to join “{r['name']}”"),
            "body": _card_body(scopes), "url": None,
            "invite": {"invite_id": r["id"], "circle_name": r["name"], "avatar": avatar, "owner_name": r["owner_name"],
                       "members": r["members"], "expires_at": r["expires_at"], "default_scopes": scopes,
                       "shares": [{"scope": s, "label": L(*P.SCOPE_LABELS[s])} for s in scopes],
                       "consent_ok": consent_ok, "rules_ok": rules_ok},
            "_read": False, "_src": ("state", None)})
    return out


def _hidden_actors(conn, uid: int, actors: set[int]) -> set[int]:
    """Of these people, whose interactions stay out of the person's inbox."""
    if not actors:
        return set()
    active = {r["id"] for r in conn.execute(
        "SELECT u.id FROM users u WHERE u.id = ANY(%s) AND NOT EXISTS (SELECT 1 FROM circle_bans b WHERE b.user_id = u.id)",
        (sorted(actors),))}
    return (actors - active) | (actors & blocks.hidden_people(conn, uid)) | blocks.masked(conn, uid, actors)


def _shown_comments(conn, uid: int, post_ids: set[int]) -> dict[tuple[int, int], list[str]]:
    """{(post, writer): comment texts} the person may still read on these posts."""
    if not post_ids:
        return {}
    out: dict[tuple[int, int], list[str]] = {}
    for c in conn.execute(
            f"""SELECT c.post_id, c.user_id, c.body FROM circle_feed_comments c
                WHERE c.post_id = ANY(%(p)s) AND c.deleted_at IS NULL
                  AND {blocks.item_shown('comment', 'c.id', 'c.user_id', '%(u)s')}""",
            {"p": sorted(post_ids), "u": uid}):
        out.setdefault((c["post_id"], c["user_id"]), []).append(c["body"])
    return out


def _circle_notifications(conn, uid: int) -> list[dict]:
    rows = conn.execute(
        """SELECT id, kind, title, body, title_en, body_en, url, data, created_at, read_at FROM notifications
           WHERE user_id = %s AND kind ~ '^circle([._]|$)' AND created_at > now() - make_interval(days => %s)
           ORDER BY created_at DESC, id DESC LIMIT %s""", (uid, P.KEEP_DAYS, P.SOURCE_MAX)).fetchall()

    def number(row: dict, key: str) -> int | None:
        value = (row["data"] or {}).get(key)
        return value if isinstance(value, int) else None

    hide_people = _hidden_actors(conn, uid, {a for a in (number(r, "actor_id") for r in rows) if a is not None})
    rows = [r for r in rows if number(r, "actor_id") not in hide_people]
    comments = _shown_comments(conn, uid, {p for p in (number(r, "post_id") for r in rows if r["kind"] == COMMENT) if p is not None})

    def still_shown(row: dict) -> bool:
        # a notification keeps a copy of the comment's first characters; the comment itself says whether it may still be read
        return row["kind"] != COMMENT or any(text.startswith(row["body"]) for text in
                                             comments.get((number(row, "post_id"), number(row, "actor_id")), ()))

    en = is_en()
    return [{"id": f"notification:{r['id']}", "type": "circle", "kind": r["kind"], "at": r["created_at"],
             "title": _pick(r, "title", en), "body": _pick(r, "body", en), "url": r["url"],
             "_read": r["read_at"] is not None, "_src": ("notification", r["id"])} for r in rows if still_shown(r)]


# ---------------------------------------------------------------- the list
def _order(item: dict) -> tuple[datetime, str]:
    return item["at"], item["id"]


def _collect(conn, uid: int) -> list[dict]:
    """Everything in the person's inbox, newest first (nothing they hid); `read` set, `_src` says where it is kept."""
    items = _notices(conn, uid) + _whatsnew(conn, uid)
    if _circles_reach(conn, uid):
        items += _invites(conn, uid) + _circle_notifications(conn, uid)
    state = {r["item_id"]: r for r in conn.execute("SELECT item_id, read_at, hidden_at FROM inbox_item_state WHERE user_id = %s",
                                                   (uid,))}
    out = []
    for item in items:
        st = state.get(item["id"])
        if st and st["hidden_at"]:
            continue
        item["read"] = item.pop("_read") or bool(st and st["read_at"])
        out.append(item)
    return sorted(out, key=_order, reverse=True)


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _cursor(item: dict) -> str:
    # digits only: a client that forgets to escape a query value cannot break it (a + would become a space)
    return f"{item['at'].astimezone(timezone.utc).strftime(_CURSOR_TIME)}_{item['id']}"


def _parse_cursor(text: str) -> tuple[datetime, str]:
    stamp, _, item_id = text.partition("_")
    try:
        return datetime.strptime(stamp, _CURSOR_TIME).replace(tzinfo=timezone.utc), item_id
    except ValueError:
        raise InboxError(422, "bad_cursor", L("游标不对，请从头读取", "Bad cursor; read from the start")) from None


def listing(conn: psycopg.Connection, uid: int, since: datetime | None = None, before: str | None = None,
            limit: int = P.PAGE_LIMIT) -> dict:
    """One page, newest first. `since`: only items newer than that (the next `since` is this answer's `now`); `before`: this
    answer's `next`, for older pages. `unread` always counts the whole inbox. Without `since` the answer is the whole truth,
    so a client that keeps a copy can drop what is no longer there (a withdrawn invite, a blocked person)."""
    items = _collect(conn, uid)
    unread = sum(not i["read"] for i in items)
    if since is not None:
        items = [i for i in items if i["at"] > since]
    if before is not None:
        edge = _parse_cursor(before)
        items = [i for i in items if _order(i) < edge]
    page = items[:limit]
    # the transaction's start, so nothing written after this reading is older than the next `since`
    now = conn.execute("SELECT now() AS t").fetchone()["t"].astimezone(timezone.utc)
    return {"items": [_public(i) for i in page], "unread": unread, "more": len(items) > limit,
            "next": _cursor(page[-1]) if len(items) > limit else None,
            "now": now.isoformat().replace("+00:00", "Z"), "keep_days": P.KEEP_DAYS}


# ---------------------------------------------------------------- read / hidden
def _set_state(conn, uid: int, item_ids: list[str], *, read: bool = False, hidden: bool = False) -> None:
    conn.execute(
        """INSERT INTO inbox_item_state (user_id, item_id, read_at, hidden_at)
           SELECT %(u)s, x, CASE WHEN %(r)s::boolean THEN now() END, CASE WHEN %(h)s::boolean THEN now() END
           FROM unnest(%(ids)s::text[]) AS x
           ON CONFLICT (user_id, item_id) DO UPDATE SET read_at = coalesce(inbox_item_state.read_at, EXCLUDED.read_at),
             hidden_at = coalesce(inbox_item_state.hidden_at, EXCLUDED.hidden_at), updated_at = now()""",
        {"u": uid, "ids": item_ids, "r": read, "h": hidden})
    conn.execute("DELETE FROM inbox_item_state WHERE user_id = %s AND updated_at < now() - make_interval(days => %s)",
                 (uid, P.KEEP_DAYS))


def _mark(conn, uid: int, items: list[dict]) -> None:
    """Write `read` where each item keeps it (see the module docstring)."""
    by_source: dict[str, list] = {"notice": [], "notification": [], "state": []}
    for i in items:
        source, row_id = i["_src"]
        by_source[source].append(i["id"] if source == "state" else row_id)
    for notice_id in by_source["notice"]:
        notices.mark_seen(conn, uid, notice_id)
    if by_source["notification"]:
        notify_store.mark_read(conn, uid, by_source["notification"])
    if by_source["state"]:
        _set_state(conn, uid, by_source["state"], read=True)


def mark_read(conn: psycopg.Connection, uid: int, item_ids: list[str] | None = None) -> dict:
    """These items of the person's own list (ids that are not in it are ignored), or the whole list (None)."""
    items = _collect(conn, uid)
    wanted = None if item_ids is None else set(item_ids)
    todo = [i for i in items if not i["read"] and (wanted is None or i["id"] in wanted)]
    _mark(conn, uid, todo)
    return {"marked": len(todo), "unread": sum(not i["read"] for i in items) - len(todo)}


def hide(conn: psycopg.Connection, uid: int, item_id: str) -> bool:
    """Take one item out of the person's list (it also counts as read, so the badge goes down). False when it is not in the
    list. Only the view changes: hiding an invite neither declines nor accepts it."""
    item = next((i for i in _collect(conn, uid) if i["id"] == item_id), None)
    if item is None:
        return False
    if not item["read"]:
        _mark(conn, uid, [item])
    _set_state(conn, uid, [item_id], hidden=True)
    return True


# ---------------------------------------------------------------- invites
def accept(conn: psycopg.Connection, uid: int, invite_id: int) -> dict:
    """One tap: join with the circle's default scopes, the ones the card listed. Returns the circle as /v1/friends/join does.
    Checked here, right before joining: the invite is addressed to this person and still open, nobody blocked anybody. Then
    the consent with the circle clause and the community rules, each with its own code so the app can show that text and send
    this again. friends/store.join checks validity and those two once more. Accepting again after an answer got lost answers
    with the circle (`already`), not an error."""
    if not _circles_reach(conn, uid):
        raise _invite_gone()
    mine = conn.execute("SELECT circle_id FROM circle_invites WHERE id = %s AND invitee_user_id = %s", (invite_id, uid)).fetchone()
    if mine and conn.execute("SELECT 1 FROM circle_members WHERE circle_id = %s AND user_id = %s",
                             (mine["circle_id"], uid)).fetchone():
        return {**friends.circle_view(conn, uid, mine["circle_id"]), "already": True}
    rows = _open_invites(conn, uid, invite_id)
    if not rows:
        raise _invite_gone()
    if not consent.agreed_at_least(conn, uid, FP.CONSENT_VERSION):
        raise InboxError(409, "consent_required", L("先确认同意书（新增了「圈子共享」一节）",
                                                    "Please confirm the consent first (it has a new section on circle sharing)"),
                         required_version=FP.CONSENT_VERSION)
    if not rules.ok(conn, uid):
        raise InboxError(rules.STATUS, "rules_required", L("请先同意圈子社区规则", "Please accept the circle community rules first"),
                         required_version=FP.RULES_VERSION)
    try:
        return friends.join(conn, uid, card_scopes(rows[0]), invite_id=invite_id)
    except LookupError:
        raise _invite_gone() from None
    except friends.FriendsError as e:   # the circle is full, the person is in too many circles …
        raise InboxError(e.status, "join_refused", str(e)) from None


def ignore(conn: psycopg.Connection, uid: int, invite_id: int) -> None:
    """Decline (friends/store.decline): the card goes and the invite stops working. Nobody else can see that it happened."""
    if not _circles_reach(conn, uid) or not friends.decline(conn, uid, invite_id):
        raise _invite_gone()


def invite_avatar(conn: psycopg.Connection, uid: int, invite_id: int) -> dict | None:
    """The circle's picture for the card: only for the person the open invite is addressed to (members use /v1/friends)."""
    rows = _open_invites(conn, uid, invite_id)
    image_id = rows[0]["avatar_image_id"] if rows else None
    return images.get(conn, image_id) if image_id else None
