// Background sync: wraps the Open Wearables Flutter SDK (on iOS: HealthKit background push + BGTask, incremental anchor sync).
// Connects with a one-time OW invitation code (POST {OW}/api/v1/invitation-code/redeem exchanges it for the SDK access / refresh tokens,
// credentials are stored in the SDK Keychain, the app does not keep them).
//
// Background sync: thin wrapper over the Open Wearables Flutter SDK.
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:open_wearables_health_sdk/health_data_type.dart';
import 'package:open_wearables_health_sdk/open_wearables_health_sdk.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'config.dart';
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

  /// invalid_code / network / server / bad_response
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

    // Release builds emit no logs by default; sync status relies on log counts, so keep them always on (logs stay on the device only).
    await OpenWearablesHealthSdk.setLogLevel(OWLogLevel.always);
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

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_statsKey, stats.value.encode());
    await prefs.setStringList(_logKey, recentLog);
  }

  // -------------------------------------------------------------------------- connect / disconnect

  /// Connects to OW with an invitation code: redeem → configure → signIn. Returns the OW user id.
  Future<String> connect({required String owHost, required String code, http.Client? client}) async {
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
    if (OpenWearablesHealthSdk.isSignedIn) await OpenWearablesHealthSdk.signOut();
    await OpenWearablesHealthSdk.configure(host: owHost);
    await OpenWearablesHealthSdk.signIn(
      userId: creds.userId,
      accessToken: creds.accessToken.startsWith('Bearer ') ? creds.accessToken : 'Bearer ${creds.accessToken}',
      refreshToken: creds.refreshToken,
    );
    await resetStats();
    return creds.userId;
  }

  Future<void> disconnect() async {
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
  Future<bool> requestAuthorization() =>
      OpenWearablesHealthSdk.requestAuthorization(types: requestedTypes);

  /// Starts background sync; calling it again while already on = sync one round immediately (skipped if the SDK is already syncing).
  Future<bool> startSync(int syncDaysBack) =>
      OpenWearablesHealthSdk.startBackgroundSync(syncDaysBack: syncDaysBack);

  /// Retry after failure: resumes an interrupted session if there is one, otherwise starts a new round.
  Future<void> retry(int syncDaysBack) async {
    final s = await status();
    if (s.hasResumableSession) {
      await OpenWearablesHealthSdk.resumeSync();
    } else {
      await startSync(syncDaysBack);
    }
  }

  Future<SdkSyncStatus> status() async {
    if (!OpenWearablesHealthSdk.isConfigured) return const SdkSyncStatus();
    try {
      return SdkSyncStatus.fromMap(await OpenWearablesHealthSdk.getSyncStatus());
    } on PlatformException {
      return const SdkSyncStatus();
    }
  }

  bool get isSyncActive => OpenWearablesHealthSdk.isSyncActive;
  bool get isSignedIn => OpenWearablesHealthSdk.isSignedIn;
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
