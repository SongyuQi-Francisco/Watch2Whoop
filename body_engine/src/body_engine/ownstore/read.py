"""Readers on the own tables (issue #252 phase 3, flag own_food_read; docs/own-food-weight-storage.md §3).

`sparky_client.reader_for_user(conn, user_id)` hands out an OwnReader instead of a SparkyFitness client when the user's
reads come from the own tables (ownstore/source.py). The module functions of sparky_client (training_log, food_entries,
weight, exercises, workout_presets, workout_plans, goal_for_date) see it and ask it instead of the API, so callers keep
calling them exactly as before and get the same shapes: the records are rebuilt by rows.py and go through the same
converters (`_session`, `_food_entry`, the weight filter) as SparkyFitness responses.

Not stored here: the weekly plan templates / presets SparkyFitness kept as a mirror of training_plans, and its food goals
(the fallback before a user's first own goal version). In read mode they still come from SparkyFitness, unchanged; once
writes are own only (own_food_only) they are empty / unavailable, Body Engine's training_plans and nutrition goal versions
are the source.
"""
from datetime import date

import psycopg

from . import rows


class OwnReader:
    own = True

    def __init__(self, conn: psycopg.Connection, user_id: int, only: bool = False):
        self.conn, self.user_id, self.only = conn, user_id, only
        self._client = None

    # ---------------------------------------------------------------- the own tables
    def training_log(self, start: date, end: date) -> list[dict]:
        from ..sparky_client import _session
        return [_session(s) for s in rows.session_records(self.conn, self.user_id, start, end)]

    def food_entries(self, start: date, end: date) -> list[dict]:
        from ..sparky_client import _food_entry
        return [_food_entry(r) for r in rows.food_day(self.conn, self.user_id, start, end)]

    def weight(self, start: date, end: date) -> list[dict]:
        from ..sparky_client import weight_rows
        return weight_rows(rows.checkins(self.conn, self.user_id, start, end))

    def exercises(self) -> list[dict]:
        return rows.exercises(self.conn, self.user_id)

    # ---------------------------------------------------------------- still SparkyFitness in read mode
    def sparky(self):
        from ..sparky_client import SparkyError, client_for_user
        if self.only:
            raise SparkyError("own_food_only: SparkyFitness is not used")
        if self._client is None:
            self._client = client_for_user(self.conn, self.user_id)
        return self._client

    def workout_presets(self) -> dict:
        from ..sparky_client import workout_presets
        return {} if self.only else workout_presets(self.sparky())

    def workout_plans(self) -> list[dict]:
        from ..sparky_client import workout_plans
        return [] if self.only else workout_plans(self.sparky())

    def goal_for_date(self, day: date) -> dict:
        from ..sparky_client import goal_for_date
        return goal_for_date(self.sparky(), day)

    def get(self, path: str, **params):
        """A raw SparkyFitness read (a caller this reader does not cover): SparkyFitness in read mode, an error when only."""
        return self.sparky().get(path, **params)
