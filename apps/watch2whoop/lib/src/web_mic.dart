// Hold-to-talk inside the app (#171): the Today page records with getUserMedia + Web Audio (web/static/ai-media.js), packs
// 16 kHz WAV and posts it to its own server (POST /v1/assistant/transcribe, local SenseVoice; nothing kept, per-user daily
// limit on the server). WKWebView only lets a page capture once the app answers WebKit's media capture request
// (WKUIDelegate, iOS 15+; the app's deployment target is 15.0), so:
// - microphone only, and only for our own Today page (same origin as the configured address, not the kept offline copy);
//   cameras stay denied (photos / videos for the AI go through the file picker, not getUserMedia);
// - the page learns that the app answers these requests from `window.Watch2WhoopFeatures.mic` (until then it points to
//   the keyboard's dictation key, as in 1.0.6 and older);
// - iOS shows its own microphone prompt (NSMicrophoneUsageDescription) the first time; when it was declined the page
//   posts [kMicSettingsMessage] and the app opens its page in iOS Settings.
// - Android (#253): the same rules decide; the app then also needs RECORD_AUDIO itself (asked on the first request,
//   android_bridge.dart), and declining it opens the app's page in system settings the same way.
import 'package:webview_flutter/webview_flutter.dart';

import 'config.dart';

/// Bridge message from the page: the microphone is off for this app, open its page in iOS Settings
/// (keep in sync with `mic-settings` in body_engine/.../web/static/ai-media.js).
const kMicSettingsMessage = 'mic-settings';

/// Tells the page which native features this app has (read when the mic button is pressed; see `voicePath` in ai-media.js).
/// `syncCenter`: the app has the sync center (watch2whoop://sync-center, resync.dart), so with the server flag sync_center
/// the Me page folds 手机同步 / 数据更新 / 数据状态 into that one row (web/static/app.js).
/// `syncRepair`: the app takes watch2whoop://setup?ow=…&code=… from the page (its 「手机同步已断开 → 重新连接」,
/// web/static/sync-repair.js; sync_repair.dart pageSetupLink); older apps get the code shown and their sync page opened.
const kFeaturesJs = 'window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},{mic:true,syncCenter:true,syncRepair:true});';

/// The same with the microphone off: an Android phone without a microphone or without the native side (android_bridge.dart);
/// the page then points to the keyboard's dictation key instead of recording.
const kFeaturesNoMicJs = 'window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},{mic:false,syncCenter:true,syncRepair:true});';

/// Whether to grant a WebKit media capture request: the microphone alone, asked by our own Today page.
/// [pageUrl] is the WebView's current address; [onShell] = the kept offline copy of the page is on screen.
bool allowMediaCapture({
  required Set<WebViewPermissionResourceType> types,
  required String? pageUrl,
  required Uri todayEntry,
  required bool onShell,
}) {
  if (onShell || types.isEmpty) return false;
  if (types.any((t) => t != WebViewPermissionResourceType.microphone)) return false;
  final page = pageUrl == null ? null : Uri.tryParse(pageUrl);
  if (page == null || (page.scheme != 'https' && page.scheme != 'http')) return false;
  return sameOrigin(page, todayEntry);
}
