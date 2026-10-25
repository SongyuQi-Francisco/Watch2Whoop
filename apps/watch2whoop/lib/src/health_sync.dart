// Background sync: wraps the Open Wearables Flutter SDK (on iOS: HealthKit background push + BGTask, incremental anchor sync).
// Connects with a one-time OW invitation code (POST {OW}/api/v1/invitation-code/redeem exchanges it for the SDK access / refresh tokens,
// credentials are stored in the SDK Keychain, the app does not keep them).
//
// Background sync: thin wrapper over the Open Wearables Flutter SDK.
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:open_wearables_health_sdk/health_data_type.dart';
import 'package:open_wearables_health_sdk/open_wearables_health_sdk.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'config.dart';
import 'platform_features.dart';
import 'raw_sync.dart';
import 'sync_stats.dart';

/// Types to request: everything the SDK supports, minus reproductive health (sensitive, unused) and nutrition (SparkyFitness is the source for nutrition).
/// Users can switch items off one by one in the system dialog; the SDK skips denied types automatically.
/// Note: SDK 0.15 does not yet support wrist temperature and HRV RMSSD (iOS 27); the iOS Shortcut fills in during the transition.
const Set<HealthDataType> kExcludedTypes = {
  HealthDataType.menstrualFlow,
  HealthDataType.cervicalMucusQuality,
  HealthDataType.ovulationTestResult,
  HealthDataType.sexualActivity,
  HealthDataType.dietaryEnergyConsumed,
  HealthDataType.dietaryCarbohydrates,
  HealthDataType.dietaryProtein,
  HealthDataType.dietaryFatTotal,
  HealthDataType.dietaryWater,
  // Alias; same HealthKit type as basalEnergy / oxygenSaturation
  HealthDataType.restingEnergy,
  HealthDataType.bloodOxygen,
};

List<HealthDataType> get requestedTypes =>
    HealthDataType.values.where((t) => !kExcludedTypes.contains(t)).toList(growable: false);

class ConnectException implements Exception {
  ConnectException(this.code, [this.detail]);

  /// invalid_code / network / server / bad_response / sign_in (the code was redeemed but the SDK did not take the new
  /// session: sync is not connected now and the user can retry, see [HealthSync.connect])
  final String code;
  final String? detail;
  @override
  String toString() => 'ConnectException($code${detail == null ? '' : ': $detail'})';
}

class HealthSync {
  HealthSync._();
  static final HealthSync instance = HealthSync._();

  static const _statsKey = 'w2w.syncStats.v1';
  static const _logKey = 'w2w.syncLog.v1';
  static const _maxLog = 200;

  final ValueNotifier<SyncStats> stats = ValueNotifier(SyncStats());
  final List<String> recentLog = [];
  late SyncLogParser _parser;
  static const _native = MethodChannel('watch2whoop/native');
  bool _started = false;
  StreamSubscription<Map<String, dynamic>>? _authErrors;

  /// Called once at app start: reads back the stats and restores the SDK session (configure() resumes background sync automatically).
  /// SDK logs are buffered first by native code (SyncLogBuffer in AppDelegate), since Dart may not be running at all during background sync;
  /// here [drainNativeLog] fetches them for counting.
  Future<void> start(AppConfig config) async {
    if (_started) return;
    _started = true;
    final prefs = await SharedPreferences.getInstance();
    stats.value = SyncStats.decode(prefs.getString(_statsKey));
    recentLog
      ..clear()
      ..addAll(prefs.getStringList(_logKey) ?? const []);
    _parser = SyncLogParser(stats.value);
    if (!hasHealthSync) return; // Android (#253): no phone health sync yet, the SDK is never touched

    // Release builds emit no logs by default; sync status relies on log counts, so keep them always on (logs stay on the device only).
    await OpenWearablesHealthSdk.setLogLevel(OWLogLevel.always);
    // The SDK's own signal that its session is dead (refresh rejected / 401 with an API key): the structured event, not a
    // log text (sync_repair.dart starts the silent reconnect from stats.authProblem). Log lines stay the fallback for
    // rounds that ran in the background while Dart was not running.
    _authErrors ??= MethodChannelOpenWearablesHealthSdk.authErrorStream.listen(
      (e) => markAuthProblem('HTTP ${e['statusCode'] ?? 401}'),
      onError: (_) {},
    );
    if (config.owConnected && config.owHost.isNotEmpty) {
      await OpenWearablesHealthSdk.configure(host: config.owHost);
    }
    await drainNativeLog();
  }

  /// Fetches the natively buffered logs for counting (called when opening the sync page or returning to the foreground).
  Future<void> drainNativeLog() async {
    List<String> lines;
    try {
      lines = (await _native.invokeListMethod<String>('drainLog')) ?? const [];
    } on MissingPluginException {
      return;
    } on PlatformException {
      return;
    }
    if (lines.isEmpty) return;
    for (final raw in lines) {
      final tab = raw.indexOf('\t');
      final at = tab > 0 ? DateTime.tryParse(raw.substring(0, tab))?.toLocal() : null;
      final msg = tab > 0 ? raw.substring(tab + 1) : raw;
      final t = at ?? DateTime.now();
      final hh = '${t.month}/${t.day} ${t.toIso8601String().substring(11, 19)}';
      recentLog.add('$hh $msg');
      _parser.handle(msg, t);
    }
    if (recentLog.length > _maxLog) recentLog.removeRange(0, recentLog.length - _maxLog);
    // ignore: invalid_use_of_protected_member, invalid_use_of_visible_for_testing_member
    stats.notifyListeners();
    await _save();
  }

  /// The sync server does not accept this phone's session (the SDK's auth error event): sync_repair.dart reconnects.
  Future<void> markAuthProblem(String why) async {
    final s = stats.value
      ..authProblem = true
      ..lastErrorAt = DateTime.now()
      ..lastError = why;
    await note('Sync server rejected the session ($why)');
    stats.value = s;
    // ignore: invalid_use_of_protected_member, invalid_use_of_visible_for_testing_member
    stats.notifyListeners();
    await _save();
  }

  /// A line of the app's own (not from the SDK) for the sync log, e.g. the beat-to-beat upload pass (raw_sync.dart).
  Future<void> note(String msg) async {
    final t = DateTime.now();
    recentLog.add('${t.month}/${t.day} ${t.toIso8601String().substring(11, 19)} $msg');
    if (recentLog.length > _maxLog) recentLog.removeRange(0, recentLog.length - _maxLog);
    try {
      await (await SharedPreferences.getInstance()).setStringList(_logKey, recentLog);
    } catch (_) {}
  }

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_statsKey, stats.value.encode());
    await prefs.setStringList(_logKey, recentLog);
  }

  // -------------------------------------------------------------------------- connect / disconnect

  /// Connects to OW with an invitation code: redeem → configure → signIn. Returns the OW user id.
  Future<String> connect({required String owHost, required String code, http.Client? client}) async {
    if (!hasHealthSync) throw ConnectException('unsupported');
    final c = client ?? http.Client();
    final http.Response res;
    try {
      res = await c
          .post(
            Uri.parse('$owHost/api/v1/invitation-code/redeem'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'code': code}),
          )
          .timeout(const Duration(seconds: 20));
    } catch (e) {
      throw ConnectException('network', '$e');
    } finally {
      if (client == null) c.close();
    }
    final creds = parseRedeemResponse(res.statusCode, res.body);

    // When switching accounts, disconnect the old one first (clears the old anchor and Keychain).
    // From here on the old session is gone and the one-time code is used: a failure must not end silently in
    // "not connected" — it is a ConnectException('sign_in') the caller shows, and connecting again mints a new code.
    try {
      if (OpenWearablesHealthSdk.isSignedIn) await OpenWearablesHealthSdk.signOut();
      await OpenWearablesHealthSdk.configure(host: owHost);
      await OpenWearablesHealthSdk.signIn(
        userId: creds.userId,
        accessToken: creds.accessToken.startsWith('Bearer ') ? creds.accessToken : 'Bearer ${creds.accessToken}',
        refreshToken: creds.refreshToken,
      );
    } catch (e) {
      await note('Connecting sync failed after the code was redeemed: $e');
      throw ConnectException('sign_in', '$e');
    }
    await resetStats();
    return creds.userId;
  }

  Future<void> disconnect() async {
    if (!hasHealthSync) return resetStats();
    try {
      await OpenWearablesHealthSdk.stopBackgroundSync();
    } catch (_) {}
    await OpenWearablesHealthSdk.signOut();
    await resetStats();
  }

  Future<void> resetStats() async {
    await drainNativeLog(); // the old account logs do not count toward the new account
    stats.value = SyncStats();
    _parser = SyncLogParser(stats.value);
    recentLog.clear();
    await _save();
  }

  // -------------------------------------------------------------------------- permission / sync

  /// Shows the HealthKit authorization prompt (the user taps it). true = at least one item was allowed.
  /// HealthKit does not tell the app which read permissions were denied, so all we can know is that the prompt was shown.
  /// The beat-to-beat HRV series and ECG that the app reads itself (raw_sync.dart) follow in a second, small sheet.
  /// Not connected to sync yet (the SDK is only configured by connecting): its sheet would throw NotConfiguredException, so
  /// only the app's own types are asked now; connecting asks for the rest.
  Future<bool> requestAuthorization() async {
    if (!hasHealthSync) return false;
    if (!OpenWearablesHealthSdk.isConfigured) return RawSync.instance.requestAuth();
    final ok = await OpenWearablesHealthSdk.requestAuthorization(types: requestedTypes);
    await RawSync.instance.requestAuth();
    return ok;
  }

  /// Starts background sync; calling it again while already on = sync one round immediately (skipped if the SDK is already syncing).
  Future<bool> startSync(int syncDaysBack) async =>
      hasHealthSync && await OpenWearablesHealthSdk.startBackgroundSync(syncDaysBack: syncDaysBack);

  /// Changes the read range (health_range.dart). "All history" after the 90-day export: the anchors only see newer
  /// samples, so they are reset and the whole history is exported again (the last 90 days are re-sent once and stored
  /// once by Open Wearables). Back to 90 days: only later full exports are limited; nothing already read is removed.
  Future<void> changeRange(int syncDaysBack) async {
    if (!hasHealthSync) return;
    if (syncDaysBack <= 0) {
      await OpenWearablesHealthSdk.stopBackgroundSync();
      await OpenWearablesHealthSdk.resetAnchors();
    }
    await OpenWearablesHealthSdk.startBackgroundSync(syncDaysBack: syncDaysBack);
  }

  /// Forced re-sync (resync.dart): send everything from the start of the day [syncDaysBack] days ago again (0 = all).
  /// Same sequence as going to "All history": stop (cancels a running pass, which would make the export skip), clear
  /// every anchor, then start with the range stored, which runs a full export of it (newest first, resumable).
  Future<void> resyncFrom(int syncDaysBack) async {
    if (!hasHealthSync) return;
    await OpenWearablesHealthSdk.stopBackgroundSync();
    await OpenWearablesHealthSdk.resetAnchors();
    await OpenWearablesHealthSdk.startBackgroundSync(syncDaysBack: syncDaysBack);
  }

  /// The server switched this user to the app's own sync engine (contract §7, mode `app`): the SDK stops syncing in
  /// the background; its credentials and anchors stay, so going back resumes where it stopped.
  Future<void> pauseForEngine() async {
    if (!hasHealthSync || !OpenWearablesHealthSdk.isConfigured) return;
    try {
      await OpenWearablesHealthSdk.stopBackgroundSync();
      await note('Open Wearables sync paused: the app\'s own sync engine is the source now');
    } catch (e) {
      await note('Pausing Open Wearables sync failed: $e');
    }
  }

  /// Retry after failure: resumes an interrupted session if there is one, otherwise starts a new round.
  Future<void> retry(int syncDaysBack) async {
    if (!hasHealthSync) return;
    final s = await status();
    if (s.hasResumableSession) {
      await OpenWearablesHealthSdk.resumeSync();
    } else {
      await startSync(syncDaysBack);
    }
  }

  Future<SdkSyncStatus> status() async {
    if (!hasHealthSync || !OpenWearablesHealthSdk.isConfigured) return const SdkSyncStatus();
    try {
      return SdkSyncStatus.fromMap(await OpenWearablesHealthSdk.getSyncStatus());
    } on PlatformException {
      return const SdkSyncStatus();
    }
  }

  bool get isSyncActive => hasHealthSync && OpenWearablesHealthSdk.isSyncActive;
  bool get isSignedIn => hasHealthSync && OpenWearablesHealthSdk.isSignedIn;
}

class RedeemedCredentials {
  const RedeemedCredentials(this.userId, this.accessToken, this.refreshToken);
  final String userId;
  final String accessToken;
  final String refreshToken;
}

/// Parses the invitation-code redeem response (OW 0.9: 200 + {access_token, refresh_token, user_id}; invalid code 404).
RedeemedCredentials parseRedeemResponse(int status, String body) {
  if (status == 404 || status == 422) throw ConnectException('invalid_code');
  if (status != 200) throw ConnectException('server', 'HTTP $status');
  try {
    final j = (jsonDecode(body) as Map).cast<String, Object?>();
    final u = j['user_id'], a = j['access_token'], r = j['refresh_token'];
    if (u is String && a is String && r is String && u.isNotEmpty && a.isNotEmpty && r.isNotEmpty) {
      return RedeemedCredentials(u, a, r);
    }
  } catch (_) {}
  throw ConnectException('bad_response');
}
