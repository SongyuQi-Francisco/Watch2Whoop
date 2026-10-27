// Native SwiftUI views over the Today page (#247 phase 2, docs/ios-native-plan.md 5.3; iOS only, from app 1.0.9):
// - the Jarvis key and its menu (ios/Runner/NativeJarvis.swift: Liquid Glass `.glassEffect()` on iOS 26+, `.ultraThinMaterial`
//   before), drawn where the page's own key is; the page hides its key while the app draws it;
// - the 今天 tab's content (ios/Runner/NativeToday.swift: three rings side by side, recovery in the middle, and the core numbers),
//   drawn over the page's content area while 今天 is the visible page.
// Both stay off unless the server's flag is on for this person (native_jarvis / native_today). Only the page knows the flags: it
// tells the app (`native:{v, jarvis, today}`, web/static/native-bridge.js), the app remembers them for the next start, and every
// load confirms or switches them off again. The page reports where the views go and whether anything covers them (`jvn:` /
// `tdn:`), and the data the 今天 tab drew (`tdn-data:`), which the app keeps on the phone so the next start shows it at once
// (cleared on sign-out). What a native key does is still decided by the page (window.BodyNative.action).
// A debug / test build can force the views on with `--dart-define=W2W_NATIVE_UI=jarvis,today[,meter]` (meter: frame timing
// in the log, NativeUI.swift FrameMeter); release builds without it behave exactly as 1.0.8.
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'i18n.dart';
import 'platform_features.dart';

const String _kFlagsKey = 'w2w.native.v1';
const String _kTodayKey = 'w2w.nativeToday.v1';

/// Forced on for a debug / test build (never set in the release configuration).
const String kNativeOverride = String.fromEnvironment('W2W_NATIVE_UI');

/// The page's messages are short; anything longer is not ours.
const int _kMaxMessage = 64 * 1024;

/// Whether this build can draw native views here: iOS only (Android keeps the page as it is).
bool get hasNativeViews => !isAndroid;

@immutable
class NativeFlags {
  const NativeFlags({this.jarvis = false, this.today = false});
  final bool jarvis, today;

  static NativeFlags fromJson(Object? j) =>
      j is Map ? NativeFlags(jarvis: j['jarvis'] == true, today: j['today'] == true) : const NativeFlags();
  Map<String, Object?> toJson() => {'jarvis': jarvis, 'today': today};

  /// The flags with the build's override on top ([kNativeOverride]).
  NativeFlags withOverride(String override) {
    final o = override.split(',').map((s) => s.trim()).toSet();
    return NativeFlags(jarvis: jarvis || o.contains('jarvis'), today: today || o.contains('today'));
  }

  @override
  bool operator ==(Object other) => other is NativeFlags && other.jarvis == jarvis && other.today == today;
  @override
  int get hashCode => Object.hash(jarvis, today);
}

/// `native:{v, jarvis, today}` from the page, or null.
NativeFlags? parseNativeFlags(String message) {
  final j = parseNativeJson(message, 'native:');
  return j == null || j['v'] != 1 ? null : NativeFlags.fromJson(j);
}

/// The JSON object after [prefix] (`jvn:`, `tdn:`, `tdn-data:` …), or null when the message is something else.
Map<String, Object?>? parseNativeJson(String message, String prefix) {
  if (!message.startsWith(prefix) || message.length > _kMaxMessage) return null;
  try {
    final j = jsonDecode(message.substring(prefix.length));
    return j is Map ? Map<String, Object?>.from(j) : null;
  } catch (_) {
    return null;
  }
}

/// `[x, y, width, height]` in points (CSS pixels of the full-screen WebView), or null.
List<double>? parseRect(Object? r) {
  if (r is! List || r.length != 4 || r.any((v) => v is! num)) return null;
  final v = r.map((e) => (e as num).toDouble()).toList();
  if (v[2] <= 0 || v[3] <= 0 || v.any((e) => !e.isFinite || e.abs() > 10000)) return null;
  return v;
}

/// The page asks for the app's camera (jarvis-entry.js runKey("photo") while the app draws the key).
const String kNativeCameraMessage = 'jv-camera';

/// Tells the page that this app can draw native views (it then loads native-bridge.js and reports the flags).
const String kNativeUiFeatureJs = 'window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},{nativeUi:{v:1}});'
    "window.dispatchEvent(new Event('w2w-features'));";

/// Which native views the app draws right now (the page hides its own Jarvis key for `nativeJarvis`).
String nativeFeaturesJs({required bool jarvis, required bool today}) =>
    'window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},'
    '{nativeUi:{v:1},nativeJarvis:${jarvis ? '{v:1,camera:true}' : 'false'},nativeToday:${today ? '{v:1}' : 'false'}});'
    "window.dispatchEvent(new Event('w2w-features'));";

const Set<String> _kJarvisIds = {'tap', 'hold', 'chat', 'voice', 'photo', 'quick'};
const Set<String> _kHoldIds = {'hold-start', 'hold-move', 'hold-end', 'hold-cancel'};

/// What the native key shows (native-bridge.js jvState phase).
const Set<String> kJarvisPhases = {'idle', 'recording', 'recognizing', 'review', 'sending', 'answering'};

/// JS that hands a native action to the page; null for anything the page does not take.
String? nativeActionJs(Map<String, Object?> a) {
  final kind = a['kind'];
  if (kind == 'jarvis' && _kJarvisIds.contains(a['id'])) {
    return 'window.BodyNative&&window.BodyNative.action(${jsonEncode({'kind': 'jarvis', 'id': a['id']})});';
  }
  // Hold to talk on the native key (native-bridge.js jvHold): the finger's position in points = the page's CSS pixels
  final x = a['x'], y = a['y'];
  if (kind == 'jarvis' && _kHoldIds.contains(a['id']) && x is num && y is num && x.isFinite && y.isFinite &&
      x.abs() < 10000 && y.abs() < 10000) {
    double r(num v) => (v * 10).round() / 10;
    return 'window.BodyNative&&window.BodyNative.action(${jsonEncode({'kind': 'jarvis', 'id': a['id'], 'x': r(x), 'y': r(y)})});';
  }
  final card = a['card'];
  if (kind == 'today' && card is String && RegExp(r'^[a-z0-9_.:-]{1,40}$').hasMatch(card)) {
    return 'window.BodyNative&&window.BodyNative.action(${jsonEncode({'kind': 'today', 'card': card})});';
  }
  return null;
}

/// JS that hands a photo from the app's camera to the page (base64, JPEG / PNG / HEIC only).
String? nativePhotoJs(String b64, String mime) {
  if (!const {'image/jpeg', 'image/png', 'image/heic'}.contains(mime)) return null;
  if (!RegExp(r'^[A-Za-z0-9+/]+=*$').hasMatch(b64)) return null;
  return 'window.BodyNative&&window.BodyNative.photo("$b64","$mime");';
}

// ---------------------------------------------------------------- the 今天 model for NativeToday.swift
// The same numbers as the page (app.js scoresHtml / activityHtml): recovery 0–100 in its zone colour, sleep score 0–100,
// yesterday's strain on the 0–21 scale, HRV / resting HR / sleep / need / debt, steps / active energy / exercise minutes.

/// I18N.num of the page: at most [digits] decimals, thousands separated by commas.
String fmtNum(num v, [int digits = 0]) {
  var s = v.toStringAsFixed(digits);
  if (s.contains('.')) s = s.replaceFirst(RegExp(r'\.?0+$'), '');
  final neg = s.startsWith('-');
  if (neg) s = s.substring(1);
  final parts = s.split('.');
  final grouped = parts[0].replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ',');
  return '${neg ? '-' : ''}$grouped${parts.length > 1 ? '.${parts[1]}' : ''}';
}

/// Minutes as h:mm (app.js hm).
String? hm(Object? m) {
  if (m is! num) return null;
  final r = m.round();
  return '${r ~/ 60}:${(r % 60).toString().padLeft(2, '0')}';
}

const List<String> _kMonths = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/// A date for a small label: 10/25 in Chinese, Oct 25 in English.
String shortDay(Object? iso) {
  final d = DateTime.tryParse('${iso ?? ''}'.length >= 10 ? '$iso'.substring(0, 10) : '');
  if (d == null) return '';
  return isZh ? '${d.month}/${d.day}' : '${_kMonths[d.month - 1]} ${d.day}';
}

const Map<String, String> _kZoneTone = {'green': 'good', 'yellow': 'mid', 'red': 'bad'};

/// The strain target's state (app.js strainCoachHtml): on target / over / how much is left; null without a current strain.
String? _coachState(Map<String, Object?> c) {
  if (c['status'] == 'in_range') return tr('达标', 'On target');
  if (c['status'] == 'above') return tr('超出', 'Over');
  final cur = c['current_strain'], low = c['target_low'], left = c['remaining_to_low'];
  if (cur is! num || low is! num) return null;
  final n = left is num ? left : low - cur;
  return tr('差 ${fmtNum(n, 1)}', '${fmtNum(n, 1)} to go');
}

/// The page's VITAL_NAME (app.js): the health monitor's names, otherwise the server's label.
String _vitalName(Map<String, Object?> x) => switch (x['key']) {
      'rhr' => tr('静息心率', 'Resting HR'),
      'hrv' => 'HRV',
      'resp_rate' => tr('呼吸', 'Breathing'),
      'spo2' => tr('血氧', 'Blood oxygen'),
      'wrist_temp' => tr('体温', 'Temperature'),
      _ => '${x['label'] ?? ''}',
    };

Map<String, Object?> _m(String label, String? value, {String? unit, String? card}) =>
    {'label': label, 'value': value ?? '—', 'unit': ?(value == null ? null : unit), 'card': ?card};

/// What NativeToday.swift draws, from a `tdn-data:` message (native-bridge.js sendData); null when it is not usable.
Map<String, Object?>? todayModel(Map<String, Object?> data) {
  if (data['v'] != 1 || data['scores'] is! Map) return null;
  final sc = Map<String, Object?>.from(data['scores'] as Map);
  Map<String, Object?> sub(Object? o) => o is Map ? Map<String, Object?>.from(o) : const {};
  final t = sub(sc['today']), y = sub(sc['yesterday']), daily = sub(data['daily']), coach = sub(data['coach']);
  final monitor = (data['monitor'] is List ? data['monitor'] as List : const []).whereType<Map>().map(sub).toList();
  num? n(Map<String, Object?> o, String k) => o[k] is num ? o[k] as num : null;
  final rec = n(t, 'recovery'), sleep = n(t, 'sleep_score'), strain = n(y, 'strain');
  final last = sub(sc['last_recovery']), empty = sub(sc['recovery_empty']), load = sub(sc['load_status']);
  final word = sc['recovery_word'] is String ? sc['recovery_word'] as String : (rec == null && empty['label'] is String ? empty['label'] as String : null);
  final reminders = data['reminders'] is int ? data['reminders'] as int : 0;
  return {
    'day': data['day'],
    'uid': data['uid'],
    'rings': [
      {'key': 'sleep', 'card': 'sleep_score', 'label': tr('睡眠分', 'Sleep score'), 'value': sleep == null ? '—' : fmtNum(sleep),
        'frac': sleep == null ? 0 : (sleep.clamp(0, 100) / 100), 'tone': 'sleep'},
      {'key': 'recovery', 'card': 'recovery', 'label': tr('恢复', 'Recovery'), 'value': rec == null ? '—' : fmtNum(rec),
        'frac': rec == null ? 0 : (rec.clamp(0, 100) / 100), 'tone': _kZoneTone[sc['recovery_zone']] ?? 'text'},
      {'key': 'strain', 'card': 'strain', 'label': tr('昨天负荷', 'Prev. strain'), 'value': strain == null ? '—' : fmtNum(strain, 1),
        'frac': strain == null ? 0 : (strain.clamp(0, 21) / 21), 'tone': 'train'},
    ],
    'word': ?word,
    'muted': rec == null,
    'sub': ?(rec == null && n(last, 'recovery') != null ? '${tr('上次', 'Last')} ${fmtNum(n(last, 'recovery')!)} · ${shortDay(last['date'])}' : null),
    'error': ?(sc['error'] is String ? tr('暂时算不出评分', 'Scores unavailable') : null),
    'sections': [
      {'title': tr('恢复', 'Recovery'), 'items': [
        _m('HRV', n(t, 'hrv_night_ms') == null ? null : fmtNum(n(t, 'hrv_night_ms')!), unit: 'ms', card: 'recovery'),
        _m(tr('静息心率', 'Resting HR'), n(t, 'rhr_night') == null ? null : fmtNum(n(t, 'rhr_night')!), unit: 'bpm', card: 'recovery'),
      ]},
      {'title': tr('睡眠', 'Sleep'), 'items': [
        _m(tr('睡眠', 'Sleep'), hm(t['sleep_actual_min']), card: 'sleep'),
        _m(tr('需求', 'Need'), hm(t['sleep_need_min']), card: 'sleep_score'),
        _m(tr('睡眠债', 'Sleep debt'), hm(t['sleep_debt_min']), card: 'sleep_score'),
      ]},
      {'title': tr('活动', 'Activity'), 'items': [
        _m(tr('步数', 'Steps'), n(daily, 'steps') == null ? null : fmtNum(n(daily, 'steps')!)),
        _m(tr('活动', 'Activity'), n(daily, 'active_kcal') == null ? null : fmtNum(n(daily, 'active_kcal')!), unit: 'kcal'),
        _m(tr('锻炼', 'Exercise'), n(daily, 'exercise_min') == null ? null : fmtNum(n(daily, 'exercise_min')!), unit: tr('分', 'min')),
      ]},
      if (n(coach, 'target_low') != null && n(coach, 'target_high') != null)
        {'title': tr('负荷目标', 'Strain target'), 'items': [
          _m(tr('区间', 'Range'), '${fmtNum(n(coach, 'target_low')!)}–${fmtNum(n(coach, 'target_high')!)}', card: 'strain_coach'),
          _m(tr('现在', 'Now'), n(coach, 'current_strain') == null ? null : fmtNum(n(coach, 'current_strain')!, 1), card: 'strain_coach'),
          {
            ..._m(tr('状态', 'Status'), _coachState(coach), card: 'strain_coach'),
            'tone': ?(coach['status'] == 'in_range' ? 'good' : coach['status'] == 'above' ? 'mid' : null),
          },
        ]},
      if (monitor.any((x) => x['value'] is num))
        {'title': tr('健康监测', 'Health monitor'), 'items': [
          for (final x in monitor)
            {
              ..._m(_vitalName(x), x['value'] is num ? fmtNum(x['value'] as num, 1) : null, unit: x['unit'] is String ? x['unit'] as String : null, card: 'health_monitor'),
              'tone': ?(x['value'] is num && (x['status'] == 'high' || x['status'] == 'low') ? 'bad' : null),
            },
        ]},
    ],
    'chips': [
      if (load['label'] is String) {'label': '${tr('负荷', 'Strain')} ${load['label']}', 'card': 'training_load'},
      if (reminders > 0) {'label': tr('$reminders 条提醒', '$reminders reminders')},
    ],
    'labels': {'web': tr('完整今天页', 'Full Today page'), 'refresh': tr('下拉刷新', 'Pull to refresh')},
  };
}

// ---------------------------------------------------------------- the controller (one per Today page)

/// Keeps NativeUI.swift in step with the page and the app (route on top, page loaded). Every call is safe on a platform or in a
/// test without the native side.
class NativeUi {
  NativeUi({required this.runJs, this.override = kNativeOverride, MethodChannel? channel})
      : _ch = channel ?? const MethodChannel('watch2whoop/nativeui');

  final Future<void> Function(String js) runJs;
  final String override;
  final MethodChannel _ch;

  NativeFlags _flags = const NativeFlags();
  bool _routeTop = true, _pageReady = false, _pageUsable = true, _started = false, _disposed = false;
  Map<String, Object?>? _jv, _td, _model;
  Map<String, Object?> _todayCache = const {};
  String _lastSent = '';

  /// The views this app draws now (flags + override, iOS only).
  NativeFlags get active => hasNativeViews ? _flags.withOverride(override) : const NativeFlags();
  bool get meter => override.split(',').contains('meter');

  Future<void> start() async {
    if (!hasNativeViews || _started) return;
    _started = true;
    try {
      final p = await SharedPreferences.getInstance();
      _flags = NativeFlags.fromJson(_decode(p.getString(_kFlagsKey)));
      final c = _decode(p.getString(_kTodayKey));
      if (c is Map) _todayCache = Map<String, Object?>.from(c);
      if (_todayCache['data'] is Map) _model = todayModel(Map<String, Object?>.from(_todayCache['data'] as Map));
    } catch (e) {
      debugPrint('native ui load failed: $e');
    }
    _ch.setMethodCallHandler(_fromNative);
    _push();
  }

  static Object? _decode(String? s) {
    if (s == null) return null;
    try {
      return jsonDecode(s);
    } catch (_) {
      return null;
    }
  }

  /// The features JS for the loaded page (sent after every page load), or null on Android.
  String? featuresJs() => hasNativeViews ? nativeFeaturesJs(jarvis: active.jarvis, today: active.today) : null;

  /// A new page is loading (nothing it reported before holds) / it loaded.
  void pageLoading() {
    _pageReady = false;
    _jv = _td = null;
    _push();
  }

  void pageLoaded() {
    _pageReady = true;
    _push();
  }

  /// The real page is on screen (not the error view, not the kept offline copy).
  void setPageUsable(bool v) {
    if (_pageUsable == v) return;
    _pageUsable = v;
    _push();
  }

  /// The Today page's route is the top one (no sync page / dialog over it).
  void setRouteTop(bool v) {
    if (_routeTop == v) return;
    _routeTop = v;
    _push();
  }

  /// Handles the page's messages that are about native views; true when [message] was one of them.
  bool handle(String message) {
    if (!hasNativeViews) return false;
    final flags = parseNativeFlags(message);
    if (flags != null) {
      _setFlags(flags);
      return true;
    }
    if (message == kNativeCameraMessage) {
      if (active.jarvis) unawaited(_invoke('camera'));
      return true;
    }
    final jv = parseNativeJson(message, 'jvn:');
    if (jv != null) {
      _jv = jv;
      _push();
      return true;
    }
    final td = parseNativeJson(message, 'tdn:');
    if (td != null) {
      _td = td;
      _push();
      unawaited(_saveToday(state: td));
      return true;
    }
    final data = parseNativeJson(message, 'tdn-data:');
    if (data != null) {
      final m = todayModel(data);
      if (m != null) {
        _model = m;
        _push();
        unawaited(_saveToday(data: data));
      }
      return true;
    }
    return false;
  }

  Future<void> _setFlags(NativeFlags f) async {
    final before = active;
    _flags = f;
    try {
      final p = await SharedPreferences.getInstance();
      await p.setString(_kFlagsKey, jsonEncode(f.toJson()));
      if (!f.today) await p.remove(_kTodayKey);
    } catch (_) {}
    if (!active.today) _todayCache = const {};
    if (active != before) {
      final js = featuresJs();
      if (js != null) await runJs(js).catchError((_) {});
    }
    _push();
  }

  Future<void> _saveToday({Map<String, Object?>? state, Map<String, Object?>? data}) async {
    if (!active.today) return;
    _todayCache = {
      ..._todayCache,
      if (state != null) 'state': {'show': state['show'] == true, 'rect': state['rect'], 'dark': state['dark'], 'theme': state['theme']},
      'data': ?data,
    };
    try {
      final p = await SharedPreferences.getInstance();
      await p.setString(_kTodayKey, jsonEncode(_todayCache));
    } catch (_) {}
  }

  /// The state NativeUI.swift draws (pure, for tests).
  @visibleForTesting
  Map<String, Object?> snapshot() {
    final a = active;
    final usable = _routeTop && _pageUsable;
    final jv = _jv ?? const {};
    final cached = _todayCache['state'] is Map ? Map<String, Object?>.from(_todayCache['state'] as Map) : const <String, Object?>{};
    // Before the page reported (a cold start, a reload): the copy kept from the last time, where it was shown last
    final td = _td ?? (_pageReady ? const <String, Object?>{} : cached);
    return {
      'jarvis': {
        'enabled': a.jarvis,
        'show': a.jarvis && usable && _pageReady && jv['show'] == true && parseRect(jv['rect']) != null,
        'rect': parseRect(jv['rect']),
        'menu': jv['menu'] == true,
        'voice': jv['voice'] is String ? jv['voice'] : 'off',
        'offline': jv['offline'] == true,
        'talk': jv['talk'] == true,
        'phase': kJarvisPhases.contains(jv['phase']) ? jv['phase'] : 'idle',
        'dark': jv['dark'] == true,
        'theme': jv['theme'] is Map ? jv['theme'] : null,
        'labels': jv['labels'] is Map ? jv['labels'] : null,
      },
      'today': {
        'enabled': a.today,
        'show': a.today && usable && td['show'] == true && parseRect(td['rect']) != null && _model != null,
        'rect': parseRect(td['rect']),
        'dark': td['dark'] == true,
        'theme': td['theme'] is Map ? td['theme'] : null,
        'model': _model,
      },
      'meter': meter,
      'material': override.split(',').contains('material'), // debug: the pre-iOS 26 keys on an iOS 26 simulator
      // debug: open / close the menu, the hold gestures and scrolling 今天 by themselves (frame timing); demo2 = only hold and lift
      'demo': override.split(',').any((o) => o == 'demo' || o == 'demo2'),
      'demo2': override.split(',').contains('demo2'),
    };
  }

  void _push() {
    if (!_started || _disposed) return;
    final s = snapshot();
    final key = jsonEncode(s);
    if (key == _lastSent) return;
    _lastSent = key;
    unawaited(_invoke('update', s));
  }

  Future<Object?> _invoke(String method, [Object? args]) async {
    try {
      return await _ch.invokeMethod<Object?>(method, args);
    } on MissingPluginException {
      return null;
    } catch (e) {
      debugPrint('native ui $method failed: $e');
      return null;
    }
  }

  Future<Object?> _fromNative(MethodCall call) async {
    final args = call.arguments is Map ? Map<String, Object?>.from(call.arguments as Map) : const <String, Object?>{};
    switch (call.method) {
      case 'action':
        final js = nativeActionJs(args);
        if (js != null) await runJs(js).catchError((_) {});
      case 'photo':
        final js = nativePhotoJs('${args['b64'] ?? ''}', '${args['mime'] ?? ''}');
        if (js != null) await runJs(js).catchError((_) {});
      case 'meter':
        debugPrint('[frame-meter] ${jsonEncode(args)}');
    }
    return null;
  }

  Future<void> dispose() async {
    _disposed = true;
    if (!_started) return;
    _ch.setMethodCallHandler(null);
    await _invoke('update', {
      'jarvis': {'enabled': false},
      'today': {'enabled': false},
      'meter': false,
    });
  }

  /// Sign-out: the kept copy of 今天 and the remembered flags go (docs/ios-native-plan.md D5).
  static Future<void> clearCache() async {
    try {
      final p = await SharedPreferences.getInstance();
      await p.remove(_kTodayKey);
      await p.remove(_kFlagsKey);
    } catch (_) {}
  }
}
