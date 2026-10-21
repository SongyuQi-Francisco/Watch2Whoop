// Beat-to-beat HRV series and ECG recordings straight from HealthKit to Body Engine (POST /v1/ingest/healthkit), not through
// Open Wearables: OW SDK 0.15 syncs neither type. The native side (ios/Runner/RawSync.swift) reads one page after the stored
// HealthKit anchor; this file uploads it and only then lets the native side store the new anchor, so nothing is lost when an
// upload fails and nothing is sent twice once it succeeded (the server also skips UUIDs it already has).
// - Runs after each sync-on-open round (home_page.dart), in the foreground: HealthKit cannot be read while the phone is locked.
// - Range: the same choice as the OW sync (health_range.dart). Going from 90 days to "All history" resets the anchors once;
//   the samples already on the server are skipped there by UUID.
// - Token: the self-service account token (Keychain), or for setup-link users an upload-only token the logged-in Today page
//   mints (POST /v1/web/ingest-token), also kept in the Keychain.
// The runner is pure Dart with an injected source / uploader (test/raw_sync_test.dart).
import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'health_range.dart';
import 'i18n.dart';

const List<String> kRawKinds = ['heartbeat', 'ecg'];

/// Records per HealthKit page: a reading is ~1 minute (~50 beats, ~1 KB as JSON), an ECG ~40 KB as base64 int16.
const Map<String, int> kRawPageSize = {'heartbeat': 200, 'ecg': 8};

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

abstract class RawSource {
  Future<bool> requestAuth();
  Future<RawPage> read(String kind, int daysBack, int limit);
  Future<void> commit(String kind, String anchor);
  Future<void> reset();
}

class NativeRawSource implements RawSource {
  static const _ch = MethodChannel('watch2whoop/native');

  @override
  Future<bool> requestAuth() async => (await _ch.invokeMethod<bool>('rawRequestAuth')) ?? false;

  @override
  Future<RawPage> read(String kind, int daysBack, int limit) async => RawPage.fromMap(
      (await _ch.invokeMapMethod<Object?, Object?>('rawRead', {'kind': kind, 'daysBack': daysBack, 'limit': limit})) ?? {});

  @override
  Future<void> commit(String kind, String anchor) => _ch.invokeMethod('rawCommit', {'kind': kind, 'anchor': anchor});

  @override
  Future<void> reset() => _ch.invokeMethod('rawReset');
}

enum RawUpload { ok, unauthorized, consent, tooLarge, failed }

class RawUploadResult {
  const RawUploadResult(this.outcome, [this.body]);
  final RawUpload outcome;
  final Map<String, Object?>? body;
}

/// POST {server}/v1/ingest/healthkit with a Bearer token.
class RawUploader {
  RawUploader(this.baseUrl, {this.client, this.timeout = const Duration(seconds: 60)});
  final String baseUrl;
  final http.Client? client;
  final Duration timeout;

  Uri get _uri {
    final b = Uri.parse(baseUrl);
    return Uri(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null, path: '/v1/ingest/healthkit');
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
      return const RawUploadResult(RawUpload.failed);
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

/// The request body for one page (the field names are what rawdata/api.py IngestIn expects).
Map<String, Object?> buildBatch(String kind, RawPage page, {required String batchId, String? device, String? appVersion}) => {
      'batch_id': batchId,
      'device': ?device,
      'app_version': ?appVersion,
      kind == 'ecg' ? 'ecgs' : 'heartbeats': page.items,
      'deleted': page.deleted,
    };

class RawSyncResult {
  RawSyncResult();
  int heartbeats = 0, ecgs = 0, deleted = 0, batches = 0;

  /// null = everything read; unauthorized / consent / failed / budget
  String? stop;
  Map<String, Object?>? serverStatus;
}

class RawSyncRunner {
  RawSyncRunner({
    required this.source,
    required this.upload,
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
  final String? device, appVersion;
  final int maxBatches, maxBytes;
  final Duration budget;
  final DateTime Function() _now;
  final String Function() _batchId;

  Future<RawSyncResult> run(int daysBack) async {
    final out = RawSyncResult();
    final t0 = _now();
    for (final kind in kRawKinds) {
      var limit = kRawPageSize[kind]!;
      while (true) {
        if (out.batches >= maxBatches || _now().difference(t0) > budget) {
          out.stop = 'budget'; // the rest next time (the anchor is where the last accepted page ended)
          return out;
        }
        final page = await source.read(kind, daysBack, limit);
        if (page.isEmpty) {
          // nothing new (or only samples the reader drops, e.g. a series with < 2 beats)
          if (page.anchor != null) await source.commit(kind, page.anchor!);
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
            if (page.anchor != null) await source.commit(kind, page.anchor!);
            final b = res.body ?? const {};
            out.heartbeats += (b['heartbeats'] as num?)?.toInt() ?? 0;
            out.ecgs += (b['ecgs'] as num?)?.toInt() ?? 0;
            out.deleted += (b['deleted'] as num?)?.toInt() ?? 0;
            if (b['status'] is Map) out.serverStatus = (b['status'] as Map).map((k, v) => MapEntry('$k', v));
          case RawUpload.tooLarge when limit > 1:
            limit = max(1, limit ~/ 2);
            continue;
          case RawUpload.unauthorized:
            out.stop = 'unauthorized';
            return out;
          case RawUpload.consent:
            out.stop = 'consent';
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
  const RawSyncStatus({this.heartbeats, this.ecgs, this.lastUpload, this.lastRun, this.stop});
  final int? heartbeats, ecgs;
  final DateTime? lastUpload, lastRun;
  final String? stop;

  String? get problem => switch (stop) {
        'consent' => tr('在今天页确认新版同意书后开始上传', 'Starts after you accept the new consent on the Today page'),
        'unauthorized' => tr('登录失效，打开今天页后重试', 'Signed out; open the Today page to retry'),
        'failed' => tr('上次上传失败，下次打开 app 重试', 'Last upload failed; retries next time you open the app'),
        _ => null,
      };

  Map<String, Object?> toJson() => {
        'heartbeats': heartbeats,
        'ecgs': ecgs,
        'last_upload': lastUpload?.toIso8601String(),
        'last_run': lastRun?.toIso8601String(),
        'stop': stop,
      };

  factory RawSyncStatus.fromJson(Map<String, Object?> j) => RawSyncStatus(
        heartbeats: (j['heartbeats'] as num?)?.toInt(),
        ecgs: (j['ecgs'] as num?)?.toInt(),
        lastUpload: DateTime.tryParse('${j['last_upload'] ?? ''}')?.toLocal(),
        lastRun: DateTime.tryParse('${j['last_run'] ?? ''}')?.toLocal(),
        stop: j['stop'] as String?,
      );
}

/// Range the anchors were read with: "All history" after 90 days needs fresh anchors (they only see newer samples).
bool needsAnchorReset(int? previousDaysBack, int daysBack) =>
    previousDaysBack != null && !isAllHistory(previousDaysBack) && isAllHistory(daysBack);

class RawSync {
  RawSync._();
  static final RawSync instance = RawSync._();

  static const _statusKey = 'w2w.raw.status.v1';
  static const _rangeKey = 'w2w.raw.range.v1';
  static const _authKey = 'w2w.raw.authAsked.v1';
  static const _tokenKey = 'w2w.ingest.token.v1';
  static const _storage = FlutterSecureStorage(
    iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device),
  );

  final ValueNotifier<RawSyncStatus> status = ValueNotifier(const RawSyncStatus());
  RawSource source = NativeRawSource();
  bool _running = false;

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final s = prefs.getString(_statusKey);
    if (s != null) {
      try {
        status.value = RawSyncStatus.fromJson((jsonDecode(s) as Map).map((k, v) => MapEntry('$k', v)));
      } catch (_) {}
    }
  }

  /// Asks HealthKit for the two read permissions (the second, small sheet after the OW one). Remembered, so people who
  /// granted Health before this version see it once on the next open. Remembered before the native call: if HealthKit
  /// aborts the app (as build 1.0.0+4 did), the automatic ask is not repeated on every open; the permissions step still
  /// asks on demand.
  Future<void> requestAuth() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(_authKey, true);
      await source.requestAuth();
    } catch (e) {
      debugPrint('raw auth failed: $e');
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
  }) async {
    if (_running || serverUrl.isEmpty) return null;
    _running = true;
    try {
      final prefs = await SharedPreferences.getInstance();
      if (prefs.getBool(_authKey) != true) await requestAuth();
      var token = accountToken;
      final ingest = token == null;
      if (ingest) {
        token = await _readToken();
        if (token == null) {
          token = await pageToken();
          if (token == null) return null; // page not logged in yet: next time
          await _storage.write(key: _tokenKey, value: token);
        }
      }
      if (needsAnchorReset(prefs.getInt(_rangeKey), daysBack)) await source.reset();
      await prefs.setInt(_rangeKey, daysBack);
      final uploader = RawUploader(serverUrl);
      final res = await RawSyncRunner(
        source: source,
        upload: (b) => uploader.upload(token!, b),
        device: 'iPhone',
        appVersion: appVersion,
      ).run(daysBack);
      if (res.stop == 'unauthorized' && ingest) await _deleteToken(); // a fresh one from the page next time
      final s = res.serverStatus;
      final old = status.value;
      status.value = RawSyncStatus(
        heartbeats: (s?['heartbeats'] as num?)?.toInt() ?? old.heartbeats,
        ecgs: (s?['ecgs'] as num?)?.toInt() ?? old.ecgs,
        lastUpload: res.batches > 0 && res.stop != 'failed' && res.stop != 'consent' && res.stop != 'unauthorized'
            ? DateTime.now()
            : old.lastUpload,
        lastRun: DateTime.now(),
        stop: res.stop == 'budget' ? null : res.stop,
      );
      await prefs.setString(_statusKey, jsonEncode(status.value.toJson()));
      return res;
    } catch (e) {
      debugPrint('raw sync failed: $e');
      return null;
    } finally {
      _running = false;
    }
  }

  Future<String?> _readToken() async {
    try {
      final t = await _storage.read(key: _tokenKey);
      return (t == null || t.isEmpty) ? null : t;
    } catch (_) {
      return null;
    }
  }

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
    } catch (_) {}
    final prefs = await SharedPreferences.getInstance();
    for (final k in [_statusKey, _rangeKey]) { // the Health permission belongs to the app, not the account
      await prefs.remove(k);
    }
    status.value = const RawSyncStatus();
  }
}
