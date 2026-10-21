// 中文 / English switch on the native first-run screens: switching redraws at once (inputs kept), is saved, and is
// handed to the Today page in the fragment so the page uses it and saves it to the profile after login.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/app_state.dart';
import 'package:watch2whoop/src/config.dart';
import 'package:watch2whoop/src/i18n.dart';
import 'package:watch2whoop/src/pages/onboarding_page.dart';
import 'package:watch2whoop/src/pages/setup_link_page.dart';
import 'package:watch2whoop/src/server_lock.dart';
import 'package:watch2whoop/src/ui.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  systemLanguageCode = () => 'en';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await setLangOverride(null);
    AppState.instance.config = const AppConfig(todayUrl: 'https://body.example.com', owHost: 'https://sync.example.com');
  });
  tearDown(() => ServerLock.current = const ServerLock(requested: kLockServer, todayUrl: kDefaultTodayUrl, owHost: kDefaultOwHost));

  group('withLangFragment', () {
    final entry = Uri.parse('https://body.example.com/app/');
    test('no override: the entry is unchanged (the page follows the profile / device)', () {
      expect(withLangFragment(entry), entry);
      expect(withLangFragment(entry.replace(fragment: 'login=ABCDEFGH2345')).fragment, 'login=ABCDEFGH2345');
    });

    test('override: added next to the login code, replacing an old one', () async {
      await setLangOverride('en');
      expect(withLangFragment(entry).toString(), 'https://body.example.com/app/#lang=en');
      expect(withLangFragment(entry.replace(fragment: 'login=ABCDEFGH2345')).fragment, 'login=ABCDEFGH2345&lang=en');
      await setLangOverride('zh');
      expect(withLangFragment(entry.replace(fragment: 'login=ABCDEFGH2345&lang=en')).fragment, 'login=ABCDEFGH2345&lang=zh');
    });
  });

  Future<void> pump(WidgetTester t, Widget page) async {
    await t.pumpWidget(MaterialApp(home: page));
    await t.pump();
  }

  testWidgets('sign-in screen: the switch redraws at once and is saved', (t) async {
    await pump(t, const OnboardingPage());
    expect(find.byType(LangSwitch), findsOneWidget);
    expect(find.text('Sign in with e-mail'), findsOneWidget);

    await t.tap(find.text('中文'));
    await t.pump();
    expect(langOverride.value, 'zh');
    expect(find.text('用邮箱登录'), findsOneWidget);
    expect(find.text('Sign in with e-mail'), findsNothing);
    expect((await SharedPreferences.getInstance()).getString('w2w.lang.v1'), 'zh');

    await t.tap(find.text('English'));
    await t.pump();
    expect(find.text('Sign in with e-mail'), findsOneWidget);
    expect(currentLang, 'en');
  });

  testWidgets('switching keeps the step and what was typed', (t) async {
    ServerLock.current = const ServerLock(requested: true, todayUrl: 'https://body.example.com', owHost: 'https://sync.example.com');
    await pump(t, const OnboardingPage());
    await t.tap(find.text('Sign in with e-mail'));
    await t.pump();
    await t.enterText(find.byType(TextField), 'someone@example.com');
    await t.tap(find.text('中文'));
    await t.pump();
    expect(find.text('发送验证码'), findsOneWidget);
    expect(find.text('someone@example.com'), findsOneWidget);
    expect(find.byType(LangSwitch), findsOneWidget, reason: 'on every step, locked builds too');
  });

  testWidgets('setup-link screen has the switch too', (t) async {
    await pump(t, const SetupLinkOnboarding());
    expect(find.text('Scan setup code'), findsOneWidget);
    await t.tap(find.text('中文'));
    await t.pump();
    expect(find.text('扫描设置二维码'), findsOneWidget);
  });
}
