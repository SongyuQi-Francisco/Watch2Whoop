// Phone sync for the Today page (P0 2026-10-26: sync must be reliable and visible).
//
// 1. The page's protocol (docs/sync-onboarding-app-requirements.md, sections 3–5; the page side is web/static/sync-status.js):
//   window.Watch2WhoopFeatures.{syncConnect, syncNow, appSettings, healthSettings} = true   (declared by the app)
//   watch2whoop://sync-connect?ow=&code=   one tap: connect (the code, or the account's own) + Health + first upload
//   watch2whoop://sync-now                 one round now, without leaving the page
//   watch2whoop://app-settings             this app's page in iOS Settings (Background App Refresh, Health)
//   watch2whoop://health-settings          the Health sheet when iOS still has types to ask, else iOS Settings
//   progress: window.BodySyncStatus.onApp({phase: permission|connecting|uploading|done|error, percent, error}); the last
//   one is sent again after the page reloads ([appProgressJs]).
//   The phone's state goes to the server after every round (PUT /v1/ingest/sync-device, [buildDeviceReport]).
//
// 2. The same steps as promises, for pages that want an answer (and the status the app knows):
//
//   window.Watch2WhoopFeatures.sync = {v: 1, enableSync: true, syncNow: true, syncStatus: true}   (declared by the app)
//   await window.Watch2WhoopSync.enableSync()  -> {ok, status, error?}   Health permission + silent connect + first sync
//   await window.Watch2WhoopSync.syncNow()     -> {ok, status, error?}   one upload round now (no 3-minute throttle)
//   await window.Watch2WhoopSync.syncStatus()  -> status                 what the phone knows, see [buildSyncStatus]
//   window 'watch2whoop:sync' events, detail = {step, message, sent?, types?}: progress while enableSync / syncNow run
//
// The promises always resolve (never reject); `ok: false` comes with `error: {code, message, action}`. Every message is
// already in the app's language, short enough for one line. Nothing here contains health values: times, counts, codes.
// Page -> app: `sync:<enable|now|status> {"id": "..."}` on the Watch2Whoop JS channel; app -> page:
// `window.Watch2WhoopSync._reply(id, result)` and `window.Watch2WhoopSync._progress(event)`.
// Pure functions + [SyncBridge] with injected steps (test/sync_bridge_test.dart); HomePage wires the real ones.
import 'dart:async';
import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;

import 'i18n.dart';
import 'raw_sync.dart';
import 'sync_stats.dart';

const String kSyncPrefix = 'sync:';
const Set<String> kSyncVerbs = {'enable', 'now', 'status'};

/// JS that defines `window.Watch2WhoopSync` (once per page) and declares the feature. Runs after every page load.
const String kSyncBridgeJs = '''
(function(){
var W=window.Watch2WhoopSync;
if(!W||W.v!==1){var n=0,w={};
function call(verb){return new Promise(function(res){var id='s'+(++n)+'_'+Date.now();w[id]=res;
try{Watch2Whoop.postMessage('sync:'+verb+' '+JSON.stringify({id:id}))}catch(e){delete w[id];res({ok:false,error:{code:'unsupported',message:String(e)}})}})}
W=window.Watch2WhoopSync={v:1,
enableSync:function(){return call('enable')},
syncNow:function(){return call('now')},
syncStatus:function(){return call('status').then(function(r){return r&&r.status?r.status:r})},
_reply:function(id,r){var f=w[id];if(f){delete w[id];f(r)}},
_progress:function(e){try{window.dispatchEvent(new CustomEvent('watch2whoop:sync',{detail:e}))}catch(x){}}};}
window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},{sync:{v:1,enableSync:true,syncNow:true,syncStatus:true}});
})();''';

/// One `sync:*` message from the page: the verb and its reply id, or null.
({String verb, String id})? parseSyncMessage(String message) {
  if (!message.startsWith(kSyncPrefix)) return null;
  final rest = message.substring(kSyncPrefix.length);
  final sp = rest.indexOf(' ');
  final verb = sp < 0 ? rest : rest.substring(0, sp);
  if (!kSyncVerbs.contains(verb) || sp < 0) return null;
  try {
    final j = jsonDecode(rest.substring(sp + 1));
    final id = j is Map ? j['id'] : null;
    if (id is! String || id.isEmpty || id.length > 64) return null;
    return (verb: verb, id: id);
  } catch (_) {
    return null;
  }
}

String syncReplyJs(String id, Map<String, Object?> result) =>
    'window.Watch2WhoopSync&&window.Watch2WhoopSync._reply(${jsonEncode(id)},${jsonEncode(result)});';

String syncProgressJs(Map<String, Object?> event) =>
    'window.Watch2WhoopSync&&window.Watch2WhoopSync._progress(${jsonEncode(event)});';

/// JS setting `window.Watch2WhoopFeatures.net` (the page's prefetch decision, app.js prefetchNetOk), or null when the
/// network kind is not known.
String? netFeatureJs(String? net) {
  const known = {'wifi', 'cellular', 'wired', 'other', 'constrained', 'offline'};
  if (net == null || !known.contains(net)) return null;
  return "window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},{net:'$net'});";
}

// ------------------------------------------------------------------------------------------------------- the phone

/// What may hold sync back right now (AppDelegate.swift DeviceState). Unknown fields stay null (Android, older builds).
class DeviceSyncState {
  const DeviceSyncState({this.net, this.backgroundRefresh, this.lowPower, this.protectedData});

  /// wifi / cellular / wired / other / constrained (Low Data Mode) / offline / unknown
  final String? net;

  /// on / off / restricted / unknown (Background App Refresh for this app)
  final String? backgroundRefresh;
  final bool? lowPower;

  /// false = the phone is locked: HealthKit cannot be read now.
  final bool? protectedData;

  factory DeviceSyncState.fromMap(Map<Object?, Object?>? m) => DeviceSyncState(
        net: m?['net'] as String?,
        backgroundRefresh: m?['backgroundRefresh'] as String?,
        lowPower: m?['lowPower'] as bool?,
        protectedData: m?['protectedData'] as bool?,
      );

  static const _ch = MethodChannel('watch2whoop/native');

  static Future<DeviceSyncState> read() async {
    try {
      return DeviceSyncState.fromMap(await _ch.invokeMapMethod<Object?, Object?>('deviceState'));
    } catch (_) {
      return const DeviceSyncState();
    }
  }
}

/// Everything [buildSyncStatus] looks at.
class SyncStatusInput {
  const SyncStatusInput({
    required this.supported,
    required this.signedIn,
    required this.connected,
    required this.healthAsked,
    this.healthNeedsAsk = false,
    this.extraNeedsAsk = false,
    this.running = false,
    this.firstSyncDone,
    required this.ow,
    required this.raw,
    this.device = const DeviceSyncState(),
    this.engineAt = const {},
    this.engineStop,
    required this.now,
  });

  /// The app's own sync engine (#219, only while the server turns it on): last accepted upload per HealthKit type of
  /// this app run, and why its last pass stopped.
  final Map<String, DateTime> engineAt;
  final String? engineStop;

  /// This phone can sync Health at all (iOS; not the Android build yet).
  final bool supported;

  /// Signed in with an account (Bearer token) — silent reconnects need it.
  final bool signedIn;

  /// The background sync (Open Wearables SDK) has a session.
  final bool connected;

  /// The Health permission sheet was shown (iOS never says what was allowed).
  final bool healthAsked;

  /// iOS says the sheet is still needed for a core type (kCoreHealthTypes: heart rate, sleep, steps …).
  final bool healthNeedsAsk;

  /// iOS says the sheet is still needed only for other types the app reads (beat-to-beat, ECG, stand time …): a warning.
  final bool extraNeedsAsk;
  final bool running;
  final bool? firstSyncDone;
  final SyncStats ow;
  final RawSyncStatus raw;
  final DeviceSyncState device;
  final DateTime now;
}

/// A reason sync does not (fully) work, for people: `code` for the page's logic, `message` in the app's language,
/// `action` = what fixes it: enable (call enableSync), settings (iOS Settings of this app, `watch2whoop://settings-app`),
/// consent (the page's consent sheet), open_app (bring the app to the front / unlock), retry, sync_page
/// (`watch2whoop://sync`), none.
class SyncProblem {
  const SyncProblem(this.code, this.severity, this.message, {this.action = 'none', this.at});
  final String code;

  /// error = no data gets through; warning = slower or partly; info = nothing to do.
  final String severity;
  final String message;
  final String action;
  final DateTime? at;

  Map<String, Object?> toJson() =>
      {'code': code, 'severity': severity, 'message': message, 'action': action, 'at': ?at?.toUtc().toIso8601String()};
}

/// How long a notice from the SDK log (locked phone, network lost, background time used up) stays relevant.
const Duration kNoticeFresh = Duration(hours: 12);

/// No upload for this long while everything looks fine: say so (the watch may be off the wrist, or iOS held sync back).
const Duration kStaleAfter = Duration(hours: 26);

DateTime? _latest(Iterable<DateTime?> ts) {
  DateTime? best;
  for (final t in ts) {
    if (t != null && (best == null || t.isAfter(best))) best = t;
  }
  return best;
}

/// The reasons, most important first.
List<SyncProblem> syncProblems(SyncStatusInput s) {
  final out = <SyncProblem>[];
  if (!s.supported) {
    return [
      SyncProblem('unsupported', 'info', tr('这台手机暂时不能同步健康数据', 'This phone cannot sync health data yet')),
    ];
  }
  final raw = s.raw, ow = s.ow, d = s.device;
  bool fresh(DateTime? t) => t != null && s.now.difference(t) < kNoticeFresh;

  if (!s.connected) {
    out.add(SyncProblem('not_connected', 'error',
        s.signedIn
            ? tr('同步还没开启', 'Sync is not turned on yet')
            : tr('同步还没连上：要用设置链接里的邀请码连接', 'Sync is not connected: it needs the invitation code from your setup link'),
        action: s.signedIn ? 'enable' : 'sync_page'));
  }
  if (!s.healthAsked || s.healthNeedsAsk) {
    out.add(SyncProblem('permission', 'error',
        tr('还没允许读取「健康」数据', 'Reading Apple Health is not allowed yet'),
        action: 'enable'));
  } else if (s.extraNeedsAsk || raw.stop == 'not_asked') {
    // Only some extra types (beat-to-beat, ECG, water, stand time …) were never asked: the core types sync. 1.0.8 called
    // this "not allowed" (health_denied) when the second sheet had not come up (#219); it is a warning, not a denial.
    out.add(SyncProblem('permission_extra', 'warning',
        tr('还有几项「健康」数据没问过读取权限', 'Some Health types have not been asked for yet'),
        action: 'enable'));
  }
  if (s.connected && ow.authProblem) {
    out.add(SyncProblem('auth', 'error',
        s.signedIn
            ? tr('同步服务器不再接受这台手机（登录失效），点一下重新连接', 'The sync server no longer accepts this phone (signed out); tap to reconnect')
            : tr('同步登录失效，要用新的邀请码重新连接', 'Sync was signed out; reconnect with a new invitation code'),
        action: s.signedIn ? 'enable' : 'sync_page', at: ow.lastErrorAt));
  }
  if (raw.stop == 'unauthorized' || raw.accountRejected) {
    out.add(SyncProblem('auth', raw.stop == 'unauthorized' ? 'error' : 'warning',
        tr('app 的登录已失效：请在「我」里退出后重新登录', 'The app was signed out: sign out under Me and sign in again'),
        action: 'none', at: raw.lastRun));
  }
  if (raw.stop == 'consent') {
    out.add(SyncProblem('consent', 'warning',
        tr('确认新版同意书后，逐拍心率和心电图才会上传', 'Beat-to-beat heart rate and ECGs upload after you accept the new consent'),
        action: 'consent', at: raw.lastRun));
  }
  if (d.net == 'offline' || raw.stop == 'offline' || (ow.lastNotice == SyncNotice.offline && fresh(ow.lastNoticeAt))) {
    out.add(SyncProblem('network', 'warning',
        d.net == 'offline'
            ? tr('现在没有网络，连上后会自动补传', 'No network right now; it catches up once you are online')
            : tr('上次同步时网络不通或很慢，下次会自动重试', 'The network was down or slow last time; it retries by itself'),
        action: 'retry', at: raw.stop == 'offline' ? raw.lastRun : ow.lastNoticeAt));
  }
  if (raw.stop == 'failed' || (ow.lastError != null && fresh(ow.lastErrorAt) && !ow.authProblem &&
      (ow.lastUploadAt == null || ow.lastErrorAt!.isAfter(ow.lastUploadAt!)))) {
    out.add(SyncProblem('server', 'warning',
        tr('上次上传没成功（服务器没接收），会自动重试', 'The last upload did not go through (the server refused it); it retries by itself'),
        action: 'retry', at: raw.stop == 'failed' ? raw.lastRun : ow.lastErrorAt));
  }
  if (raw.stop == 'healthkit') {
    out.add(SyncProblem('healthkit', 'warning',
        tr('读取「健康」出错，下次打开 app 重试', 'Reading Apple Health failed; it retries next time you open the app'),
        action: 'retry', at: raw.lastRun));
  }
  if (d.protectedData == false || (ow.lastNotice == SyncNotice.locked && fresh(ow.lastNoticeAt))) {
    out.add(SyncProblem('locked', 'info',
        tr('手机锁着时读不到「健康」数据，解锁后会继续', 'Apple Health cannot be read while the phone is locked; it continues once unlocked'),
        action: 'open_app', at: ow.lastNoticeAt));
  }
  if (d.backgroundRefresh == 'off' || d.backgroundRefresh == 'restricted') {
    out.add(SyncProblem('background_refresh_off', 'warning',
        d.backgroundRefresh == 'off'
            ? tr('「后台 App 刷新」关着：只在打开 app 时同步', 'Background App Refresh is off: it only syncs while the app is open')
            : tr('「后台 App 刷新」被系统限制：只在打开 app 时同步', 'Background App Refresh is restricted: it only syncs while the app is open'),
        action: d.backgroundRefresh == 'off' ? 'settings' : 'none'));
  }
  if (d.lowPower == true) {
    out.add(SyncProblem('low_power', 'info',
        tr('低电量模式下 iOS 会推迟后台同步', 'In Low Power Mode iOS delays background sync'),
        action: 'none'));
  }
  if (ow.lastNotice == SyncNotice.backgroundTimeout && fresh(ow.lastNoticeAt)) {
    out.add(SyncProblem('timeout', 'info',
        tr('后台时间用完，没传完的部分下次接着传', 'Background time ran out; the rest goes next time'),
        action: 'open_app', at: ow.lastNoticeAt));
  }
  final last = _latest([ow.lastUploadAt, raw.lastUpload]);
  if (out.every((p) => p.severity == 'info') && s.connected && last != null && s.now.difference(last) > kStaleAfter) {
    out.add(SyncProblem('stale', 'warning',
        tr('超过一天没有新数据上传：打开 app 或检查手表是否在戴', 'Nothing new uploaded for over a day: open the app, or check the watch is worn'),
        action: 'retry', at: last));
  }
  return out;
}

String? _iso(DateTime? t) => t?.toUtc().toIso8601String();

/// The answer of `syncStatus()`: no health values, only times, counts and reasons.
Map<String, Object?> buildSyncStatus(SyncStatusInput s) {
  final problems = syncProblems(s);
  final types = <Map<String, Object?>>[
    for (final e in s.ow.perType.entries)
      {'key': e.key, 'channel': 'ow', 'count': e.value, 'lastUpload': _iso(s.ow.perTypeAt[e.key])},
    for (final e in s.raw.kindsAt.entries) {'key': e.key, 'channel': 'app', 'lastUpload': _iso(e.value)},
    for (final e in s.engineAt.entries) {'key': e.key, 'channel': 'engine', 'lastUpload': _iso(e.value)},
  ]..sort((a, b) => '${b['lastUpload'] ?? ''}'.compareTo('${a['lastUpload'] ?? ''}'));
  return {
    'v': 1,
    'supported': s.supported,
    'signedIn': s.signedIn,
    'connected': s.connected,
    'authorized': {'asked': s.healthAsked, 'needsAsk': s.healthNeedsAsk},
    'running': s.running,
    'firstSyncDone': s.firstSyncDone,
    'lastUpload': _iso(_latest([s.ow.lastUploadAt, s.raw.lastUpload, ...s.engineAt.values])),
    if (s.engineAt.isNotEmpty || s.engineStop != null) 'engine': {'stop': s.engineStop},
    'lastRound': _iso(_latest([s.ow.lastCompleteAt, s.raw.lastRun])),
    'device': {
      'net': s.device.net,
      'backgroundRefresh': s.device.backgroundRefresh,
      'lowPower': s.device.lowPower,
      'locked': s.device.protectedData == null ? null : !s.device.protectedData!,
    },
    'types': types,
    'problems': [for (final p in problems) p.toJson()],
    'ok': s.supported && problems.every((p) => p.severity != 'error'),
  };
}

// ------------------------------------------------------------------------------------------------------ the steps

/// Outcome of connecting the background sync from enableSync: connected, or why not.
class SyncConnectOutcome {
  const SyncConnectOutcome({this.connected = false, this.manualNeeded = false, this.message});
  final bool connected;

  /// No account token / the server cannot mint codes: the native sync page with an invitation code is the way.
  final bool manualNeeded;
  final String? message;
}

/// enableSync / syncNow with injected steps; one operation at a time (a second call gets the running one's answer).
class SyncBridge {
  SyncBridge({
    required this.supported,
    required this.isConnected,
    required this.canUpload,
    required this.needsReconnect,
    required this.connect,
    required this.askHealth,
    required this.runRound,
    required this.status,
    required this.progress,
  });

  final bool Function() supported;

  /// The background sync has a working session.
  final bool Function() isConnected;

  /// Something can be uploaded at all: the background sync is connected, or Health was asked for (the app's own uploads
  /// — beat-to-beat series, ECGs, the types the SDK cannot read — go without the background sync).
  final bool Function() canUpload;

  /// The session is there but the server refuses it (401): connect again.
  final bool Function() needsReconnect;
  /// [link]: the sync part of the page's `sync-connect` link (OW address, one-time code), when it sent one.
  final Future<SyncConnectOutcome> Function(({String? owHost, String? code})? link) connect;

  /// Shows the Health permission sheet(s) when iOS needs them; remembers that it asked.
  final Future<void> Function() askHealth;

  /// One upload round now (waits for it, then the app's own uploads), whether it ran.
  final Future<bool> Function() runRound;
  final Future<Map<String, Object?>> Function() status;

  /// step: connect / permission / upload / done / error, with a message for people.
  final void Function(Map<String, Object?> event) progress;

  Future<Map<String, Object?>>? _op;

  bool get busy => _op != null;

  Future<Map<String, Object?>> enable({({String? owHost, String? code})? link}) => _once(() => _enable(link));
  Future<Map<String, Object?>> now() => _once(_now);

  Future<Map<String, Object?>> _once(Future<Map<String, Object?>> Function() f) {
    final running = _op;
    if (running != null) return running;
    final op = _op = f().whenComplete(() => _op = null);
    return op;
  }

  Map<String, Object?> _error(String code, String message, String action, Map<String, Object?> status) {
    progress({'step': 'error', 'code': code, 'message': message});
    return {'ok': false, 'error': {'code': code, 'message': message, 'action': action}, 'status': status};
  }

  Future<Map<String, Object?>> _enable(({String? owHost, String? code})? link) async {
    if (!supported()) {
      return _error('unsupported', tr('这台手机暂时不能同步健康数据', 'This phone cannot sync health data yet'), 'none', await status());
    }
    SyncConnectOutcome? failed;
    if (!isConnected() || needsReconnect()) {
      progress({'step': 'connect', 'message': tr('正在连接同步…', 'Connecting sync…')});
      final c = await connect(link);
      if (!c.connected) failed = c;
    }
    // Health and the app's own uploads go even when the background sync could not connect (no account / no network):
    // those types still arrive, and the answer says what is missing.
    progress({'step': 'permission', 'message': tr('请在弹出的「健康」窗口里允许读取', 'Allow reading in the Health sheet')});
    await askHealth();
    if (failed == null) return _upload();
    progress({'step': 'upload', 'message': tr('正在上传…', 'Uploading…')});
    await runRound();
    return _error(failed.manualNeeded ? 'needs_code' : 'connect',
        failed.message ?? tr('同步没连上，请再试一次', 'Sync did not connect; try again'),
        failed.manualNeeded ? 'sync_page' : 'retry', await status());
  }

  Future<Map<String, Object?>> _now() async {
    if (!supported() || !canUpload()) {
      final st = await status();
      final p = (st['problems'] as List?)?.cast<Map<String, Object?>>().firstOrNull;
      return _error('${p?['code'] ?? 'not_connected'}', '${p?['message'] ?? tr('同步还没开启', 'Sync is not turned on yet')}',
          '${p?['action'] ?? 'enable'}', st);
    }
    return _upload();
  }

  Future<Map<String, Object?>> _upload() async {
    progress({'step': 'upload', 'message': tr('正在上传…', 'Uploading…')});
    final ran = await runRound();
    final st = await status();
    final errors = ((st['problems'] as List?) ?? const []).cast<Map<String, Object?>>().where((p) => p['severity'] == 'error');
    final first = errors.firstOrNull;
    if (first != null) return _error('${first['code']}', '${first['message']}', '${first['action']}', st);
    progress({'step': 'done', 'message': ran ? tr('已同步', 'Synced') : tr('正在后台同步', 'Syncing in the background')});
    return {'ok': true, 'ran': ran, 'status': st};
  }
}

// ------------------------------------------------------------------------------------- the page's protocol (doc §3–5)

/// The page's link-driven features; declared on iOS only (Android has no phone sync yet).
const String kSyncLinkFeaturesJs = 'window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},'
    '{syncConnect:true,syncNow:true,appSettings:true,healthSettings:true});';

/// The page's names for the steps (doc §3).
const Map<String, String> kAppPhases = {
  'connect': 'connecting',
  'permission': 'permission',
  'upload': 'uploading',
  'done': 'done',
  'error': 'error',
};

/// One progress event of [SyncBridge] as the page's `onApp` argument, or null for steps it does not know.
Map<String, Object?>? onAppEvent(Map<String, Object?> e) {
  final phase = kAppPhases['${e['step']}'];
  if (phase == null) return null;
  final percent = (e['percent'] as num?)?.toInt();
  return {
    'phase': phase,
    'percent': ?percent?.clamp(0, 100),
    'error': phase == 'error' ? '${e['message'] ?? ''}' : null,
  };
}

String appProgressJs(Map<String, Object?> onApp) =>
    'window.BodySyncStatus&&window.BodySyncStatus.onApp&&window.BodySyncStatus.onApp(${jsonEncode(onApp)});';

/// The sync part of a `watch2whoop://sync-connect?ow=…&code=…` link (never server addresses or login codes); null for
/// other links. The OW address must be http(s).
({String? owHost, String? code})? parseSyncConnect(Uri uri) {
  if (uri.scheme != 'watch2whoop' || uri.host != 'sync-connect') return null;
  final code = uri.queryParameters['code']?.trim();
  final ow = uri.queryParameters['ow']?.trim();
  final owOk = ow != null && ow.isNotEmpty && (ow.startsWith('https://') || ow.startsWith('http://')) && ow.length < 300;
  final codeOk = code != null && RegExp(r'^[A-Za-z0-9-]{4,64}$').hasMatch(code);
  return (owHost: owOk ? ow : null, code: codeOk ? code : null);
}

/// The Health types whose permission matters most for the scores (HealthKit identifiers): after connecting, any of
/// them iOS still wants to ask about means the sheet did not come up (swallowed, #214) and is asked again.
const List<String> kCoreHealthTypes = [
  'HKQuantityTypeIdentifierHeartRate',
  'HKQuantityTypeIdentifierRestingHeartRate',
  'HKQuantityTypeIdentifierHeartRateVariabilitySDNN',
  'HKCategoryTypeIdentifierSleepAnalysis',
  'HKQuantityTypeIdentifierStepCount',
  'HKQuantityTypeIdentifierActiveEnergyBurned',
  'HKQuantityTypeIdentifierOxygenSaturation',
  'HKQuantityTypeIdentifierRespiratoryRate',
  'HKWorkoutTypeIdentifier',
];

/// The identifiers of [ids] iOS has never asked about (RawSync.swift healthUnasked); [] when it cannot say.
Future<List<String>> healthUnasked(Iterable<String> ids) async {
  try {
    return (await const MethodChannel('watch2whoop/native').invokeListMethod<String>('healthUnasked', {'types': ids.toList()})) ??
        const [];
  } catch (_) {
    return const [];
  }
}

// ---------------------------------------------------------------------------------- the phone's state (doc §4)

/// last_error of the report from the status problems (doc §4): auth / network / locked / health_denied / server / other.
String? deviceLastError(List<SyncProblem> problems) {
  for (final p in problems) {
    if (p.severity == 'info' && p.code != 'locked') continue;
    final e = switch (p.code) {
      'auth' => 'auth',
      'network' => 'network',
      'locked' => 'locked',
      'permission' => 'health_denied',
      'server' => 'server',
      // permission_extra: only types beyond the core ones were never asked, not a denial (HealthKit cannot say "denied")
      'not_connected' || 'background_refresh_off' || 'low_power' || 'stale' || 'consent' || 'unsupported' ||
      'permission_extra' => null,
      _ => 'other',
    };
    if (e != null) return e;
  }
  return null;
}

/// The body of PUT /v1/ingest/sync-device: the phone's state only, no health values. Fields the server does not know
/// are ignored there (the SDK's own state is sent along for the sync investigation, docs/audits).
Map<String, Object?> buildDeviceReport(
  SyncStatusInput s, {
  String? appVersion,
  List<String> unasked = const [],
  bool foreground = true,
  bool? sdkActive,
  SdkSyncStatus? sdk,
  int? firstSyncPercent,
}) {
  final d = s.device;
  return {
    'app_version': ?appVersion,
    'background_refresh': switch (d.backgroundRefresh) {
      'on' => 'available',
      'off' => 'denied',
      'restricted' => 'restricted',
      _ => null,
    },
    'low_power': d.lowPower,
    'health_readable': d.protectedData,
    'last_attempt_at': _iso(_latest([s.ow.lastCompleteAt, s.ow.lastErrorAt, s.raw.lastRun])),
    'last_upload_at': _iso(_latest([s.ow.lastUploadAt, s.raw.lastUpload, ...s.engineAt.values])),
    'last_error': deviceLastError(syncProblems(s)),
    'unasked_types': unasked.take(60).toList(),
    // extra, for the server's sync investigation (ignored by servers that do not read them)
    'foreground': foreground,
    'connected': s.connected,
    'health_asked': s.healthAsked,
    'net': d.net,
    'sdk_sync_active': ?sdkActive,
    if (sdk != null)
      'sdk_status': {
        'syncing': sdk.isSyncing,
        'full_export': sdk.isFullExport,
        'initial_export_done': sdk.initialExportDone,
        'resumable': sdk.hasResumableSession,
        'sent': sdk.sentCount,
        'types_done': sdk.completedTypes,
      },
    'first_sync_percent': ?firstSyncPercent,
  };
}

/// Sends the report at most once per [minGap] for the same content (a changed field goes at once); a server without
/// the endpoint (404) or no network is not an error.
class DeviceReporter {
  DeviceReporter({this.client, this.minGap = const Duration(minutes: 10), DateTime Function()? now}) : _now = now ?? DateTime.now;
  final http.Client? client;
  final Duration minGap;
  final DateTime Function() _now;
  String? _lastBody;
  DateTime? _lastAt;

  /// Whether it was sent (and accepted).
  Future<bool> send(String serverUrl, String token, Map<String, Object?> report) async {
    final body = jsonEncode(report);
    final now = _now();
    if (body == _lastBody && _lastAt != null && now.difference(_lastAt!) < minGap) return false;
    if (serverUrl.isEmpty) return false;
    final b = Uri.parse(serverUrl);
    final uri = Uri(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null, path: '/v1/ingest/sync-device');
    final c = client ?? http.Client();
    try {
      final res = await c
          .put(uri,
              headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/json', 'Accept': 'application/json'},
              body: body)
          .timeout(const Duration(seconds: 10));
      final ok = res.statusCode >= 200 && res.statusCode < 300;
      if (ok) {
        _lastBody = body;
        _lastAt = now;
      }
      return ok;
    } catch (_) {
      return false;
    } finally {
      if (client == null) c.close();
    }
  }
}
