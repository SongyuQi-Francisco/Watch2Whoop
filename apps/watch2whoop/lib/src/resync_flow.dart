// Glue of the forced re-sync (resync.dart) with the Open Wearables SDK, RawSync and the server; called by the sync page
// (start, a step every few seconds while it is open) and after each sync round on open (home_page.dart), so a closed app
// continues where it stopped.
import 'dart:async';

import 'app_state.dart';
import 'backfill.dart';
import 'health_range.dart';
import 'health_sync.dart';
import 'i18n.dart';
import 'raw_sync.dart';
import 'resync.dart';
import 'sync_engine.dart';

Future<String?> _token() async => AppState.instance.accountToken ?? await RawSync.instance.ingestToken();

/// Starts a re-sync of the last [days] days (null = all). Returns null when started, else why not (one line).
Future<String?> startResync(int? days) async {
  final c = AppState.instance.config;
  final token = await _token();
  if (token == null || c.todayUrl.isEmpty) return tr('先打开今天页登录，再回来重试', 'Sign in on the Today page first, then try again');
  final r = await ResyncApi(c.todayUrl, token).start(days);
  if (r?['accepted'] != true) return resyncRefusal(r);
  final ow = HealthSync.instance.owInUse(c);
  // the app's own engine (#219 A3, contract §8): a range re-sync, which also drops what the phone no longer has
  final engine = BackfillService.instance.engine != null && await SyncEngine.instance.startRange(daysBackOf(days));
  await Resync.instance.save(ResyncState(days: days, startedAt: DateTime.now(), owDone: !ow, engineDone: !engine, engineRange: engine));
  await RawSync.instance.source.reset(anchorSet: kResyncAnchorSet);
  if (ow) await HealthSync.instance.resyncFrom(daysBackOf(days));
  await HealthSync.instance.note('Re-sync started: ${days == null ? 'all history' : '$days days'}${engine ? ' (engine range)' : ''}');
  unawaited(stepResync());
  return null;
}

/// Stops the phone part here and asks the server to drop the job (before its server part).
Future<void> cancelResync() async {
  final c = AppState.instance.config;
  final token = await _token();
  if (token != null) await ResyncApi(c.todayUrl, token).cancel();
  if (Resync.instance.state.value?.engineRange == true) await SyncEngine.instance.cancelRange();
  if (Resync.instance.state.value?.owDone == false) await HealthSync.instance.startSync(c.syncDaysBack);
  await Resync.instance.save(null);
  await HealthSync.instance.note('Re-sync stopped');
}

bool _stepping = false;

/// The page's `onApp` for a backfill started by the Today page (home_page.dart sets it).
void Function(Map<String, Object?> event)? onBackfillProgress;

/// The Today page's 「补全历史」 (docs/sync-onboarding-app-requirements.md §10, `watch2whoop://backfill?days=…`): the
/// page already created the server's job; the app joins it — never POSTs one itself. Returns null when the phone part
/// runs (or continues), else why not. The first progress goes out at once (the page gives up after 15 minutes without).
Future<String?> joinPageBackfill() async {
  final c = AppState.instance.config;
  final token = await _token();
  if (token == null || c.todayUrl.isEmpty) return 'no_token';
  final api = ResyncApi(c.todayUrl, token);
  final job = await api.status();
  if (job == null) return 'offline';
  if (job['status'] != 'phone') return 'not_phone'; // cancelled, or already in its server part: nothing to do
  final days = (job['days'] as num?)?.toInt(); // null = all history
  final running = Resync.instance.state.value;
  if (running == null || running.days != days) {
    final ow = HealthSync.instance.owInUse(c);
    // the app's own engine (when the server has it on) reads the whole range too: background URLSession, resumable
    final engine = await BackfillService.instance.startEngine(daysBackOf(days), to: DateTime.now().millisecondsSinceEpoch / 1000);
    await Resync.instance.save(ResyncState(days: days, startedAt: DateTime.now(), owDone: !ow, engineDone: !engine, fromPage: true));
    await RawSync.instance.source.reset(anchorSet: kResyncAnchorSet);
    if (ow) await HealthSync.instance.resyncFrom(daysBackOf(days));
    // the read range follows (14 -> 90 days / all), so later full exports keep the history
    if (!isAllHistory(c.syncDaysBack) && (days == null || days > c.syncDaysBack)) {
      await AppState.instance.update(c.copyWith(syncDaysBack: daysBackOf(days)));
    }
    await HealthSync.instance.note('Backfill from the Today page: ${days == null ? 'all history' : '$days days'}'
        '${engine ? ' (engine too)' : ''}');
  }
  await api.progress(percent: 1); // started: the card stops waiting
  onBackfillProgress?.call(backfillOnApp('uploading', percent: 1));
  unawaited(stepResync());
  return null;
}

/// One step of the phone part: a RawSync pass with the re-sync anchors (time-budgeted), the OW export's progress, a
/// progress report; when both are done, phone_done and the usual range for the OW SDK again.
Future<void> stepResync({Future<String?> Function()? pageToken}) async {
  final s = Resync.instance.state.value;
  if (s == null || _stepping) return;
  _stepping = true;
  try {
    final c = AppState.instance.config;
    var next = s;
    if (!next.rawDone) {
      final res = await RawSync.instance.run(
        serverUrl: c.todayUrl,
        daysBack: daysBackOf(s.days),
        accountToken: AppState.instance.accountToken,
        pageToken: pageToken ?? RawSync.instance.ingestToken,
        anchorSet: kResyncAnchorSet,
      );
      // done unless it stopped for time or a failure (a kind HealthKit cannot read does not hold it back)
      if (res != null && (res.stop == null || res.stop == 'consent')) next = next.copyWith(rawDone: true);
    }
    var enginePercent = 100;
    if (!next.engineDone && next.engineRange) {
      // the engine's range re-sync: gone from its state = range_done went (or it was dropped)
      final r = (await SyncEngine.instance.status())?.range;
      if (r == null) {
        next = next.copyWith(engineDone: true);
      } else {
        enginePercent = (r['percent'] as num?)?.toInt() ?? 0;
      }
    } else if (!next.engineDone) {
      final e = await BackfillService.instance.engineStatus();
      if (e == null || e['done'] == true || (e['active'] != true && e['error'] != null)) {
        next = next.copyWith(engineDone: true); // finished, or stopped with an error (shown in its status; OW / RawSync still count)
      } else {
        enginePercent = (e['percent'] as num?)?.toInt() ?? 0;
      }
    }
    var owPercent = 100;
    if (!next.owDone) {
      final st = await HealthSync.instance.status();
      if (st.initialExportDone) {
        next = next.copyWith(owDone: true);
        await HealthSync.instance.startSync(c.syncDaysBack); // the usual range again; the anchors only see newer samples
      } else {
        owPercent = HistoryProgress.from(st, requestedTypes.length, DateTime.now()).percent;
      }
    }
    final token = await _token();
    if (token != null) {
      final done = next.owDone && next.rawDone && next.engineDone;
      final percent = phonePercent(next, owPercent, enginePercent: enginePercent);
      final r = await ResyncApi(c.todayUrl, token).progress(percent: done ? 100 : percent.clamp(1, 99), done: done);
      if (next.fromPage) onBackfillProgress?.call(backfillOnApp(done ? 'done' : 'uploading', percent: done ? 100 : percent));
      if (done && r != null) {
        await HealthSync.instance.note('Re-sync: phone part done');
        await Resync.instance.save(null);
        return;
      }
    }
    await Resync.instance.save(next);
  } finally {
    _stepping = false;
  }
}
