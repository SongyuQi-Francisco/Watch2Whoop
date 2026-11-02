// History backfill (owner's decision 2026-10-26): the first sync reads only the last 14 days (sync_params.dart); the
// older history is filled in afterwards, in the background, after the newest data.
//
// Two entry points:
// - the Today page's 「补全历史」 card (docs/sync-onboarding-app-requirements.md §10): the app declares
//   `Watch2WhoopFeatures.backfill = true`; the page creates the server's re-sync job (POST /v1/users/me/sync/backfill)
//   and opens `watch2whoop://backfill?days=90|all`; the app joins that job (resync_flow.dart joinPageBackfill: GET
//   /v1/ingest/resync, the phone part, PUT progress, phone_done) — it never creates the job itself;
// - the read range on the sync page / in onboarding (14 / 90 days / all history, [BackfillService.setRange]).
//
// The phone part, depending on the server's sync mode (sync_engine.dart, GET /v1/ingest/healthkit `engine`):
// - the app's own engine (modes shadow / app): the native backfill (ios/Runner/Backfill.swift) walks back day by day,
//   uploads each day with a background URLSession (continues while the app is suspended or was ended by the system),
//   resumes from its persisted cursor, and runs in a BGProcessingTask while the phone is idle; the foreground gives it
//   time only after each round of new data;
// - Open Wearables (mode ow, the flag apple_sync_engine off, the default): the SDK's own full export of the range (stop,
//   reset anchors, start; newest first, resumable, continued in the SDK's own background tasks). Limits of the SDK: while
//   that export runs its rounds are export rounds (new samples arrive with it, newest first, not as a separate
//   incremental round), and there is no background URLSession of ours on that path. RawSync's types read the range again
//   with their own anchor set.
import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app_state.dart';
import 'health_range.dart';
import 'health_sync.dart';
import 'raw_sync.dart';
import 'sync_engine.dart';
import 'sync_params.dart';

/// The page's 「补全历史」 button (docs §10): declared on iOS (the phone part needs HealthKit).
const String kBackfillFeatureJs = 'window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},{backfill:true});';

/// `watch2whoop://backfill?days=90|all` from the page: true when it is that link (the days come from the server's job).
bool isBackfillLink(Uri uri) => uri.scheme == 'watch2whoop' && uri.host == 'backfill';

/// The page's `onApp` progress for the backfill (docs §10: always with `kind: "backfill"`).
Map<String, Object?> backfillOnApp(String phase, {int? percent, String? error}) =>
    {'kind': 'backfill', 'phase': phase, 'percent': ?percent?.clamp(0, 100), 'error': error};

/// Whether going from [previous] to [next] days needs the history read again (a larger range; 0 = all).
bool rangeGrows(int? previous, int next) {
  if (previous == null) return false;
  if (isAllHistory(previous)) return false;
  return isAllHistory(next) || next > previous;
}

class BackfillService {
  BackfillService._();
  static final BackfillService instance = BackfillService._();
  static const _ch = MethodChannel('watch2whoop/native');
  static const _toKey = 'w2w.backfill.to.v1';

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
  EngineInfo? get engine {
    final e = EngineInfo.fromStatus(RawSync.instance.lastServerStatus);
    return e != null && e.active ? e : null;
  }

  /// The start of the first-sync window, fixed the first time a range backfill starts (the cursor walks back from it).
  Future<double> _firstWindowStart() async {
    final p = await SharedPreferences.getInstance();
    final kept = p.getDouble(_toKey);
    if (kept != null) return kept;
    final now = DateTime.now();
    final to = DateTime(now.year, now.month, now.day).subtract(const Duration(days: kFirstSyncDays)).millisecondsSinceEpoch / 1000;
    await p.setDouble(_toKey, to);
    return to;
  }

  /// Starts the native engine backfill of [days] (0 = all) back to [to] (default: the first-sync window's start);
  /// false when the engine is not on for this user or there is no token.
  Future<bool> startEngine(int days, {double? to, String? token}) async {
    final e = engine;
    if (e == null) return false;
    final state = AppState.instance;
    final t = token ?? RawSync.instance.lastToken ?? state.accountToken ?? await RawSync.instance.ingestToken();
    if (t == null) return false;
    final ios = iosMajor();
    final reg = await SyncEngine.instance.registry();
    final r = await _native('backfillStart', {
      'server': state.config.todayUrl,
      'token': t,
      'days': days,
      'to': to ?? await _firstWindowStart(),
      'powerOnly': kBackfillPowerOnly,
      // quantities (raw samples and / or 15-minute buckets), sleep and other categories, workouts (A3)
      'types': [
        for (final x in reg)
          if (x.engine && e.types.contains(x.hk) && (ios == 0 || x.minIos == 0 || ios >= x.minIos))
            {'hk': x.hk, 'kind': x.kind, 'unit': x.unit, 'upload': x.upload},
      ],
    });
    return r != null;
  }

  /// The native backfill's state (active, done, percent, error …), or null when there is none.
  Future<Map<String, Object?>?> engineStatus() async {
    final s = await _native('backfillStatus');
    if (s == null || (s['active'] != true && s['done'] != true && s['batches'] == 0)) return null;
    return s;
  }

  /// Read range [days] (0 = all history) from the sync page / onboarding: a larger one reads the history again on every
  /// path; a smaller one only changes what later full exports read (nothing already on the server is removed).
  Future<void> setRange(int days) async {
    final state = AppState.instance;
    final previous = state.config.syncDaysBack;
    await state.update(state.config.copyWith(syncDaysBack: days));
    final grows = rangeGrows(previous, days);
    final engineMode = await SyncEngine.instance.mode();
    if (state.config.owConnected && HealthSync.instance.isSignedIn && engineMode != 'app') {
      try {
        await HealthSync.instance.changeRange(days, grows: grows);
      } catch (e) {
        onLog?.call('Backfill: changing the Open Wearables range failed: $e');
      }
    }
    final started = grows && await startEngine(days);
    onLog?.call('Backfill: read range ${previous == 0 ? 'all' : previous} -> ${days == 0 ? 'all' : days} days'
        '${grows ? ' (history is read again)' : ''}${started ? ', engine backfill started' : ''}');
  }

  /// A slice of the engine backfill in the foreground, after the new data went.
  Future<void> pump() async {
    final s = await _native('backfillStatus');
    if (s == null || s['active'] != true) return;
    await _native('backfillPump', {'seconds': kBackfillPumpSeconds});
  }

  /// Sign-out: stop the native backfill and forget its window.
  Future<void> clear() async {
    await _native('backfillCancel');
    await (await SharedPreferences.getInstance()).remove(_toKey);
  }
}
