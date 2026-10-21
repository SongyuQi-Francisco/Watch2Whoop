"""Daily water intake (issue #125).

- store.py: water_intake rows (migration 540), daily totals, the day's goal (diet profile water_ml, else params defaults,
  plus training / sauna extras), undo.
- api.py: GET / POST /v1/users/{user}/water, DELETE /water/{id} (scope nutrition; writes only for oneself).
- tools.py: log_water for the user's AI (undo through rollback_write, writelog kind "water").
- Apple Health: the app uploads HKQuantityTypeIdentifierDietaryWater samples to POST /v1/ingest/healthkit (rawdata/ingest.py,
  water[]); Open Wearables 0.9 maps only Android's hydration, so it cannot carry Apple's water.
- rules/hydration.py: one afternoon reminder when less than half of the goal is in.

Gradual rollout (docs/rollout.md): feature flag FEATURE = "water" (rollout/registry.py), off until an admin turns it on
(`body-engine flags set water --state cohort` for the beta group). Off: the endpoints answer 404, the Today page draws
no card, log_water refuses, get_briefing has no water, the rule does not run and Apple Health water is not stored.
"""
import psycopg

from ..rollout import flags
from .params import FEATURE


def enabled(conn: psycopg.Connection, user_id: int) -> bool:
    """The water flag for this user (rollout/flags.enabled)."""
    return flags.enabled(conn, user_id, FEATURE)


__all__ = ["FEATURE", "enabled"]
