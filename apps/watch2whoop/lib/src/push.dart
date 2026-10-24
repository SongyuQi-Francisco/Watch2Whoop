// Push notifications (#175; server: body_engine/src/body_engine/notify/).
//
// - Permission is never asked at launch: the Today page sends `push:ask` the first time someone opens the Friends tab or
//   the notification settings (web/static/notify.js, rollout flag notifications); iOS shows its dialog only once.
// - Once allowed, every app start asks APNs for the device token again (it can change) and registers it with the server
//   for the signed-in account (POST /v1/users/me/push-devices). Sign-out removes it (DELETE) before the token is revoked.
// - A tapped notification carries a path on the server ("/app/?tab=friends&feed=3"); HomePage opens it in the WebView.
// Native side: PushBridge in ios/Runner/AppDelegate.swift (channel "watch2whoop/push").
import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'account_flow.dart';
import 'app_state.dart';
import 'i18n.dart';
import 'platform_features.dart';

const _kRegisteredKey = 'w2w.pushRegistered';

/// The page asks for permission / registration (bridge message from the Today page).
const kPushAskMessage = 'push:ask';

/// `badge:<n>` from the page: the unread count for the app icon.
int? parseBadgeMessage(String message) =>
    message.startsWith('badge:') ? int.tryParse(message.substring('badge:'.length)) : null;

/// The URL to open for a tapped notification: [path] must be a path on the Today page's server (a full URL to another
/// host, `//host` or a non-http scheme is refused and the notification list opens instead).
Uri notificationTarget(Uri todayEntry, String path) {
  Uri on(String p, String? query, String? fragment) => Uri(
      scheme: todayEntry.scheme, host: todayEntry.host, port: todayEntry.hasPort ? todayEntry.port : null,
      path: p, query: query, fragment: fragment);
  final fallback = on('/app/', null, 'notifications');
  if (!path.startsWith('/') || path.startsWith('//')) return fallback;
  final p = Uri.tryParse(path);
  if (p == null || p.hasScheme || p.hasAuthority) return fallback;
  return on(p.path, p.hasQuery ? p.query : null, p.hasFragment ? p.fragment : null);
}

/// JavaScript telling the page the permission state (`watch2whoop:push` {state: granted | denied | unknown}).
String pushStateJs(String state) =>
    "window.dispatchEvent(new CustomEvent('watch2whoop:push', {detail: {state: '${state.replaceAll(RegExp(r'[^a-z]'), '')}'}}));";

/// JavaScript after a notification arrived while the app is open: the page refreshes its unread count.
const kPushReceivedJs = "window.dispatchEvent(new Event('watch2whoop:notification'));";

/// What was registered last, so the same token is not sent again on every start (re-sent after a day anyway).
String registrationKey(String server, String deviceToken, String environment) => '$server|$deviceToken|$environment';

class PushService {
  PushService._();
  static final PushService instance = PushService._();
  static const _ch = MethodChannel('watch2whoop/push');

  /// A tapped notification's path, waiting for HomePage.
  final ValueNotifier<String?> opened = ValueNotifier(null);

  /// A notification arrived while the app was open (counter; HomePage tells the page).
  final ValueNotifier<int> received = ValueNotifier(0);

  void start() {
    if (!hasPush) return; // Android (#253): no FCM on Huawei phones without Google services
    _ch.setMethodCallHandler((call) async {
      switch (call.method) {
        case 'token':
          final m = (call.arguments as Map?)?.cast<String, Object?>() ?? const {};
          await _register(m['token'] as String?, m['environment'] as String?);
        case 'opened':
          await _takeOpened();
        case 'received':
          received.value++;
        case 'error':
          debugPrint('push registration failed: ${call.arguments}');
      }
      return null;
    });
    _takeOpened(); // cold start from a tap
    refresh();
  }

  Future<String> _call(String method) async {
    if (!hasPush) return 'unsupported';
    try {
      return (await _ch.invokeMethod<String>(method)) ?? 'unknown';
    } on MissingPluginException {
      return 'unsupported';
    } on PlatformException {
      return 'unknown';
    }
  }

  /// Permission state: granted / denied / unknown (never asked) / unsupported.
  Future<String> status() => _call('status');

  /// From the page's `push:ask`: asks iOS when it never asked before, otherwise re-registers. Returns the state.
  Future<String> ask() async {
    if (AppState.instance.accountToken == null) return status();
    return (await status()) == 'unknown' ? _call('request') : _call('register');
  }

  /// App start / back in the foreground: re-register when already allowed (no dialog).
  Future<void> refresh() async {
    if (AppState.instance.accountToken == null) return;
    await _call('register');
  }

  Future<void> setBadge(int n) async {
    if (!hasPush) return;
    try {
      await _ch.invokeMethod('setBadge', n < 0 ? 0 : n);
    } on MissingPluginException {
      // older native side
    } on PlatformException {
      // ignore
    }
  }

  Future<void> _takeOpened() async {
    try {
      final list = (await _ch.invokeListMethod<String>('takeOpened')) ?? const [];
      if (list.isNotEmpty) opened.value = list.last;
    } on MissingPluginException {
      // older native side
    } on PlatformException {
      // ignore
    }
  }

  Future<void> _register(String? deviceToken, String? environment) async {
    final state = AppState.instance;
    final token = state.accountToken;
    if (deviceToken == null || environment == null || token == null) return;
    final server = state.config.todayUrl;
    final prefs = await SharedPreferences.getInstance();
    final key = registrationKey(server, deviceToken, environment);
    final last = prefs.getString(_kRegisteredKey) ?? '';
    final at = DateTime.tryParse(last.split('#').last);
    if (last.startsWith('$key#') && at != null && DateTime.now().difference(at) < const Duration(days: 1)) return;
    String? version;
    try {
      version = (await PackageInfo.fromPlatform()).version;
    } catch (_) {}
    try {
      await accountApiFor(server).registerPushDevice(token,
          deviceToken: deviceToken, environment: environment, appVersion: version, lang: currentLang);
      await prefs.setString(_kRegisteredKey, '$key#${DateTime.now().toIso8601String()}');
    } catch (e) {
      debugPrint('push device registration failed: $e');
    }
  }

  /// Sign-out: the server stops pushing to this phone for the account (before the account token is revoked).
  Future<void> unregister(String accountToken) async {
    if (!hasPush) return; // nothing was registered
    final prefs = await SharedPreferences.getInstance();
    String? deviceToken;
    try {
      final t = await _ch.invokeMapMethod<String, String>('takeToken');
      deviceToken = t?['token'];
    } catch (_) {}
    try {
      await accountApiFor(AppState.instance.config.todayUrl).removePushDevice(accountToken, deviceToken: deviceToken);
    } catch (e) {
      debugPrint('push device removal failed: $e');
    }
    await prefs.remove(_kRegisteredKey);
    await setBadge(0);
  }
}
