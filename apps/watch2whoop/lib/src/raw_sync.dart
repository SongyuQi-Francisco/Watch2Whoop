// Beat-to-beat HRV series, ECG recordings and drinks (dietary water, #135) straight from HealthKit to Body Engine
// (POST /v1/ingest/healthkit), not through Open Wearables: OW SDK 0.15 syncs none of these types. The native side (ios/Runner/RawSync.swift) reads one page after the stored
// HealthKit anchor; this file uploads it and only then lets the native side store the new anchor, so nothing is lost when an
// upload fails and nothing is sent twice once it succeeded (the server also skips UUIDs it already has).
// - Runs after each sync-on-open round (home_page.dart), in the foreground: HealthKit cannot be read while the phone is locked.
// - Range: the same choice as the OW sync (health_range.dart). Going from 90 days to "All history" resets the anchors once;
//   the samples already on the server are skipped there by UUID.
// - Token: the self-service account token (Keychain), or for setup-link users an upload-only token the logged-in Today page
//   mints (POST /v1/web/ingest-token), also kept in the Keychain.
// - Water is behind the server's `water` rollout flag: the app reads it only while the server says water_enabled (GET status
//   or an earlier answer of this pass) and keeps the water anchor when a batch comes back with water_enabled false, so drinks
//   logged while the flag is off are sent once it is on. That is not an error and is not retried within the pass.
// - Beat-to-beat readings and ECGs need consent 2026-10-22.1 (409 otherwise); water only the ordinary consent, so a 409 on
//   those skips to water instead of ending the pass.
// - Permission: whether to ask is what HealthKit says (`rawAuthStatus`), not a flag of our own. Up to 1.0.6 a flag was set
//   before asking, and the ask right after the OW sheet never showed (iOS cannot present a second sheet while the first is
//   being dismissed): the types stayed undetermined, every read failed with HealthKit error 5 and the whole pass ended
//   silently, so no app ever uploaded a beat. A read error now skips only that kind and shows on the sync page and its log.
// - The quantity types of the registry (hk_types.dart: stand time, exercise minutes, wrist temperature …, which the OW
//   SDK cannot read) go the same way as `quantities[]`, each with its own anchor (kind = the HealthKit identifier), only
//   the types the server lists in `metric_types` and only while it says `metrics_enabled` (emergency flag
//   app_types_pause; when off the anchors stay and the samples go later). Ordinary consent, like water.
// - Forced re-sync (resync.dart): `run(anchorSet: 'resync')` reads every kind again from the start of the range with a
//   separate set of anchors ("resync:heartbeat", "resync:HKQuantity…"); the regular anchors do not move, so nothing is
//   skipped afterwards and the server drops what it already has (UUIDs).
// - Weak network (#295 A3): the status GET has its own short timeout and is asked at most once per pass; when the
//   server cannot be reached the pass ends at once as `offline` (before, every type asked again and waited 60 s each).
//   Uploads time out after [kUploadTimeout]; the whole pass also ends at its budget, so sync-on-open is never held up.
// - 401 with the account token (#295 A2): the account token was revoked (signed out elsewhere, account reset). The pass
//   gets an upload-only token from the logged-in Today page instead (the same way setup-link users upload), keeps it in
//   the Keychain and goes on; the account token is not tried again for uploads until it changes (a new sign-in).
// The runner is pure Dart with an injected source / uploader (test/raw_sync_test.dart).
import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart' show sha256;
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'health_range.dart';
import 'hk_types.dart';
import 'i18n.dart';
import 'platform_features.dart';
import 'ui.dart' show ago;

const List<String> kRawKinds = ['heartbeat', 'ecg', 'water'];

/// Records per HealthKit page: a reading is ~1 minute (~50 beats, ~1 KB as JSON), an ECG ~40 KB as base64 int16, a drink
/// ~100 bytes (the server takes up to 2000 per batch).
const Map<String, int> kRawPageSize = {'heartbeat': 200, 'ecg': 8, 'water': 500};

/// Samples per page of a registry quantity type (~170 bytes each as JSON; the server takes 2000 per type and batch).
const int kQuantityPageSize = 2000;

/// The anchor set of the forced re-sync (resync.dart).
const String kResyncAnchorSet = 'resync';

/// The kind without its anchor set ("resync:heartbeat" -> "heartbeat").
String baseKind(String kind) => kind.contains(':') ? kind.substring(kind.lastIndexOf(':') + 1) : kind;

/// The field of the request body each kind goes into (rawdata/api.py IngestIn).
const Map<String, String> kRawBatchField = {'heartbeat': 'heartbeats', 'ecg': 'ecgs', 'water': 'water'};

/// One page from the native reader.
class RawPage {
  const RawPage({this.items = const [], this.deleted = const [], this.anchor, this.more = false});

  final List<Map<String, Object?>> items;
  final List<String> deleted;
  final String? anchor;
  final bool more;

  bool get isEmpty => items.isEmpty && deleted.isEmpty;

  factory RawPage.fromMap(Map<Object?, Object?> m) => RawPage(
        items: [
          for (final it in (m['items'] as List?) ?? const [])
            if (it is Map) it.map((k, v) => MapEntry('$k', v)),
        ],
        deleted: [for (final d in (m['deleted'] as List?) ?? const []) '$d'],
        anchor: m['anchor'] as String?,
        more: m['more'] == true,
      );
}

/// HealthKit error code "Authorization not determined" (HKError.Code.errorAuthorizationNotDetermined): the type was never
/// asked for, so reading it fails instead of returning nothing (a refused read returns nothing).
const int kHealthKitNotDetermined = 5;

/// Automatic permission asks while iOS still says the sheet is needed; counted before the native call, so a build that
/// crashes in HealthKit (as 1.0.0+4 did) does not crash on every open. The sync page asks on demand without this limit.
const int kRawMaxAutoAsks = 3;

/// Answer of the native permission ask.
class RawAuth {
  const RawAuth({this.asked = false, this.error});

  /// iOS no longer needs to ask (the user answered the sheet now or earlier; what was chosen stays unknown).
  final bool asked;

  /// e.g. `not_presented`: the sheet did not come up (another sheet was still closing).
  final String? error;

  factory RawAuth.fromMap(Map<Object?, Object?>? m) => RawAuth(asked: m?['asked'] == true, error: m?['error'] as String?);
}

/// Read failure of one kind: [notAsked] = the permission was never asked (HealthKit error 5).
({bool notAsked, String message}) readProblemOf(Object e) {
  if (e is PlatformException) {
    final notAsked = e.code == 'healthkit' && '${e.details}' == '$kHealthKitNotDetermined';
    return (notAsked: notAsked, message: e.message ?? e.code);
  }
  return (notAsked: false, message: '$e');
}

abstract class RawSource {
  /// `unnecessary` (already asked), `shouldRequest` (the sheet would show) or `unknown`.
  Future<String> authStatus();
  Future<RawAuth> requestAuth();

  /// [kind] may carry an anchor set ("resync:heartbeat"); a registry kind is a HealthKit identifier.
  Future<RawPage> read(String kind, int daysBack, int limit);
  Future<void> commit(String kind, String anchor);

  /// Forgets the regular anchors, or only the anchors of [anchorSet].
  Future<void> reset({String? anchorSet});
}

class NativeRawSource implements RawSource {
  static const _ch = MethodChannel('watch2whoop/native');

  /// The registry's types go with the RawSync sheet (the native side drops the ones this iOS does not know).
  static Map<String, Object?> get _types => {'types': [for (final t in kHkTypes) t.hk]};

  @override
  Future<String> authStatus() async => (await _ch.invokeMethod<String>('rawAuthStatus', _types)) ?? 'unknown';

  @override
  Future<RawAuth> requestAuth() async =>
      RawAuth.fromMap(await _ch.invokeMapMethod<Object?, Object?>('rawRequestAuth', _types));

  @override
  Future<RawPage> read(String kind, int daysBack, int limit) async => RawPage.fromMap((await _ch.invokeMapMethod<Object?, Object?>(
          'rawRead', {'kind': kind, 'daysBack': daysBack, 'limit': limit, 'unit': ?hkTypeOf(baseKind(kind))?.unit})) ??
      {});

  @override
  Future<void> commit(String kind, String anchor) => _ch.invokeMethod('rawCommit', {'kind': kind, 'anchor': anchor});

  @override
  Future<void> reset({String? anchorSet}) => _ch.invokeMethod('rawReset', {'set': ?anchorSet});
}

/// The newest sample on the phone per HealthKit type (hk_types.dart kPhoneTypes); empty when HealthKit cannot say.
Future<Map<String, DateTime>> phoneLatest(Iterable<String> types) async {
  try {
    final m = await const MethodChannel('watch2whoop/native')
        .invokeMapMethod<String, String>('healthLatest', {'types': types.toSet().toList()});
    return {for (final e in (m ?? const {}).entries) if (DateTime.tryParse(e.value) != null) e.key: DateTime.parse(e.value).toLocal()};
  } catch (_) {
    return const {};
  }
}

enum RawUpload { ok, unauthorized, consent, tooLarge, failed, offline }

/// The status GET: small answer, so a short timeout (a phone on a captive portal must not wait a minute per pass).
const Duration kStatusTimeout = Duration(seconds: 10);

/// One upload (≤ 1 MB).
const Duration kUploadTimeout = Duration(seconds: 45);

/// The status GET was answered with 401: the token is not valid (any longer).
class RawUnauthorized implements Exception {
  const RawUnauthorized();
}

class RawUploadResult {
  const RawUploadResult(this.outcome, [this.body]);
  final RawUpload outcome;
  final Map<String, Object?>? body;
}

/// POST {server}/v1/ingest/healthkit with a Bearer token.
class RawUploader {
  RawUploader(this.baseUrl, {this.client, this.timeout = kUploadTimeout, this.statusTimeout = kStatusTimeout});
  final String baseUrl;
  final http.Client? client;
  final Duration timeout, statusTimeout;

  Uri get _uri {
    final b = Uri.parse(baseUrl);
    return Uri(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null, path: '/v1/ingest/healthkit');
  }

  /// GET the same path: what this account uploaded so far and whether the server takes water (`water_enabled`); null when
  /// the server cannot be reached (or answers something else); throws [RawUnauthorized] on 401.
  Future<Map<String, Object?>?> status(String token) async {
    final c = client ?? http.Client();
    try {
      final res =
          await c.get(_uri, headers: {'Authorization': 'Bearer $token', 'Accept': 'application/json'}).timeout(statusTimeout);
      if (res.statusCode == 401) throw const RawUnauthorized();
      if (res.statusCode != 200) return null;
      final j = jsonDecode(res.body);
      return j is Map ? j.map((k, v) => MapEntry('$k', v)) : null;
    } on RawUnauthorized {
      rethrow;
    } catch (_) {
      return null;
    } finally {
      if (client == null) c.close();
    }
  }

  Future<RawUploadResult> upload(String token, Map<String, Object?> batch) async {
    final c = client ?? http.Client();
    try {
      final res = await c
          .post(_uri,
              headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/json', 'Accept': 'application/json'},
              body: jsonEncode(batch))
          .timeout(timeout);
      Map<String, Object?>? body;
      try {
        final j = jsonDecode(res.body);
        if (j is Map) body = j.map((k, v) => MapEntry('$k', v));
      } catch (_) {}
      return RawUploadResult(switch (res.statusCode) {
        200 => RawUpload.ok,
        401 => RawUpload.unauthorized,
        409 => RawUpload.consent,
        413 => RawUpload.tooLarge,
        _ => RawUpload.failed,
      }, body);
    } catch (_) {
      return const RawUploadResult(RawUpload.offline);
    } finally {
      if (client == null) c.close();
    }
  }
}

/// Random UUID v4 (batch id).
String newUuid([Random? random]) {
  final r = random ?? Random.secure();
  final b = List<int>.generate(16, (_) => r.nextInt(256));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  final h = b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
  return '${h.substring(0, 8)}-${h.substring(8, 12)}-${h.substring(12, 16)}-${h.substring(16, 20)}-${h.substring(20)}';
}

/// The request body for one page (the field names are what rawdata/api.py IngestIn expects). A registry kind goes as one
/// `quantities` group with its unit.
Map<String, Object?> buildBatch(String kind, RawPage page, {required String batchId, String? device, String? appVersion}) {
  final q = hkTypeOf(baseKind(kind));
  return {
    'batch_id': batchId,
    'device': ?device,
    'app_version': ?appVersion,
    if (q == null) kRawBatchField[baseKind(kind)]!: page.items,
    if (q != null) 'quantities': [
      {'type': q.hk, 'unit': q.unit, 'items': page.items},
    ],
    'deleted': page.deleted,
  };
}

class RawSyncResult {
  RawSyncResult();
  int heartbeats = 0, ecgs = 0, water = 0, metrics = 0, deleted = 0, batches = 0;

  /// The server's water flag was off (water not read or not taken; the anchor stays): not an error.
  bool waterOff = false;

  /// The server takes no registry types (paused, or an older server): not read, the anchors stay. Not an error.
  bool metricsOff = false;

  /// null = everything read; unauthorized / consent / failed / offline (the server could not be reached) / budget
  String? stop;

  /// Kinds (heartbeat / ecg / water / a HealthKit identifier) with at least one accepted upload in this pass.
  final Set<String> uploaded = {};

  /// A kind could not be read from HealthKit (the others still went): `not_asked` or `healthkit`, with [readError].
  String? readProblem, readError;
  Map<String, Object?>? serverStatus;
}

class RawSyncRunner {
  RawSyncRunner({
    required this.source,
    required this.upload,
    this.status,
    this.device,
    this.appVersion,
    this.maxBatches = 40,
    this.maxBytes = 1000000,
    this.budget = const Duration(minutes: 2),
    DateTime Function()? now,
    String Function()? batchId,
  })  : _now = now ?? DateTime.now,
        _batchId = batchId ?? newUuid;

  final RawSource source;
  final Future<RawUploadResult> Function(Map<String, Object?> batch) upload;

  /// GET status (RawUploader.status): asked before water is read, unless an answer of this pass already said.
  final Future<Map<String, Object?>?> Function()? status;
  final String? device, appVersion;
  final int maxBatches, maxBytes;
  final Duration budget;
  final DateTime Function() _now;
  final String Function() _batchId;

  /// The server's answer for [key]: from an answer of this pass, else one GET status (an answer without the key = an
  /// older server). At most one GET per pass; null afterwards = the server could not be reached ([_unreachable]).
  Future<Object?> _server(RawSyncResult out, String key) async {
    var v = out.serverStatus?[key];
    if (v == null && status != null && out.serverStatus == null && !_asked) {
      _asked = true;
      final st = await status!();
      v = st?[key];
      if (st != null) out.serverStatus = {...?out.serverStatus, ...st};
      if (st == null) _unreachable = true;
    }
    return v;
  }

  bool _asked = false, _unreachable = false;

  /// One pass over every kind: heartbeat, ECG, water, then the registry types. [anchorSet] = a separate set of anchors
  /// (the forced re-sync reads the range again without moving the regular ones).
  Future<RawSyncResult> run(int daysBack, {String anchorSet = ''}) async {
    final out = RawSyncResult();
    final t0 = _now();
    _asked = _unreachable = false;
    var rawConsent = true;
    kinds:
    for (final kind in [...kRawKinds, for (final t in kHkTypes) t.hk]) {
      final quantity = hkTypeOf(kind) != null;
      final name = anchorSet.isEmpty ? kind : '$anchorSet:$kind';
      if (kind != 'water' && !quantity && !rawConsent) continue; // the ECG would get the same 409 as the beats
      if (_now().difference(t0) > budget) {
        out.stop = 'budget';
        return out;
      }
      if (quantity) {
        final Object? on;
        try {
          on = await _server(out, 'metrics_enabled');
        } on RawUnauthorized {
          out.stop = 'unauthorized';
          return out;
        }
        if (_unreachable) {
          out.stop = 'offline'; // no answer at all: the uploads would not get through either; the next open tries again
          return out;
        }
        final types = out.serverStatus?['metric_types'];
        if (on != true || types is! List || !types.contains(kind)) {
          out.metricsOff = true; // paused, an older server or unreachable: the anchor stays, next pass
          continue;
        }
      }
      if (kind == 'water') {
        final Object? on;
        try {
          on = await _server(out, 'water_enabled');
        } on RawUnauthorized {
          out.stop = 'unauthorized';
          return out;
        }
        if (_unreachable) {
          out.stop = 'offline';
          return out;
        }
        if (on != true) {
          out.waterOff = true; // flag off, an older server, or unreachable: try again next pass
          continue;
        }
      }
      var limit = kRawPageSize[kind] ?? kQuantityPageSize;
      while (true) {
        if (out.batches >= maxBatches || _now().difference(t0) > budget) {
          out.stop = 'budget'; // the rest next time (the anchor is where the last accepted page ended)
          return out;
        }
        final RawPage page;
        try {
          page = await source.read(name, daysBack, limit);
        } catch (e) {
          final p = readProblemOf(e);
          if (out.readProblem != 'not_asked') out.readProblem = p.notAsked ? 'not_asked' : 'healthkit';
          out.readError ??= p.message;
          continue kinds; // the anchor stays; the next kind may still be readable
        }
        if (page.isEmpty) {
          // nothing new (or only samples the reader drops, e.g. a series with < 2 beats)
          if (page.anchor != null) await source.commit(name, page.anchor!);
          if (!page.more) break;
          continue;
        }
        final batch = buildBatch(kind, page, batchId: _batchId(), device: device, appVersion: appVersion);
        if (utf8.encode(jsonEncode(batch)).length > maxBytes && limit > 1) {
          limit = max(1, limit ~/ 2); // same anchor, smaller page
          continue;
        }
        final res = await upload(batch);
        out.batches++;
        switch (res.outcome) {
          case RawUpload.ok:
            final b = res.body ?? const {};
            if (b['status'] is Map) out.serverStatus = (b['status'] as Map).map((k, v) => MapEntry('$k', v));
            if (kind == 'water' && b['water_enabled'] != true) {
              out.waterOff = true; // the flag went off meanwhile: the drinks were not stored, keep the anchor
              continue kinds;
            }
            if (quantity && b['metrics_enabled'] != true) {
              out.metricsOff = true; // paused meanwhile: not stored, keep the anchor
              continue kinds;
            }
            if (page.anchor != null) await source.commit(name, page.anchor!);
            out.uploaded.add(kind);
            out.heartbeats += (b['heartbeats'] as num?)?.toInt() ?? 0;
            out.ecgs += (b['ecgs'] as num?)?.toInt() ?? 0;
            out.water += (b['water'] as num?)?.toInt() ?? 0;
            out.metrics += (b['metrics'] as num?)?.toInt() ?? 0;
            out.deleted += (b['deleted'] as num?)?.toInt() ?? 0;
          case RawUpload.tooLarge when limit > 1:
            limit = max(1, limit ~/ 2);
            continue;
          case RawUpload.unauthorized:
            out.stop = 'unauthorized';
            return out;
          case RawUpload.consent:
            out.stop = 'consent';
            if (kind == 'water' || quantity) return out; // the ordinary consent: nothing else goes either
            rawConsent = false; // the new consent is not accepted yet: water still goes
            continue kinds;
          case RawUpload.offline:
            out.stop = 'offline'; // timed out / no network: the anchor stays, the next open tries again
            return out;
          default:
            out.stop = 'failed';
            return out;
        }
        if (!page.more) break;
      }
    }
    return out;
  }
}

// ------------------------------------------------------------------------------ app glue

/// What the sync page shows (shared_preferences; counts are the server's totals of app uploads).
class RawSyncStatus {
  const RawSyncStatus({
    this.heartbeats,
    this.ecgs,
    this.water,
    this.lastUpload,
    this.lastRun,
    this.stop,
    this.lastHeartbeats,
    this.lastEcgs,
    this.serverLastUpload,
    this.error,
    this.metrics,
    this.syncCenter = false,
    this.kindsAt = const {},
    this.accountRejected = false,
  });

  /// Last accepted upload of this phone per kind (heartbeat / ecg / water / a HealthKit identifier).
  final Map<String, DateTime> kindsAt;

  /// The account token got 401 on upload: uploads go on with the Today page's upload-only token; the app itself needs a
  /// new sign-in for everything else (sync_bridge.dart reports it).
  final bool accountRejected;

  /// The server's totals of app uploads (metrics = registry samples).
  final int? heartbeats, ecgs, water, metrics;

  /// The server's sync_center flag for this account (the sync page shows the sync center).
  final bool syncCenter;

  /// Last pass that uploaded something from this phone / last pass at all.
  final DateTime? lastUpload, lastRun;

  /// Why the last pass did not get everything: consent / unauthorized / failed (upload), not_asked / healthkit (reading).
  final String? stop;

  /// What the last uploading pass added (beat-to-beat readings, ECGs).
  final int? lastHeartbeats, lastEcgs;

  /// The server's last accepted batch of this account (any phone).
  final DateTime? serverLastUpload;

  /// HealthKit's message for `healthkit`.
  final String? error;

  String? get problem {
    final why = error ?? '-';
    return switch (stop) {
      'consent' => tr('在今天页确认新版同意书后开始上传', 'Starts after you accept the new consent on the Today page'),
      'unauthorized' => tr('登录失效，打开今天页后重试', 'Signed out; open the Today page to retry'),
      'failed' => tr('上次上传失败，下次打开 app 重试', 'Last upload failed; retries next time you open the app'),
      'offline' => tr('连不上服务器（网络不通或很慢），下次打开 app 重试', 'Could not reach the server (no or slow network); retries next time you open the app'),
      'not_asked' => tr('「健康」还没问过这几项的读取权限，点「允许读取」', 'Health has not asked for these yet; tap "Allow reading"'),
      'healthkit' => tr('读取「健康」出错：$why。下次打开 app 重试', 'Reading Health failed: $why. Retries next time you open the app'),
      _ => null,
    };
  }

  Map<String, Object?> toJson() => {
        'heartbeats': heartbeats,
        'ecgs': ecgs,
        'water': water,
        'last_upload': lastUpload?.toIso8601String(),
        'last_run': lastRun?.toIso8601String(),
        'stop': stop,
        'last_heartbeats': lastHeartbeats,
        'last_ecgs': lastEcgs,
        'server_last_upload': serverLastUpload?.toIso8601String(),
        'error': error,
        'metrics': metrics,
        'sync_center': syncCenter,
        'kinds_at': kindsAt.map((k, v) => MapEntry(k, v.toIso8601String())),
        'account_rejected': accountRejected,
      };

  factory RawSyncStatus.fromJson(Map<String, Object?> j) => RawSyncStatus(
        heartbeats: (j['heartbeats'] as num?)?.toInt(),
        ecgs: (j['ecgs'] as num?)?.toInt(),
        water: (j['water'] as num?)?.toInt(),
        lastUpload: DateTime.tryParse('${j['last_upload'] ?? ''}')?.toLocal(),
        lastRun: DateTime.tryParse('${j['last_run'] ?? ''}')?.toLocal(),
        stop: j['stop'] as String?,
        lastHeartbeats: (j['last_heartbeats'] as num?)?.toInt(),
        lastEcgs: (j['last_ecgs'] as num?)?.toInt(),
        serverLastUpload: DateTime.tryParse('${j['server_last_upload'] ?? ''}')?.toLocal(),
        error: j['error'] as String?,
        metrics: (j['metrics'] as num?)?.toInt(),
        syncCenter: j['sync_center'] == true,
        kindsAt: {
          for (final e in ((j['kinds_at'] as Map?) ?? const {}).entries)
            if (DateTime.tryParse('${e.value}') != null) '${e.key}': DateTime.parse('${e.value}').toLocal(),
        },
        accountRejected: j['account_rejected'] == true,
      );
}

/// The status after one pass: the server's totals when it answered, what this pass added, and why it stopped.
RawSyncStatus nextRawStatus(RawSyncStatus old, RawSyncResult res, DateTime now, {bool? accountRejected}) {
  final s = res.serverStatus;
  final uploaded = res.batches > 0 && res.stop != 'failed' && res.stop != 'offline' && res.stop != 'consent' && res.stop != 'unauthorized';
  final stop = res.stop == 'budget' ? null : res.stop;
  return RawSyncStatus(
    heartbeats: (s?['heartbeats'] as num?)?.toInt() ?? old.heartbeats,
    ecgs: (s?['ecgs'] as num?)?.toInt() ?? old.ecgs,
    water: s?['water_enabled'] == true ? (s?['water'] as num?)?.toInt() ?? old.water : null,
    lastUpload: uploaded ? now : old.lastUpload,
    lastRun: now,
    stop: stop ?? res.readProblem,
    lastHeartbeats: uploaded ? res.heartbeats : old.lastHeartbeats,
    lastEcgs: uploaded ? res.ecgs : old.lastEcgs,
    serverLastUpload: DateTime.tryParse('${s?['last_upload_at'] ?? ''}')?.toLocal() ?? old.serverLastUpload,
    error: stop == null && res.readProblem == 'healthkit' ? res.readError : null,
    metrics: (s?['metrics'] as num?)?.toInt() ?? old.metrics,
    syncCenter: s == null ? old.syncCenter : s['sync_center'] == true,
    kindsAt: {...old.kindsAt, for (final k in res.uploaded) k: now},
    accountRejected: accountRejected ?? old.accountRejected,
  );
}

/// 「上次上传」 on the sync page: when this phone last uploaded and what that pass added (beat-to-beat readings, ECGs);
/// before its first upload the server's last accepted batch of the account (e.g. from another phone).
String rawLastUploadText(RawSyncStatus s) {
  final at = s.lastUpload ?? s.serverLastUpload;
  if (at == null) return '—';
  final ecg = (s.lastEcgs ?? 0) > 0 ? tr(' / 心电图 +${s.lastEcgs}', ' / ECG +${s.lastEcgs}') : '';
  final added = s.lastUpload == null || s.lastHeartbeats == null ? '' : ' · +${s.lastHeartbeats}$ecg';
  return '${ago(at)}$added';
}

/// One line for the sync log (English, like the SDK's lines).
String rawLogLine(RawSyncResult r) => [
      'Beat-to-beat/ECG/water/other types: ${r.heartbeats} readings, ${r.ecgs} ECGs, ${r.water} drinks, '
          '${r.metrics} samples in ${r.batches} batches',
      if (r.stop != null) 'stopped: ${r.stop}',
      if (r.readProblem != null) 'HealthKit: ${r.readProblem == 'not_asked' ? 'access not asked yet' : r.readError}',
      if (r.waterOff) 'water off on the server',
      if (r.metricsOff) 'other types off on the server',
    ].join('; ');

/// What is kept about a refused account token: its sha256 (hex), never the token.
String tokenFingerprint(String token) => sha256.convert(utf8.encode(token)).toString();

/// Range the anchors were read with: "All history" after 90 days needs fresh anchors (they only see newer samples).
bool needsAnchorReset(int? previousDaysBack, int daysBack) =>
    previousDaysBack != null && !isAllHistory(previousDaysBack) && isAllHistory(daysBack);

class RawSync {
  RawSync._();
  static final RawSync instance = RawSync._();

  static const _statusKey = 'w2w.raw.status.v1';
  static const _rangeKey = 'w2w.raw.range.v1';
  static const _authTriesKey = 'w2w.raw.authTries.v1'; // automatic asks while iOS says shouldRequest (kRawMaxAutoAsks)
  static const _tokenKey = 'w2w.ingest.token.v1';
  static const _rejectedKey = 'w2w.raw.accountRejected.v1'; // sha256 of the account token that got 401 (never the token)
  static const _storage = FlutterSecureStorage(
    iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device),
  );

  final ValueNotifier<RawSyncStatus> status = ValueNotifier(const RawSyncStatus());
  RawSource source = NativeRawSource();

  /// The server's last answer (GET / POST /v1/ingest/healthkit) of this app run, with its `engine` block: the sync
  /// engine (sync_engine.dart) runs only when that says so, without asking again.
  Map<String, Object?>? lastServerStatus;

  /// The token the last pass uploaded with (account or upload-only), for the sync engine right after it.
  String? lastToken;
  bool _running = false;

  /// The sync page's log (HealthSync.note, set in main.dart).
  void Function(String line)? onLog;

  void _log(String line) {
    debugPrint(line);
    onLog?.call(line);
  }

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final s = prefs.getString(_statusKey);
    if (s != null) {
      try {
        status.value = RawSyncStatus.fromJson((jsonDecode(s) as Map).map((k, v) => MapEntry('$k', v)));
      } catch (_) {}
    }
  }

  /// Asks HealthKit for the read permissions (the second, small sheet after the OW one; the native side waits until the
  /// first one has closed). The try is counted before the native call: if HealthKit aborts the app (as build 1.0.0+4 did),
  /// the automatic ask in [run] stops after kRawMaxAutoAsks; this call itself (permissions step, sync page) always asks.
  /// Returns whether iOS no longer needs to ask.
  Future<bool> requestAuth() async {
    if (!hasHealthSync) return false; // Android (#253): no HealthKit
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setInt(_authTriesKey, (prefs.getInt(_authTriesKey) ?? 0) + 1);
      final r = await source.requestAuth().timeout(const Duration(minutes: 3), onTimeout: () => const RawAuth(error: 'timeout'));
      if (r.asked) await prefs.setInt(_authTriesKey, 0);
      _log('Beat-to-beat/ECG/water Health access: ${r.asked ? 'asked' : 'not asked (${r.error ?? '-'})'}');
      return r.asked;
    } catch (e) {
      _log('Beat-to-beat/ECG/water Health access failed: $e');
      return false;
    }
  }

  /// Whether [run] asks by itself: iOS says the sheet is needed and the automatic tries are not used up.
  Future<bool> shouldAutoAsk() async {
    final prefs = await SharedPreferences.getInstance();
    if ((prefs.getInt(_authTriesKey) ?? 0) >= kRawMaxAutoAsks) return false;
    try {
      return await source.authStatus() == 'shouldRequest';
    } catch (_) {
      return false;
    }
  }

  /// One upload pass. [accountToken]: the self-service account token; [pageToken]: asks the logged-in Today page for an
  /// upload-only token (setup-link users).
  Future<RawSyncResult?> run({
    required String serverUrl,
    required int daysBack,
    String? accountToken,
    required Future<String?> Function() pageToken,
    String? appVersion,
    String anchorSet = '',
  }) async {
    if (_running || serverUrl.isEmpty || !hasHealthSync) return null;
    _running = true;
    try {
      final prefs = await SharedPreferences.getInstance();
      if (await shouldAutoAsk()) await requestAuth();
      // An account token that got 401 before is not used for uploads again until it changes (a new sign-in)
      final rejected = accountToken != null && prefs.getString(_rejectedKey) == tokenFingerprint(accountToken);
      if (accountToken != null && !rejected) await prefs.remove(_rejectedKey);
      var token = rejected ? null : accountToken;
      var ingest = token == null;
      if (ingest) {
        token = await _ingestOrPage(pageToken);
        if (token == null) return null; // page not logged in yet: next time
      }
      if (needsAnchorReset(prefs.getInt(_rangeKey), daysBack)) await source.reset();
      await prefs.setInt(_rangeKey, daysBack);
      final uploader = RawUploader(serverUrl);
      Future<RawSyncResult> pass(String t) => RawSyncRunner(
            source: source,
            upload: (b) => uploader.upload(t, b),
            status: () => uploader.status(t),
            device: 'iPhone',
            appVersion: appVersion,
          ).run(daysBack, anchorSet: anchorSet);
      var res = await pass(token);
      if (res.stop == 'unauthorized' && ingest) await _deleteToken(); // a fresh one from the page next time
      if (res.stop == 'unauthorized' && !ingest && accountToken != null) {
        // The account token itself is refused: go on with the page's upload-only token (#295 A2)
        await prefs.setString(_rejectedKey, tokenFingerprint(accountToken));
        _log('Upload: the app sign-in was refused (401); continuing with the Today page sign-in');
        final t = await _ingestOrPage(pageToken);
        if (t != null) {
          token = t;
          ingest = true;
          res = await pass(t);
          if (res.stop == 'unauthorized') await _deleteToken();
        }
      }
      // Nothing reached the server this pass (e.g. Health not readable): still show its totals and last upload
      if (res.serverStatus == null && res.stop != 'unauthorized' && res.stop != 'offline') {
        try {
          res.serverStatus = await uploader.status(token);
        } on RawUnauthorized {
          // shown through res.stop / accountRejected
        }
      }
      _log(rawLogLine(res));
      if (res.serverStatus != null) lastServerStatus = res.serverStatus;
      lastToken = res.stop == 'unauthorized' ? null : token;
      status.value = nextRawStatus(status.value, res, DateTime.now(),
          accountRejected: accountToken != null && prefs.getString(_rejectedKey) == tokenFingerprint(accountToken));
      await prefs.setString(_statusKey, jsonEncode(status.value.toJson()));
      return res;
    } catch (e) {
      _log('Beat-to-beat/ECG/water upload failed: $e');
      return null;
    } finally {
      _running = false;
    }
  }

  /// The kept upload-only token, or a fresh one from the logged-in Today page (kept in the Keychain).
  Future<String?> _ingestOrPage(Future<String?> Function() pageToken) async {
    var t = await _readToken();
    if (t != null) return t;
    t = await pageToken();
    if (t != null) await _storage.write(key: _tokenKey, value: t);
    return t;
  }

  Future<String?> _readToken() async {
    try {
      final t = await _storage.read(key: _tokenKey);
      return (t == null || t.isEmpty) ? null : t;
    } catch (_) {
      return null;
    }
  }

  /// The stored upload-only token (setup-link users), for reading the data status (data_status.dart); null when none yet.
  Future<String?> ingestToken() => _readToken();

  /// Also called on a fresh install (AppState.load): Keychain items survive deleting the app.
  Future<void> deleteToken() => _deleteToken();

  Future<void> _deleteToken() async {
    try {
      await _storage.delete(key: _tokenKey);
    } catch (_) {}
  }

  /// Sign out / account deleted: forget the token, anchors and status (the next account starts from scratch).
  Future<void> clear() async {
    await _deleteToken();
    try {
      await source.reset();
      await source.reset(anchorSet: kResyncAnchorSet);
    } catch (_) {}
    final prefs = await SharedPreferences.getInstance();
    for (final k in [_statusKey, _rangeKey, _rejectedKey]) { // the Health permission (and _authTriesKey) belongs to the app, not the account
      await prefs.remove(k);
    }
    status.value = const RawSyncStatus();
  }
}
