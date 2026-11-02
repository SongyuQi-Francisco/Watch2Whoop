// Automatic repair of the background sync when Open Wearables stops accepting this phone (seen in production: the SDK's
// credentials became invalid and every upload got 401 for days, unnoticed). When the SDK log shows it (SyncStats.authProblem:
// an upload with HTTP 401, a refresh rejected, "auth is invalid"), the app reconnects once by itself the same way 「连接同步」
// does: a fresh invitation code of the user's own sync account from the server (POST /v1/account/ow-invitation), redeemed
// silently (account_flow.dart connectSyncViaAccount); only for accounts with a Bearer token (self sign-up / e-mail / Apple).
// Limits: at most [maxPerDay] attempts per local day, the n-th attempt of a day only after [backoff][n] since the previous
// one (a server that keeps refusing must not be asked in a loop); the attempts are kept across restarts. Only in the
// foreground (the Health permission sheet may need the user, like after 「连接同步」).
// Pure Dart with injected callbacks (test/sync_repair_test.dart); the Today page's banner covers apps without this
// (web/static/sync-repair.js).
import 'dart:async';
import 'dart:convert';

import 'config.dart';

/// A watch2whoop://setup link the Today page opened (its 「重新连接」 button, web/static/sync-repair.js): only the sync part
/// is taken (the OW address and the invitation code); server addresses and login codes come from scanned / pasted setup
/// info only. null when there is no invitation code. The app then asks once before reconnecting (home_page.dart _onLink).
SetupInfo? pageSetupLink(String url) {
  if (!url.toLowerCase().startsWith('$kSetupScheme://setup')) return null;
  final info = parseSetupText(url);
  if (info.code == null) return null;
  return SetupInfo(owHost: info.owHost, code: info.code);
}

/// What one repair attempt ended with.
enum RepairOutcome { connected, notPossible, failed }

class SyncAutoRepair {
  SyncAutoRepair({
    required this.needsRepair,
    required this.canRepair,
    required this.repair,
    required this.load,
    required this.save,
    DateTime Function()? now,
    this.maxPerDay = 3,
    this.backoff = const [Duration.zero, Duration(minutes: 15), Duration(hours: 2)],
  }) : _now = now ?? DateTime.now;

  /// The SDK reported an authentication problem (HealthSync.stats.authProblem).
  final bool Function() needsRepair;

  /// The app can reconnect on its own: signed in to an account, sync was connected before, in the foreground.
  final bool Function() canRepair;

  /// One silent reconnect (connectSyncViaAccount, then sync starts again).
  final Future<RepairOutcome> Function() repair;

  /// The attempts kept across restarts (ISO times).
  final Future<String?> Function() load;
  final Future<void> Function(String) save;

  final DateTime Function() _now;
  final int maxPerDay;
  final List<Duration> backoff;
  bool _running = false;

  bool get running => _running;

  static bool _sameDay(DateTime a, DateTime b) => a.year == b.year && a.month == b.month && a.day == b.day;

  /// Today's attempts (local time), oldest first.
  Future<List<DateTime>> attemptsToday() async {
    final now = _now();
    List<DateTime> all;
    try {
      all = ((jsonDecode(await load() ?? '[]') as List).cast<String>()).map(DateTime.parse).map((t) => t.toLocal()).toList();
    } catch (_) {
      all = [];
    }
    return all.where((t) => _sameDay(t, now)).toList()..sort();
  }

  /// Repairs when needed and allowed now. Returns the outcome, or null when nothing was tried.
  Future<RepairOutcome?> trigger() async {
    if (_running || !needsRepair() || !canRepair()) return null;
    _running = true;
    try {
      final now = _now();
      final today = await attemptsToday();
      if (today.length >= maxPerDay) return null;
      final gap = backoff[today.length.clamp(0, backoff.length - 1)];
      if (today.isNotEmpty && now.difference(today.last) < gap) return null;
      await save(jsonEncode([...today, now].map((t) => t.toUtc().toIso8601String()).toList()));
      try {
        return await repair();
      } catch (_) {
        return RepairOutcome.failed;
      }
    } finally {
      _running = false;
    }
  }
}
