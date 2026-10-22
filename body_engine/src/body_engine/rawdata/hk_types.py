"""Registry of the HealthKit quantity types the Watch2Whoop app reads itself and uploads as raw samples
(POST /v1/ingest/healthkit `quantities[]`, rawdata/quantities.py): the types the Open Wearables SDK (iOS 0.15) cannot read,
so they stopped arriving once people synced only through the app (before, they came from the Apple Health export and the
iOS Shortcut).

One row per type, and nothing else in the code is specific to a type:
- `hk`: the HealthKit identifier;
- `metric`: the daily_metrics name, always importers.common.snake(hk), the name the Apple Health export import, the
  Shortcut and the Open Wearables importer use, so the daily values of every source meet in daily_metric_sources;
- `unit`: the unit the app reads the values in (a HealthKit unit string, `HKUnit(from:)`), which is also the unit stored:
  the same unit those importers store (tests/test_app_types.py);
- `agg`: how a day is made from the samples, exactly as the export import does it (importers/apple_zip.daily_rows):
  `sum` (HealthKit cumulative types) = the largest single-source total of the day, `avg` = mean, min and max of all
  samples; the day of a sample is the user's local date of its start;
- `lo` / `hi`: plausible values; a sample outside is dropped (not the whole batch, or one odd value would block the
  upload forever);
- `kind`: the data-status key (sync/data_status.py), `score`: the raw-sample field the scores read (scores/inputs.py,
  e.g. sleeping wrist temperature -> "temp"), None = only daily values.

The app keeps the same table (apps/watch2whoop/lib/src/hk_types.dart). Both sides have a test that the two tables match
(tests/test_app_types.py, apps/watch2whoop/test/hk_types_test.dart), and the app sends the unit with every batch, which the
server checks. Adding a type = one row here and one in the app; the app asks for its permission and keeps its own anchor.
"""
from __future__ import annotations

from dataclasses import dataclass

AGGS = ("sum", "avg")


@dataclass(frozen=True)
class HkType:
    hk: str
    metric: str
    unit: str
    agg: str
    lo: float
    hi: float
    kind: str
    score: str | None = None


_Q = "HKQuantityTypeIdentifier"

HK_TYPES: tuple[HkType, ...] = (
    HkType(_Q + "AppleStandTime", "apple_stand_time", "min", "sum", 0, 1440, "stand_time"),
    HkType(_Q + "AppleExerciseTime", "apple_exercise_time", "min", "sum", 0, 1440, "exercise_time"),
    HkType(_Q + "TimeInDaylight", "time_in_daylight", "min", "sum", 0, 1440, "daylight"),
    HkType(_Q + "PhysicalEffort", "physical_effort", "kcal/hr·kg", "sum", 0, 40, "physical_effort"),   # summed like the export import
    HkType(_Q + "AppleSleepingWristTemperature", "apple_sleeping_wrist_temperature", "degC", "avg", 25, 45,
           "wrist_temperature", "temp"),
    HkType(_Q + "EnvironmentalAudioExposure", "environmental_audio_exposure", "dBASPL", "avg", 0, 160, "environmental_audio"),
    HkType(_Q + "HeadphoneAudioExposure", "headphone_audio_exposure", "dBASPL", "avg", 0, 160, "headphone_audio"),
    HkType(_Q + "WalkingHeartRateAverage", "walking_heart_rate_average", "count/min", "avg", 25, 230, "walking_heart_rate"),
    HkType(_Q + "StairAscentSpeed", "stair_ascent_speed", "m/s", "avg", 0, 5, "stair_speed"),
    HkType(_Q + "StairDescentSpeed", "stair_descent_speed", "m/s", "avg", 0, 5, "stair_speed"),
    HkType(_Q + "AppleWalkingSteadiness", "apple_walking_steadiness", "%", "avg", 0, 1, "walking_steadiness"),
)

BY_HK: dict[str, HkType] = {t.hk: t for t in HK_TYPES}
BY_METRIC: dict[str, HkType] = {t.metric: t for t in HK_TYPES}
# raw-sample field of the scores -> types (scores/inputs.load_app_db)
SCORE_TYPES: dict[str, str] = {t.hk: t.score for t in HK_TYPES if t.score}

assert len(BY_HK) == len(HK_TYPES) and len(BY_METRIC) == len(HK_TYPES), "duplicate HealthKit type or metric"
assert all(t.agg in AGGS and t.lo < t.hi for t in HK_TYPES)


def metrics_of_kind(kind: str) -> tuple[str, ...]:
    return tuple(t.metric for t in HK_TYPES if t.kind == kind)
