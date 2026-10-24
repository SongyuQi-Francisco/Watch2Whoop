// Android only (#253): what WKWebView / iOS give the app for free and an Android WebView does not.
// - Microphone: the WebView's PermissionRequest only works once the app itself holds RECORD_AUDIO, so the app asks for it
//   when the Today page first wants the microphone (web_mic.dart decides whether the page may have it at all).
// - File inputs (<input type=file>): Android WebView shows nothing unless the app picks the files. The app opens the
//   system picker (gallery / files) with the camera offered next to it, or the camera directly for `capture`.
// - The app's page in system settings (microphone declined, like `app-settings:` on iOS).
// Native side: MainActivity.kt (channel "watch2whoop/android"). Every call degrades to "no" / nothing picked when the
// native side is missing or fails, so the page falls back to the keyboard / keeps working without the file.
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

const _ch = MethodChannel('watch2whoop/android');

/// The `accept` attribute values of a file input as one list of lower-case MIME types / `.ext` entries
/// (WebView passes them as given, sometimes still comma-separated in one entry).
List<String> normalizeAcceptTypes(Iterable<String> accept) {
  final out = <String>[];
  for (final a in accept) {
    for (final part in a.split(',')) {
      final t = part.trim().toLowerCase();
      if (t.isNotEmpty && !out.contains(t)) out.add(t);
    }
  }
  return out;
}

/// What a file input should offer: the camera for photos and / or videos next to the picker.
/// No `accept` (or `*/*`) = any file, which includes photos.
({bool photo, bool video}) cameraKinds(List<String> accept) {
  if (accept.isEmpty || accept.contains('*/*')) return (photo: true, video: false);
  bool any(String prefix, List<String> exts) =>
      accept.any((t) => t.startsWith(prefix) || exts.contains(t));
  return (
    photo: any('image/', const ['.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp']),
    video: any('video/', const ['.mp4', '.mov', '.3gp', '.webm']),
  );
}

/// The arguments of `pickFiles` for a file input.
Map<String, Object> pickRequest({required List<String> acceptTypes, required bool multiple, required bool capture}) {
  final accept = normalizeAcceptTypes(acceptTypes);
  final cam = cameraKinds(accept);
  return {
    'accept': accept,
    'multiple': multiple,
    // `capture` goes straight to the camera, only when there is a camera kind to take
    'capture': capture && (cam.photo || cam.video),
    'photo': cam.photo,
    'video': cam.video,
  };
}

class AndroidBridge {
  AndroidBridge._();

  /// The phone has a microphone and the native side answers (otherwise the page keeps the keyboard's dictation key).
  static Future<bool> hasMic() => _bool('hasMic');

  /// Asks for RECORD_AUDIO when needed (system dialog); true = the app may record.
  static Future<bool> requestMic() => _bool('requestMic');

  /// This app's page in system settings (permissions).
  static Future<void> openAppSettings() async {
    try {
      await _ch.invokeMethod<void>('openAppSettings');
    } on MissingPluginException {
      // no native side
    } on PlatformException catch (e) {
      debugPrint('openAppSettings failed: $e');
    }
  }

  /// Picks files for a file input; content:// URIs, empty when cancelled or on failure.
  static Future<List<String>> pickFiles({
    required List<String> acceptTypes,
    required bool multiple,
    required bool capture,
  }) async {
    try {
      final r = await _ch.invokeListMethod<String>(
          'pickFiles', pickRequest(acceptTypes: acceptTypes, multiple: multiple, capture: capture));
      return r ?? const [];
    } on MissingPluginException {
      return const [];
    } on PlatformException catch (e) {
      debugPrint('pickFiles failed: $e');
      return const [];
    }
  }

  static Future<bool> _bool(String method) async {
    try {
      return (await _ch.invokeMethod<bool>(method)) ?? false;
    } on MissingPluginException {
      return false;
    } on PlatformException catch (e) {
      debugPrint('$method failed: $e');
      return false;
    }
  }
}
