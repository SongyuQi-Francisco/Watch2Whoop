// Self-service account glue: store the session, connect sync silently with a server-minted OW invitation code,
// log the Today page in with a fresh one-time code, sign out / account deleted → back to onboarding.
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:webview_flutter/webview_flutter.dart';

import 'account_api.dart';
import 'app_state.dart';
import 'config.dart';
import 'health_sync.dart';
import 'i18n.dart';

/// Short device label the server shows in the token list.
String get deviceLabel {
  if (kIsWeb) return 'Watch2Whoop web';
  if (Platform.isIOS) return 'iPhone (Watch2Whoop)';
  return 'Watch2Whoop ${Platform.operatingSystem}';
}

AccountApi accountApiFor(String todayUrl) => AccountApi(todayUrl);

/// After /v1/auth/* succeeded: keep the token in the Keychain and the server address in the config.
Future<void> saveSession(AccountSession s, String todayUrl) async {
  await AppState.instance.setAccountToken(s.token);
  AppState.instance.webLoginNeeded = true;
  await AppState.instance.update(AppState.instance.config.copyWith(todayUrl: todayUrl));
}

class SyncConnectResult {
  const SyncConnectResult({this.connected = false, this.manualNeeded = false, this.error});
  final bool connected;

  /// The server cannot mint invitation codes (503) or has no sync address: use "Connect with invitation code" on the sync page.
  final bool manualNeeded;
  final Object? error;
}

/// Connects background sync without showing the user any code: POST /v1/account/ow-invitation → redeem with the OW SDK.
Future<SyncConnectResult> connectSyncViaAccount() async {
  final state = AppState.instance;
  final token = state.accountToken;
  if (token == null) return const SyncConnectResult(manualNeeded: true);
  final api = accountApiFor(state.config.todayUrl);
  final OwInvitation inv;
  try {
    inv = await api.owInvitationWhenReady(token);
  } on AccountApiException catch (e) {
    return SyncConnectResult(manualNeeded: e.code == 'unavailable', error: e);
  }
  var owHost = state.config.owHost;
  if (inv.owUrl != null) {
    final c = checkServerUrl(inv.owUrl!, UrlKind.ow);
    if (c.isOk) owHost = c.url!;
  }
  if (owHost.isEmpty) return const SyncConnectResult(manualNeeded: true);
  if (owHost != state.config.owHost && state.config.owConnected) {
    await HealthSync.instance.disconnect();
    await state.update(state.config.copyWith(clearOwUserId: true));
  }
  await state.update(state.config.copyWith(owHost: owHost));
  try {
    final userId = await HealthSync.instance.connect(owHost: owHost, code: inv.code);
    await state.update(state.config.copyWith(owUserId: userId));
    return const SyncConnectResult(connected: true);
  } catch (e) {
    return SyncConnectResult(error: e);
  }
}

/// Today page URL with a fresh one-time login code (`/app/#login=…`), or null when not signed in / the server refused.
Future<Uri?> freshWebLoginEntry() async {
  final state = AppState.instance;
  final token = state.accountToken;
  if (token == null) return null;
  try {
    final code = await accountApiFor(state.config.todayUrl).webLoginCode(token);
    return state.config.todayEntry.replace(fragment: 'login=$code');
  } on AccountApiException catch (e) {
    debugPrint('web-login-code failed: $e');
    return null;
  }
}

/// Signs this device out and returns to onboarding. [revoke]: also revoke the app token on the server
/// (not needed after the account was deleted). Health data already on the server is not touched here.
Future<void> signOutToOnboarding({bool revoke = true}) async {
  final state = AppState.instance;
  final token = state.accountToken;
  if (revoke && token != null) {
    try {
      await accountApiFor(state.config.todayUrl).logout(token);
    } catch (e) {
      debugPrint('logout failed: $e');
    }
  }
  try {
    if (HealthSync.instance.isSignedIn || state.config.owConnected) await HealthSync.instance.disconnect();
  } catch (e) {
    debugPrint('OW disconnect failed: $e');
  }
  try {
    await WebViewCookieManager().clearCookies();
  } catch (_) {}
  state.webLoginNeeded = false;
  await state.setAccountToken(null);
  try {
    await setLangOverride(null); // the next person starts in the system language
  } catch (e) {
    debugPrint('lang reset failed: $e');
  }
  final c = state.config;
  await state.update(AppConfig(todayUrl: c.todayUrl, owHost: c.owHost, syncDaysBack: c.syncDaysBack));
}
