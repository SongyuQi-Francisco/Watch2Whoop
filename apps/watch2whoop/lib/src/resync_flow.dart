// Glue of the forced re-sync (resync.dart) with the Open Wearables SDK, RawSync and the server; called by the sync page
// (start, a step every few seconds while it is open) and after each sync round on open (home_page.dart), so a closed app
// continues where it stopped.
import 'dart:async';

import 'app_state.dart';
import 'health_range.dart';
import 'health_sync.dart';
import 'i18n.dart';
import 'raw_sync.dart';
import 'resync.dart';

Future<String?> _token() async => AppState.instance.accountToken ?? await RawSync.instance.ingestToken();

/// Starts a re-sync of the last [days] days (null = all). Returns null when started, else why not (one line).
Future<String?> startResync(int? days) async {
  final c = AppState.instance.config;
  final token = await _token();
  if (token == null || c.todayUrl.isEmpty) return tr('先打开今天页登录，再回来重试', 'Sign in on the Today page first, then try again');
  final r = await ResyncApi(c.todayUrl, token).start(days);
  if (r?['accepted'] != true) return resyncRefusal(r);
  final ow = c.owConnected && HealthSync.instance.isSignedIn;
  await Resync.instance.save(ResyncState(days: days, startedAt: DateTime.now(), owDone: !ow));
  await RawSync.instance.source.reset(anchorSet: kResyncAnchorSet);
  if (ow) await HealthSync.instance.resyncFrom(daysBackOf(days));
  await HealthSync.instance.note('Re-sync started: ${days == null ? 'all history' : '$days days'}');
  unawaited(stepResync());
  return null;
}

/// Stops the phone part here and asks the server to drop the job (before its server part).
Future<void> cancelResync() async {
  final c = AppState.instance.config;
  final token = await _token();
  if (token != null) await ResyncApi(c.todayUrl, token).cancel();
  if (Resync.instance.state.value?.owDone == false) await HealthSync.instance.startSync(c.syncDaysBack);
  await Resync.instance.save(null);
  await HealthSync.instance.note('Re-sync stopped');
}

bool _stepping = false;

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
      final done = next.owDone && next.rawDone;
      final r = await ResyncApi(c.todayUrl, token).progress(percent: phonePercent(next, owPercent), done: done);
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
