import 'package:flutter/foundation.dart';

import 'config.dart';

/// Global config (saved on change, and the UI is notified).
class AppState extends ChangeNotifier {
  AppState._();
  static final AppState instance = AppState._();

  AppConfig config = const AppConfig();

  Future<void> load() async {
    config = await ConfigStore.load();
  }

  Future<void> update(AppConfig c) async {
    config = c;
    await ConfigStore.save(c);
    notifyListeners();
  }
}
