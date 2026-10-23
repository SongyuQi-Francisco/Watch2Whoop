// The HealthKit quantity types the app reads itself (RawSync, raw_sync.dart) because the Open Wearables SDK (iOS 0.15)
// cannot: one registry row per type, nothing else in the app is specific to a type. The server keeps the same table
// (body_engine/src/body_engine/rawdata/hk_types.py); test/hk_types_test.dart and the server's tests/test_sync_center.py
// check that both list the same rows, and every upload carries the unit, which the server checks again.
// - hk: HealthKit identifier (also the RawSync kind and the name of its own anchor);
// - metric: the server's daily metric; unit: the HealthKit unit string the values are read in (`HKUnit(from:)`);
// - agg: sum / avg, how the server makes a day; kind: the data-status key the sync center groups it under.
// Adding a type = one row here and one on the server; its read permission is asked with the others.

class HkType {
  const HkType(this.hk, this.metric, this.unit, this.agg, this.kind);
  final String hk, metric, unit, agg, kind;
}

const List<HkType> kHkTypes = [
  HkType('HKQuantityTypeIdentifierAppleStandTime', 'apple_stand_time', 'min', 'sum', 'stand_time'),
  HkType('HKQuantityTypeIdentifierAppleExerciseTime', 'apple_exercise_time', 'min', 'sum', 'exercise_time'),
  HkType('HKQuantityTypeIdentifierTimeInDaylight', 'time_in_daylight', 'min', 'sum', 'daylight'),
  HkType('HKQuantityTypeIdentifierPhysicalEffort', 'physical_effort', 'kcal/hr·kg', 'sum', 'physical_effort'),
  HkType('HKQuantityTypeIdentifierAppleSleepingWristTemperature', 'apple_sleeping_wrist_temperature', 'degC', 'avg',
      'wrist_temperature'),
  HkType('HKQuantityTypeIdentifierEnvironmentalAudioExposure', 'environmental_audio_exposure', 'dBASPL', 'avg',
      'environmental_audio'),
  HkType('HKQuantityTypeIdentifierHeadphoneAudioExposure', 'headphone_audio_exposure', 'dBASPL', 'avg', 'headphone_audio'),
  HkType('HKQuantityTypeIdentifierWalkingHeartRateAverage', 'walking_heart_rate_average', 'count/min', 'avg',
      'walking_heart_rate'),
  HkType('HKQuantityTypeIdentifierStairAscentSpeed', 'stair_ascent_speed', 'm/s', 'avg', 'stair_speed'),
  HkType('HKQuantityTypeIdentifierStairDescentSpeed', 'stair_descent_speed', 'm/s', 'avg', 'stair_speed'),
  HkType('HKQuantityTypeIdentifierAppleWalkingSteadiness', 'apple_walking_steadiness', '%', 'avg', 'walking_steadiness'),
];

HkType? hkTypeOf(String hk) {
  for (final t in kHkTypes) {
    if (t.hk == hk) return t;
  }
  return null;
}

/// Data-status key -> the HealthKit types whose newest sample on the phone the sync center shows ("没采集还是没同步").
/// Series / ECG / workouts use their HKObjectType identifiers (RawSync.swift `healthLatest`).
final Map<String, List<String>> kPhoneTypes = {
  'heart_rate': ['HKQuantityTypeIdentifierHeartRate'],
  'hrv': ['HKQuantityTypeIdentifierHeartRateVariabilitySDNN'],
  'resting_heart_rate': ['HKQuantityTypeIdentifierRestingHeartRate'],
  'sleep': ['HKCategoryTypeIdentifierSleepAnalysis'],
  'steps': ['HKQuantityTypeIdentifierStepCount'],
  'active_energy': ['HKQuantityTypeIdentifierActiveEnergyBurned'],
  'workouts': ['HKWorkoutTypeIdentifier'],
  'weight': ['HKQuantityTypeIdentifierBodyMass'],
  'blood_oxygen': ['HKQuantityTypeIdentifierOxygenSaturation'],
  'respiratory_rate': ['HKQuantityTypeIdentifierRespiratoryRate'],
  'vo2max': ['HKQuantityTypeIdentifierVO2Max'],
  'heartbeats': ['HKDataTypeIdentifierHeartbeatSeries'],
  'ecg': ['HKDataTypeIdentifierElectrocardiogram'],
  'water': ['HKQuantityTypeIdentifierDietaryWater'],
  for (final t in kHkTypes) t.kind: [for (final x in kHkTypes) if (x.kind == t.kind) x.hk],
};
