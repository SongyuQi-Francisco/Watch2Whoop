import 'package:flutter/foundation.dart';

import 'account_store.dart';
import 'config.dart';

/// Global config (saved on change, and the UI is notified).
class AppState extends ChangeNotifier {
  AppState._();
  static final AppState instance = AppState._();

  AppConfig config = const AppConfig();

  /// Bearer token of the self-service account (Keychain); null for setup-link users.
  String? accountToken;

  /// Set right after a fresh sign-in: the Today page is logged in with a new one-time code on its first load.
  bool webLoginNeeded = false;

  Future<void> load() async {
    final fresh = !await ConfigStore.exists();
    config = await ConfigStore.load();
    // Keychain items survive deleting the app: a fresh install must not silently reuse the old account.
    if (fresh) await AccountStore.deleteToken();
    accountToken = await AccountStore.readToken();
  }

  Future<void> update(AppConfig c) async {
    config = c;
    await ConfigStore.save(c);
    notifyListeners();
  }

  Future<void> setAccountToken(String? token) async {
    accountToken = token;
    if (token == null) {
      await AccountStore.deleteToken();
    } else {
      await AccountStore.writeToken(token);
    }
    notifyListeners();
  }
}
