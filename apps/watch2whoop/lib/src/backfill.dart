// History backfill (owner's decision 2026-10-26): the first sync reads only the last 14 days (sync_params.dart); the
// Today page's 「补全历史」 card (or the sync page's read range) asks for 90 days or all history afterwards.
//
// Page API (declared as `window.Watch2WhoopFeatures.backfill = {v: 1, days: [90], all: true}`; names to be aligned with
// the page's PR):
//   await window.Watch2WhoopBackfill.start(90 | "all")  -> {ok, status}
//   await window.Watch2WhoopBackfill.status()           -> status
//   window 'watch2whoop:backfill' events, detail = status, while it runs
// status = {v, target (days, 0 = all), range, active, done, percent, channel, ow: {...}, engine: {...}, error}.
//
// Two ways, depending on the server's sync mode (sync_engine.dart, GET /v1/ingest/healthkit `engine`):
// - the app's own engine (modes shadow / app): the native backfill (ios/Runner/Backfill.swift) walks back day by day
//   from the start of the first-sync window, uploads each day with a background URLSession (continues while the app is
//   suspended), resumes from its persisted cursor after an interruption, and runs in a BGProcessingTask while the phone
//   is idle; the newest data always goes first (the foreground gives it time only after each round of new data);
// - Open Wearables (mode ow, the flag apple_sync_engine off, the default): the SDK's own full export with the larger
//   range (stop, reset anchors, start; newest first, resumable, continued in the SDK's own background tasks). Limits of
//   the SDK: while that export runs, its rounds are export rounds (new samples arrive with it, newest first, not as a
//   separate incremental round), and there is no background URLSession of ours on that path. RawSync's types re-read
//   the larger range too (anchor reset; the server skips what it has).
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app_state.dart';
import 'health_range.dart';
import 'health_sync.dart';
import 'raw_sync.dart';
import 'sync_engine.dart';
import 'sync_params.dart';
import 'sync_stats.dart' show SdkSyncStatus;

const String kBackfillPrefix = 'backfill:';

/// JS defining `window.Watch2WhoopBackfill` (once per page) and the feature.
const String kBackfillJs = '''
(function(){
var B=window.Watch2WhoopBackfill;
if(!B||B.v!==1){var n=0,w={};
function call(verb,days){return new Promise(function(res){var id='b'+(++n)+'_'+Date.now();w[id]=res;
try{Watch2Whoop.postMessage('backfill:'+verb+' '+JSON.stringify({id:id,days:days}))}catch(e){delete w[id];res({ok:false,error:String(e)})}})}
B=window.Watch2WhoopBackfill={v:1,
start:function(days){return call('start',days===undefined?90:days)},
status:function(){return call('status').then(function(r){return r&&r.status?r.status:r})},
_reply:function(id,r){var f=w[id];if(f){delete w[id];f(r)}},
_progress:function(s){try{window.dispatchEvent(new CustomEvent('watch2whoop:backfill',{detail:s}))}catch(x){}}};}
window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},{backfill:{v:1,days:[90],all:true}});
})();''';

/// One `backfill:*` message: verb (start / status), reply id and the days (start only; 0 = all history), or null.
({String verb, String id, int days})? parseBackfillMessage(String message) {
  if (!message.startsWith(kBackfillPrefix)) return null;
  final rest = message.substring(kBackfillPrefix.length);
  final sp = rest.indexOf(' ');
  if (sp < 0) return null;
  final verb = rest.substring(0, sp);
  if (verb != 'start' && verb != 'status') return null;
  try {
    final j = jsonDecode(rest.substring(sp + 1));
    final id = j is Map ? j['id'] : null;
    if (id is! String || id.isEmpty || id.length > 64) return null;
    final d = (j as Map)['days'];
    var days = kBackfillDays;
    if (verb == 'start') {
      if (d == 'all' || d == 0) {
        days = kAllHistory;
      } else if (d is int && d > 0 && d <= 3650) {
        days = d;
      } else if (d != null) {
        return null;
      }
    }
    return (verb: verb, id: id, days: days);
  } catch (_) {
    return null;
  }
}

String backfillReplyJs(String id, Map<String, Object?> r) =>
    'window.Watch2WhoopBackfill&&window.Watch2WhoopBackfill._reply(${jsonEncode(id)},${jsonEncode(r)});';

String backfillProgressJs(Map<String, Object?> status) =>
    'window.Watch2WhoopBackfill&&window.Watch2WhoopBackfill._progress(${jsonEncode(status)});';

/// Whether going from [previous] to [next] days needs the history read again (a larger range; 0 = all).
bool rangeGrows(int? previous, int next) {
  if (previous == null) return false;
  if (isAllHistory(previous)) return false;
  return isAllHistory(next) || next > previous;
}

/// The page's status from the OW export and the native engine's status (pure, tested).
Map<String, Object?> backfillStatus({
  required int target,
  required int range,
  HistoryProgress? ow,
  Map<String, Object?>? engine,
  String? error,
}) {
  final owActive = ow != null && ow.running && !ow.done;
  final engActive = engine?['active'] == true;
  final parts = <int>[
    if (ow != null && (owActive || ow.done)) ow.percent,
    if (engine != null && (engActive || engine['done'] == true)) (engine['percent'] as num?)?.toInt() ?? 0,
  ];
  final percent = parts.isEmpty ? null : parts.reduce((a, b) => a < b ? a : b);
  return {
    'v': 1,
    'target': target,
    'range': range,
    'active': owActive || engActive,
    'done': parts.isNotEmpty && !owActive && !engActive,
    'percent': percent,
    'channel': engine != null && ow != null ? 'both' : (engine != null ? 'engine' : 'ow'),
    if (ow != null) 'ow': {'percent': ow.percent, 'sent': ow.sent, 'done': ow.done, 'running': ow.running},
    'engine': ?engine,
    'error': error ?? engine?['error'],
  };
}

class BackfillService {
  BackfillService._();
  static final BackfillService instance = BackfillService._();
  static const _ch = MethodChannel('watch2whoop/native');
  static const _toKey = 'w2w.backfill.to.v1';
  static const _targetKey = 'w2w.backfill.target.v1';

  void Function(String line)? onLog;

  Future<Map<String, Object?>?> _native(String method, [Map<String, Object?>? args]) async {
    try {
      return await _ch.invokeMapMethod<String, Object?>(method, args);
    } catch (e) {
      debugPrint('$method failed: $e');
      return null;
    }
  }

  /// The engine block of the server's last answer says the app's own engine uploads (shadow / app).
  EngineInfo? get _engine {
    final e = EngineInfo.fromStatus(RawSync.instance.lastServerStatus);
    return e != null && e.active ? e : null;
  }

  /// The start of the first-sync window, fixed the first time a backfill starts (the native cursor walks back from it).
  Future<double> _to() async {
    final p = await SharedPreferences.getInstance();
    final kept = p.getDouble(_toKey);
    if (kept != null) return kept;
    final now = DateTime.now();
    final to = DateTime(now.year, now.month, now.day).subtract(const Duration(days: kFirstSyncDays)).millisecondsSinceEpoch / 1000;
    await p.setDouble(_toKey, to);
    return to;
  }

  /// Read range [days] (0 = all history): a larger one starts the backfill on every path; a smaller one only changes
  /// what later full exports read (nothing already on the server is removed).
  Future<Map<String, Object?>> setRange(int days, {String? token}) async {
    final state = AppState.instance;
    final previous = state.config.syncDaysBack;
    await state.update(state.config.copyWith(syncDaysBack: days));
    (await SharedPreferences.getInstance()).setInt(_targetKey, days);
    final grows = rangeGrows(previous, days);
    final engineMode = await SyncEngine.instance.mode();
    // Open Wearables: the SDK's own full export of the larger range (not in mode app: there the SDK is paused)
    if (state.config.owConnected && HealthSync.instance.isSignedIn && engineMode != 'app') {
      try {
        await HealthSync.instance.changeRange(days, grows: grows);
      } catch (e) {
        onLog?.call('Backfill: changing the Open Wearables range failed: $e');
      }
    }
    // the app's own engine: the native backfill (background URLSession + BGProcessingTask)
    final engine = _engine;
    if (grows && engine != null) {
      final t = token ?? RawSync.instance.lastToken ?? state.accountToken ?? await RawSync.instance.ingestToken();
      if (t != null) {
        final ios = iosMajor();
        final reg = await SyncEngine.instance.registry();
        await _native('backfillStart', {
          'server': state.config.todayUrl,
          'token': t,
          'days': days,
          'to': await _to(),
          'powerOnly': kBackfillPowerOnly,
          'types': [
            for (final x in reg)
              if (x.readByA1 && engine.types.contains(x.hk) && (ios == 0 || x.minIos == 0 || ios >= x.minIos))
                {'hk': x.hk, 'kind': x.kind, 'unit': x.unit},
          ],
        });
      }
    }
    onLog?.call('Backfill: read range ${previous == 0 ? 'all' : previous} -> ${days == 0 ? 'all' : days} days'
        '${grows ? ' (history is read again)' : ''}${engine != null ? ', engine ${engine.mode}' : ''}');
    return status();
  }

  /// The combined progress for the page and the sync page.
  Future<Map<String, Object?>> status() async {
    final c = AppState.instance.config;
    HistoryProgress? ow;
    if (c.owConnected && HealthSync.instance.isSignedIn) {
      final SdkSyncStatus s = await HealthSync.instance.status();
      ow = HistoryProgress.from(s, requestedTypes.length, DateTime.now());
    }
    final engine = await _native('backfillStatus');
    final target = (await SharedPreferences.getInstance()).getInt(_targetKey) ?? c.syncDaysBack;
    return backfillStatus(
      target: target,
      range: c.syncDaysBack,
      ow: ow,
      engine: engine == null || (engine['active'] != true && engine['done'] != true && engine['batches'] == 0) ? null : engine,
    );
  }

  /// A slice of the engine backfill in the foreground, after the new data went; returns the status when it ran.
  Future<Map<String, Object?>?> pump() async {
    final s = await _native('backfillStatus');
    if (s == null || s['active'] != true) return null;
    await _native('backfillPump', {'seconds': kBackfillPumpSeconds});
    return status();
  }

  /// Sign-out: stop the native backfill and forget its window.
  Future<void> clear() async {
    await _native('backfillCancel');
    final p = await SharedPreferences.getInstance();
    await p.remove(_toKey);
    await p.remove(_targetKey);
  }
}
