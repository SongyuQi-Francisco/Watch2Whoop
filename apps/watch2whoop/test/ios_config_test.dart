// iOS settings whose absence kills the app outright (no Dart or Swift error to catch):
// - TCC ends the process when the app touches a privacy-protected service without its usage description. The Today
//   page in the WebView can save an image to Photos (long press → Save to Photos) and record a video (file input →
//   Take Video, which needs the microphone).
// - HealthKit raises NSInvalidArgumentException when a type is requested without its parent type (build 1.0.0+4).
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

Map<String, String> plistStrings(String xml) => {
      for (final m in RegExp(r'<key>(\w+)</key>\s*<string>([^<]*)</string>').allMatches(xml)) m.group(1)!: m.group(2)!,
    };

Map<String, String> infoPlistStrings(String text) => {
      for (final m in RegExp(r'^"(\w+)" = "(.*)";$', multiLine: true).allMatches(text)) m.group(1)!: m.group(2)!,
    };

void main() {
  final plist = plistStrings(File('ios/Runner/Info.plist').readAsStringSync());
  final usage = plist.keys.where((k) => k.endsWith('UsageDescription')).toSet();

  test('every privacy service the app or the Today page can reach has a usage description', () {
    const required = {
      'NSCameraUsageDescription', // QR scanner; photo / video capture from the Today page file inputs
      'NSMicrophoneUsageDescription', // "Take Video" from the Today page video input
      'NSPhotoLibraryAddUsageDescription', // long press on an image → Save to Photos
      'NSHealthShareUsageDescription',
      'NSHealthUpdateUsageDescription',
    };
    expect(usage, containsAll(required));
    for (final k in usage) {
      expect(plist[k]!.trim(), isNotEmpty, reason: k);
    }
  });

  test('both InfoPlist.strings translate every usage description', () {
    for (final lang in ['en', 'zh-Hans']) {
      final strings = infoPlistStrings(File('ios/Runner/$lang.lproj/InfoPlist.strings').readAsStringSync());
      expect(strings.keys.toSet(), usage, reason: lang);
      for (final v in strings.values) {
        expect(v.trim(), isNotEmpty, reason: lang);
      }
    }
  });

  test('HealthKit series types are requested together with their parent types', () {
    // Parent types: HKSeriesType.heartbeat() → HRV SDNN, HKSeriesType.workoutRoute() → workout.
    for (final f in Directory('ios/Runner').listSync().whereType<File>().where((f) => f.path.endsWith('.swift'))) {
      final src = f.readAsStringSync();
      for (final set in RegExp(r'\[[^\[\]]*HKSeriesType\.\w+\(\)[^\[\]]*\]').allMatches(src).map((m) => m.group(0)!)) {
        if (set.contains('HKSeriesType.heartbeat()')) {
          expect(set, contains('.heartRateVariabilitySDNN'), reason: '${f.path}: $set');
        }
        if (set.contains('HKSeriesType.workoutRoute()')) {
          expect(set, contains('workoutType()'), reason: '${f.path}: $set');
        }
      }
    }
  });
}
