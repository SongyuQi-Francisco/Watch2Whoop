// The Body Engine account token (self-service sign-in) lives in the iOS Keychain, never in shared_preferences.
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class AccountStore {
  static const _tokenKey = 'w2w.account.token.v1';
  static const _storage = FlutterSecureStorage(
    // Readable after the first unlock (background wake-ups), never synced to iCloud or restored to another device.
    iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device),
  );

  static Future<String?> readToken() async {
    try {
      final t = await _storage.read(key: _tokenKey);
      return (t == null || t.isEmpty) ? null : t;
    } catch (_) {
      return null;
    }
  }

  static Future<void> writeToken(String token) => _storage.write(key: _tokenKey, value: token);

  static Future<void> deleteToken() async {
    try {
      await _storage.delete(key: _tokenKey);
    } catch (_) {}
  }
}
