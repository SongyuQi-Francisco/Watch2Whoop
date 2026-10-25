// First sync 14 days + history backfill (owner's decision 2026-10-26): page messages, ranges, status, native wiring.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/backfill.dart';
import 'package:watch2whoop/src/config.dart';
import 'package:watch2whoop/src/health_range.dart';
import 'package:watch2whoop/src/sync_params.dart';
import 'package:watch2whoop/src/sync_stats.dart';

void main() {
  test('the first sync reads 14 days; the backfill offers 90 days and all history', () {
    expect(kFirstSyncDays, 14);
    expect(kBackfillDays, 90);
    expect(const AppConfig().syncDaysBack, kFirstSyncDays);
    expect(AppConfig.fromJson(const {'syncDaysBack': 90}).syncDaysBack, 90, reason: 'existing installs keep their range');
  });

  test('page messages: start with days or "all", status; nothing else', () {
    expect(parseBackfillMessage('backfill:start {"id":"b1","days":90}'), (verb: 'start', id: 'b1', days: 90));
    expect(parseBackfillMessage('backfill:start {"id":"b1","days":"all"}')?.days, kAllHistory);
    expect(parseBackfillMessage('backfill:start {"id":"b1"}')?.days, kBackfillDays);
    expect(parseBackfillMessage('backfill:status {"id":"b2"}')?.verb, 'status');
    for (final bad in ['backfill:start {"id":"b1","days":-3}', 'backfill:start {"id":"b1","days":"x"}', 'backfill:stop {"id":"b"}',
        'backfill:start', 'backfill:status {}', 'sync:now {"id":"x"}']) {
      expect(parseBackfillMessage(bad), isNull, reason: bad);
    }
    expect(kBackfillJs, contains('window.Watch2WhoopBackfill={v:1'));
    expect(kBackfillJs, contains('{backfill:{v:1,days:[90],all:true}}'));
    expect(kBackfillJs, contains("'watch2whoop:backfill'"));
    final r = backfillReplyJs('b1', {'ok': true});
    expect(jsonDecode(r.substring(r.indexOf(',') + 1, r.lastIndexOf(')'))), {'ok': true});
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

  test('status: the slower of the two paths; done when both are', () {
    final owRunning = HistoryProgress.from(const SdkSyncStatus(isFullExport: true, completedTypes: 10, sentCount: 500), 40, DateTime(2026, 10, 26));
    final s = backfillStatus(target: 90, range: 90, ow: owRunning, engine: {'active': true, 'percent': 60, 'error': null});
    expect([s['active'], s['done'], s['percent'], s['channel']], [true, false, 25, 'both']);
    final done = HistoryProgress.from(const SdkSyncStatus(initialExportDone: true), 40, DateTime(2026, 10, 26));
    final d = backfillStatus(target: 90, range: 90, ow: done, engine: {'active': false, 'done': true, 'percent': 100});
    expect([d['active'], d['done'], d['percent']], [false, true, 100]);
    final e = backfillStatus(target: 0, range: 0, engine: {'active': false, 'error': 'auth'});
    expect([e['channel'], e['error'], e['percent']], ['engine', 'auth', null]);
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
