// UI strings: Chinese first, English when the device language is not Chinese.
// The language follows the system by default; once signed in, the Today page reports the profile language over the JS channel
// (`lang:zh` / `lang:en` / `lang:` = follow the system), which is kept as an override until sign-out.
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

const String _kLangKey = 'w2w.lang.v1';

/// JS channel message prefix the Today page uses to report the profile language.
const String kLangMessagePrefix = 'lang:';

/// Language override: 'zh' / 'en', or null = follow the system language. The root widget listens to it and rebuilds.
final ValueNotifier<String?> langOverride = ValueNotifier<String?>(null);

/// 'zh' / 'en' for a language tag such as `zh`, `zh-Hans`, `en_AU`; null for empty or unsupported (= follow the system).
String? normalizeLang(String? tag) {
  final t = (tag ?? '').trim().toLowerCase();
  if (t == 'zh' || t.startsWith('zh-') || t.startsWith('zh_')) return 'zh';
  if (t == 'en' || t.startsWith('en-') || t.startsWith('en_')) return 'en';
  return null;
}

/// Parses a JS channel message: null when it is not a language message, otherwise a record whose `lang` is the override
/// to apply (null = follow the system).
({String? lang})? parseLangMessage(String message) =>
    message.startsWith(kLangMessagePrefix) ? (lang: normalizeLang(message.substring(kLangMessagePrefix.length))) : null;

/// Loads the saved override; call before runApp so the first frame is already in the right language.
Future<void> loadLangOverride() async {
  try {
    final prefs = await SharedPreferences.getInstance();
    langOverride.value = normalizeLang(prefs.getString(_kLangKey));
  } catch (e) {
    debugPrint('loadLangOverride failed: $e');
  }
}

/// Sets (and saves) the override; null clears it (= follow the system). Listeners are only notified on a change.
Future<void> setLangOverride(String? lang) async {
  final v = normalizeLang(lang);
  langOverride.value = v;
  final prefs = await SharedPreferences.getInstance();
  if (v == null) {
    await prefs.remove(_kLangKey);
  } else {
    await prefs.setString(_kLangKey, v);
  }
}

/// Device language code; replaceable in tests (the test binding's locale is not PlatformDispatcher.instance's).
@visibleForTesting
String Function() systemLanguageCode = () => PlatformDispatcher.instance.locale.languageCode;

String get systemLang => systemLanguageCode() == 'zh' ? 'zh' : 'en';

/// Language the native screens use right now: the override first, else the system language.
String get currentLang => langOverride.value ?? systemLang;

bool get isZh => currentLang == 'zh';

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
