"""Chat photos do not stay for ever (owner's decision, 2026-11-04): what a person sent to Jarvis in a chat (photos, frames from a
video) is replaced by a line of text once it is CHAT_IMAGE_KEEP_DAYS old. The app never showed them again anyway, only how many
there were; the only use left was going back to the model in the kept turns of that conversation.

Only pictures inside the person's own chat messages. The words of the conversation, the AI's answers, avatars and circle pictures
(table images), exercise pictures and health data are not touched. Copies in backups go when the backups rotate (privacy policy).
Posture photos go sooner: when the turn ends (posture.py).
"""
import psycopg
from psycopg.types.json import Jsonb

from . import posture
from . import posture_params as pp


def expire(conn: psycopg.Connection) -> int:
    """The hourly job: every user, idempotent. Returns how many messages were rewritten."""
    rows = conn.execute("""SELECT id, user_id, content FROM ai_messages
                           WHERE role = 'user' AND created_at < now() - make_interval(days => %s)
                             AND content @> '[{"type": "image"}]'""", (pp.CHAT_IMAGE_KEEP_DAYS,)).fetchall()
    for r in rows:
        conn.execute("UPDATE ai_messages SET content = %s, redact_after = NULL WHERE id = %s AND user_id = %s",
                     (Jsonb(posture.without_images(r["content"])), r["id"], r["user_id"]))
    conn.commit()
    return len(rows)
