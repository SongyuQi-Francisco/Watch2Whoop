// The app's own sync engine (#219): the registry, when it runs at all, what Dart hands to the native engine
// (ios/Runner/SyncEngine.swift, A2: the round itself is native), the mode, and the sync center's view of it (A4).
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/hk_types.dart';
import 'package:watch2whoop/src/resync.dart' show TypeVerdict;
import 'package:watch2whoop/src/sync_engine.dart';

const _sleep = 'HKCategoryTypeIdentifierSleepAnalysis';
const _workout = 'HKWorkoutTypeIdentifier';
const _hrv = 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN';
const _hr = 'HKQuantityTypeIdentifierHeartRate';
const _steps = 'HKQuantityTypeIdentifierStepCount';
const _energy = 'HKQuantityTypeIdentifierActiveEnergyBurned';
const _beats = 'HKDataTypeIdentifierHeartbeatSeries';

class FakeNative implements EngineNative {
  final List<String> calls = [];
  final List<Map<String, Object?>> args = [];
  Map<Object?, Object?>? runAnswer = {'batches': 2, 'samples': 40, 'stop': null, 'uploaded': [_hr, _sleep], 'mode': 'shadow'};
  Map<Object?, Object?>? statusAnswer;

  @override
  Future<Map<Object?, Object?>?> configure(Map<String, Object?> a) async {
    calls.add('configure');
    args.add(a);
    return {'mode': 'shadow', 'active': true};
  }

  @override
  Future<Map<Object?, Object?>?> run(Map<String, Object?> a) async {
    calls.add('run');
    args.add(a);
    return runAnswer;
  }

  @override
  Future<Map<Object?, Object?>?> status() async => statusAnswer;
  @override
  Future<void> setCellular(bool on) async => calls.add('cellular:$on');
  @override
  Future<Map<Object?, Object?>?> rangeStart(int days) async {
    calls.add('range:$days');
    return {'id': 'r', 'percent': 0};
  }

  @override
  Future<void> rangeCancel() async => calls.add('rangeCancel');
  @override
  Future<void> rearm() async => calls.add('rearm');
  @override
  Future<void> reset() async => calls.add('reset');
}

final _registry = [
  const EngineType(hk: _sleep, key: 'sleep_analysis', kind: 'category', bg: 'immediate', order: 1, status: 'sleep'),
  const EngineType(hk: _workout, key: 'workout', kind: 'workout', unit: 'min', bg: 'immediate', order: 2, status: 'workouts'),
  const EngineType(hk: _hrv, key: 'heart_rate_variability_sdnn', kind: 'quantity', unit: 'ms', bg: 'immediate', order: 3, status: 'hrv'),
  const EngineType(hk: _beats, key: 'heartbeat_series', kind: 'heartbeat'),
  const EngineType(hk: _steps, key: 'step_count', kind: 'quantity', unit: 'count', upload: 'raw+stats', bg: 'hourly'),
  const EngineType(hk: _energy, key: 'active_energy_burned', kind: 'quantity', unit: 'kcal', upload: 'stats', bg: 'hourly'),
  const EngineType(hk: _hr, key: 'heart_rate', kind: 'quantity', unit: 'count/min', bg: 'hourly', order: 99),
];

Map<String, Object?> _status(String mode, {bool enabled = true, List<String> accepts = const ['phase', 'samples']}) => {
      'engine': {'enabled': enabled, 'mode': mode, 'accepts': accepts, 'types': [_sleep, _hr, _steps, _energy, _workout]},
    };

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    SyncEngine.instance.resetForTest();
    SyncEngine.instance.registryForTest = _registry;
    SyncEngine.instance.onModeChange = null;
  });

  test('registry: the shipped JSON is the server table; the engine reads quantities, categories and workouts', () {
    final reg = parseRegistry(File('assets/healthkit_types.json').readAsStringSync());
    expect(reg.first.hk, _sleep);
    expect(reg.last.hk, _hr, reason: 'heart rate (the largest) last');
    final byHk = {for (final t in reg) t.hk: t};
    // the direct-upload types of 1.0.7 (hk_types.dart) are in the engine's table with the same unit
    for (final t in kHkTypes) {
      expect(byHk[t.hk]?.unit, t.unit, reason: t.hk);
    }
    expect(reg.where((t) => t.engine).map((t) => t.kind).toSet(), {'quantity', 'category', 'workout'});
    expect(byHk[_workout]!.engine, isTrue, reason: 'workouts (A3)');
    expect(byHk['HKQuantityTypeIdentifierBasalEnergyBurned']!.stats, isTrue, reason: 'statistics buckets (A3, D1)');
    expect(byHk['HKQuantityTypeIdentifierBasalEnergyBurned']!.raw, isFalse);
    expect(byHk[_beats]!.engine, isFalse, reason: 'RawSync');
    expect(byHk['HKDataTypeIdentifierElectrocardiogram']!.engine, isFalse, reason: 'RawSync');
    // background delivery: what wakes the app (design §3.1.3)
    expect(byHk[_sleep]!.bg, 'immediate');
    expect(byHk[_hr]!.bg, 'hourly');
    expect(byHk[_hr]!.page, [2000, 200]);
    // the server compares the same file with rawdata/hk_types.engine_json() (tests/test_apple_sync_engine.py)
    final server = File('../../body_engine/src/body_engine/rawdata/hk_types.py');
    if (server.existsSync()) {
      final hks = RegExp(r'_q\("(\w+)"').allMatches(server.readAsStringSync()).map((m) => 'HKQuantityTypeIdentifier${m[1]}');
      for (final hk in hks) {
        expect(byHk.containsKey(hk), isTrue, reason: hk);
      }
    }
    expect(File('pubspec.yaml').readAsStringSync(), contains('- assets/healthkit_types.json'));
  });

  test('BGTask identifiers of the engine are permitted, next to Open Wearables\' and the backfill\'s', () {
    final plist = File('ios/Runner/Info.plist').readAsStringSync();
    for (final id in ['watch2whoop.sync.refresh', 'watch2whoop.sync.process', 'watch2whoop.sync.backfill',
      'com.openwearables.healthsdk.task.refresh']) {
      expect(plist, contains('<string>$id</string>'), reason: id);
    }
    expect(File('ios/Runner/Runner.entitlements').readAsStringSync(), contains('com.apple.developer.healthkit.background-delivery'));
  });

  test('off unless the server says: flag off, mode ow, an older server or no answer', () async {
    expect(EngineInfo.fromStatus(null), isNull);
    expect(EngineInfo.fromStatus({'heartbeats': 1}), isNull, reason: 'older server');
    expect(EngineInfo.fromStatus(_status('ow', enabled: false, accepts: ['heartbeats']))!.active, isFalse);
    expect(EngineInfo.fromStatus(_status('ow', accepts: ['phase']))!.active, isFalse);
    expect(EngineInfo.fromStatus(_status('shadow'))!.active, isTrue);
    expect(EngineInfo.fromStatus(_status('app'))!.active, isTrue);

    final n = FakeNative();
    SyncEngine.instance.native = n;
    expect(await SyncEngine.instance.run(serverUrl: 'https://x.example', token: 't', serverStatus: null, daysBack: 14), isNull);
    expect(n.calls, isEmpty);
    // the flag went off: the native side learns it (its observers stop), no round
    expect(
        await SyncEngine.instance.run(
            serverUrl: 'https://x.example', token: 't', serverStatus: _status('ow', enabled: false), daysBack: 14),
        isNull);
    expect(n.calls, ['configure']);
    expect((n.args.single['engine'] as Map)['enabled'], isFalse);
  });

  test('a foreground round: what Dart hands over, the result, the mode', () async {
    final n = FakeNative();
    SyncEngine.instance.native = n;
    final modes = <String>[];
    SyncEngine.instance.onModeChange = (from, to) => modes.add('$from>$to');
    final r = await SyncEngine.instance.run(
        serverUrl: 'https://x.example', token: 'tok', serverStatus: _status('shadow'), daysBack: 14, appVersion: '1.0.9+9');
    expect(n.calls, ['run']);
    final a = n.args.single;
    expect([a['server'], a['token'], a['daysBack'], a['appVersion'], a['trigger']], ['https://x.example', 'tok', 14, '1.0.9+9', 'foreground']);
    expect((a['engine'] as Map)['mode'], 'shadow');
    final reg = [for (final t in a['registry'] as List) (t as Map)['hk']];
    expect(reg, [_sleep, _workout, _hrv, _steps, _energy, _hr], reason: 'engine kinds only, in the registry order');
    expect(((a['registry'] as List)[4] as Map)['upload'], 'stats');
    expect(r!.samples, 40);
    expect(r.uploaded, {_hr, _sleep});
    expect(SyncEngine.instance.uploadedAt.keys, containsAll([_hr, _sleep]));
    expect(modes, ['ow>shadow']);

    // the server switched this user to `app` (an answer of a round): Open Wearables stops (home page), Dart knows
    n.runAnswer = {'batches': 1, 'samples': 3, 'uploaded': [_hr], 'mode': 'app'};
    await SyncEngine.instance.run(serverUrl: 'https://x.example', token: 'tok', serverStatus: _status('shadow'), daysBack: 14);
    expect(modes, ['ow>shadow', 'shadow>app']);
    expect(SyncEngine.instance.appMode, isTrue);
    expect((await SharedPreferences.getInstance()).getString('w2w.sync.mode.v1'), 'app', reason: 'kept across restarts');

    // busy / backoff: not a round of its own
    n.runAnswer = {'stop': 'busy'};
    expect(await SyncEngine.instance.run(serverUrl: 'https://x.example', token: 'tok', serverStatus: _status('app'), daysBack: 14),
        isNull);
  });

  test('round result and the log line carry counts only', () {
    final r = EngineResult.fromMap({
      'batches': 3, 'samples': 120, 'bytes': 9000, 'types': 12, 'stop': 'locked', 'retryAfter': 30.2,
      'skipped': {_sleep: 'not_asked', _hrv: 'wifi_only'}, 'uploaded': [_hr],
    });
    expect([r.stop, r.retryAfter, r.skipped[_sleep], r.uploaded], ['locked', const Duration(seconds: 31), 'not_asked', {_hr}]);
    final line = engineLogLine(r);
    expect(line, contains('120 items of 1/12 types in 3 batches'));
    expect(line, contains('stopped: locked'));
    expect(engineStopText('locked'), isNotNull);
    expect(engineStopText(null), isNull);
    expect(engineStopText('budget'), isNull);
  });

  test('native status: first-sync progress, per type, range, log for display', () {
    final s = EngineStatus.fromMap({
      'mode': 'app', 'enabled': true, 'active': true, 'cellularHistory': false, 'running': false,
      'firstSync': {'types': 8, 'done': 6, 'percent': 75},
      'types': {
        _hr: {'backfilled': true, 'okAt': 1761955200.0},
        _sleep: {'backfilled': false, 'error': 'not_asked', 'errorAt': 1761955100.0},
      },
      'range': {'percent': 40, 'batches': 3},
      'lastRun': {'trigger': 'observer', 'result': 'ok', 'started': 1761955200.0},
      'lastBackgroundAt': 1761955300.0, 'lastStop': '',
      'log': ['2026-10-29T01:02:03Z\tobserver ok: 12 items, 2 types, 1 batches'],
    });
    expect([s.mode, s.active, s.firstPercent, s.firstSyncDone, s.lastStop], ['app', true, 75, false, null]);
    expect(s.types[_hr]!.firstDone, isTrue);
    expect(s.types[_sleep]!.error, 'not_asked');
    expect(s.range!['percent'], 40);
    expect(s.lastBackgroundAt, isNotNull);
    final shown = engineLogForDisplay(s.log).single;
    expect(shown, endsWith('observer ok: 12 items, 2 types, 1 batches'));
    expect(shown, isNot(contains('\t')));
  });

  test('sync center (A4): server status per type, not recorded / not synced by time', () async {
    final server = EngineServerStatus.fromJson({
      'mode': 'shadow',
      'types': [
        {'type': _hr, 'latest_sample_at': '2026-11-01T18:58:00+11:00'},
        {'type': _sleep, 'latest_sample_at': '2026-10-30T07:00:00+11:00'},
        {'type': _steps, 'latest_sample_at': null},
      ],
      'failures': {'recovered': false, 'last': {'code': 'contract', 'reason': 'format'}},
    });
    expect(server.latest.keys, {_hr, _sleep});
    expect(server.openFailure, 'contract');
    expect(EngineServerStatus.fromJson({'failures': {'recovered': true}}).openFailure, isNull);

    final t = DateTime.parse('2026-11-01T18:58:00+11:00');
    expect(engineVerdict(null, t), TypeVerdict.notCollected);
    expect(engineVerdict(t, null), TypeVerdict.notSynced);
    expect(engineVerdict(t.add(const Duration(minutes: 10)), t), TypeVerdict.synced, reason: 'within the slack');
    expect(engineVerdict(t.add(const Duration(hours: 2)), t), TypeVerdict.notSynced);
    expect(engineVerdict(t, t.add(const Duration(minutes: 15))), TypeVerdict.synced, reason: 'newest bucket after the sample');

    final phone = {_hr: t, _sleep: DateTime.parse('2026-11-01T07:00:00+11:00'), _steps: t};
    final rows = engineRows(_registry, const {}, phone, server.latest);
    expect([for (final r in rows) r.type.hk].take(2), [_sleep, _steps], reason: 'not synced first');
    expect(rows.firstWhere((r) => r.type.hk == _hr).verdict, TypeVerdict.synced);
    expect(rows.firstWhere((r) => r.type.hk == _energy).verdict, TypeVerdict.notCollected);
    expect(rows.any((r) => r.type.hk == _beats), isFalse, reason: 'RawSync types are not the engine\'s');
    expect(engineRows(_registry, {_hr}, phone, server.latest).map((r) => r.type.hk), [_hr]);
    expect(engineTypeLabel(_registry.last), 'Heart rate');

    final seen = <Uri>[];
    final got = await fetchEngineServerStatus('https://x.example/app/', 'tok', client: MockClient((req) async {
      seen.add(req.url);
      expect(req.headers['Authorization'], 'Bearer tok');
      return http.Response(jsonEncode({'mode': 'app', 'types': []}), 200);
    }));
    expect(seen.single.path, '/v1/ingest/sync-status');
    expect(got!.mode, 'app');
  });

  test('a new user: the engine block first, `app` = no Open Wearables; range re-sync, cellular, rearm, reset', () async {
    final n = FakeNative();
    SyncEngine.instance.native = n;
    final info = await fetchEngineInfo('https://x.example', 'tok', client: MockClient((req) async {
      expect(req.url.path, '/v1/ingest/healthkit');
      return http.Response(jsonEncode(_status('app')), 200);
    }));
    expect([info!.active, info.mode], [true, 'app']);
    expect(await fetchEngineInfo('https://x.example', 'tok', client: MockClient((_) async => http.Response('', 401))), isNull);

    expect(await SyncEngine.instance.startRange(30), isTrue);
    await SyncEngine.instance.setCellularHistory(true);
    await SyncEngine.instance.rearm();
    await SyncEngine.instance.clear();
    expect(n.calls, ['range:30', 'cellular:true', 'rearm', 'reset']);
    expect(SyncEngine.instance.appMode, isFalse);
  });

  test('one permission sheet lists every type the app reads, the two Open Wearables-only ones too', () async {
    final all = await SyncEngine.instance.authTypes();
    expect(all, containsAll([_sleep, _workout, _hr, _beats, ...kOwOnlyHealthTypes]));
  });
}
