// "Setup info" completes everything in one step: server address → connect OW (invitation code) → keep the one-time Today page login code (auto login when the Today page opens).
// Sources: a watch2whoop://setup?… link opened by scanning the QR code with the system camera, scanning inside the app, or pasting.
// One-step setup from a setup link / QR code / pasted text.
import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'app_state.dart';
import 'config.dart';
import 'health_sync.dart';

class SetupResult {
  const SetupResult({this.serversSet = false, this.owConnected = false, this.loginCodeSaved = false, this.owError});
  final bool serversSet;
  final bool owConnected;
  final bool loginCodeSaved;

  /// Why connecting to OW failed (ConnectException etc.); the other parts still take effect.
  final Object? owError;
}

/// Applies the setup info. Throws [FormatException] when the address format is wrong (message is the checkServerUrl error code).
Future<SetupResult> applySetup(SetupInfo info) async {
  var c = AppState.instance.config;
  var serversSet = false;
  if (info.todayUrl != null) {
    final t = checkServerUrl(info.todayUrl!, UrlKind.today);
    if (!t.isOk) throw FormatException(t.error!, 'today');
    c = c.copyWith(todayUrl: t.url);
    serversSet = true;
  }
  if (info.owHost != null) {
    final o = checkServerUrl(info.owHost!, UrlKind.ow);
    if (!o.isOk) throw FormatException(o.error!, 'ow');
    if (o.url != c.owHost && c.owConnected) {
      // Switched OW server: the old connection is void
      await HealthSync.instance.disconnect();
      c = c.copyWith(clearOwUserId: true);
    }
    c = c.copyWith(owHost: o.url);
    serversSet = true;
  }
  if (info.loginCode != null) c = c.copyWith(pendingLoginCode: info.loginCode);
  await AppState.instance.update(c);

  var owConnected = false;
  Object? owError;
  if (info.code != null && c.owHost.isNotEmpty) {
    try {
      final userId = await HealthSync.instance.connect(owHost: c.owHost, code: info.code!);
      await AppState.instance.update(AppState.instance.config.copyWith(owUserId: userId));
      owConnected = true;
    } catch (e) {
      owError = e;
    }
  }
  return SetupResult(
    serversSet: serversSet,
    owConnected: owConnected,
    loginCodeSaved: info.loginCode != null,
    owError: owError,
  );
}

/// Receives watch2whoop:// links (stored first by LinkInbox in the native AppDelegate, fetched on cold start / return to foreground).
class SetupLinks {
  SetupLinks._();
  static final SetupLinks instance = SetupLinks._();
  static const _native = MethodChannel('watch2whoop/native');

  /// Setup info received, waiting for the UI to handle it (onboarding uses it directly; the Today page asks first).
  final ValueNotifier<SetupInfo?> pending = ValueNotifier(null);

  void start() {
    _native.setMethodCallHandler((call) async {
      if (call.method == 'linksPending') await take();
      return null;
    });
    take();
  }

  Future<void> take() async {
    List<String> links;
    try {
      links = (await _native.invokeListMethod<String>('takeLinks')) ?? const [];
    } on MissingPluginException {
      return;
    } on PlatformException {
      return;
    }
    for (final l in links) {
      final info = parseSetupText(l);
      if (!info.isEmpty) pending.value = info;
    }
  }
}
