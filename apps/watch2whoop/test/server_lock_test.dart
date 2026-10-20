import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/app_state.dart';
import 'package:watch2whoop/src/config.dart';
import 'package:watch2whoop/src/pages/onboarding_page.dart';
import 'package:watch2whoop/src/pages/setup_link_page.dart';
import 'package:watch2whoop/src/server_lock.dart';
import 'package:watch2whoop/src/ui.dart';

const _locked = ServerLock(requested: true, todayUrl: 'https://body.example.com', owHost: 'https://sync.example.com');
const _open = ServerLock(requested: false, todayUrl: 'https://body.example.com', owHost: 'https://sync.example.com');

void main() {
  group('ServerLock', () {
    test('locked only with the flag and both built-in addresses valid', () {
      expect(_locked.locked, isTrue);
      expect(_open.locked, isFalse);
      expect(const ServerLock(requested: true, todayUrl: '', owHost: 'https://sync.example.com').locked, isFalse);
      expect(const ServerLock(requested: true, todayUrl: 'https://body.example.com', owHost: 'http://sync.example.com').locked, isFalse);
    });

    test('enforce: built-in servers always; a config for another server starts over', () {
      final fresh = _locked.enforce(const AppConfig());
      expect(fresh.reset, isFalse);
      expect(fresh.config.todayUrl, 'https://body.example.com');
      expect(fresh.config.owHost, 'https://sync.example.com');

      final same = _locked.enforce(const AppConfig(
          todayUrl: 'https://BODY.example.com/', owHost: 'https://sync.example.com', owUserId: 'u1', onboardingDone: true));
      expect(same.reset, isFalse);
      expect(same.config.owUserId, 'u1');
      expect(same.config.onboardingDone, isTrue);

      final other = _locked.enforce(const AppConfig(
          todayUrl: 'https://other.example.org', owHost: 'https://sync.example.com', owUserId: 'u1', onboardingDone: true, syncDaysBack: 30));
      expect(other.reset, isTrue);
      expect(other.config.todayUrl, 'https://body.example.com');
      expect(other.config.owUserId, isNull);
      expect(other.config.onboardingDone, isFalse);
      expect(other.config.syncDaysBack, 30);

      const mine = AppConfig(todayUrl: 'https://my.server.test', owHost: 'https://my.server.test:8443');
      expect(_open.enforce(mine).config.todayUrl, 'https://my.server.test', reason: 'unlocked builds keep what the user set');
      expect(_open.enforce(mine).reset, isFalse);
    });

    test('admit: a link for another server is refused; one for this server keeps its codes', () {
      final other = parseSetupText('watch2whoop://setup?today=https://evil.example.net&ow=https://sync.example.com&code=ABCD2345');
      expect(() => _locked.admit(other), throwsA(isA<FormatException>().having((e) => e.message, 'message', 'other_server')));
      final otherOw = parseSetupText('watch2whoop://setup?ow=https://sync.example.com:9443&code=ABCD2345');
      expect(() => _locked.admit(otherOw), throwsA(isA<FormatException>()), reason: 'another port is another server');

      final same = _locked.admit(parseSetupText(
          'watch2whoop://setup?today=body.example.com&ow=https://sync.example.com/api/v1&code=ABCD2345&login=ABCDEFGH2345'));
      expect(same.todayUrl, isNull);
      expect(same.owHost, isNull);
      expect(same.code, 'ABCD2345');
      expect(same.loginCode, 'ABCDEFGH2345');

      expect(_open.admit(other).todayUrl, 'https://evil.example.net', reason: 'unlocked builds take any server');
    });

    test('redact hides URLs and the built-in hosts only when locked', () {
      const line = 'POST https://sync.example.com:443/api/v1/sdk/users/x/sync failed; host sync.example.com, BODY.EXAMPLE.COM';
      final r = _locked.redact(line);
      expect(r.contains('example.com'), isFalse);
      expect(r.contains('EXAMPLE.COM'), isFalse);
      expect(r, contains('/api/v1/sdk/users/x/sync failed'));
      expect(_open.redact(line), line);
    });
  });

  group('onboarding', () {
    tearDown(() => ServerLock.current = const ServerLock(requested: kLockServer, todayUrl: kDefaultTodayUrl, owHost: kDefaultOwHost));

    Future<void> pump(WidgetTester t, ServerLock lock, Widget page) async {
      ServerLock.current = lock;
      AppState.instance.config = lock.enforce(const AppConfig(todayUrl: 'https://body.example.com', owHost: 'https://sync.example.com')).config;
      await t.pumpWidget(MaterialApp(home: page));
      await t.pump();
    }

    bool showsHost(WidgetTester t) => find
        .byWidgetPredicate((w) =>
            (w is Text && (w.data ?? '').contains('example.com')) ||
            (w is EditableText && w.controller.text.contains('example.com')))
        .evaluate()
        .isNotEmpty;

    testWidgets('locked: no server, no setup-link path', (t) async {
      await pump(t, _locked, const OnboardingPage());
      expect(showsHost(t), isFalse);
      expect(find.text('Server'), findsNothing);
      expect(find.text('Change'), findsNothing);
      expect(find.byType(UrlField), findsNothing);
      expect(find.text('I have a setup link'), findsNothing);
      expect(find.text('Sign in with e-mail'), findsOneWidget);
    });

    testWidgets('unlocked: the server row and the setup-link path stay', (t) async {
      await pump(t, _open, const OnboardingPage());
      expect(find.text('Server'), findsOneWidget);
      expect(showsHost(t), isTrue);
      expect(find.text('I have a setup link'), findsOneWidget);
    });

    testWidgets('locked setup-link page: no scan, paste or manual entry', (t) async {
      await pump(t, _locked, SetupLinkOnboarding(onBack: () {}));
      expect(find.text('Scan setup code'), findsNothing);
      expect(find.text('Paste setup text'), findsNothing);
      expect(find.text('Enter servers manually'), findsNothing);
      expect(showsHost(t), isFalse);
      expect(find.text('Sign in with an account instead'), findsOneWidget);
    });

    testWidgets('unlocked setup-link page keeps scan, paste and manual entry', (t) async {
      await pump(t, _open, const SetupLinkOnboarding());
      expect(find.text('Scan setup code'), findsOneWidget);
      expect(find.text('Paste setup text'), findsOneWidget);
      expect(find.text('Enter servers manually'), findsOneWidget);
    });

    test('the refusal message names no server', () {
      expect(urlError('other_server'), 'This setup link is for a different server');
    });
  });
}
