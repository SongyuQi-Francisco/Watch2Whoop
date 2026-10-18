import 'package:flutter_test/flutter_test.dart';
import 'package:open_wearables_health_sdk/health_data_type.dart';
import 'package:watch2whoop/src/health_sync.dart';
import 'package:watch2whoop/src/sync_stats.dart';

void main() {
  final t0 = DateTime(2026, 10, 17, 9);
  DateTime at(int min) => t0.add(Duration(minutes: min));

  test('parseBreakdown', () {
    expect(parseBreakdown('HeartRate: 1200, sleep: 40, workouts: 2'), {'HeartRate': 1200, 'sleep': 40, 'workouts': 2});
    expect(parseBreakdown(''), isEmpty);
    expect(parseBreakdown('bad, X: y, Z: 3'), {'Z': 3});
  });

  group('SyncLogParser (OW iOS SDK 0.15 logs)', () {
    test('counts only on upload success, accumulates per type', () {
      final s = SyncStats();
      final p = SyncLogParser(s);
      expect(p.handle('Sending 812 KB, 1240 items (HeartRate: 1200, sleep: 40)', at(0)), isFalse);
      expect(s.perType, isEmpty, reason: 'upload result not received yet');
      expect(p.handle('upload=sync req=A1 bytes=831000 sent=831000/831000 status=200', at(1)), isTrue);
      expect(s.perType, {'HeartRate': 1200, 'sleep': 40});
      expect(s.lastUploadAt, at(1));

      p.handle('Sending 10 KB, 3 items (HeartRate: 2, workouts: 1)', at(2));
      p.handle('upload=sync req=A2 bytes=10000 sent=10000/10000 status=202', at(3));
      expect(s.perType, {'HeartRate': 1202, 'sleep': 40, 'workouts': 1});
      expect(s.total, 1243);
      expect(s.sortedTypes.first.key, 'HeartRate');
    });

    test('upload failure: not counted, reason recorded; a later success does not count the failed batch', () {
      final s = SyncStats();
      final p = SyncLogParser(s);
      p.handle('Sending 812 KB, 100 items (HeartRate: 100)', at(0));
      p.handle('upload=sync req=B1 bytes=1 sent=0/1 error=NSURLErrorDomain(-1001)', at(1));
      expect(s.perType, isEmpty);
      expect(s.lastError, 'NSURLErrorDomain(-1001)');
      expect(s.lastErrorAt, at(1));

      p.handle('upload=outbox-retry req=B1 bytes=1 sent=1/1 status=200', at(2));
      expect(s.perType, isEmpty, reason: 'background retries do not know which batch, only the time is recorded');
      expect(s.lastUploadAt, at(2));

      p.handle('Sending 1 KB, 5 items (StepCount: 5)', at(3));
      p.handle('upload=sync req=B2 bytes=1 sent=1/1 status=500', at(4));
      expect(s.lastError, 'HTTP 500');
      expect(s.perType, isEmpty);
    });

    test('401 → needs reconnecting; cleared by a later successful upload', () {
      final s = SyncStats();
      final p = SyncLogParser(s);
      p.handle('upload=sync req=C1 bytes=1 sent=1/1 status=401', at(0));
      expect(s.authProblem, isTrue);
      p.handle('Token refresh rejected: HTTP 401', at(1));
      expect(s.authProblem, isTrue);
      p.handle('upload=sync-401-retry req=C1 bytes=1 sent=1/1 status=200', at(2));
      expect(s.authProblem, isFalse);
    });

    test('round finished, screen locked, offline, background time used up', () {
      final s = SyncStats();
      final p = SyncLogParser(s);
      p.handle('Sync complete: 1500 samples across 12 types', at(0));
      expect(s.lastCompleteAt, at(0));
      p.handle('HeartRate: protected data inaccessible - pausing sync', at(1));
      expect(s.lastNotice, SyncNotice.locked);
      p.handle('Network lost', at(2));
      expect(s.lastNotice, SyncNotice.offline);
      p.handle('Background time low (3s left) - pausing sync before next upload', at(3));
      expect(s.lastNotice, SyncNotice.backgroundTimeout);
      expect(s.lastNoticeAt, at(3));
      expect(p.handle('  HeartRate: 1000 samples (newest first)', at(4)), isFalse);
    });

    test('stats JSON round trip; bad data falls back to empty', () {
      final s = SyncStats(perType: {'HeartRate': 3}, lastUploadAt: at(0), lastError: 'HTTP 500', lastErrorAt: at(1))
        ..lastNotice = SyncNotice.locked
        ..lastNoticeAt = at(2)
        ..authProblem = true;
      final r = SyncStats.decode(s.encode());
      expect(r.toJson(), s.toJson());
      expect(SyncStats.decode('{oops').perType, isEmpty);
      expect(SyncStats.decode(null).total, 0);
    });
  });

  test('SdkSyncStatus.fromMap (result of getSyncStatus)', () {
    final s = SdkSyncStatus.fromMap({
      'hasResumableSession': true,
      'sentCount': 4200,
      'completedTypes': 3,
      'isFullExport': true,
      'initialExportDone': false,
      'isSyncing': true,
      'createdAt': '2026-10-17T08:00:00Z',
    });
    expect([s.hasResumableSession, s.sentCount, s.completedTypes, s.isFullExport, s.initialExportDone, s.isSyncing],
        [true, 4200, 3, true, false, true]);
    expect(s.createdAt, DateTime.utc(2026, 10, 17, 8));
    final empty = SdkSyncStatus.fromMap({'createdAt': null});
    expect([empty.sentCount, empty.isSyncing, empty.createdAt], [0, false, null]);
  });

  group('parseRedeemResponse (OW invitation code redeem)', () {
    test('success', () {
      final c = parseRedeemResponse(200, '{"access_token":"a","refresh_token":"r","user_id":"u"}');
      expect([c.userId, c.accessToken, c.refreshToken], ['u', 'a', 'r']);
    });
    test('invalid code / server error / incomplete response', () {
      expect(() => parseRedeemResponse(404, '{"detail":"Invalid or expired invitation code"}'),
          throwsA(isA<ConnectException>().having((e) => e.code, 'code', 'invalid_code')));
      expect(() => parseRedeemResponse(502, 'Bad gateway'),
          throwsA(isA<ConnectException>().having((e) => e.code, 'code', 'server')));
      expect(() => parseRedeemResponse(200, '{"access_token":"a"}'),
          throwsA(isA<ConnectException>().having((e) => e.code, 'code', 'bad_response')));
      expect(() => parseRedeemResponse(200, 'not json'),
          throwsA(isA<ConnectException>().having((e) => e.code, 'code', 'bad_response')));
    });
  });

  test('requested health types: include core types, exclude reproductive health and nutrition', () {
    final t = requestedTypes;
    for (final must in [
      HealthDataType.heartRate,
      HealthDataType.heartRateVariabilitySDNN,
      HealthDataType.restingHeartRate,
      HealthDataType.sleep,
      HealthDataType.workout,
      HealthDataType.steps,
      HealthDataType.activeEnergy,
      HealthDataType.oxygenSaturation,
      HealthDataType.respiratoryRate,
      HealthDataType.bodyMass,
      HealthDataType.bodyFatPercentage,
      HealthDataType.vo2Max,
    ]) {
      expect(t, contains(must));
    }
    expect(t, isNot(contains(HealthDataType.menstrualFlow)));
    expect(t, isNot(contains(HealthDataType.dietaryProtein)));
    expect(t, isNot(contains(HealthDataType.bloodOxygen)), reason: 'aliases are not requested twice');
  });
}
