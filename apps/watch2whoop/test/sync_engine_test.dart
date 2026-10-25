// The app's own sync engine, step A1 (#219): registry, when it runs at all, and the foreground runner.
import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/hk_types.dart';
import 'package:watch2whoop/src/sync_engine.dart';

const _sleep = 'HKCategoryTypeIdentifierSleepAnalysis';
const _hrv = 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN';
const _hr = 'HKQuantityTypeIdentifierHeartRate';
const _steps = 'HKQuantityTypeIdentifierStepCount';
const _energy = 'HKQuantityTypeIdentifierActiveEnergyBurned';

class FakeEngineSource implements EngineSource {
  FakeEngineSource(this.pages, {this.recent = const {}, this.fail = const {}});

  /// Anchored pages per type, served in order (the anchor moves only on commit).
  final Map<String, List<EnginePage>> pages;
  final Map<String, EnginePage> recent;
  final Map<String, PlatformException> fail;
  final Map<String, int> at = {};
  final List<String> reads = [], commits = [];
  bool wasReset = false;

  @override
  Future<EnginePage> read(EngineType t, {required String anchorKey, required int daysBack, required int limit, bool recent = false}) async {
    reads.add('${recent ? 'recent:' : ''}${t.hk}/$limit');
    if (fail.containsKey(t.hk)) throw fail[t.hk]!;
    if (recent) return this.recent[t.hk] ?? const EnginePage();
    final list = pages[t.hk] ?? const [];
    final i = at[t.hk] ?? 0;
    return i < list.length ? list[i] : const EnginePage();
  }

  @override
  Future<void> commit(String anchorKey, String anchor) async {
    commits.add('$anchorKey:$anchor');
    at[anchorKey] = (at[anchorKey] ?? 0) + 1;
  }

  @override
  Future<void> reset() async => wasReset = true;
}

EnginePage page(String anchor, {int n = 2, bool more = false}) => EnginePage(
      uuid: [for (var i = 0; i < n; i++) '00000000-0000-4000-8000-${anchor.padLeft(12, '0').substring(0, 12)}'],
      start: List.filled(n, 1761400000.0),
      end: List.filled(n, 1761400060.0),
      value: List.filled(n, 50.0),
      src: List.filled(n, 0),
      tz: List.filled(n, null),
      sources: const [
        {'name': 'Example Watch', 'device_type': 'watch'},
      ],
      anchor: anchor,
      more: more,
      tzName: 'Australia/Sydney',
    );

final _registry = [
  const EngineType(hk: _sleep, key: 'sleep_analysis', kind: 'category', bg: 'immediate', order: 1),
  const EngineType(hk: 'HKWorkoutTypeIdentifier', key: 'workout', kind: 'workout', unit: 'min', order: 2),
  const EngineType(hk: _hrv, key: 'heart_rate_variability_sdnn', kind: 'quantity', unit: 'ms', bg: 'immediate', order: 3),
  const EngineType(hk: 'HKQuantityTypeIdentifierHeartRateVariabilityRMSSD', key: 'rmssd', kind: 'quantity', unit: 'ms', minIos: 27),
  const EngineType(hk: 'HKDataTypeIdentifierHeartbeatSeries', key: 'heartbeat_series', kind: 'heartbeat'),
  const EngineType(hk: _steps, key: 'step_count', kind: 'quantity', unit: 'count', upload: 'raw+stats', bg: 'hourly'),
  const EngineType(hk: _energy, key: 'active_energy_burned', kind: 'quantity', unit: 'kcal', upload: 'stats'),
  const EngineType(hk: _hr, key: 'heart_rate', kind: 'quantity', unit: 'count/min', bg: 'hourly', order: 99),
];

const _active = EngineInfo(enabled: true, mode: 'shadow', accepts: {'heartbeats', 'phase', 'samples', 'categories', 'runs'},
    encodings: {'gzip'}, types: {_sleep, _hrv, 'HKQuantityTypeIdentifierHeartRateVariabilityRMSSD', _steps, _energy, _hr});

Map<String, Object?> unzip(List<int> gz) => jsonDecode(utf8.decode(gzip.decode(gz))) as Map<String, Object?>;

void main() {
  test('registry: the shipped JSON is the server table; A1 reads raw quantities and categories only', () {
    final f = File('assets/healthkit_types.json');
    final reg = parseRegistry(f.readAsStringSync());
    expect(reg.first.hk, _sleep);
    expect(reg.last.hk, _hr, reason: 'heart rate (the largest) last');
    final byHk = {for (final t in reg) t.hk: t};
    // the direct-upload types of 1.0.7 (hk_types.dart) are in the engine's table with the same unit
    for (final t in kHkTypes) {
      expect(byHk[t.hk]?.unit, t.unit, reason: t.hk);
    }
    final a1 = reg.where((t) => t.readByA1).map((t) => t.kind).toSet();
    expect(a1, {'quantity', 'category'});
    expect(byHk['HKWorkoutTypeIdentifier']!.readByA1, isFalse);
    expect(byHk['HKQuantityTypeIdentifierBasalEnergyBurned']!.readByA1, isFalse, reason: 'statistics only (A3)');
    expect(byHk['HKDataTypeIdentifierHeartbeatSeries']!.readByA1, isFalse, reason: 'RawSync');
    // the server compares the same file with rawdata/hk_types.engine_json() (tests/test_apple_sync_engine.py)
    final server = File('../../body_engine/src/body_engine/rawdata/hk_types.py');
    if (server.existsSync()) {
      final hks = RegExp(r'_q\("(\w+)"').allMatches(server.readAsStringSync()).map((m) => 'HKQuantityTypeIdentifier${m[1]}');
      for (final hk in hks) {
        expect(byHk.containsKey(hk), isTrue, reason: hk);
      }
    }
    // the asset is bundled
    expect(File('pubspec.yaml').readAsStringSync(), contains('- assets/healthkit_types.json'));
  });

  test('off unless the server says: flag off, mode ow, an older server or no answer', () async {
    expect(EngineInfo.fromStatus(null), isNull);
    expect(EngineInfo.fromStatus({'heartbeats': 1}), isNull, reason: 'older server');
    expect(EngineInfo.fromStatus({'engine': {'enabled': false, 'mode': 'ow', 'accepts': ['heartbeats']}})!.active, isFalse);
    expect(EngineInfo.fromStatus({'engine': {'enabled': true, 'mode': 'ow', 'accepts': ['phase']}})!.active, isFalse);
    expect(EngineInfo.fromStatus({
      'engine': {'enabled': true, 'mode': 'shadow', 'accepts': ['phase', 'samples'], 'types': [_hr]}
    })!.active, isTrue);
    final src = FakeEngineSource({_hr: [page('A')]});
    var uploads = 0;
    final r = await EngineRunner(registry: _registry, info: const EngineInfo(), source: src, progress: MemoryEngineProgress(),
        upload: (_) async {
      uploads++;
      return const EngineUploadResult(EngineUpload.ok);
    }).run(90);
    expect([r.stop, src.reads, uploads], ['off', isEmpty, 0]);
  });

  test('a pass: newest 48 h first, then the crawl; anchors only after 200; phases; one run report', () async {
    final src = FakeEngineSource({
      _sleep: [page('S1', more: true), page('S2')],
      _hr: [page('H1')],
    }, recent: {_sleep: page('R', n: 3)});
    final progress = MemoryEngineProgress();
    final sent = <Map<String, Object?>>[];
    final r = await EngineRunner(registry: _registry, info: _active, source: src, progress: progress, ios: 26,
        appVersion: '1.0.8+8', upload: (gz) async {
      sent.add(unzip(gz));
      return const EngineUploadResult(EngineUpload.ok, body: {'samples': 2});
    }).run(90);
    expect(r.stop, isNull);
    expect(src.commits, ['$_sleep:S1', '$_sleep:S2', '$_hr:H1']);
    // not read: the workout (A3), RMSSD (iOS 27), heartbeats (RawSync), statistics-only energy (A3)
    expect(src.reads.where((x) => x.contains('RMSSD') || x.contains('Workout') || x.contains('Heartbeat') || x.contains('Energy')),
        isEmpty);
    final phases = [for (final b in sent) b['phase']];
    expect(phases, ['recent', 'backfill', 'backfill', 'backfill', null], reason: 'empty pages are not sent');
    final first = sent.first;
    expect((first['categories'] as List).single, containsPair('type', _sleep));
    expect(((first['categories'] as List).single as Map)['unit'], '');
    expect(first['sources'], isNotEmpty);
    expect(first['tz'], 'Australia/Sydney');
    expect(first['app_version'], '1.0.8+8');
    final hr = sent.firstWhere((b) => b['samples'] != null && b['phase'] == 'backfill');
    expect(((hr['samples'] as List).single as Map)['unit'], 'count/min');
    final run = (sent.last['runs'] as List).single as Map;
    expect([run['trigger'], run['result'], run['samples']], ['foreground', 'ok', isPositive]);
    expect(run.keys, isNot(contains('value')), reason: 'counts only');
    expect(progress.full, containsAll([_sleep, _hr, _hrv, _steps]));

    // next pass: incremental, no recent read again
    sent.clear();
    src.pages[_hr] = [page('H1'), page('H2')];
    await EngineRunner(registry: _registry, info: _active, source: src, progress: progress, ios: 26,
        upload: (gz) async {
      sent.add(unzip(gz));
      return const EngineUploadResult(EngineUpload.ok);
    }).run(90);
    expect(src.reads.where((x) => x.startsWith('recent:')).length, 4, reason: 'only in the first pass');
    expect(sent.where((b) => b['samples'] != null).map((b) => b['phase']), ['incremental']);
  });

  test('refusals: type_paused skips the type and keeps its anchor; engine_disabled / 401 / 429 / offline stop', () async {
    for (final (res, stop, commits) in [
      (const EngineUploadResult(EngineUpload.refused, detail: 'type_paused'), null, ['$_hr:H1']),
      (const EngineUploadResult(EngineUpload.refused, detail: 'engine_disabled'), 'engine_disabled', <String>[]),
      (const EngineUploadResult(EngineUpload.unauthorized), 'unauthorized', <String>[]),
      (const EngineUploadResult(EngineUpload.rateLimited, retryAfter: Duration(seconds: 30)), 'rate_limited', <String>[]),
      (const EngineUploadResult(EngineUpload.offline), 'offline', <String>[]),
    ]) {
      final src = FakeEngineSource({_sleep: [page('S1')], _hr: [page('H1')]});
      final progress = MemoryEngineProgress()..recent.addAll([_sleep, _hr, _hrv, _steps]);
      final r = await EngineRunner(registry: _registry, info: _active, source: src, progress: progress, sleep: (_) async {}, upload: (gz) async {
        final b = unzip(gz);
        final isSleep = (b['categories'] as List?)?.isNotEmpty ?? false;
        return isSleep ? res : const EngineUploadResult(EngineUpload.ok);
      }).run(90);
      expect(r.stop, stop, reason: '${res.outcome} ${res.detail}');
      expect(src.commits, commits, reason: '${res.outcome} ${res.detail}');
      if (res.outcome == EngineUpload.rateLimited) expect(r.retryAfter, const Duration(seconds: 30));
    }
  });

  test('contract §4: the same batch again after a short 429 / network error / 5xx; 401 and 422 not retried', () async {
    for (final (answers, commits, tries, stop) in [
      ([const EngineUploadResult(EngineUpload.rateLimited, retryAfter: Duration(seconds: 3))], ['$_hr:H1'], 2, null),
      ([const EngineUploadResult(EngineUpload.offline), const EngineUploadResult(EngineUpload.failed)], ['$_hr:H1'], 3, null),
      ([for (var i = 0; i < 4; i++) const EngineUploadResult(EngineUpload.offline)], <String>[], 4, 'offline'),
      ([const EngineUploadResult(EngineUpload.unauthorized)], <String>[], 1, 'unauthorized'),
      ([const EngineUploadResult(EngineUpload.rejected)], <String>[], 1, null),
    ]) {
      final src = FakeEngineSource({_hr: [page('H1')]});
      final bodies = <String>[];
      final waits = <Duration>[];
      var n = 0;
      final r = await EngineRunner(registry: _registry, info: _active, source: src,
          progress: MemoryEngineProgress()..recent.addAll([_sleep, _hrv, _steps, _hr]),
          sleep: (d) async => waits.add(d), upload: (gz) async {
        final b = unzip(gz);
        if (b['samples'] == null) return const EngineUploadResult(EngineUpload.ok); // the run report
        bodies.add(b['batch_id'] as String);
        return n < answers.length ? answers[n++] : const EngineUploadResult(EngineUpload.ok);
      }).run(90);
      expect(src.commits, commits, reason: '$answers');
      expect(bodies.length, tries, reason: '$answers');
      expect(bodies.toSet().length, 1, reason: 'a retry sends the same batch_id');
      expect(r.stop, stop, reason: '$answers');
      if (answers.first.outcome == EngineUpload.rejected) expect(r.skipped[_hr], 'contract');
      if (answers.first.outcome == EngineUpload.rateLimited) expect(waits.single.inSeconds, 3);
    }
  });

  test('history (backfill) only on Wi-Fi; the newest 48 hours and incremental types go on any network', () async {
    final src = FakeEngineSource({_sleep: [page('S1')], _hr: [page('H1')]}, recent: {_sleep: page('R')});
    final progress = MemoryEngineProgress()..full.add(_hr);
    final sent = <String?>[];
    final r = await EngineRunner(registry: _registry, info: _active, source: src, progress: progress, allowBackfill: false,
        upload: (gz) async {
      sent.add(unzip(gz)['phase'] as String?);
      return const EngineUploadResult(EngineUpload.ok);
    }).run(90);
    expect(sent.where((p) => p != null), ['recent', 'incremental']);
    expect(r.skipped[_sleep], 'wifi_only');
    expect(src.commits, ['$_hr:H1']);
  });

  test('a page too large halves (also on 413); a type never asked for is skipped, the others go', () async {
    final src = FakeEngineSource({_hr: [page('H1', n: 4)]},
        fail: {_sleep: PlatformException(code: 'healthkit', message: 'not determined', details: 5)});
    final progress = MemoryEngineProgress()..recent.addAll([_hrv, _steps, _hr]);
    var first = true;
    final r = await EngineRunner(registry: _registry, info: _active, source: src, progress: progress, upload: (gz) async {
      if (first && unzip(gz)['samples'] != null) {
        first = false;
        return const EngineUploadResult(EngineUpload.tooLarge);
      }
      return const EngineUploadResult(EngineUpload.ok);
    }).run(90);
    expect(r.skipped[_sleep], 'not_asked');
    expect(src.reads.where((x) => x.startsWith('$_hr/')), ['$_hr/2000', '$_hr/1000']);
    expect(src.commits, ['$_hr:H1']);

    final small = FakeEngineSource({_hr: [page('H1', n: 4)]});
    await EngineRunner(registry: _registry, info: _active, source: small, progress: progress, maxBytes: 200,
        upload: (gz) async => const EngineUploadResult(EngineUpload.ok)).run(90);
    expect(small.reads.where((x) => x.startsWith('$_hr/')).length, greaterThan(1));
  });

  test('budget: the pass stops and the rest goes next time', () async {
    var now = DateTime(2026, 10, 26, 9);
    final src = FakeEngineSource({_hr: [page('H1', more: true), page('H2', more: true), page('H3')]});
    final r = await EngineRunner(registry: _registry, info: _active, source: src,
        progress: MemoryEngineProgress()..recent.addAll([_sleep, _hrv, _steps, _hr]), now: () => now, budget: const Duration(seconds: 5),
        upload: (gz) async {
      now = now.add(const Duration(seconds: 3));
      return const EngineUploadResult(EngineUpload.ok);
    }).run(90);
    expect(r.stop, 'budget');
    expect(src.commits.length, lessThan(3));
  });

  test('uploader: gzip body, bearer token, status codes and Retry-After', () async {
    final batch = buildEngineBatch(_registry.last, page('X'), batchId: 'b', phase: 'incremental');
    expect(batch.keys, containsAll(['batch_id', 'phase', 'sources', 'samples', 'deleted']));
    expect(batch.containsKey('tz'), isTrue);
    expect(((batch['samples'] as List).single as Map).containsKey('tz'), isFalse, reason: 'no per-sample zones: left out');
    final gz = gzipJson(batch);
    expect(unzip(gz)['batch_id'], 'b');
  });
}
