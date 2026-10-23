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
(tests/test_sync_center.py, apps/watch2whoop/test/hk_types_test.dart), and the app sends the unit with every batch, which the
server checks. Adding a type = one row here and one in the app; the app asks for its permission and keeps its own anchor.

ENGINE_TYPES (below) is the registry of the app's own sync engine (docs/apple-sync-engine.md §3.1.1, flag
apple_sync_engine): every type it reads, the HK_TYPES above included with the same metric, unit and aggregation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..importers.common import snake

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


# ---------------------------------------------------------------------------------------------------------------------
# The sync engine's registry (docs/apple-sync-engine.md §3.1.1; ingest: rawdata/engine.py, flag apple_sync_engine).
# The app ships the same table as apps/watch2whoop/assets/healthkit_types.json = engine_json() (`python -m
# body_engine.rawdata.hk_types > that file`; tests/test_apple_sync_engine.py checks the two are equal).
KINDS = ("quantity", "category", "workout", "heartbeat", "ecg", "water")
UPLOADS = ("raw", "stats", "raw+stats")
BG = ("immediate", "hourly", "none")
_C = "HKCategoryTypeIdentifier"


@dataclass(frozen=True)
class EngineType:
    """One HealthKit type the sync engine reads.
    - `hk`: HealthKit identifier (= hk_samples.type, like HK_TYPES); `key`: the Body Engine name (daily_metrics metric for
      quantities, importers.common.snake(hk) as the other importers use);
    - `kind`: quantity / category (value = the HealthKit integer) / workout / heartbeat / ecg / water; the last three keep
      their own batch fields and tables (rawdata/ingest.py);
    - `unit`: the HKUnit string the app reads values in, `factor`: value × factor = the stored value in `store_unit` (the
      unit the other importers store, importers/ow.py SPECS); '' for non-quantities;
    - `upload`: raw samples (samples[] / categories[] / workouts[]) and / or 15-minute statistics buckets (stats[], only
      HealthKit cumulative types; Apple de-duplicates devices), D1 of the design;
    - `agg`: how a day is made (sum / avg, as importers/apple_zip), None for non-quantities;
    - `lo` / `hi`: plausible stored values (per sample, and per bucket for stats); outside = dropped;
    - app only: `bg` background delivery frequency, `order` first-sync order (lower first), `parents` types whose
      permission must be asked together, `min_ios` (0 = the app's minimum), `page` samples per request (foreground,
      background);
    - `flag` / `consent`: the rollout flag / consent version the type needs; `status`: the sync/data_status kind."""
    hk: str
    key: str
    kind: str
    unit: str = ""
    factor: float = 1.0
    store_unit: str = ""
    upload: str = "raw"
    agg: str | None = None
    lo: float = 0
    hi: float = 0
    bg: str = "none"
    order: int = 50
    parents: tuple[str, ...] = ()
    min_ios: int = 0
    page: tuple[int, int] = (2000, 200)
    flag: str | None = None
    consent: str | None = None
    status: str | None = None

    @property
    def raw(self) -> bool:
        return "raw" in self.upload

    @property
    def stats(self) -> bool:
        return "stats" in self.upload


def _q(name: str, unit: str, agg: str, lo: float, hi: float, *, store_unit: str | None = None, **kw) -> EngineType:
    return EngineType(_Q + name, snake(name), "quantity", unit, store_unit=store_unit or unit, agg=agg, lo=lo, hi=hi, **kw)


def _phase1(t: HkType, **kw) -> EngineType:
    """An HK_TYPES row: same metric, unit, aggregation and plausible range."""
    return EngineType(t.hk, t.metric, "quantity", t.unit, store_unit=t.unit, agg=t.agg, lo=t.lo, hi=t.hi,
                      status=t.kind, **kw)


def _p(name: str) -> HkType:
    return BY_HK[_Q + name]


_RAW_CONSENT = "2026-10-22.1"   # = rawdata/ingest.RAW_CONSENT_VERSION (checked by the tests)
ENGINE_TYPES: tuple[EngineType, ...] = (
    # sleep and workouts first (the Today page needs them), then the overnight readings, heart rate last (largest)
    EngineType(_C + "SleepAnalysis", "sleep_analysis", "category", lo=0, hi=5, bg="immediate", order=1, status="sleep"),
    EngineType("HKWorkoutTypeIdentifier", "workout", "workout", "min", store_unit="min", lo=0, hi=2880, bg="immediate",
               order=2, page=(200, 50), status="workouts"),
    _q("HeartRateVariabilitySDNN", "ms", "avg", 1, 500, bg="immediate", order=3, status="hrv"),
    _q("HeartRateVariabilityRMSSD", "ms", "avg", 1, 500, bg="immediate", order=3, min_ios=27, status="hrv"),
    _q("RestingHeartRate", "count/min", "avg", 20, 200, bg="immediate", order=4, status="resting_heart_rate"),
    _q("RespiratoryRate", "count/min", "avg", 4, 60, bg="immediate", order=5, status="respiratory_rate"),
    _q("OxygenSaturation", "%", "avg", 50, 100, factor=100.0, bg="immediate", order=6, status="blood_oxygen"),  # 0–1 -> 0–100
    _phase1(_p("AppleSleepingWristTemperature"), bg="immediate", order=7, upload="raw"),
    EngineType("HKDataTypeIdentifierElectrocardiogram", "electrocardiogram", "ecg", bg="immediate", order=8, page=(5, 2),
               consent=_RAW_CONSENT, status="ecg"),
    EngineType("HKDataTypeIdentifierHeartbeatSeries", "heartbeat_series", "heartbeat", bg="immediate", order=9,
               parents=(_Q + "HeartRateVariabilitySDNN",), page=(200, 50), consent=_RAW_CONSENT, status="heartbeats"),
    _q("VO2Max", "ml/(kg*min)", "avg", 10, 100, store_unit="mL/min·kg", order=10, status="vo2max"),
    _q("BodyMass", "kg", "avg", 20, 400, order=11, status="weight"),
    _q("BodyFatPercentage", "%", "avg", 2, 75, factor=100.0, order=11),            # 0–1 -> 0–100 like the Shortcut import
    _q("LeanBodyMass", "kg", "avg", 10, 300, order=11),
    _q("BodyMassIndex", "count", "avg", 8, 90, order=11),
    _q("Height", "cm", "avg", 50, 260, order=11),
    _q("BodyTemperature", "degC", "avg", 30, 45, order=12),
    _q("BloodPressureSystolic", "mmHg", "avg", 40, 300, order=12),
    _q("BloodPressureDiastolic", "mmHg", "avg", 20, 200, order=12),
    _q("BloodGlucose", "mmol<180.15588000005408>/L", "avg", 0.5, 50, store_unit="mmol/L", order=12),
    EngineType("HKQuantityTypeIdentifierDietaryWater", "dietary_water", "water", "mL", store_unit="mL", lo=0, hi=5000,
               order=13, flag="water", status="water"),
    EngineType(_C + "MindfulSession", "mindful_session", "category", lo=0, hi=0, order=14),
    _q("WorkoutEffortScore", "appleEffortScore", "avg", 0, 10, order=15, min_ios=18),
    _q("EstimatedWorkoutEffortScore", "appleEffortScore", "avg", 0, 10, order=15, min_ios=18),
    # cumulative: 15-minute statistics buckets; steps and distances also as raw samples (minute steps for stress)
    _q("StepCount", "count", "sum", 0, 100000, upload="raw+stats", bg="hourly", order=20, status="steps"),
    _q("DistanceWalkingRunning", "km", "sum", 0, 100, upload="raw+stats", bg="hourly", order=21),
    _q("DistanceCycling", "km", "sum", 0, 300, upload="raw+stats", order=22),
    _q("DistanceSwimming", "m", "sum", 0, 20000, upload="raw+stats", order=22),
    _q("SwimmingStrokeCount", "count", "sum", 0, 20000, upload="raw+stats", order=22),
    _q("FlightsClimbed", "count", "sum", 0, 500, upload="stats", order=23),
    _q("ActiveEnergyBurned", "kcal", "sum", 0, 5000, upload="stats", bg="hourly", order=24, status="active_energy"),
    _q("BasalEnergyBurned", "kcal", "sum", 0, 5000, upload="stats", bg="hourly", order=24),
    _phase1(_p("AppleExerciseTime"), upload="raw+stats", order=25),
    _phase1(_p("AppleStandTime"), upload="raw+stats", order=25),
    _phase1(_p("TimeInDaylight"), upload="raw+stats", order=25),
    _phase1(_p("PhysicalEffort"), order=26),        # not cumulative in HealthKit: raw only (summed like the export import)
    _phase1(_p("WalkingHeartRateAverage"), order=27),
    _q("WalkingSpeed", "km/hr", "avg", 0, 20, order=28),
    _q("WalkingStepLength", "cm", "avg", 10, 250, order=28),
    _q("WalkingDoubleSupportPercentage", "%", "avg", 0, 1, order=28),
    _q("WalkingAsymmetryPercentage", "%", "avg", 0, 1, order=28),
    _q("SixMinuteWalkTestDistance", "m", "avg", 0, 1500, order=28),
    _phase1(_p("AppleWalkingSteadiness"), order=28),
    _phase1(_p("StairAscentSpeed"), order=28),
    _phase1(_p("StairDescentSpeed"), order=28),
    _q("RunningSpeed", "km/hr", "avg", 0, 45, order=29),
    _q("RunningStrideLength", "m", "avg", 0.1, 4, order=29),
    _q("RunningPower", "W", "avg", 0, 2500, order=29),
    _q("RunningVerticalOscillation", "cm", "avg", 0, 40, order=29),
    _q("RunningGroundContactTime", "ms", "avg", 50, 2000, order=29),
    _q("CyclingSpeed", "km/hr", "avg", 0, 120, order=29),
    _q("CyclingPower", "W", "avg", 0, 3000, order=29),
    _q("CyclingCadence", "count/min", "avg", 0, 250, order=29),
    _q("CyclingFunctionalThresholdPower", "W", "avg", 0, 1000, order=29),
    _q("WaterTemperature", "degC", "avg", -5, 45, order=30),
    _phase1(_p("EnvironmentalAudioExposure"), order=31),
    _phase1(_p("HeadphoneAudioExposure"), order=31),
    _q("EnvironmentalSoundReduction", "dBASPL", "avg", 0, 160, order=31),
    _q("HeartRate", "count/min", "avg", 20, 250, bg="hourly", order=99, status="heart_rate"),
)

ENGINE_BY_HK: dict[str, EngineType] = {t.hk: t for t in ENGINE_TYPES}
assert len(ENGINE_BY_HK) == len(ENGINE_TYPES) and len({t.key for t in ENGINE_TYPES}) == len(ENGINE_TYPES)
assert all(t.kind in KINDS and t.upload in UPLOADS and t.bg in BG and t.lo <= t.hi for t in ENGINE_TYPES)
assert all((t.kind == "quantity") == (t.agg in AGGS) for t in ENGINE_TYPES)
assert all(not t.stats or (t.kind == "quantity" and t.agg == "sum") for t in ENGINE_TYPES)
assert set(BY_HK) <= set(ENGINE_BY_HK), "every type of the app's direct upload is also read by the engine"


def engine_json() -> dict:
    """The registry as the app ships it (apps/watch2whoop/assets/healthkit_types.json)."""
    return {"types": [{**asdict(t), "parents": list(t.parents), "page": list(t.page)} for t in ENGINE_TYPES]}


if __name__ == "__main__":
    import json
    print(json.dumps(engine_json(), ensure_ascii=False, indent=1))
