// Language override (issue #42): the Today page reports the profile language over the JS channel; native screens follow it.
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/i18n.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  var system = 'en';
  systemLanguageCode = () => system;

  setUp(() async {
    system = 'en';
    SharedPreferences.setMockInitialValues({});
    await setLangOverride(null);
  });

  group('parseLangMessage', () {
    test('lang:zh / lang:en set the override, lang: follows the system', () {
      expect(parseLangMessage('lang:zh'), (lang: 'zh'));
      expect(parseLangMessage('lang:en'), (lang: 'en'));
      expect(parseLangMessage('lang:'), (lang: null));
    });

    test('regional tags are normalized, unsupported languages follow the system', () {
      expect(parseLangMessage('lang:zh-Hans'), (lang: 'zh'));
      expect(parseLangMessage('lang:en_AU'), (lang: 'en'));
      expect(parseLangMessage('lang:fr'), (lang: null));
      expect(parseLangMessage('lang: EN '), (lang: 'en'));
    });

    test('other bridge messages are not language messages', () {
      for (final m in ['logged-in', 'login-failed', 'sync', 'settings', 'w2w-me:200', 'apple-sign-in', 'language:zh', '']) {
        expect(parseLangMessage(m), isNull, reason: m);
      }
    });
  });

  group('override', () {
    test('isZh / tr follow the system language without an override', () {
      system = 'zh';
      expect(isZh, isTrue);
      expect(tr('中文', 'English'), '中文');
      system = 'en';
      expect(isZh, isFalse);
      expect(tr('中文', 'English'), 'English');
    });

    test('the override wins over the system language, null goes back to it', () async {
      system = 'zh';
      await setLangOverride('en');
      expect(isZh, isFalse);
      expect(tr('中文', 'English'), 'English');
      expect(typeLabel('HeartRate'), 'Heart rate');

      system = 'en';
      await setLangOverride('zh');
      expect(isZh, isTrue);
      expect(typeLabel('HeartRate'), '心率');

      await setLangOverride(null);
      expect(isZh, isFalse);
    });

    test('is saved in SharedPreferences and loaded again', () async {
      await setLangOverride('zh');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('w2w.lang.v1'), 'zh');

      langOverride.value = null; // simulate a cold start
      await loadLangOverride();
      expect(langOverride.value, 'zh');

      await setLangOverride(null);
      expect(prefs.containsKey('w2w.lang.v1'), isFalse);
      await loadLangOverride();
      expect(langOverride.value, isNull);
    });

    test('loads a value saved by an earlier run; garbage means system', () async {
      SharedPreferences.setMockInitialValues({'w2w.lang.v1': 'en'});
      await loadLangOverride();
      expect(langOverride.value, 'en');
      SharedPreferences.setMockInitialValues({'w2w.lang.v1': 'xx'});
      await loadLangOverride();
      expect(langOverride.value, isNull);
    });

    test('listeners (the root widget) are notified only on a change', () async {
      var n = 0;
      void count() => n++;
      langOverride.addListener(count);
      addTearDown(() => langOverride.removeListener(count));
      await setLangOverride('en');
      await setLangOverride('en');
      await setLangOverride('zh');
      await setLangOverride(null);
      expect(n, 3);
    });
  });
}
