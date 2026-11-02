// Jarvis voice input on the phone itself (#53, app 1.0.8). The Today page (web/static/jarvis-entry.js, rollout flag
// jarvis_orbit) talks to Apple's on-device speech recogniser through the app instead of recording in the WebView, so the
// words appear while the person is still talking:
// - the app tells the page it can (`window.Watch2WhoopFeatures.voice = {langs, onDevice: true}`, only the languages whose
//   on-device model this phone has; plus `haptics: true`); without it (1.0.7 and older) the page records and its server
//   recognises, as before;
// - page -> app over the JS channel: `voice:start {session, lang, audio}`, `voice:stop {session}`, `voice:cancel {session}`,
//   `voice:lang {session, lang, audio}`, and `haptic:<light|medium|selection|success|warning>`;
// - app -> page: `window.Watch2WhoopVoice.event({type, session, ...})` with started / level / partial / final / error.
// The recogniser, the microphone and the in-memory copy of a Chinese recording for the server's final pass are native
// (ios/Runner/VoiceInput.swift, channel "watch2whoop/voice"). Parsing and the JS are plain functions (test/voice_bridge_test.dart).
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'web_mic.dart';

const String kVoicePrefix = 'voice:';
const String kHapticPrefix = 'haptic:';
const Set<String> kVoiceVerbs = {'start', 'stop', 'cancel', 'lang'};
const Set<String> kVoiceLangs = {'zh', 'en'};
const Set<String> kHapticKinds = {'light', 'medium', 'selection', 'success', 'warning'};

/// One voice:* message from the page, checked: a known verb, a session id, a known language where one is needed.
@immutable
class VoiceCommand {
  const VoiceCommand(this.verb, this.args);
  final String verb;
  final Map<String, Object?> args;
}

VoiceCommand? parseVoiceMessage(String message) {
  if (!message.startsWith(kVoicePrefix)) return null;
  final rest = message.substring(kVoicePrefix.length);
  final sp = rest.indexOf(' ');
  final verb = sp < 0 ? rest : rest.substring(0, sp);
  if (!kVoiceVerbs.contains(verb) || sp < 0) return null;
  Object? json;
  try {
    json = jsonDecode(rest.substring(sp + 1));
  } catch (_) {
    return null;
  }
  if (json is! Map) return null;
  final session = json['session'];
  if (session is! String || session.isEmpty || session.length > 64) return null;
  final lang = json['lang'];
  final needsLang = verb == 'start' || verb == 'lang';
  if (needsLang && !kVoiceLangs.contains(lang)) return null;
  return VoiceCommand(verb, {
    'session': session,
    if (needsLang) 'lang': lang,
    if (needsLang) 'audio': json['audio'] == true,
  });
}

/// `haptic:<kind>` from the page, or null.
String? parseHapticMessage(String message) {
  if (!message.startsWith(kHapticPrefix)) return null;
  final kind = message.substring(kHapticPrefix.length);
  return kHapticKinds.contains(kind) ? kind : null;
}

/// JS that hands one recogniser event to the page (jarvis-entry.js registers window.Watch2WhoopVoice).
String voiceEventJs(Map<String, Object?> event) =>
    'window.Watch2WhoopVoice&&window.Watch2WhoopVoice.event(${jsonEncode(event)});';

/// The features the page reads: the microphone / sync center ones of [kFeaturesJs] ([kFeaturesNoMicJs] when [mic] is
/// false), haptics (iOS only: the native side is VoiceInput.swift), and the on-device voice languages when there are any.
String featuresJs({bool mic = true, bool haptics = true, List<String> voiceLangs = const []}) {
  final langs = voiceLangs.where(kVoiceLangs.contains).toList();
  return '${mic ? kFeaturesJs : kFeaturesNoMicJs}'
      '${haptics ? 'window.Watch2WhoopFeatures.haptics=true;' : ''}'
      '${langs.isEmpty ? '' : 'window.Watch2WhoopFeatures.voice=${jsonEncode({'langs': langs, 'onDevice': true})};'}';
}

/// The native side (VoiceInput.swift). Every call is safe without it (another platform, a test): no languages, nothing happens.
class VoiceBridge {
  VoiceBridge._();
  static final VoiceBridge instance = VoiceBridge._();
  static const MethodChannel _channel = MethodChannel('watch2whoop/voice');

  /// Called with each event of the recogniser (on the main isolate), to hand to the page.
  void Function(Map<String, Object?> event)? onEvent;
  List<String>? _langs;
  bool _attached = false;

  void attach() {
    if (_attached) return;
    _attached = true;
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'event' && call.arguments is Map) {
        onEvent?.call(Map<String, Object?>.from(call.arguments as Map));
      }
      return null;
    });
  }

  /// The languages the on-device recogniser serves on this phone ("zh", "en"); asked once per app run.
  Future<List<String>> langs() async {
    final known = _langs;
    if (known != null) return known;
    try {
      final r = await _channel.invokeListMethod<String>('langs');
      return _langs = (r ?? const <String>[]).where(kVoiceLangs.contains).toList();
    } catch (_) {
      return _langs = const <String>[];
    }
  }

  Future<void> command(VoiceCommand c) async {
    try {
      await _channel.invokeMethod<void>(c.verb, c.args);
    } catch (e) {
      debugPrint('voice ${c.verb} failed: $e');
    }
  }

  /// Ends whatever is running (the app left the foreground, the page went away).
  Future<void> cancelAll() => command(const VoiceCommand('cancel', {'session': '*'}));

  Future<void> haptic(String kind) async {
    try {
      await _channel.invokeMethod<void>('haptic', kind);
    } catch (_) {}
  }
}
