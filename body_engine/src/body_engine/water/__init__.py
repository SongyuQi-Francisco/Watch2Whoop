"""Daily water intake (issue #125).

- store.py: water_intake rows (migration 540), daily totals, the day's goal (diet profile water_ml, else params defaults,
  plus training / sauna extras), undo.
- api.py: GET / POST /v1/users/{user}/water, DELETE /water/{id} (scope nutrition; writes only for oneself).
- tools.py: log_water for the user's AI (undo through rollback_write, writelog kind "water").
- Apple Health: the app uploads HKQuantityTypeIdentifierDietaryWater samples to POST /v1/ingest/healthkit (rawdata/ingest.py,
  water[]); Open Wearables 0.9 maps only Android's hydration, so it cannot carry Apple's water.
- rules/hydration.py: one afternoon reminder when less than half of the goal is in.

Gradual rollout: everything above asks enabled(conn, user_id) first. The feature-flag module is being built in another
pull request; until it is merged this hook returns True for everyone. Once it lands, replace the body with its check of
params.FEATURE (default: the beta group only).
"""
import psycopg

from .params import FEATURE


def enabled(conn: psycopg.Connection, user_id: int) -> bool:
    """Feature-flag hook for FEATURE ("water"). TODO(feature flags): call the flag module here once it is merged."""
    return True


__all__ = ["FEATURE", "enabled"]
