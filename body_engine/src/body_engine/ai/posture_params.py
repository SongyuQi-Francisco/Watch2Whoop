"""Posture photos in Jarvis (issue #174, docs/posture-photos-plan.md phase 1): the values and decisions, all in one place."""
from . import run_params as rp

FLAG = "posture_photos"           # rollout/registry.py; off by default

# What POST /v1/assistant/chat's `purpose` may be. Anything else is a 422: an unknown value must never fall back to an
# ordinary chat photo, which stays in the conversation (ai/chat.py).
PURPOSES = ("posture",)

# Whose pictures are replaced by a placeholder when the turn ends. None = an ordinary chat photo (no purpose).
# Decision D2 of the plan (section 9): to cover every chat photo, add None here; nothing else needs to change.
REDACT_PURPOSES = frozenset({"posture"})

# How long a picture may stay when the turn did not replace it (the server stopped mid-turn, a write confirmation never
# answered): the hourly job (batch.py) replaces it after this. Longer than one turn may run, so it never takes a picture
# from a turn that is still using it. The worst case is this + the hour until the next run.
REDACT_AFTER_MINUTES = 30
assert REDACT_AFTER_MINUTES * 60 > rp.MAX_RUN_SECONDS

# Every other chat photo (and video frame) stays this long, then ai/image_retention.py replaces it the same way. Owner, 2026-11-04:
# periodic rather than at the end of the turn, which is too soon for an ordinary photo the person may still ask about.
CHAT_IMAGE_KEEP_DAYS = 30

# Decision D11 of the plan: a body photo only from a person the profile says is an adult. No birthday = not known to be one.
MIN_AGE = 18

# Decision D4 of the plan (section 3.5): the fallback AI takes posture photos only after it passes the phase 0 evaluation,
# which has not run and has no approved budget. Until then its entry gives the photo guide and asks for a description in words.
# Before turning this on: the first-use notice must say where the photos go (the plan's section 8; the fallback's display
# name is "Jarvis").
FALLBACK_PHOTOS = False


def redacts(purpose: str | None) -> bool:
    """The one test of whether a picture with this purpose is replaced when the turn ends (D2: change REDACT_PURPOSES)."""
    return purpose in REDACT_PURPOSES
