// The app's own HealthKit sync engine (#219, docs/apple-sync-engine.md §3.1, docs/apple-sync-engine-app-contract.md).
// - Registry: assets/healthkit_types.json, the server's rawdata/hk_types.engine_json() byte for byte (both sides test it).
// - Off unless the server says so: it runs only while the server's `engine` block (GET / POST /v1/ingest/healthkit)
//   has `enabled` and `mode` shadow / app (flag apple_sync_engine). In `app` mode the engine is the source of the scores
//   and Open Wearables is stopped (its credentials stay for a rollback, contract §7).
// - A1 (1.0.8) read natively and uploaded from here. From A2 the whole round is native (ios/Runner/SyncEngine.swift),
//   because HealthKit background delivery and BGTasks wake the app without Flutter: reading (raw samples, sleep stages,
//   workouts, 15-minute buckets of cumulative types, deletions), gzip batches, anchors after 200, retries, runs[],
//   observers, BGAppRefresh / BGProcessing, resuming after unlock / when the network is back, Wi-Fi-only history.
// - This side hands over the server, the token (Keychain on the native side), the registry and the server's last engine
//   block; starts foreground rounds; keeps the mode (Open Wearables stops in `app`); starts range re-syncs; and reads the
//   native state for the sync center (per type, first-sync progress, run log). No health values pass through here.
import 'dart:convert';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'data_status.dart' show iosName;
import 'i18n.dart';
import 'resync.dart' show TypeVerdict;
import 'sync_params.dart';

const String kEngineRegistryAsset = 'assets/healthkit_types.json';

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
    this.page = const [2000, 200],
    this.flag,
    this.consent,
    this.status,
  });

  final String hk, key, kind, unit, upload, bg;
  final int order, minIos;
  final List<int> page;
  final String? flag, consent;

  /// The data-status key the sync center groups it under (null: not shown there).
  final String? status;

  bool get raw => upload.contains('raw');
  bool get stats => upload.contains('stats');

  /// Read by the engine: quantities (raw and / or 15-minute buckets), categories, workouts. Heartbeat series, ECGs and
  /// water stay with RawSync (raw_sync.dart).
  bool get engine => kind == 'quantity' || kind == 'category' || kind == 'workout';

  factory EngineType.fromJson(Map<String, Object?> j) => EngineType(
        hk: j['hk'] as String,
        key: j['key'] as String,
        kind: j['kind'] as String,
        unit: (j['unit'] as String?) ?? '',
        upload: (j['upload'] as String?) ?? 'raw',
        bg: (j['bg'] as String?) ?? 'none',
        order: (j['order'] as num?)?.toInt() ?? 50,
        minIos: (j['min_ios'] as num?)?.toInt() ?? 0,
        page: [for (final x in (j['page'] as List?) ?? const [2000, 200]) (x as num).toInt()],
        flag: j['flag'] as String?,
        consent: j['consent'] as String?,
        status: j['status'] as String?,
      );

  /// What the native engine needs of it.
  Map<String, Object?> toNative() =>
      {'hk': hk, 'kind': kind, 'unit': unit, 'upload': upload, 'bg': bg, 'order': order, 'min_ios': minIos, 'page': page};
}

/// The iOS major version this runs on (min_ios of the registry); 0 = unknown (then nothing is skipped).
int iosMajor() {
  if (kIsWeb || !Platform.isIOS) return 0;
  return int.tryParse(RegExp(r'(\d+)').firstMatch(Platform.operatingSystemVersion)?.group(1) ?? '') ?? 0;
}

/// The registry in reading order (the JSON's order is the server's: sleep and the overnight readings first, heart rate last).
List<EngineType> parseRegistry(String json) => [
      for (final t in ((jsonDecode(json) as Map)['types'] as List).cast<Map>()) EngineType.fromJson(t.cast<String, Object?>()),
    ];

/// Types whose read permission only the Open Wearables SDK asked for (not in the registry): asked in the same sheet so
/// its own request shows nothing new (one sheet, #214).
const List<String> kOwOnlyHealthTypes = [
  'HKQuantityTypeIdentifierWaistCircumference',
  'HKQuantityTypeIdentifierInsulinDelivery',
];

/// The server's `engine` block (GET /v1/ingest/healthkit, rawdata/engine.describe).
@immutable
class EngineInfo {
  const EngineInfo({this.enabled = false, this.mode = 'ow', this.accepts = const {}, this.encodings = const {}, this.types = const {},
      this.raw = const {}});
  final bool enabled;
  final String mode;
  final Set<String> accepts, encodings, types;

  /// The block as the server sent it (handed to the native engine).
  final Map<String, Object?> raw;

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
      raw: e.map((k, v) => MapEntry('$k', v)),
    );
  }
}

/// The answer of one native round.
class EngineResult {
  EngineResult({this.batches = 0, this.samples = 0, this.bytes = 0, this.types = 0, this.stop, this.mode, this.retryAfter,
      Map<String, String>? skipped, Set<String>? uploaded})
      : skipped = skipped ?? {},
        uploaded = uploaded ?? {};

  final int batches, samples, bytes, types;

  /// null = everything read; off / busy / backoff / budget / locked / offline / unauthorized / rate_limited /
  /// engine_disabled / consent_required / failed / expired …
  final String? stop;

  /// The server's mode after the round (from its answers).
  final String? mode;
  final Duration? retryAfter;

  /// Types skipped this round and why (type_paused, not_asked, wifi_only, contract …): the next round tries again.
  final Map<String, String> skipped;

  /// HealthKit identifiers with at least one accepted batch this round.
  final Set<String> uploaded;

  factory EngineResult.fromMap(Map<Object?, Object?> m) => EngineResult(
        batches: (m['batches'] as num?)?.toInt() ?? 0,
        samples: (m['samples'] as num?)?.toInt() ?? 0,
        bytes: (m['bytes'] as num?)?.toInt() ?? 0,
        types: (m['types'] as num?)?.toInt() ?? 0,
        stop: m['stop'] as String?,
        mode: m['mode'] as String?,
        retryAfter: m['retryAfter'] is num ? Duration(seconds: (m['retryAfter'] as num).ceil()) : null,
        skipped: {for (final e in ((m['skipped'] as Map?) ?? const {}).entries) '${e.key}': '${e.value}'},
        uploaded: {for (final x in (m['uploaded'] as List?) ?? const []) '$x'},
      );
}

/// One line for the sync log (English, counts only).
String engineLogLine(EngineResult r) => [
      'Sync engine: ${r.samples} items of ${r.uploaded.length}/${r.types} types in ${r.batches} batches',
      if (r.stop != null) 'stopped: ${r.stop}',
      if (r.skipped.isNotEmpty) 'skipped: ${r.skipped.values.toSet().join(', ')} (${r.skipped.length})',
    ].join('; ');

/// Per type on this phone (native state): first read done, last accepted upload, last error.
class EngineTypeState {
  const EngineTypeState({this.firstDone = false, this.okAt, this.error, this.errorAt});
  final bool firstDone;
  final DateTime? okAt, errorAt;
  final String? error;

  static DateTime? _t(Object? v) => v is num && v > 0 ? DateTime.fromMillisecondsSinceEpoch((v * 1000).round()) : null;

  factory EngineTypeState.fromMap(Map<Object?, Object?> m) => EngineTypeState(
        firstDone: m['backfilled'] == true,
        okAt: _t(m['okAt']),
        error: m['error'] as String?,
        errorAt: _t(m['errorAt']),
      );
}

/// The native engine's state for the sync center (no health values).
class EngineStatus {
  const EngineStatus({
    this.mode = 'ow',
    this.active = false,
    this.cellularHistory = false,
    this.running = false,
    this.types = const {},
    this.firstTypes = 0,
    this.firstDone = 0,
    this.log = const [],
    this.range,
    this.lastRun,
    this.lastBackgroundAt,
    this.lastStop,
  });

  final String mode;
  final bool active, cellularHistory, running;
  final Map<String, EngineTypeState> types;
  final int firstTypes, firstDone;
  final List<String> log;

  /// A range re-sync in progress: {percent, batches, error …}.
  final Map<String, Object?>? range;

  /// The last round: {trigger, result, error, started, duration_ms, batches, samples}.
  final Map<String, Object?>? lastRun;
  final DateTime? lastBackgroundAt;
  final String? lastStop;

  /// The first read of the first-sync window (every type once), 0–100.
  int get firstPercent => firstTypes == 0 ? 0 : (firstDone * 100 ~/ firstTypes);
  bool get firstSyncDone => firstTypes > 0 && firstDone >= firstTypes;

  factory EngineStatus.fromMap(Map<Object?, Object?> m) {
    final first = (m['firstSync'] as Map?) ?? const {};
    Map<String, Object?>? map(Object? v) => v is Map ? v.map((k, x) => MapEntry('$k', x)) : null;
    final lastStop = m['lastStop'] as String?;
    return EngineStatus(
      mode: '${m['mode'] ?? 'ow'}',
      active: m['active'] == true,
      cellularHistory: m['cellularHistory'] == true,
      running: m['running'] == true,
      types: {
        for (final e in ((m['types'] as Map?) ?? const {}).entries)
          if (e.value is Map) '${e.key}': EngineTypeState.fromMap(e.value as Map),
      },
      firstTypes: (first['types'] as num?)?.toInt() ?? 0,
      firstDone: (first['done'] as num?)?.toInt() ?? 0,
      log: [for (final l in (m['log'] as List?) ?? const []) '$l'],
      range: map(m['range']),
      lastRun: map(m['lastRun']),
      lastBackgroundAt: EngineTypeState._t(m['lastBackgroundAt']),
      lastStop: lastStop == null || lastStop.isEmpty ? null : lastStop,
    );
  }
}

/// "ISO time\tline" of the native run log -> "10/29 09:12:03 line" (newest last), for the sync log.
List<String> engineLogForDisplay(List<String> lines) => [
      for (final l in lines)
        () {
          final tab = l.indexOf('\t');
          final at = tab > 0 ? DateTime.tryParse(l.substring(0, tab))?.toLocal() : null;
          final msg = tab > 0 ? l.substring(tab + 1) : l;
          if (at == null) return msg;
          return '${at.month}/${at.day} ${at.toIso8601String().substring(11, 19)} $msg';
        }(),
    ];

/// What a round's stop means for people (null = nothing to say).
String? engineStopText(String? stop) => switch (stop) {
      null || 'budget' || 'busy' || 'off' => null,
      'locked' => tr('手机锁着时读不到「健康」，解锁后自动继续', 'Health cannot be read while the phone is locked; it continues once unlocked'),
      'offline' => tr('没有网络，联网后自动补传', 'No network; it catches up once online'),
      'unauthorized' || 'backoff' => tr('登录失效：打开今天页重新登录', 'Signed out: sign in on the Today page again'),
      'rate_limited' => tr('服务器请稍后再传，会自动继续', 'The server asked to wait; it continues by itself'),
      'consent_required' => tr('确认同意书后才会上传', 'Uploads start after you accept the consent'),
      'expired' => tr('后台时间用完，下次接着传', 'Background time ran out; it continues next time'),
      _ => tr('上次同步没完成（$stop），会自动重试', 'The last sync did not finish ($stop); it retries by itself'),
    };

// ------------------------------------------------------------------------------ sync center: phone vs server (A4)

/// GET /v1/ingest/sync-status (contract §6): per HealthKit type the newest sample the server has, the last round the app
/// reported, rejected requests and the pipeline. Times, counts and codes only.
class EngineServerStatus {
  const EngineServerStatus({this.mode = 'ow', this.latest = const {}, this.lastRun, this.failures, this.pipeline});
  final String mode;

  /// HealthKit identifier -> start of the newest sample (cumulative types: the newest bucket) on the server.
  final Map<String, DateTime> latest;
  final Map<String, Object?>? lastRun, failures, pipeline;

  factory EngineServerStatus.fromJson(Map<String, Object?> j) {
    Map<String, Object?>? map(Object? v) => v is Map ? v.map((k, x) => MapEntry('$k', x)) : null;
    return EngineServerStatus(
      mode: '${j['mode'] ?? 'ow'}',
      latest: {
        for (final t in (j['types'] as List?) ?? const [])
          if (t is Map && t['type'] is String && DateTime.tryParse('${t['latest_sample_at']}') != null)
            t['type'] as String: DateTime.parse('${t['latest_sample_at']}').toLocal(),
      },
      lastRun: map(j['last_run']),
      failures: map(j['failures']),
      pipeline: map(j['pipeline']),
    );
  }

  /// Rejected requests after the last success (contract §6 `failures.recovered = false`): code of the last one.
  String? get openFailure {
    final f = failures;
    if (f == null || f['recovered'] != false) return null;
    final last = f['last'];
    return last is Map ? '${last['code'] ?? last['reason'] ?? 'error'}' : 'error';
  }
}

Future<EngineServerStatus?> fetchEngineServerStatus(String serverUrl, String token, {http.Client? client}) async {
  if (serverUrl.isEmpty) return null;
  final b = Uri.parse(serverUrl);
  final c = client ?? http.Client();
  try {
    final res = await c.get(Uri(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null, path: '/v1/ingest/sync-status'),
        headers: {'Authorization': 'Bearer $token', 'Accept': 'application/json'}).timeout(const Duration(seconds: 15));
    if (res.statusCode != 200) return null;
    final j = jsonDecode(utf8.decode(res.bodyBytes));
    return j is Map ? EngineServerStatus.fromJson(j.map((k, v) => MapEntry('$k', v))) : null;
  } catch (_) {
    return null;
  } finally {
    if (client == null) c.close();
  }
}

/// A newer sample on the phone than on the server by more than this = not synced (a round may be under way).
const Duration kEngineSyncSlack = Duration(minutes: 30);

/// "Not recorded or not synced" per type, by time: nothing on the phone = not recorded (or not allowed to read); the
/// phone's newest sample clearly after the server's = not synced; else synced.
TypeVerdict engineVerdict(DateTime? phone, DateTime? server) {
  if (phone == null) return TypeVerdict.notCollected;
  if (server == null) return TypeVerdict.notSynced;
  return phone.isAfter(server.add(kEngineSyncSlack)) ? TypeVerdict.notSynced : TypeVerdict.synced;
}

class EngineTypeRow {
  const EngineTypeRow(this.type, this.phone, this.server, this.verdict);
  final EngineType type;
  final DateTime? phone, server;
  final TypeVerdict verdict;
}

/// One row per engine type the server takes (all engine types when it did not say): not synced first, then synced,
/// then not recorded; the registry's order inside.
List<EngineTypeRow> engineRows(List<EngineType> registry, Set<String> serverTypes, Map<String, DateTime> phone,
    Map<String, DateTime> server) {
  final rows = [
    for (final t in registry)
      if (t.engine && (serverTypes.isEmpty || serverTypes.contains(t.hk)))
        EngineTypeRow(t, phone[t.hk], server[t.hk], engineVerdict(phone[t.hk], server[t.hk])),
  ];
  int rank(TypeVerdict v) => switch (v) {
        TypeVerdict.notSynced => 0,
        TypeVerdict.synced => 1,
        TypeVerdict.unknown => 2,
        TypeVerdict.notCollected => 3,
      };
  final order = {for (var i = 0; i < rows.length; i++) rows[i].type.hk: i};
  rows.sort((a, b) {
    final r = rank(a.verdict).compareTo(rank(b.verdict));
    return r != 0 ? r : order[a.type.hk]!.compareTo(order[b.type.hk]!);
  });
  return rows;
}

/// Names of the registry types without a data-status label (as Apple's Health app calls them).
String? _typeName(String key) => switch (key) {
      'time_in_daylight' => tr('日照时间', 'Time in daylight'),
      'physical_effort' => tr('体能消耗', 'Physical effort'),
      'stair_ascent_speed' => tr('上楼速度', 'Stair ascent speed'),
      'stair_descent_speed' => tr('下楼速度', 'Stair descent speed'),
      'environmental_audio_exposure' => tr('环境音量', 'Environmental audio exposure'),
      'headphone_audio_exposure' => tr('耳机音量', 'Headphone audio exposure'),
      'environmental_sound_reduction' => tr('环境降噪', 'Environmental sound reduction'),
      'apple_stand_time' => tr('站立时间', 'Apple stand time'),
      'apple_exercise_time' => tr('锻炼分钟数', 'Apple exercise time'),
      'distance_walking_running' => tr('步行+跑步距离', 'Distance walking running'),
      'distance_cycling' => tr('骑行距离', 'Distance cycling'),
      'distance_swimming' => tr('游泳距离', 'Distance swimming'),
      'swimming_stroke_count' => tr('划水次数', 'Swimming stroke count'),
      'flights_climbed' => tr('已爬楼层', 'Flights climbed'),
      'basal_energy_burned' => tr('静息能量', 'Basal energy burned'),
      'walking_heart_rate_average' => tr('步行平均心率', 'Walking heart rate average'),
      'apple_walking_steadiness' => tr('步行稳定性', 'Apple walking steadiness'),
      'heart_rate_variability_rmssd' => tr('心率变异性（RMSSD）', 'Heart rate variability rmssd'),
      'body_fat_percentage' => tr('体脂率', 'Body fat percentage'),
      'lean_body_mass' => tr('去脂体重', 'Lean body mass'),
      'body_mass_index' => tr('身体质量指数', 'Body mass index'),
      'height' => tr('身高', 'Height'),
      'body_temperature' => tr('体温', 'Body temperature'),
      'blood_pressure_systolic' => tr('收缩压', 'Blood pressure systolic'),
      'blood_pressure_diastolic' => tr('舒张压', 'Blood pressure diastolic'),
      'blood_glucose' => tr('血糖', 'Blood glucose'),
      'mindful_session' => tr('正念', 'Mindful session'),
      'walking_speed' => tr('步行速度', 'Walking speed'),
      'walking_step_length' => tr('步长', 'Walking step length'),
      'walking_double_support_percentage' => tr('双支撑时间', 'Walking double support percentage'),
      'walking_asymmetry_percentage' => tr('步伐不对称', 'Walking asymmetry percentage'),
      'six_minute_walk_test_distance' => tr('六分钟步行距离', 'Six minute walk test distance'),
      'running_speed' => tr('跑步速度', 'Running speed'),
      'running_stride_length' => tr('跑步步长', 'Running stride length'),
      'running_power' => tr('跑步功率', 'Running power'),
      'running_vertical_oscillation' => tr('垂直振幅', 'Running vertical oscillation'),
      'running_ground_contact_time' => tr('触地时间', 'Running ground contact time'),
      'cycling_speed' => tr('骑行速度', 'Cycling speed'),
      'cycling_power' => tr('骑行功率', 'Cycling power'),
      'cycling_cadence' => tr('骑行踏频', 'Cycling cadence'),
      'cycling_functional_threshold_power' => tr('功能性阈值功率', 'Cycling functional threshold power'),
      'water_temperature' => tr('水温', 'Water temperature'),
      'workout_effort_score' => tr('体能训练强度', 'Workout effort score'),
      'estimated_workout_effort_score' => tr('估算体能训练强度', 'Estimated workout effort score'),
      _ => null,
    };

/// A label for people: the iOS switch name when the type has a data-status key, else its Health app name (Chinese) or
/// its server name in words.
String engineTypeLabel(EngineType t) {
  final words = t.key.replaceAll('_', ' ');
  final fallback = words.isEmpty ? t.hk : '${words[0].toUpperCase()}${words.substring(1)}';
  final named = _typeName(t.key);
  if (named != null) return named;
  return t.status == null ? fallback : iosName(t.status!, fallback);
}

/// The native side (channel watch2whoop/native, methods engine*); a fake in tests.
abstract class EngineNative {
  Future<Map<Object?, Object?>?> configure(Map<String, Object?> args);
  Future<Map<Object?, Object?>?> run(Map<String, Object?> args);
  Future<Map<Object?, Object?>?> status();
  Future<void> setCellular(bool on);
  Future<Map<Object?, Object?>?> rangeStart(int days);
  Future<void> rangeCancel();
  Future<void> rearm();
  Future<void> reset();
}

class ChannelEngineNative implements EngineNative {
  static const _ch = MethodChannel('watch2whoop/native');

  Future<Map<Object?, Object?>?> _map(String m, [Object? args]) async {
    try {
      return await _ch.invokeMapMethod<Object?, Object?>(m, args);
    } on MissingPluginException {
      return null; // Android / tests
    } on PlatformException catch (e) {
      debugPrint('$m failed: ${e.code}');
      return null;
    }
  }

  @override
  Future<Map<Object?, Object?>?> configure(Map<String, Object?> args) => _map('engineConfigure', args);
  @override
  Future<Map<Object?, Object?>?> run(Map<String, Object?> args) => _map('engineRun', args);
  @override
  Future<Map<Object?, Object?>?> status() => _map('engineStatus');
  @override
  Future<void> setCellular(bool on) => _map('engineSetCellular', {'on': on});
  @override
  Future<Map<Object?, Object?>?> rangeStart(int days) => _map('engineRangeStart', {'days': days});
  @override
  Future<void> rangeCancel() => _map('engineRangeCancel');
  @override
  Future<void> rearm() => _map('engineRearm');
  @override
  Future<void> reset() => _map('engineReset');
}

/// GET {server}/v1/ingest/healthkit: the engine block for this token (null = no answer).
Future<EngineInfo?> fetchEngineInfo(String serverUrl, String token, {http.Client? client}) async {
  if (serverUrl.isEmpty) return null;
  final b = Uri.parse(serverUrl);
  final c = client ?? http.Client();
  try {
    final res = await c.get(Uri(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null, path: '/v1/ingest/healthkit'),
        headers: {'Authorization': 'Bearer $token', 'Accept': 'application/json'}).timeout(const Duration(seconds: 15));
    if (res.statusCode != 200) return null;
    final j = jsonDecode(res.body);
    return j is Map ? EngineInfo.fromStatus(j.map((k, v) => MapEntry('$k', v))) : null;
  } catch (_) {
    return null;
  } finally {
    if (client == null) c.close();
  }
}

/// The app glue: registry from the asset, the server's engine block from RawSync's last answer, the native engine.
class SyncEngine {
  SyncEngine._();
  static final SyncEngine instance = SyncEngine._();

  EngineNative native = ChannelEngineNative();
  List<EngineType>? _registry;

  /// The last round of this app run (sync status); null before the first one.
  EngineResult? last;

  /// Last accepted upload per HealthKit type in this app run (sync_bridge.dart status).
  final Map<String, DateTime> uploadedAt = {};
  void Function(String line)? onLog;

  Future<List<EngineType>> registry() async => _registry ??= parseRegistry(await rootBundle.loadString(kEngineRegistryAsset));

  @visibleForTesting
  set registryForTest(List<EngineType> r) => _registry = r;

  /// Every HealthKit type the app reads, for the one permission sheet (RawSync.swift `rawRequestAuth` adds the heartbeat
  /// series' parent).
  Future<List<String>> authTypes() async => [
        for (final t in await registry()) t.hk,
        ...kOwOnlyHealthTypes,
      ];

  Map<String, Object?> _args(List<EngineType> reg, EngineInfo info,
          {required String serverUrl, required String token, required int daysBack, String? appVersion}) =>
      {
        'server': serverUrl,
        'token': token,
        'daysBack': daysBack,
        'appVersion': ?appVersion,
        'engine': info.raw.isEmpty
            ? {'enabled': info.enabled, 'mode': info.mode, 'accepts': info.accepts.toList(), 'types': info.types.toList()}
            : info.raw,
        'registry': [for (final t in reg) if (t.engine) t.toNative()],
      };

  /// One foreground round when the server's engine block says active; otherwise the native side learns that it is off
  /// (observers stop). Returns null when it did nothing.
  Future<EngineResult?> run({
    required String serverUrl,
    required String token,
    required Map<String, Object?>? serverStatus,
    required int daysBack,
    String? appVersion,
    String trigger = 'foreground',
  }) async {
    final info = EngineInfo.fromStatus(serverStatus);
    if (info == null || serverUrl.isEmpty) return null;
    final reg = await registry();
    final args = _args(reg, info, serverUrl: serverUrl, token: token, daysBack: daysBack, appVersion: appVersion);
    if (!info.active) {
      await native.configure(args);
      await _setMode(info.enabled ? info.mode : 'ow');
      return null;
    }
    try {
      final m = await native.run({...args, 'trigger': trigger});
      if (m == null) return null;
      final res = EngineResult.fromMap(m);
      // the round's own answers are the newest word of the server on the mode
      await _setMode(res.mode ?? info.mode);
      if (res.stop == 'busy' || res.stop == 'off' || res.stop == 'backoff') return null;
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
    }
  }

  /// A new user (no upload yet): ask the server for the engine block first, so a user in `app` mode never sets up Open
  /// Wearables. Hands it to the native side too. null = no answer.
  Future<EngineInfo?> refreshInfo({required String serverUrl, required String token, int daysBack = kFirstSyncDays, String? appVersion}) async {
    final info = await fetchEngineInfo(serverUrl, token);
    if (info == null) return null;
    await _setMode(info.enabled ? info.mode : 'ow');
    await native.configure(_args(await registry(), info, serverUrl: serverUrl, token: token, daysBack: daysBack, appVersion: appVersion));
    return info;
  }

  Future<EngineStatus?> status() async {
    final m = await native.status();
    if (m == null) return null;
    final s = EngineStatus.fromMap(m);
    if (m['enabled'] != null) await _setMode(m['enabled'] == true ? s.mode : 'ow');
    return s;
  }

  Future<void> setCellularHistory(bool on) => native.setCellular(on);

  /// A foreground round with what the native side kept (server, token, engine block): the sync center's 「立即同步」.
  Future<EngineResult?> runNow() async {
    final m = await native.run({'trigger': 'foreground'});
    if (m == null) return null;
    final res = EngineResult.fromMap(m);
    if (res.mode != null) await _setMode(res.mode!);
    if (res.stop != 'busy' && res.stop != 'off' && res.stop != 'backoff') {
      last = res;
      for (final hk in res.uploaded) {
        uploadedAt[hk] = DateTime.now();
      }
      onLog?.call(engineLogLine(res));
    }
    return res;
  }

  /// Range re-sync of the last [days] days (0 = all history), contract §8; false when the engine is off.
  Future<bool> startRange(int days) async => (await native.rangeStart(days)) != null;
  Future<void> cancelRange() => native.rangeCancel();

  /// After Open Wearables stopped (it disables background delivery of its types and cancels every BGTask request).
  Future<void> rearm() => native.rearm();

  // ------------------------------------------------------------------------------------------------ the mode

  /// The server's mode for this user (contract §7), kept across restarts; changed only by an answer of the server.
  static const _modeKey = 'w2w.sync.mode.v1';
  String? _mode;

  /// The mode known right now (after [mode] was read once): `app` = the engine is the source, Open Wearables stays off.
  bool get appMode => _mode == 'app';

  /// Called when the mode changes (HomePage: `app` stops the Open Wearables SDK, back from `app` starts it again).
  void Function(String from, String to)? onModeChange;

  Future<String> mode() async => _mode ??= (await SharedPreferences.getInstance()).getString(_modeKey) ?? 'ow';

  Future<void> _setMode(String to) async {
    final from = await mode();
    if (to == from) return;
    _mode = to;
    await (await SharedPreferences.getInstance()).setString(_modeKey, to);
    onLog?.call('Sync engine mode: $from -> $to');
    onModeChange?.call(from, to);
  }

  /// An answer of the server that only exists in the mode `app` (409 sync_engine of ow-invitation, contract §7.1).
  Future<void> serverSaysAppMode() => _setMode('app');

  @visibleForTesting
  void resetForTest() {
    _mode = null;
    last = null;
    uploadedAt.clear();
  }

  /// Sign-out / account deleted: the next account starts from scratch.
  Future<void> clear() async {
    try {
      await native.reset();
    } catch (_) {}
    last = null;
    uploadedAt.clear();
    _mode = null;
    try {
      await (await SharedPreferences.getInstance()).remove(_modeKey);
    } catch (_) {}
  }
}
