// Sync center: the registry of the types the app reads itself (hk_types.dart) matches the server's, RawSync uploads them
// per type with their own anchors, the forced re-sync uses its own anchor set, and the phone / server verdict per type.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/data_status.dart';
import 'package:watch2whoop/src/hk_types.dart';
import 'package:watch2whoop/src/raw_sync.dart';
import 'package:watch2whoop/src/resync.dart';

/// A minimal native reader: kind -> pages.
class Pages implements RawSource {
  Pages(this.pages);
  final Map<String, List<RawPage>> pages;
  final reads = <String>[], commits = <String>[];
  final _pos = <String, int>{};

  @override
  Future<String> authStatus() async => 'unnecessary';
  @override
  Future<RawAuth> requestAuth() async => const RawAuth(asked: true);
  @override
  Future<RawPage> read(String kind, int daysBack, int limit) async {
    reads.add(kind);
    final i = _pos[kind] ?? 0, list = pages[kind] ?? const [];
    return i < list.length ? list[i] : const RawPage();
  }

  @override
  Future<void> commit(String kind, String anchor) async {
    commits.add('$kind:$anchor');
    _pos[kind] = (_pos[kind] ?? 0) + 1;
  }

  @override
  Future<void> reset({String? anchorSet}) async {}
}

const stand = 'HKQuantityTypeIdentifierAppleStandTime';
const temp = 'HKQuantityTypeIdentifierAppleSleepingWristTemperature';

RawPage standPage(String anchor) => RawPage(items: [
      {'uuid': '3f2504e0-4f89-11d3-9a0c-0305e82c3301', 'start': '2026-10-22T01:00:00.000Z', 'end': '2026-10-22T01:05:00.000Z',
        'value': 3.0, 'source': 'Apple Watch'},
    ], anchor: anchor);

Map<String, Object?> server({bool on = true, List<String> types = const [stand, temp]}) =>
    {'metrics_enabled': on, 'metric_types': on ? types : <String>[], 'water_enabled': false};

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('the registry lists the same rows as the server (rawdata/hk_types.py)', () {
    final py = File('../../body_engine/src/body_engine/rawdata/hk_types.py');
    if (!py.existsSync()) return; // the release zip has only this directory
    final rows = RegExp(r'HkType\(_Q \+ "(\w+)", "([^"]+)", "([^"]+)", "([^"]+)", [^,]+, [^,]+,\s*"([^"]+)"')
        .allMatches(py.readAsStringSync())
        .map((m) => ['HKQuantityTypeIdentifier${m[1]}', m[2], m[3], m[4], m[5]].join('|'))
        .toList();
    expect(rows, [for (final t in kHkTypes) [t.hk, t.metric, t.unit, t.agg, t.kind].join('|')]);
    expect(kPhoneTypes['stair_speed'], hasLength(2));
  });

  test('registry types: only what the server takes, one group with its unit, own anchors', () async {
    final src = Pages({stand: [standPage('S1')], temp: const []});
    final sent = <Map<String, Object?>>[];
    final r = await RawSyncRunner(source: src, status: () async => server(), upload: (b) async {
      sent.add(b);
      return RawUploadResult(RawUpload.ok, {'metrics': 1, 'metrics_enabled': true, 'status': server()});
    }).run(90);
    expect(r.stop, isNull);
    expect(r.metrics, 1);
    expect(src.commits, ['$stand:S1']);
    final g = (sent.single['quantities'] as List).single as Map;
    expect([g['type'], g['unit']], [stand, 'min']);
    expect(src.reads, isNot(contains('HKQuantityTypeIdentifierTimeInDaylight'))); // not in metric_types

    // paused on the server (or an older server): not read, nothing sent, anchors stay
    for (final st in <Map<String, Object?>?>[server(on: false), {'heartbeats': 1}, null]) {
      final off = Pages({stand: [standPage('S1')]});
      var uploads = 0;
      final r2 = await RawSyncRunner(source: off, status: () async => st, upload: (b) async {
        uploads++;
        return const RawUploadResult(RawUpload.ok, {});
      }).run(90);
      expect(r2.metricsOff, isTrue, reason: '$st');
      expect(off.reads.where((k) => k.startsWith('HK')), isEmpty);
      expect(uploads, 0);
    }

    // paused between the check and the upload: not stored, the anchor stays
    final flip = Pages({stand: [standPage('S1')]});
    await RawSyncRunner(source: flip, status: () async => server(),
        upload: (b) async => const RawUploadResult(RawUpload.ok, {'metrics_enabled': false})).run(90);
    expect(flip.commits, isEmpty);
  });

  test('the forced re-sync reads every kind with its own anchor set', () async {
    final src = Pages({'resync:$stand': [standPage('R1')]});
    await RawSyncRunner(source: src, status: () async => server(),
        upload: (b) async => RawUploadResult(RawUpload.ok, {'metrics': 1, 'metrics_enabled': true, 'status': server()}))
        .run(7, anchorSet: kResyncAnchorSet);
    expect(src.reads.first, 'resync:heartbeat');
    expect(src.commits, ['resync:$stand:R1']);
    expect(baseKind('resync:$stand'), stand);
  });

  test('phone / server verdict per type', () {
    final today = DateTime(2026, 10, 23, 9);
    expect(verdictOf(phone: null, serverDate: '2026-10-20'), TypeVerdict.notCollected);
    expect(verdictOf(phone: today, serverDate: '2026-10-21'), TypeVerdict.notSynced);
    expect(verdictOf(phone: today, serverAt: DateTime(2026, 10, 23, 7)), TypeVerdict.synced);
    expect(verdictOf(phone: today), TypeVerdict.notSynced);
    expect(verdictNote('vo2max', TypeVerdict.notCollected), isNotNull);
    expect(verdictNote('heart_rate', TypeVerdict.synced), isNull);
    final rows = compareTypes([
      const DataTypeStatus(key: 'stand_time', label: '站立时间', state: DataState.ok, lastDate: '2026-10-20'),
      const DataTypeStatus(key: 'mystery', label: 'x', state: DataState.never),
    ], {stand: today}, kPhoneTypes);
    expect(rows.map((r) => r.verdict), [TypeVerdict.notSynced, TypeVerdict.unknown]);
  });

  test('re-sync helpers', () {
    expect(parseCustomDays(' 14 '), 14);
    expect(parseCustomDays('0'), isNull);
    expect(parseCustomDays('4000'), isNull);
    expect(daysBackOf(null), 0);
    final s = ResyncState(days: 30, startedAt: DateTime.utc(2026, 10, 23));
    expect(phonePercent(s, 40), 20);
    expect(phonePercent(s.copyWith(owDone: true, rawDone: true), 0), 100);
    expect(ResyncState.fromJson(s.toJson())!.days, 30);
    expect(resyncRefusal({'reason': 'throttled', 'retry_after': 130}), contains(' 3 '));
  });
}
