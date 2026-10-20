import 'package:flutter/foundation.dart';

import 'account_store.dart';
import 'config.dart';
import 'health_sync.dart';
import 'server_lock.dart';

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
    final locked = ServerLock.current.enforce(await ConfigStore.load());
    config = locked.config;
    // Keychain items survive deleting the app: a fresh install must not silently reuse the old account.
    // A locked build that finds a config for another server starts over as well (that account and sync belong there).
    if (fresh || locked.reset) await AccountStore.deleteToken();
    if (locked.reset) {
      try {
        await HealthSync.instance.disconnect();
      } catch (e) {
        debugPrint('disconnect after server lock reset failed: $e');
      }
      await ConfigStore.save(config);
    }
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
