// UI strings: Chinese first, English when the device language is not Chinese.
import 'dart:ui';

bool get isZh => PlatformDispatcher.instance.locale.languageCode == 'zh';

String tr(String zh, String en) => isZh ? zh : en;

/// Type names in the SDK log -> display names. Unlisted ones are shown as is.
const Map<String, (String, String)> _typeLabels = {
  'HeartRate': ('心率', 'Heart rate'),
  'RestingHeartRate': ('静息心率', 'Resting heart rate'),
  'HeartRateVariabilitySDNN': ('HRV（SDNN）', 'HRV (SDNN)'),
  'WalkingHeartRateAverage': ('步行平均心率', 'Walking heart rate'),
  'StepCount': ('步数', 'Steps'),
  'ActiveEnergyBurned': ('活动能量', 'Active energy'),
  'BasalEnergyBurned': ('静息能量', 'Resting energy'),
  'AppleExerciseTime': ('锻炼分钟', 'Exercise minutes'),
  'DistanceWalkingRunning': ('步行 + 跑步距离', 'Walking + running distance'),
  'DistanceCycling': ('骑车距离', 'Cycling distance'),
  'FlightsClimbed': ('爬楼', 'Flights climbed'),
  'OxygenSaturation': ('血氧', 'Blood oxygen'),
  'RespiratoryRate': ('呼吸频率', 'Respiratory rate'),
  'VO2Max': ('最大摄氧量', 'VO₂ max'),
  'BodyMass': ('体重', 'Weight'),
  'BodyFatPercentage': ('体脂率', 'Body fat'),
  'LeanBodyMass': ('去脂体重', 'Lean body mass'),
  'BodyMassIndex': ('BMI', 'BMI'),
  'Height': ('身高', 'Height'),
  'BodyTemperature': ('体温', 'Body temperature'),
  'WalkingSpeed': ('步行速度', 'Walking speed'),
  'WalkingStepLength': ('步长', 'Step length'),
  'WalkingAsymmetryPercentage': ('步行不对称', 'Walking asymmetry'),
  'WalkingDoubleSupportPercentage': ('双脚支撑时间', 'Double support time'),
  'RunningPower': ('跑步功率', 'Running power'),
  'BloodPressureSystolic': ('收缩压', 'Systolic BP'),
  'BloodPressureDiastolic': ('舒张压', 'Diastolic BP'),
  'BloodGlucose': ('血糖', 'Blood glucose'),
  'MindfulSession': ('正念', 'Mindfulness'),
  'WorkoutEffortScore': ('训练强度评分', 'Workout effort'),
  'EstimatedWorkoutEffortScore': ('估计训练强度', 'Estimated effort'),
  'sleep': ('睡眠', 'Sleep'),
  'workouts': ('训练', 'Workouts'),
};

String typeLabel(String key) {
  final l = _typeLabels[key];
  return l == null ? key : tr(l.$1, l.$2);
}
