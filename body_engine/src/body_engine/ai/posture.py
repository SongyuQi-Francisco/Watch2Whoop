"""Posture photos in Jarvis (issue #174, docs/posture-photos-plan.md phase 1): sent once to the AI the user picked, then gone.

A posture message is stored like any other (the tool loop reads the history from the database on every model call), with
ai_messages.redact_after set. When the turn ends, however it ends, chat.py calls finish_turn: its pictures become one
{"type": "image_redacted", "purpose": ..., "n": ...} block and only the text stays. A turn waiting for a write confirmation has
not ended, so the pictures wait with it. What no turn end reached (the server stopped mid-turn, a confirmation never
answered) is replaced by redact_overdue, from the hourly job. Later turns see a line of text instead (context.show_redacted),
so the pictures never go out again, also not to another AI the user switches to.

Which pictures this applies to is decided by posture_params.redacts alone. Ordinary chat photos are not touched here; they go
after a month (image_retention.py). Until then they stay with the AI they were sent to: for_model keeps them from any other.
"""
import logging
from datetime import date

import psycopg
from psycopg import pq
from psycopg.types.json import Jsonb

from .. import queries
from ..i18n import L
from ..rollout import flags
from . import posture_params as pp

log = logging.getLogger(__name__)

IMAGE, REDACTED = "image", "image_redacted"
OFF, GUIDE, PHOTOS = "off", "guide", "photos"


def age_on(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _adult(conn: psycopg.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT birth_date FROM users WHERE id = %s", (user_id,)).fetchone()
    return bool(row and row["birth_date"]) and age_on(row["birth_date"], queries.local_today(conn, user_id)) >= pp.MIN_AGE


def mode(conn: psycopg.Connection, user_id: int, in_app: bool, fallback: bool, vision: bool | None) -> str:
    """What this user's posture entry does: off = no entry; guide = the photo guide and a description in words, no photo is
    sent; photos = three photos to the AI in use. Sent to the app with the AI status; start of a posture message checks it too."""
    if not flags.enabled(conn, user_id, pp.FLAG) or not _adult(conn, user_id):
        return OFF
    if not in_app or vision is False or (fallback and not pp.FALLBACK_PHOTOS):
        return GUIDE
    return PHOTOS


def notice() -> str:
    """The fixed line before a posture answer: no model has passed the phase 0 evaluation (the plan's section 3.5)."""
    return L("这个模型看体态的准确度有限，结论仅供参考", "This model's accuracy on posture is limited; treat the result as for reference only")


def tag(images: list[dict], purpose: str | None) -> list[dict]:
    return [{**b, "purpose": purpose} for b in images] if purpose else images


def needs_redaction(content: list[dict]) -> bool:
    return any(b["type"] == IMAGE and pp.redacts(b.get("purpose")) for b in content)


def is_posture(content: list[dict]) -> bool:
    return any(b["type"] in (IMAGE, REDACTED) and b.get("purpose") == "posture" for b in content)


def image_count(content: list[dict]) -> int:
    """Pictures of a message for the app (image data is never returned), also after they were replaced."""
    return sum(b.get("n", 1) for b in content if b["type"] in (IMAGE, REDACTED))


def without_images(content: list[dict]) -> list[dict]:
    """The message with its pictures replaced: one placeholder where the first was, the text as it was."""
    images = [b for b in content if b["type"] == IMAGE]
    if not images:
        return content
    out, placed = [], False
    for b in content:
        if b["type"] != IMAGE:
            out.append(b)
        elif not placed:
            out.append({"type": REDACTED, "purpose": images[0].get("purpose"), "n": len(images)})
            placed = True
    return out


def ai_key(provider: dict) -> str:
    """The AI a picture goes to. Another provider, model or address counts as another AI (behind one address, a model can be another
    vendor's), and so does a provider deleted and added again."""
    return f"{provider['id'] or 'fallback'}|{provider['kind']}:{provider['model'] or ''}|{provider['base_url'] or ''}"


def for_model(rows: list[dict], images_upto: int | None) -> list[dict]:
    """The stored messages as the AI in use may read them. Pictures from before the conversation moved to this AI stay behind; nothing
    stored changes. Once a picture is gone from an earlier message, reasoning a vendor tied to the exact history (native) no longer
    fits: earlier turns go out as plain content, as they do after a switch. The turn under way keeps its own, written after the change."""
    upto = images_upto or 0
    current = max((i for i, r in enumerate(rows) if r["role"] == "user"), default=0)
    out, changed = [], False
    for i, r in enumerate(rows):
        if r["id"] <= upto:
            r = {**r, "content": without_images(r["content"])}
        changed = changed or any(b.get("type") == REDACTED for b in r["content"])
        out.append({**r, "native": None} if changed and (i < current or r["id"] <= upto) else r)
    return out


def _replace(conn: psycopg.Connection, rows: list[dict]) -> int:
    for r in rows:
        conn.execute("UPDATE ai_messages SET content = %s, redact_after = NULL WHERE id = %s AND user_id = %s",
                     (Jsonb(without_images(r["content"])), r["id"], r["user_id"]))
    return len(rows)


def redact_conversation(conn: psycopg.Connection, conv_id: int, user_id: int) -> int:
    """Every waiting picture of this conversation (an earlier unanswered confirmation may have left some). Idempotent."""
    return _replace(conn, conn.execute("""SELECT id, user_id, content FROM ai_messages
                                          WHERE conversation_id = %s AND user_id = %s AND redact_after IS NOT NULL""",
                                       (conv_id, user_id)).fetchall())


def redact_overdue(conn: psycopg.Connection) -> int:
    """The hourly job: what no turn end reached. All users; idempotent; only reads and rewrites messages with redact_after set."""
    n = _replace(conn, conn.execute("SELECT id, user_id, content FROM ai_messages WHERE redact_after <= now()").fetchall())
    conn.commit()
    return n


def finish_turn(conn: psycopg.Connection, conv_id: int, user_id: int) -> None:
    """The end of a turn, called from a `finally`: it runs after a failure too, and must never hide the failure. If it does
    not get through, redact_overdue still will. Nothing is committed when there is nothing to replace (every ordinary chat)."""
    try:
        if conn.info.transaction_status == pq.TransactionStatus.INERROR:   # what failed is gone either way (runs.py commits = rolls back)
            conn.rollback()
        state = conn.execute("""SELECT c.state FROM ai_conversations c WHERE c.id = %s AND c.user_id = %s AND EXISTS (
                                  SELECT 1 FROM ai_messages m WHERE m.conversation_id = c.id AND m.redact_after IS NOT NULL)""",
                             (conv_id, user_id)).fetchone()
        if state is None or state["state"] == "awaiting_confirmation":
            return
        redact_conversation(conn, conv_id, user_id)
        conn.commit()
    except Exception as e:  # noqa: BLE001  the class name only: a message could quote the statement's parameters
        log.warning("posture photos: not replaced at the end of the turn (%s); the hourly job will", type(e).__name__)
