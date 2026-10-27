// Native SwiftUI views over the Today page (native_ui.dart, #247 phase 2): the page's messages, the JS back to the page, the
// 今天 model (the same numbers as the page), and what the native side is told to draw (off unless the flags say so).
import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/i18n.dart';
import 'package:watch2whoop/src/native_ui.dart';

Map<String, Object?> _data({Object? recovery = 66}) => {
      'v': 1,
      'uid': 7,
      'day': '2026-10-27',
      'scores': {
        'today': {'recovery': ?recovery, 'hrv_night_ms': 52.4, 'rhr_night': 51, 'sleep_actual_min': 432, 'sleep_score': 81, 'sleep_debt_min': 35},
        'yesterday': {'strain': 12.34, 'date': '2026-10-26'},
        'recovery_zone': 'yellow',
        'recovery_word': '一般',
        'last_recovery': {'recovery': 70, 'date': '2026-10-25'},
        'load_status': {'label': '适中'},
      },
      'daily': {'steps': 12345, 'active_kcal': 512.6},
      'reminders': 2,
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() => langOverride.value = 'zh');

  test('flags: only a v1 message counts; the build override adds views, never removes them', () {
    expect(parseNativeFlags('native:{"v":1,"jarvis":true,"today":false}'), const NativeFlags(jarvis: true));
    expect(parseNativeFlags('native:{"v":2,"jarvis":true}'), isNull);
    expect(parseNativeFlags('native:not json'), isNull);
    expect(parseNativeFlags('nativex:{"v":1}'), isNull);
    expect(const NativeFlags().withOverride(''), const NativeFlags());
    expect(const NativeFlags().withOverride('jarvis, meter'), const NativeFlags(jarvis: true));
    expect(const NativeFlags(today: true).withOverride('jarvis'), const NativeFlags(jarvis: true, today: true));
    expect(kNativeOverride, '', reason: 'release builds never force the views on');
  });

  test('messages and rects are checked', () {
    expect(parseNativeJson('jvn:{"show":true}', 'jvn:'), {'show': true});
    expect(parseNativeJson('jvn:[1]', 'jvn:'), isNull);
    expect(parseNativeJson('jvn:{"a":"${'x' * 70000}"}', 'jvn:'), isNull);
    expect(parseRect([1, 2, 3, 4]), [1.0, 2.0, 3.0, 4.0]);
    for (final bad in [null, [1, 2, 3], [0, 0, 0, 4], [0, 0, 1, '4'], [0, 0, 1, 1e9]]) {
      expect(parseRect(bad), isNull, reason: '$bad');
    }
  });

  test('only known actions and photo types reach the page', () {
    expect(nativeActionJs({'kind': 'jarvis', 'id': 'chat'}), 'window.BodyNative&&window.BodyNative.action({"kind":"jarvis","id":"chat"});');
    expect(nativeActionJs({'kind': 'jarvis', 'id': 'hold'}), contains('"hold"'));
    expect(nativeActionJs({'kind': 'jarvis', 'id': 'eval'}), isNull);
    // hold to talk: the finger's position goes along, rounded; without a usable position nothing is sent
    expect(nativeActionJs({'kind': 'jarvis', 'id': 'hold-move', 'x': 12.345, 'y': 800}),
        'window.BodyNative&&window.BodyNative.action({"kind":"jarvis","id":"hold-move","x":12.3,"y":800.0});');
    expect(nativeActionJs({'kind': 'jarvis', 'id': 'hold-end', 'x': 0, 'y': 0}), contains('"hold-end"'));
    expect(nativeActionJs({'kind': 'jarvis', 'id': 'hold-move'}), isNull);
    expect(nativeActionJs({'kind': 'jarvis', 'id': 'hold-move', 'x': double.nan, 'y': 1}), isNull);
    expect(nativeActionJs({'kind': 'jarvis', 'id': 'hold-jump', 'x': 1, 'y': 1}), isNull);
    expect(nativeActionJs({'kind': 'today', 'card': 'recovery'}), contains('"card":"recovery"'));
    expect(nativeActionJs({'kind': 'today', 'card': 'x"]);alert(1);//'}), isNull);
    expect(nativePhotoJs('QUJD', 'image/jpeg'), 'window.BodyNative&&window.BodyNative.photo("QUJD","image/jpeg");');
    expect(nativePhotoJs('QUJD"', 'image/jpeg'), isNull);
    expect(nativePhotoJs('QUJD', 'text/html'), isNull);
    expect(nativeFeaturesJs(jarvis: true, today: false), contains('nativeJarvis:{v:1,camera:true},nativeToday:false'));
    expect(kNativeUiFeatureJs, contains("dispatchEvent(new Event('w2w-features'))"));
  });

  test('numbers are formatted like the page (I18N.num, hm)', () {
    expect(fmtNum(12345), '12,345');
    expect(fmtNum(12.34, 1), '12.3');
    expect(fmtNum(12.0, 1), '12');
    expect(fmtNum(-1234.5, 1), '-1,234.5');
    expect(hm(432), '7:12');
    expect(hm(5.6), '0:06');
    expect(hm(null), isNull);
    expect(shortDay('2026-10-25'), '10/25');
    langOverride.value = 'en';
    expect(shortDay('2026-10-25'), 'Oct 25');
  });

  test('the 今天 model: three rings with recovery in the middle, the core numbers, the chips', () {
    final m = todayModel(_data())!;
    final rings = (m['rings'] as List).cast<Map<String, Object?>>();
    expect(rings.map((r) => r['key']), ['sleep', 'recovery', 'strain']);
    expect(rings[1], containsPair('value', '66'));
    expect(rings[1], containsPair('tone', 'mid'));
    expect(rings[1]['frac'], closeTo(0.66, 1e-9));
    expect(rings[2], containsPair('value', '12.3'));
    expect(rings[2]['frac'], closeTo(12.34 / 21, 1e-9));
    expect(m['word'], '一般');
    expect(m.containsKey('sub'), isFalse);
    final items = [for (final s in (m['sections'] as List).cast<Map>()) ...(s['items'] as List).cast<Map>()];
    expect({for (final i in items) i['label']: i['value']},
        {'HRV': '52', '静息心率': '51', '睡眠': '7:12', '需求': '—', '睡眠债': '0:35', '步数': '12,345', '活动': '513', '锻炼': '—'});
    expect(items.firstWhere((i) => i['label'] == '需求').containsKey('unit'), isFalse, reason: 'no unit after a dash');
    expect(m['chips'], [{'label': '负荷 适中', 'card': 'training_load'}, {'label': '2 条提醒'}]);
    // no recovery yet: an empty ring, the last one under it
    final e = todayModel(_data(recovery: null))!;
    expect((e['rings'] as List)[1], containsPair('value', '—'));
    expect(e['muted'], isTrue);
    expect(e['sub'], '上次 70 · 10/25');
    expect(todayModel({'v': 2, 'scores': {}}), isNull);
    // the strain target and the health monitor, when the page had them
    final full = todayModel({
      ..._data(),
      'coach': {'target_low': 8, 'target_high': 12, 'current_strain': 6.25, 'status': 'below'},
      'monitor': [
        {'key': 'rhr', 'label': 'x', 'value': 52, 'unit': 'bpm', 'status': 'normal'},
        {'key': 'spo2', 'value': 91.04, 'unit': '%', 'status': 'low'},
        {'key': 'resp_rate', 'value': null},
      ],
    })!;
    final secs = (full['sections'] as List).cast<Map>();
    expect(secs.map((s) => s['title']), ['恢复', '睡眠', '活动', '负荷目标', '健康监测']);
    expect((secs[3]['items'] as List).map((i) => (i as Map)['value']), ['8–12', '6.3', '差 1.8']);
    final vitals = (secs[4]['items'] as List).cast<Map>();
    expect(vitals.map((i) => '${i['label']} ${i['value']} ${i['tone']}'), ['静息心率 52 null', '血氧 91 bad', '呼吸 — null']);
  });

  group('controller', () {
    late List<MethodCall> calls;
    late List<String> js;
    const ch = MethodChannel('watch2whoop/nativeui-test');

    setUp(() {
      calls = [];
      js = [];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(ch, (c) async {
        calls.add(c);
        return null;
      });
    });

    Map<String, Object?> last() => Map<String, Object?>.from(calls.lastWhere((c) => c.method == 'update').arguments as Map);
    Map<String, Object?> part(String k) => Map<String, Object?>.from(last()[k] as Map);

    test('flags off: nothing is drawn and the page keeps its key', () async {
      SharedPreferences.setMockInitialValues({});
      final ui = NativeUi(runJs: (s) async => js.add(s), override: '', channel: ch);
      await ui.start();
      ui.pageLoaded();
      expect(ui.handle('native:{"v":1,"jarvis":false,"today":false}'), isTrue);
      await Future<void>.delayed(Duration.zero);
      expect(part('jarvis')['enabled'], isFalse);
      expect(part('today')['enabled'], isFalse);
      expect(js, isEmpty, reason: 'nothing changed, the features are not announced again');
      expect(ui.featuresJs(), contains('nativeJarvis:false,nativeToday:false'));
      expect(ui.handle('sync:now 1'), isFalse);
    });

    test('flags on: announced again, shown where the page says, hidden under another route', () async {
      SharedPreferences.setMockInitialValues({});
      final ui = NativeUi(runJs: (s) async => js.add(s), override: '', channel: ch);
      await ui.start();
      ui.pageLoaded();
      ui.handle('native:{"v":1,"jarvis":true,"today":true}');
      await Future<void>.delayed(Duration.zero);
      expect(js.single, contains('nativeJarvis:{v:1,camera:true},nativeToday:{v:1}'));
      ui.handle('jvn:${jsonEncode({'show': true, 'rect': [167, 760, 56, 56], 'menu': true})}');
      expect(part('jarvis'), containsPair('show', true));
      expect(part('jarvis')['rect'], [167.0, 760.0, 56.0, 56.0]);
      expect(part('jarvis')['phase'], 'idle');
      ui.handle('jvn:${jsonEncode({'show': true, 'rect': [167, 760, 56, 56], 'talk': true, 'phase': 'answering'})}');
      expect(part('jarvis'), containsPair('phase', 'answering'));
      expect(part('jarvis'), containsPair('talk', true));
      ui.handle('jvn:${jsonEncode({'show': true, 'rect': [167, 760, 56, 56], 'phase': 'dancing'})}');
      expect(part('jarvis')['phase'], 'idle');
      ui.handle('tdn:${jsonEncode({'show': true, 'rect': [0, 110, 390, 630]})}');
      expect(part('today')['show'], isFalse, reason: 'no data yet');
      ui.handle('tdn-data:${jsonEncode(_data())}');
      expect(part('today')['show'], isTrue);
      ui.setRouteTop(false);
      expect(part('jarvis')['show'], isFalse);
      expect(part('today')['show'], isFalse);
      ui.setRouteTop(true);
      ui.setPageUsable(false); // the error view / the kept offline copy
      expect(part('jarvis')['show'], isFalse);
      ui.setPageUsable(true);
      ui.handle(kNativeCameraMessage);
      await Future<void>.delayed(Duration.zero);
      expect(calls.where((c) => c.method == 'camera'), hasLength(1));
      await ui.dispose();
      expect(part('jarvis')['enabled'], isFalse);
    });

    test('the next start shows the kept 今天 at once; sign-out forgets it', () async {
      SharedPreferences.setMockInitialValues({});
      final a = NativeUi(runJs: (s) async {}, override: '', channel: ch);
      await a.start();
      a.pageLoaded();
      a.handle('native:{"v":1,"jarvis":false,"today":true}');
      a.handle('tdn:${jsonEncode({'show': true, 'rect': [0, 110, 390, 630], 'dark': true})}');
      a.handle('tdn-data:${jsonEncode(_data())}');
      await Future<void>.delayed(const Duration(milliseconds: 10));
      final b = NativeUi(runJs: (s) async {}, override: '', channel: ch);
      await b.start(); // the page has not loaded yet
      expect(part('today')['show'], isTrue);
      expect(part('today')['dark'], isTrue);
      expect((part('today')['model'] as Map)['day'], '2026-10-27');
      b.pageLoading();
      b.pageLoaded();
      b.handle('tdn:${jsonEncode({'show': false, 'rect': [0, 110, 390, 630]})}'); // e.g. the saved tab is 趋势
      expect(part('today')['show'], isFalse);
      await NativeUi.clearCache();
      final c = NativeUi(runJs: (s) async {}, override: '', channel: ch);
      await c.start();
      expect(part('today')['enabled'], isFalse);
    });

    test('the build override draws the views without the flags', () async {
      SharedPreferences.setMockInitialValues({});
      final ui = NativeUi(runJs: (s) async {}, override: 'jarvis,meter', channel: ch);
      await ui.start();
      expect(part('jarvis')['enabled'], isTrue);
      expect(last()['meter'], isTrue);
    });
  });

  test('the page and the app speak the same protocol', () {
    final bridge = File('../../body_engine/src/body_engine/web/static/native-bridge.js').readAsStringSync();
    for (final m in ['"native:"', '"jvn:"', '"tdn:"', '"tdn-data:"', 'w2w-features', 'window.BodyNative']) {
      expect(bridge, contains(m.replaceAll('"', '')), reason: m);
    }
    final jv = File('../../body_engine/src/body_engine/web/static/jarvis-entry.js').readAsStringSync();
    expect(jv, contains('tell("$kNativeCameraMessage")'));
    for (final id in ['chat', 'voice', 'photo', 'quick']) {
      expect(jv, contains('{ id: "$id"'));
    }
  });
}
