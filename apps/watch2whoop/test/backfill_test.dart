// First sync 14 days + history backfill (owner's decision 2026-10-26; the page's 「补全历史」 per
// docs/sync-onboarding-app-requirements.md §10): link, onApp progress, ranges, the re-sync state, native wiring.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/backfill.dart';
import 'package:watch2whoop/src/config.dart';
import 'package:watch2whoop/src/health_range.dart';
import 'package:watch2whoop/src/resync.dart';
import 'package:watch2whoop/src/sync_params.dart';

void main() {
  test('the first sync reads 14 days; existing installs keep their range', () {
    expect(kFirstSyncDays, 14);
    expect(kBackfillDays, 90);
    expect(const AppConfig().syncDaysBack, kFirstSyncDays);
    expect(AppConfig.fromJson(const {'syncDaysBack': 90}).syncDaysBack, 90);
  });

  test('the page protocol (§10): feature true, the link, onApp always with kind backfill', () {
    expect(kBackfillFeatureJs, contains('{backfill:true}'));
    expect(isBackfillLink(Uri.parse('watch2whoop://backfill?days=90')), isTrue);
    expect(isBackfillLink(Uri.parse('watch2whoop://backfill?days=all')), isTrue);
    expect(isBackfillLink(Uri.parse('watch2whoop://sync-connect')), isFalse);
    expect(isBackfillLink(Uri.parse('https://backfill/')), isFalse);
    expect(backfillOnApp('uploading', percent: 140), {'kind': 'backfill', 'phase': 'uploading', 'percent': 100, 'error': null});
    expect(backfillOnApp('error', error: 'offline')['kind'], 'backfill');
    // the page side, when it is in this checkout
    final page = File('../../body_engine/src/body_engine/web/static/sync-status.js');
    if (page.existsSync() && page.readAsStringSync().contains('watch2whoop://backfill')) {
      final js = page.readAsStringSync();
      expect(js, contains('backfill'));
      expect(js, contains('kind'));
    }
  });

  test('a larger range reads the history again; a smaller one does not', () {
    expect(rangeGrows(14, 90), isTrue);
    expect(rangeGrows(14, kAllHistory), isTrue);
    expect(rangeGrows(90, kAllHistory), isTrue);
    expect(rangeGrows(90, 14), isFalse);
    expect(rangeGrows(kAllHistory, 90), isFalse);
    expect(rangeGrows(90, 90), isFalse);
    expect(rangeGrows(null, 90), isFalse);
  });

  test('the re-sync state carries the engine part and where it came from; the percent counts all parts', () {
    final s = ResyncState(days: 90, startedAt: DateTime(2026, 10, 26), engineDone: false, fromPage: true);
    final back = ResyncState.fromJson(jsonDecode(jsonEncode(s.toJson())));
    expect([back!.engineDone, back.fromPage, back.days], [false, true, 90]);
    expect(ResyncState.fromJson({'started_at': '2026-10-26T00:00:00'})!.engineDone, isTrue, reason: 'older state: no engine part');
    expect(phonePercent(s, 40, enginePercent: 70), ((40 + 0 + 70) / 3).round());
    expect(phonePercent(s.copyWith(owDone: true, rawDone: true, engineDone: true), 0), 100);
    expect(phonePercent(const ResyncStateFixture().value, 40), 20, reason: 'without the engine as before');
  });

  test('native: BGTask id in Info.plist, registered at launch, background session routed, file in the project', () {
    final plist = File('ios/Runner/Info.plist').readAsStringSync();
    expect(plist, contains('<string>watch2whoop.sync.backfill</string>'));
    expect(plist, contains('<string>processing</string>'));
    final app = File('ios/Runner/AppDelegate.swift').readAsStringSync();
    expect(app, contains('Backfill.register()'));
    expect(app, contains('identifier == Backfill.sessionId'));
    final swift = File('ios/Runner/Backfill.swift').readAsStringSync();
    expect(swift, contains('static let taskId = "watch2whoop.sync.backfill"'));
    expect(swift, contains('URLSessionConfiguration.background(withIdentifier:'));
    expect(swift, contains('uploadTask(with: req, fromFile:'));
    expect(swift, contains('kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly'));
    expect(swift, contains('"phase": "backfill"'));
    expect(File('ios/Runner.xcodeproj/project.pbxproj').readAsStringSync(), contains('Backfill.swift in Sources'));
  });
}

class ResyncStateFixture {
  const ResyncStateFixture();
  ResyncState get value => ResyncState(days: 7, startedAt: DateTime(2026, 10, 26));
}
