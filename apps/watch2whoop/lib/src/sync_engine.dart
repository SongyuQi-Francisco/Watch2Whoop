// The app's own HealthKit sync engine, step A1 (#219, docs/apple-sync-engine.md §3.1 and §6): foreground core.
// - Registry: assets/healthkit_types.json, the server's rawdata/hk_types.engine_json() byte for byte (both sides test it).
// - Off unless the server says so: it runs only while the last GET/POST /v1/ingest/healthkit answer of RawSync has
//   `engine.enabled` and `engine.mode` is shadow / app (flag apple_sync_engine, off by default) — no extra request, no
//   HealthKit read, nothing uploaded otherwise. Open Wearables stays the source of the scores in ow / shadow mode.
// - What A1 reads: the registry's raw quantities (samples[]) and categories (categories[], e.g. sleep stages), only the
//   types the server lists in `engine.types`, only the ones this iOS has (min_ios), only in the order of the registry.
//   Workouts and 15-minute statistics buckets (A3), background delivery and BGTasks (A2) come later; beat-to-beat series,
//   ECGs and water stay with RawSync (raw_sync.dart), which runs right before.
// - Per type: first the newest 48 hours (phase `recent`, no anchor), then the anchored crawl from the start of the range
//   (phase `backfill` until it has read everything once, `incremental` afterwards). The anchor is committed only after
//   the server answered 200 (an interrupted upload is read again; the server skips UUIDs it has).
// - Each request is gzip JSON ≤ [kEngineMaxBytes] compressed (the page halves otherwise, and on 413); 409 type_paused /
//   engine_disabled keep the anchor and skip the type / stop; 429 stops for Retry-After; 401 stops (RawSync then repairs
//   the token); a network error ends the pass as offline. The pass ends after [kEngineBudget] or [kEngineMaxBatches].
// - Each pass reports itself in `runs[]` (trigger, result, duration, counts; no health values) for the server's sync_runs.
// Deviations from the design, on purpose for A1: the upload is in Dart (testable, like RawSync); it moves to Swift with
// A2 when background rounds need it without Flutter. Anchors are the native side's own (`w2w.sync.anchor.<type>`);
// sign-out resets them. No new Health permission sheet: types never asked are skipped (HealthKit error 5) until the
// sheet for the engine's types is added together with the cut-over (blood pressure / glucose need the owner's OK, D6).
// Pure Dart runner with an injected source / uploader (test/sync_engine_test.dart); native reader ios/Runner/SyncEngine.swift.
import 'dart:convert';
import 'dart:io' show gzip, Platform;
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'raw_sync.dart' show newUuid;
import 'sync_bridge.dart' show DeviceSyncState;

const String kEngineRegistryAsset = 'assets/healthkit_types.json';

/// One request, gzip-compressed (the server takes 2 MB on the wire; the design aims at ≤ 1 MB).
const int kEngineMaxBytes = 1000000;
const int kEngineMaxBatches = 30;
const Duration kEngineBudget = Duration(seconds: 90);
const Duration kEngineUploadTimeout = Duration(seconds: 30); // contract §5: connect 10 s, whole request 30 s

/// Retries of the same batch (same batch_id) after a network error / 5xx, contract §4: 2, 4, 8 seconds (+ jitter).
const List<Duration> kEngineRetryDelays = [Duration(seconds: 2), Duration(seconds: 4), Duration(seconds: 8)];

/// A 429 with a Retry-After up to this is waited for within the pass (the same batch again); longer = next pass.
const Duration kEngineMaxRetryWait = Duration(seconds: 15);

/// After a 401 the engine waits this long before trying again (contract §4: no retry loop).
const Duration kEngineAuthBackoff = Duration(minutes: 15);

/// One row of the registry (rawdata/hk_types.EngineType).
@immutable
class EngineType {
  const EngineType({
    required this.hk,
    required this.key,
    required this.kind,
    this.unit = '',
    this.upload = 'raw',
    this.bg = 'none',
    this.order = 50,
    this.minIos = 0,
    this.pageForeground = 2000,
    this.flag,
    this.consent,
  });

  final String hk, key, kind, unit, upload, bg;
  final int order, minIos, pageForeground;
  final String? flag, consent;

  bool get raw => upload.contains('raw');

  /// A1 reads raw quantities and categories; workouts / statistics (A3) and heartbeat / ECG / water (RawSync) not here.
  bool get readByA1 => (kind == 'quantity' && raw) || kind == 'category';

  /// The request field this type goes in.
  String get field => kind == 'category' ? 'categories' : 'samples';

  factory EngineType.fromJson(Map<String, Object?> j) => EngineType(
        hk: j['hk'] as String,
        key: j['key'] as String,
        kind: j['kind'] as String,
        unit: (j['unit'] as String?) ?? '',
        upload: (j['upload'] as String?) ?? 'raw',
        bg: (j['bg'] as String?) ?? 'none',
        order: (j['order'] as num?)?.toInt() ?? 50,
        minIos: (j['min_ios'] as num?)?.toInt() ?? 0,
        pageForeground: ((j['page'] as List?)?.first as num?)?.toInt() ?? 2000,
        flag: j['flag'] as String?,
        consent: j['consent'] as String?,
      );
}

/// The registry in reading order (the JSON's order is the server's: sleep and the overnight readings first, heart rate last).
List<EngineType> parseRegistry(String json) => [
      for (final t in ((jsonDecode(json) as Map)['types'] as List).cast<Map>()) EngineType.fromJson(t.cast<String, Object?>()),
    ];

/// The server's `engine` block (GET /v1/ingest/healthkit, rawdata/engine.describe).
@immutable
class EngineInfo {
  const EngineInfo({this.enabled = false, this.mode = 'ow', this.accepts = const {}, this.encodings = const {}, this.types = const {}});
  final bool enabled;
  final String mode;
  final Set<String> accepts, encodings, types;

  /// The engine uploads at all: the server takes its fields and the user is in shadow / app mode.
  bool get active => enabled && (mode == 'shadow' || mode == 'app') && accepts.contains('phase');

  static EngineInfo? fromStatus(Map<String, Object?>? status) {
    final e = status?['engine'];
    if (e is! Map) return null;
    Set<String> set(Object? v) => {for (final x in (v as List?) ?? const []) '$x'};
    return EngineInfo(
      enabled: e['enabled'] == true,
      mode: '${e['mode'] ?? 'ow'}',
      accepts: set(e['accepts']),
      encodings: set(e['encodings']),
      types: set(e['types']),
    );
  }
}

/// One columnar page from the native reader.
class EnginePage {
  const EnginePage({
    this.uuid = const [],
    this.start = const [],
    this.end = const [],
    this.value = const [],
    this.src = const [],
    this.tz = const [],
    this.sources = const [],
    this.deleted = const [],
    this.anchor,
    this.more = false,
    this.unsupported = false,
    this.tzName,
  });

  final List<String> uuid;
  final List<double> start, end, value;
  final List<int> src;
  final List<String?> tz;
  final List<Map<String, Object?>> sources;
  final List<String> deleted;
  final String? anchor, tzName;
  final bool more, unsupported;

  bool get isEmpty => uuid.isEmpty && deleted.isEmpty;

  factory EnginePage.fromMap(Map<Object?, Object?> m) {
    List<T> l<T>(Object? v, T Function(Object?) f) => [for (final x in (v as List?) ?? const []) f(x)];
    return EnginePage(
      uuid: l(m['uuid'], (x) => '$x'),
      start: l(m['start'], (x) => (x as num).toDouble()),
      end: l(m['end'], (x) => (x as num).toDouble()),
      value: l(m['value'], (x) => (x as num).toDouble()),
      src: l(m['src'], (x) => (x as num).toInt()),
      tz: l(m['tz'], (x) => x as String?),
      sources: l(m['sources'], (x) => (x as Map).map((k, v) => MapEntry('$k', v))),
      deleted: l(m['deleted'], (x) => '$x'),
      anchor: m['anchor'] as String?,
      more: m['more'] == true,
      unsupported: m['unsupported'] == true,
      tzName: m['tzName'] as String?,
    );
  }
}

abstract class EngineSource {
  /// One page of [t]: the newest 48 hours ([recent]) or the next page after the anchor named [anchorKey].
  Future<EnginePage> read(EngineType t, {required String anchorKey, required int daysBack, required int limit, bool recent});
  Future<void> commit(String anchorKey, String anchor);
  Future<void> reset();
}

class NativeEngineSource implements EngineSource {
  static const _ch = MethodChannel('watch2whoop/native');

  @override
  Future<EnginePage> read(EngineType t, {required String anchorKey, required int daysBack, required int limit, bool recent = false}) async =>
      EnginePage.fromMap(await _ch.invokeMapMethod<Object?, Object?>('engineRead', {
            'type': t.hk,
            'kind': t.kind,
            'unit': t.unit,
            'anchor': anchorKey,
            'daysBack': daysBack,
            'limit': limit,
            'recent': recent,
          }) ??
          const {});

  @override
  Future<void> commit(String anchorKey, String anchor) => _ch.invokeMethod('engineCommit', {'anchor': anchorKey, 'value': anchor});

  @override
  Future<void> reset() => _ch.invokeMethod('engineReset');
}

/// The request body for one page (rawdata/api.py IngestIn, engine fields).
Map<String, Object?> buildEngineBatch(EngineType t, EnginePage p,
    {required String batchId, required String phase, String? appVersion, List<Map<String, Object?>> runs = const []}) {
  final group = {
    'type': t.hk,
    'unit': t.kind == 'category' ? '' : t.unit,
    'uuid': p.uuid,
    'start': p.start,
    'end': p.end,
    'value': p.value,
    'src': p.src,
    if (p.tz.any((z) => z != null)) 'tz': p.tz,
  };
  return {
    'batch_id': batchId,
    'device': 'iPhone',
    'app_version': ?appVersion,
    'phase': phase,
    'tz': ?p.tzName,
    'sources': p.sources,
    if (p.uuid.isNotEmpty) t.field: [group],
    'deleted': p.deleted,
    if (runs.isNotEmpty) 'runs': runs,
  };
}

List<int> gzipJson(Map<String, Object?> body) => gzip.encode(utf8.encode(jsonEncode(body)));

enum EngineUpload { ok, unauthorized, refused, rateLimited, tooLarge, rejected, failed, offline }

class EngineUploadResult {
  const EngineUploadResult(this.outcome, {this.body, this.detail, this.retryAfter});
  final EngineUpload outcome;
  final Map<String, Object?>? body;

  /// 409: engine_disabled / type_paused / consent_required; 422: why the batch was refused.
  final String? detail;
  final Duration? retryAfter;
}

/// POST {server}/v1/ingest/healthkit with gzip JSON.
class EngineUploader {
  EngineUploader(this.baseUrl, this.token, {this.client, this.appVersion});
  final String baseUrl, token;
  final String? appVersion;
  final http.Client? client;

  Uri get _uri {
    final b = Uri.parse(baseUrl);
    return Uri(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null, path: '/v1/ingest/healthkit');
  }

  Future<EngineUploadResult> upload(List<int> gz) async {
    final c = client ?? http.Client();
    try {
      final res = await c
          .post(_uri,
              headers: {
                'Authorization': 'Bearer $token',
                'Content-Type': 'application/json',
                'Content-Encoding': 'gzip',
                'Accept': 'application/json',
                'X-App-Version': ?appVersion,
              },
              body: gz)
          .timeout(kEngineUploadTimeout);
      Map<String, Object?>? body;
      try {
        final j = jsonDecode(res.body);
        if (j is Map) body = j.map((k, v) => MapEntry('$k', v));
      } catch (_) {}
      final detail = body?['detail'] is String ? body!['detail'] as String : null;
      final retry = int.tryParse(res.headers['retry-after'] ?? '');
      return EngineUploadResult(
        switch (res.statusCode) {
          200 => EngineUpload.ok,
          401 => EngineUpload.unauthorized,
          409 => EngineUpload.refused,
          413 => EngineUpload.tooLarge,
          422 || 400 => EngineUpload.rejected,
          429 => EngineUpload.rateLimited,
          _ => EngineUpload.failed,
        },
        body: body,
        detail: detail,
        retryAfter: retry == null ? null : Duration(seconds: retry),
      );
    } catch (_) {
      return const EngineUploadResult(EngineUpload.offline);
    } finally {
      if (client == null) c.close();
    }
  }
}

class EngineResult {
  int batches = 0, samples = 0, bytes = 0, types = 0;

  /// null = everything read; off (server: not active) / unauthorized / engine_disabled / rate_limited / offline / failed / budget
  String? stop;

  /// Types skipped this pass and why (type_paused, not_asked, healthkit, rejected …): the next pass tries again.
  final Map<String, String> skipped = {};

  /// The HealthKit identifiers with at least one accepted page this pass.
  final Set<String> uploaded = {};
  Duration? retryAfter;
  Map<String, Object?>? serverStatus;
}

/// What the runner keeps between passes (shared_preferences): which types finished their 48-hour read and their first
/// full crawl.
abstract class EngineProgress {
  Future<bool> recentDone(String hk);
  Future<void> setRecentDone(String hk);
  Future<bool> backfilled(String hk);
  Future<void> setBackfilled(String hk);
  Future<void> clear();
}

class PrefsEngineProgress implements EngineProgress {
  static const _recent = 'w2w.sync.recent.v1', _backfilled = 'w2w.sync.backfilled.v1';

  Future<bool> _has(String key, String hk) async => ((await SharedPreferences.getInstance()).getStringList(key) ?? const []).contains(hk);
  Future<void> _add(String key, String hk) async {
    final p = await SharedPreferences.getInstance();
    await p.setStringList(key, {...?p.getStringList(key), hk}.toList());
  }

  @override
  Future<bool> recentDone(String hk) => _has(_recent, hk);
  @override
  Future<void> setRecentDone(String hk) => _add(_recent, hk);
  @override
  Future<bool> backfilled(String hk) => _has(_backfilled, hk);
  @override
  Future<void> setBackfilled(String hk) => _add(_backfilled, hk);
  @override
  Future<void> clear() async {
    final p = await SharedPreferences.getInstance();
    await p.remove(_recent);
    await p.remove(_backfilled);
  }
}

class MemoryEngineProgress implements EngineProgress {
  final Set<String> recent = {}, full = {};
  @override
  Future<bool> recentDone(String hk) async => recent.contains(hk);
  @override
  Future<void> setRecentDone(String hk) async => recent.add(hk);
  @override
  Future<bool> backfilled(String hk) async => full.contains(hk);
  @override
  Future<void> setBackfilled(String hk) async => full.add(hk);
  @override
  Future<void> clear() async {
    recent.clear();
    full.clear();
  }
}

/// The iOS major version this runs on (min_ios of the registry); 0 = unknown (then nothing is skipped).
int iosMajor() {
  if (kIsWeb || !Platform.isIOS) return 0;
  return int.tryParse(RegExp(r'(\d+)').firstMatch(Platform.operatingSystemVersion)?.group(1) ?? '') ?? 0;
}

class EngineRunner {
  EngineRunner({
    required this.registry,
    required this.info,
    required this.source,
    required this.upload,
    required this.progress,
    this.appVersion,
    this.trigger = 'foreground',
    this.ios = 0,
    this.maxBatches = kEngineMaxBatches,
    this.maxBytes = kEngineMaxBytes,
    this.budget = kEngineBudget,
    this.allowBackfill = true,
    this.retryDelays = kEngineRetryDelays,
    Future<void> Function(Duration)? sleep,
    DateTime Function()? now,
    String Function()? batchId,
  })  : _now = now ?? DateTime.now,
        _sleep = sleep ?? Future<void>.delayed,
        _batchId = batchId ?? newUuid;

  /// Read history beyond the newest 48 hours (first crawl): Wi-Fi only by default (contract §5, design §3.1.8); types
  /// that finished their first crawl go on incrementally on any network.
  final bool allowBackfill;
  final List<Duration> retryDelays;
  final Future<void> Function(Duration) _sleep;

  final List<EngineType> registry;
  final EngineInfo info;
  final EngineSource source;
  final Future<EngineUploadResult> Function(List<int> gz) upload;
  final EngineProgress progress;
  final String? appVersion;
  final String trigger;
  final int ios, maxBatches, maxBytes;
  final Duration budget;
  final DateTime Function() _now;
  final String Function() _batchId;

  /// The types this pass reads: A1's kinds, accepted by the server now, known to this iOS.
  List<EngineType> get types => [
        for (final t in registry)
          if (t.readByA1 && info.types.contains(t.hk) && (ios == 0 || t.minIos == 0 || ios >= t.minIos)) t,
      ];

  Future<EngineResult> run(int daysBack) async {
    final out = EngineResult();
    if (!info.active) {
      out.stop = 'off';
      return out;
    }
    final t0 = _now();
    types:
    for (final t in types) {
      out.types++;
      var limit = t.pageForeground;
      var recent = !await progress.recentDone(t.hk);
      final backfilled = await progress.backfilled(t.hk);
      while (true) {
        if (out.batches >= maxBatches || _now().difference(t0) > budget) {
          out.stop = 'budget';
          return _report(out, t0);
        }
        final EnginePage page;
        try {
          page = await source.read(t, anchorKey: t.hk, daysBack: daysBack, limit: limit, recent: recent);
        } on PlatformException catch (e) {
          out.skipped[t.hk] = e.code == 'healthkit' && '${e.details}' == '5' ? 'not_asked' : e.code;
          continue types; // the anchor stays; the next type may be readable
        } catch (e) {
          out.skipped[t.hk] = 'read';
          continue types;
        }
        if (page.unsupported) continue types;
        final phase = recent ? 'recent' : (backfilled ? 'incremental' : 'backfill');
        if (phase == 'backfill' && !allowBackfill) {
          out.skipped[t.hk] = 'wifi_only'; // history waits for Wi-Fi; the newest 48 hours already went
          continue types;
        }
        if (page.isEmpty) {
          if (recent) {
            await progress.setRecentDone(t.hk);
            recent = false;
            continue;
          }
          if (page.anchor != null) await source.commit(t.hk, page.anchor!);
          if (!page.more) {
            if (!backfilled) await progress.setBackfilled(t.hk);
            continue types;
          }
          continue;
        }
        final gz = gzipJson(buildEngineBatch(t, page, batchId: _batchId(), phase: phase, appVersion: appVersion));
        if (gz.length > maxBytes && limit > 1) {
          limit = max(1, limit ~/ 2);
          continue;
        }
        var res = await upload(gz);
        out.batches++;
        out.bytes += gz.length;
        // the same batch (same batch_id) again: a 429 with a short Retry-After, a network error or 5xx with backoff
        for (var i = 0;
            i < retryDelays.length && _now().difference(t0) < budget &&
                (res.outcome == EngineUpload.offline || res.outcome == EngineUpload.failed ||
                    (res.outcome == EngineUpload.rateLimited && (res.retryAfter ?? kEngineMaxRetryWait * 2) <= kEngineMaxRetryWait));
            i++) {
          final wait = res.outcome == EngineUpload.rateLimited ? res.retryAfter! : retryDelays[i];
          await _sleep(wait + Duration(milliseconds: Random().nextInt(500)));
          res = await upload(gz);
        }
        switch (res.outcome) {
          case EngineUpload.ok:
            final b = res.body ?? const {};
            if (b['status'] is Map) out.serverStatus = (b['status'] as Map).map((k, v) => MapEntry('$k', v));
            if (recent) {
              await progress.setRecentDone(t.hk); // the newest 48 hours went; the anchored crawl sends them again (skipped)
              recent = false;
            } else if (page.anchor != null) {
              await source.commit(t.hk, page.anchor!);
            }
            out.samples += page.uuid.length;
            out.uploaded.add(t.hk);
            if (!recent && !page.more && phase != 'recent') {
              if (!backfilled) await progress.setBackfilled(t.hk);
              continue types;
            }
          case EngineUpload.tooLarge when limit > 1:
            limit = max(1, limit ~/ 2);
          case EngineUpload.refused:
            if (res.detail == 'type_paused') {
              out.skipped[t.hk] = 'type_paused'; // not taken right now: the anchor stays, sent once it is taken again
              continue types;
            }
            // engine_disabled (re-read the engine block next pass) / consent_required (the page shows the consent)
            out.stop = res.detail ?? 'refused';
            return _report(out, t0);
          case EngineUpload.rejected:
            out.skipped[t.hk] = 'contract'; // an app bug for this type: its anchor stays, the other types go on
            continue types;
          case EngineUpload.rateLimited:
            out.stop = 'rate_limited';
            out.retryAfter = res.retryAfter;
            return out; // nothing more this pass (not even the run report)
          case EngineUpload.unauthorized:
            out.stop = 'unauthorized';
            return out;
          case EngineUpload.offline:
            out.stop = 'offline';
            return out;
          default:
            out.stop = 'failed';
            return _report(out, t0);
        }
      }
    }
    return _report(out, t0);
  }

  /// One small batch with this pass's `runs[]` entry (counts only), when anything was tried.
  Future<EngineResult> _report(EngineResult out, DateTime t0) async {
    if (out.batches == 0 && out.skipped.isEmpty) return out;
    final result = switch (out.stop) {
      null => out.skipped.isEmpty ? 'ok' : 'partial',
      'budget' => 'partial',
      _ => 'error',
    };
    final contract = out.skipped.containsValue('contract');
    final run = {
      'id': _batchId(),
      'trigger': trigger,
      'result': result,
      if (out.stop != null && out.stop != 'budget') 'error': out.stop else if (contract) 'error': 'contract',
      'started': t0.millisecondsSinceEpoch / 1000,
      'duration_ms': _now().difference(t0).inMilliseconds,
      'batches': out.batches,
      'bytes': out.bytes,
      'samples': out.samples,
    };
    try {
      final res = await upload(gzipJson({'batch_id': _batchId(), 'device': 'iPhone', 'app_version': ?appVersion, 'runs': [run]}));
      if (res.outcome == EngineUpload.ok && res.body?['status'] is Map) {
        out.serverStatus = (res.body!['status'] as Map).map((k, v) => MapEntry('$k', v));
      }
    } catch (_) {}
    return out;
  }
}

/// One line for the sync log (English, counts only).
String engineLogLine(EngineResult r) => [
      'Sync engine: ${r.samples} samples of ${r.uploaded.length}/${r.types} types in ${r.batches} batches',
      if (r.stop != null) 'stopped: ${r.stop}',
      if (r.skipped.isNotEmpty) 'skipped: ${r.skipped.values.toSet().join(', ')} (${r.skipped.length})',
    ].join('; ');

/// The app glue: registry from the asset, the server's engine block from RawSync's last answer.
class SyncEngine {
  SyncEngine._();
  static final SyncEngine instance = SyncEngine._();

  EngineSource source = NativeEngineSource();
  EngineProgress progress = PrefsEngineProgress();
  List<EngineType>? _registry;
  bool _running = false;
  DateTime? _notBefore; // 429 Retry-After

  /// The last pass (for the sync status / log); null before the first one.
  EngineResult? last;

  /// Last accepted upload per HealthKit type in this app run (sync_bridge.dart status).
  final Map<String, DateTime> uploadedAt = {};
  void Function(String line)? onLog;

  Future<List<EngineType>> registry() async => _registry ??= parseRegistry(await rootBundle.loadString(kEngineRegistryAsset));

  /// One pass when the server's engine block says active; returns null when it did nothing.
  Future<EngineResult?> run({
    required String serverUrl,
    required String token,
    required Map<String, Object?>? serverStatus,
    required int daysBack,
    String? appVersion,
    String trigger = 'foreground',
  }) async {
    final info = EngineInfo.fromStatus(serverStatus);
    if (info != null) await _setMode(info);
    if (_running || info == null || !info.active || serverUrl.isEmpty) return null;
    if (_notBefore != null && DateTime.now().isBefore(_notBefore!)) return null;
    _running = true;
    try {
      final up = EngineUploader(serverUrl, token, appVersion: appVersion);
      final net = (await DeviceSyncState.read()).net;
      final res = await EngineRunner(
        registry: await registry(),
        info: info,
        source: source,
        upload: up.upload,
        progress: progress,
        appVersion: appVersion,
        trigger: trigger,
        ios: iosMajor(),
        allowBackfill: net == null || net == 'wifi' || net == 'wired' || net == 'unknown',
      ).run(daysBack);
      if (res.retryAfter != null) _notBefore = DateTime.now().add(res.retryAfter!);
      if (res.stop == 'unauthorized') _notBefore = DateTime.now().add(kEngineAuthBackoff);
      last = res;
      final at = DateTime.now();
      for (final hk in res.uploaded) {
        uploadedAt[hk] = at;
      }
      final line = engineLogLine(res);
      debugPrint(line);
      onLog?.call(line);
      return res;
    } catch (e) {
      onLog?.call('Sync engine failed: $e');
      return null;
    } finally {
      _running = false;
    }
  }

  /// The server's mode for this user (contract §7), kept across restarts; changed only by an answer of the server.
  static const _modeKey = 'w2w.sync.mode.v1';
  String? _mode;

  /// Called when the mode changes (HomePage: `app` stops the Open Wearables SDK, back from `app` starts it again).
  void Function(String from, String to)? onModeChange;

  Future<String> mode() async => _mode ??= (await SharedPreferences.getInstance()).getString(_modeKey) ?? 'ow';

  Future<void> _setMode(EngineInfo info) async {
    final to = info.enabled ? info.mode : 'ow';
    final from = await mode();
    if (to == from) return;
    _mode = to;
    await (await SharedPreferences.getInstance()).setString(_modeKey, to);
    onLog?.call('Sync engine mode: $from -> $to');
    onModeChange?.call(from, to);
  }

  /// Sign-out / account deleted: the next account starts from scratch.
  Future<void> clear() async {
    try {
      await source.reset();
    } catch (_) {}
    await progress.clear();
    last = null;
    uploadedAt.clear();
    _mode = null;
    _notBefore = null;
    try {
      await (await SharedPreferences.getInstance()).remove(_modeKey);
    } catch (_) {}
  }
}
