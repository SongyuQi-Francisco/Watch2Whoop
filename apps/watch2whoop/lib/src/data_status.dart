// Data status (issue #163): which Health data types the server actually received, and what the user can do about a
// type that never arrives. HealthKit never tells an app whether *read* access was granted (only whether the prompt was
// shown), so the answer comes from the server: GET {Today page}/v1/ingest/data-status (sync/data_status.py), with the
// account token or the upload-only token RawSync keeps. Per type: ok / stale / never, last day, days and records of the
// last 7 days. The native side (RawSync.swift `healthAskStatus`) says per type whether the iOS prompt was already shown:
// only then can asking again show the sheet; otherwise the user changes it in iOS Settings (the app never does).
// Pure Dart except [fetchDataStatus]'s HTTP call (test/data_status_test.dart).
import 'dart:async';
import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;

import 'i18n.dart';
import 'raw_sync.dart';
import 'sync_stats.dart';

enum DataState { ok, stale, never }

class DataTypeStatus {
  const DataTypeStatus({
    required this.key,
    required this.label,
    required this.state,
    this.channel = 'ow',
    this.lastDate,
    this.lastAt,
    this.daysSince,
    this.days = 0,
    this.count = 0,
    this.sources = const [],
    this.notes = const [],
  });

  final String key, label, channel;
  final DataState state;
  final String? lastDate;
  final DateTime? lastAt;
  final int? daysSince;
  final int days, count;
  final List<String> sources, notes;

  factory DataTypeStatus.fromJson(Map<String, Object?> j) => DataTypeStatus(
        key: '${j['key']}',
        label: '${j['label'] ?? j['key']}',
        state: DataState.values.firstWhere((s) => s.name == j['status'], orElse: () => DataState.never),
        channel: '${j['channel'] ?? 'ow'}',
        lastDate: j['last_date'] as String?,
        lastAt: DateTime.tryParse('${j['last_at'] ?? ''}')?.toLocal(),
        daysSince: (j['days_since'] as num?)?.toInt(),
        days: (j['days'] as num?)?.toInt() ?? 0,
        count: (j['count'] as num?)?.toInt() ?? 0,
        sources: [for (final s in (j['sources'] as List?) ?? const []) '$s'],
        notes: [for (final s in (j['notes'] as List?) ?? const []) '$s'],
      );

  /// A permission does not help: the app's sync cannot read it yet, or the server does not take it.
  bool get unsupported => notes.contains('sdk_unsupported') || notes.contains('flag_off');
}

class DataStatusReport {
  const DataStatusReport({required this.types, this.windowDays = 7, this.owLastSampleAt, this.appUploadAt, this.strength});

  final List<DataTypeStatus> types;
  final int windowDays;
  final DateTime? owLastSampleAt, appUploadAt;

  /// Strength-training records of the last weeks by source (#210): {weeks, days, sessions: {watch, sparky, hevy}}.
  final Map<String, Object?>? strength;

  int count(DataState s) => types.where((t) => t.state == s).length;

  factory DataStatusReport.fromJson(Map<String, Object?> j) {
    final ch = (j['channels'] as Map?) ?? const {};
    DateTime? d(Object? v) => DateTime.tryParse('${v ?? ''}')?.toLocal();
    return DataStatusReport(
      types: [
        for (final t in (j['types'] as List?) ?? const [])
          if (t is Map) DataTypeStatus.fromJson(t.map((k, v) => MapEntry('$k', v))),
      ],
      windowDays: (j['window_days'] as num?)?.toInt() ?? 7,
      owLastSampleAt: d(ch['ow_last_sample_at']),
      appUploadAt: d(ch['app_upload_at']),
      strength: j['strength'] is Map ? (j['strength'] as Map).map((k, v) => MapEntry('$k', v)) : null,
    );
  }
}

/// What the server's data says overall (sync investigation 2026-10-26: "no watch data" and "sync broken" looked the
/// same on the sync page).
enum SyncVerdict {
  ok,

  /// Nothing has ever arrived.
  empty,

  /// Data used to arrive, nothing is current any more: the sync is broken (or the phone was not opened for long).
  broken,

  /// The iPhone's own types arrive (steps, energy), none of the watch's (heart rate, HRV, resting heart rate, sleep):
  /// no paired / worn watch, or its types are not allowed in Health. Not a sync problem.
  noWatch,
}

const Set<String> _watchKinds = {'heart_rate', 'hrv', 'resting_heart_rate', 'sleep'};
const Set<String> _phoneKinds = {'steps', 'active_energy'};

SyncVerdict syncVerdict(DataStatusReport r) {
  final relevant = r.types.where((t) => _watchKinds.contains(t.key) || _phoneKinds.contains(t.key)).toList();
  if (relevant.isEmpty || relevant.every((t) => t.state == DataState.never)) return SyncVerdict.empty;
  if (relevant.every((t) => t.state != DataState.ok)) return SyncVerdict.broken;
  final watch = relevant.where((t) => _watchKinds.contains(t.key));
  final phone = relevant.where((t) => _phoneKinds.contains(t.key));
  if (watch.isNotEmpty && watch.every((t) => t.state == DataState.never) && phone.any((t) => t.state != DataState.never)) {
    return SyncVerdict.noWatch;
  }
  return SyncVerdict.ok;
}

String? syncVerdictText(SyncVerdict v) => switch (v) {
      SyncVerdict.ok => null,
      SyncVerdict.empty => tr('服务器还没收到任何健康数据', 'The server has not received any health data yet'),
      SyncVerdict.broken => tr('同步断了：最近没有收到新数据。打开 app 等一轮同步，还不行就「重新申请健康权限」或重新连接同步',
          'Sync is broken: nothing new arrived lately. Keep the app open for a round; if that does not help, ask for Health access again or reconnect sync'),
      SyncVerdict.noWatch => tr('只收到 iPhone 的数据，没有手表数据：确认 Apple Watch 已配对并戴着，在「健康」里允许心率和睡眠。这不是同步故障',
          'Only iPhone data arrives, nothing from a watch: check the Apple Watch is paired and worn, and heart rate and sleep are allowed in Health. This is not a sync fault'),
    };

enum DataStatusProblem { noToken, unauthorized, oldServer, failed }

class DataStatusResult {
  const DataStatusResult.ok(DataStatusReport this.report) : problem = null;
  const DataStatusResult.problem(DataStatusProblem this.problem) : report = null;
  final DataStatusReport? report;
  final DataStatusProblem? problem;

  String? get problemText => switch (problem) {
        DataStatusProblem.noToken => tr('打开今天页登录后显示', 'Shown once you sign in on the Today page'),
        DataStatusProblem.unauthorized => tr('登录失效，打开今天页后重试', 'Signed out; open the Today page and try again'),
        DataStatusProblem.oldServer => tr('服务器还不支持数据状态', 'The server does not support data status yet'),
        DataStatusProblem.failed => tr('读取失败，下拉刷新重试', 'Could not load; pull down to retry'),
        null => null,
      };
}

/// GET {serverUrl}/v1/ingest/data-status (labels in the app's language).
Future<DataStatusResult> fetchDataStatus({required String serverUrl, required String? token, http.Client? client}) async {
  if (token == null || token.isEmpty) return const DataStatusResult.problem(DataStatusProblem.noToken);
  if (serverUrl.isEmpty) return const DataStatusResult.problem(DataStatusProblem.failed);
  final b = Uri.parse(serverUrl);
  final uri = Uri(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null, path: '/v1/ingest/data-status');
  final c = client ?? http.Client();
  try {
    final res = await c.get(uri, headers: {
      'Authorization': 'Bearer $token',
      'Accept': 'application/json',
      'X-Body-Lang': currentLang,
    }).timeout(const Duration(seconds: 20));
    if (res.statusCode == 401) return const DataStatusResult.problem(DataStatusProblem.unauthorized);
    if (res.statusCode == 404 || res.statusCode == 405) return const DataStatusResult.problem(DataStatusProblem.oldServer);
    if (res.statusCode != 200) return const DataStatusResult.problem(DataStatusProblem.failed);
    final j = jsonDecode(utf8.decode(res.bodyBytes));
    if (j is! Map) return const DataStatusResult.problem(DataStatusProblem.failed);
    return DataStatusResult.ok(DataStatusReport.fromJson(j.map((k, v) => MapEntry('$k', v))));
  } catch (_) {
    return const DataStatusResult.problem(DataStatusProblem.failed);
  } finally {
    if (client == null) c.close();
  }
}

// ------------------------------------------------------------------------------ the iOS prompt per type

/// Types the app asks for (the OW sheet + the RawSync sheet). Wrist temperature is not among them: SDK 0.15 cannot read it.
const Set<String> kAskableKeys = {
  'heart_rate', 'hrv', 'resting_heart_rate', 'sleep', 'steps', 'active_energy', 'workouts', 'weight', 'blood_oxygen',
  'respiratory_rate', 'vo2max', 'heartbeats', 'ecg', 'water',
};

/// key -> the iOS prompt was already shown for it (RawSync.swift healthAskStatus); null when HealthKit cannot say.
Future<Map<String, bool>?> healthAskStatus() async {
  try {
    final m = await const MethodChannel('watch2whoop/native').invokeMapMethod<String, bool>('healthAskStatus');
    return m;
  } on MissingPluginException {
    return null;
  } on PlatformException {
    return null;
  }
}

/// Asking again shows the iOS sheet only for types never asked; true when at least one of the app's types is such.
bool askWouldShowPrompt(Map<String, bool>? asked) =>
    asked != null && kAskableKeys.any((k) => asked[k] == false);

enum DataFix { none, unsupported, askAgain, openSettings }

/// What to offer for one type: nothing when data arrives; "ask again" when the iOS prompt never covered it; otherwise
/// iOS Settings (the app cannot see or change a decision already made).
DataFix fixFor(DataTypeStatus t, Map<String, bool>? asked) {
  if (t.state == DataState.ok) return DataFix.none;
  if (t.unsupported || !kAskableKeys.contains(t.key)) return DataFix.unsupported;
  if (asked != null && asked[t.key] == false) return DataFix.askAgain;
  return DataFix.openSettings;
}

/// The switch's name in iOS Settings → Health → Data Access & Devices → Watch2Whoop.
String iosName(String key, String fallback) => switch (key) {
      'heart_rate' => tr('心率', 'Heart Rate'),
      'hrv' => tr('心率变异性', 'Heart Rate Variability'),
      'resting_heart_rate' => tr('静息心率', 'Resting Heart Rate'),
      'sleep' => tr('睡眠', 'Sleep'),
      'steps' => tr('步数', 'Steps'),
      'active_energy' => tr('活动能量', 'Active Energy'),
      'workouts' => tr('体能训练', 'Workouts'),
      'weight' => tr('体重', 'Weight'),
      'blood_oxygen' => tr('血氧', 'Blood Oxygen'),
      'respiratory_rate' => tr('呼吸速率', 'Respiratory Rate'),
      'wrist_temperature' => tr('睡眠时手腕温度', 'Sleeping Wrist Temperature'),
      'vo2max' => tr('心肺适能', 'Cardio Fitness'),
      'heartbeats' => tr('心跳序列', 'Heartbeat Series'),
      'ecg' => tr('心电图', 'Electrocardiograms (ECG)'),
      'water' => tr('水', 'Water'),
      _ => fallback,
    };

/// Steps in iOS Settings; [name] = the switch to turn on (null = all of them).
List<String> settingsSteps([String? name]) => [
      tr('打开 iPhone 的「设置」', 'Open Settings on the iPhone'),
      tr('点「健康」（新版 iOS：「App」→「健康」）', 'Tap Health (newer iOS: Apps → Health)'),
      tr('点「数据访问与设备」→「Watch2Whoop」', 'Tap Data Access & Devices → Watch2Whoop'),
      name == null
          ? tr('打开要同步的项目，或者全部打开', 'Turn on what should sync, or turn them all on')
          : tr('打开「$name」，或者全部打开', 'Turn on $name, or turn them all on'),
      tr('回到这里点「立即同步」', 'Come back here and tap Sync now'),
    ];

String get settingsAltPath =>
    tr('也可以：「健康」app → 右上角头像 →「App」→「Watch2Whoop」', 'Or: Health app → profile picture (top right) → Apps → Watch2Whoop');

/// Likely reasons for a type that is not ok.
List<String> reasonsFor(DataTypeStatus t) {
  if (t.state == DataState.ok) return const [];
  final notes = [
    for (final n in t.notes)
      ?switch (n) {
        'sdk_unsupported' => tr('app 的同步还读不了这一项，要用快捷指令或导入健康历史',
            "The app's sync can't read this yet; use the Shortcut or import your Health history"),
        'app_direct' => tr('这一项由 app 打开时自己读取：解锁手机、打开一次 app', 'The app reads this itself when it opens: unlock the phone and open the app'),
        'consent' => tr('要先在今天页确认新版同意书', 'Accept the new consent on the Today page first'),
        'on_demand' => tr('只有在手表上测过心电图才会有', 'Only there after you record an ECG on the watch'),
        'needs_device' => tr('要有智能体重秤，或在「健康」里手动记录', 'Needs a smart scale, or log it by hand in Health'),
        'outdoor' => tr('戴手表户外步行、跑步或徒步后才会估算', 'Estimated only after outdoor walks, runs or hikes with the watch'),
        'flag_off' => tr('这台服务器还没开饮水功能', "Water isn't switched on on this server yet"),
        _ => null,
      },
  ];
  if (t.unsupported) return notes;
  final general = t.state == DataState.never
      ? [
          tr('健康权限里没有打开这一项', 'Not switched on in the Health permissions'),
          tr('手表或 iPhone 没有记录这类数据', "Your watch or iPhone doesn't record this"),
          tr('第一次同步还没传完', "The first sync hasn't finished"),
        ]
      : [
          tr('健康权限被关掉了', 'The Health permission was switched off'),
          tr('最近没戴手表，或很久没打开 app', 'Watch not worn lately, or the app not opened for a long time'),
          tr('后台同步被 iOS 暂停：打开一次 app', 'iOS paused the background sync: open the app once'),
        ];
  return [...general, ...notes];
}

String stateLabel(DataState s) => switch (s) {
      DataState.ok => tr('正常', 'OK'),
      DataState.stale => tr('很久没收到', 'Not received lately'),
      DataState.never => tr('从没收到', 'Never received'),
    };

String sinceLabel(DataTypeStatus t) => switch (t.daysSince) {
      null => '—',
      0 => tr('今天', 'Today'),
      1 => tr('昨天', 'Yesterday'),
      final n => tr('$n 天前', '$n days ago'),
    };

// ------------------------------------------------------------------------------ this phone's last sync

class LastSync {
  const LastSync(this.at, this.ok, [this.error]);
  final DateTime? at;
  final bool ok;
  final String? error;
}

/// The latest sync event on this phone: an OW upload (ok / failed) or the app's direct upload pass.
LastSync lastSyncOf(SyncStats s, RawSyncStatus raw) {
  final events = <LastSync>[
    if (s.lastUploadAt != null) LastSync(s.lastUploadAt, true),
    if (s.lastErrorAt != null) LastSync(s.lastErrorAt, false, s.lastError),
    if (raw.lastUpload != null) LastSync(raw.lastUpload, true),
    if (raw.lastRun != null && raw.problem != null) LastSync(raw.lastRun, false, raw.problem),
  ];
  if (events.isEmpty) return const LastSync(null, false);
  events.sort((a, b) => b.at!.compareTo(a.at!));
  return events.first;
}
