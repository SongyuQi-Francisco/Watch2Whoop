// Permissions step (issue #60): Health only, why / if declined / system prompt / iOS Settings once asked, shown once.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/app_state.dart';
import 'package:watch2whoop/src/config.dart';
import 'package:watch2whoop/src/i18n.dart';
import 'package:watch2whoop/src/pages/permissions_page.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  systemLanguageCode = () => 'en';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await setLangOverride(null);
    AppState.instance.config = const AppConfig(todayUrl: 'https://body.example.com', owHost: 'https://sync.example.com', onboardingDone: true);
  });

  group('shown once', () {
    test('only after onboarding, for someone never asked and not seen before', () {
      const done = AppConfig(onboardingDone: true);
      expect(shouldShowPermissions(done), isTrue);
      expect(shouldShowPermissions(const AppConfig()), isFalse, reason: 'during onboarding the step is part of the flow');
      expect(shouldShowPermissions(done.copyWith(permissionsSeen: true)), isFalse);
      expect(shouldShowPermissions(done.copyWith(healthRequested: true)), isFalse, reason: 'already asked before the update');
    });

    test('the flag is saved with the config', () {
      final c = AppConfig.fromJson(const AppConfig(permissionsSeen: true).toJson());
      expect(c.permissionsSeen, isTrue);
      expect(AppConfig.fromJson(const {}).permissionsSeen, isFalse, reason: 'configs saved by older builds');
    });
  });

  Future<void> pump(WidgetTester t, Widget w) async {
    await t.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(child: w))));
    await t.pump();
  }

  testWidgets('not asked yet: why, if declined, and the button that shows the iOS prompt', (t) async {
    var asked = 0;
    await pump(t, PermissionsPanel(onRequestHealth: () async {
      asked++;
      await AppState.instance.update(AppState.instance.config.copyWith(healthRequested: true));
    }));
    expect(find.text('Health data (read only)'), findsOneWidget);
    expect(find.textContaining('Recovery, strain and sleep scores'), findsOneWidget);
    expect(find.textContaining('Nothing is synced'), findsOneWidget);
    expect(find.text('Open Settings'), findsNothing);
    await t.tap(find.text('Allow Health access'));
    await t.pump();
    expect(asked, 1, reason: 'the button only shows the system prompt; nothing is switched on for the user');
    expect(find.text('Asked'), findsOneWidget);
    expect(find.text('Open Settings'), findsOneWidget);
  });

  testWidgets('asked before: a button opens the app page in iOS Settings', (t) async {
    AppState.instance.config = AppState.instance.config.copyWith(healthRequested: true);
    var opened = 0;
    await pump(t, PermissionsPanel(openSettings: () async => opened++));
    await t.tap(find.text('Open Settings'));
    expect(opened, 1);
    expect(find.textContaining('no notifications'), findsOneWidget, reason: 'unused permissions are not asked for');
  });

  testWidgets('stand-alone page: Done marks it seen; Chinese follows the app language', (t) async {
    await setLangOverride('zh');
    await t.pumpWidget(MaterialApp(home: Builder(builder: (c) => TextButton(
        onPressed: () => Navigator.of(c).push(MaterialPageRoute(builder: (_) => const PermissionsPage())), child: const Text('open')))));
    await t.tap(find.text('open'));
    await t.pumpAndSettle();
    expect(find.text('权限'), findsOneWidget);
    expect(find.text('允许读取健康数据'), findsOneWidget);
    await t.scrollUntilVisible(find.text('完成'), 100);
    await t.tap(find.text('完成'));
    await t.pumpAndSettle();
    expect(AppState.instance.config.permissionsSeen, isTrue);
    expect(find.text('open'), findsOneWidget);
    await setLangOverride(null);
  });

  testWidgets('closing the page counts as skipped', (t) async {
    await t.pumpWidget(MaterialApp(home: Builder(builder: (c) => TextButton(
        onPressed: () => Navigator.of(c).push(MaterialPageRoute(builder: (_) => const PermissionsPage())), child: const Text('open')))));
    await t.tap(find.text('open'));
    await t.pumpAndSettle();
    await t.pageBack();
    await t.pumpAndSettle();
    expect(AppState.instance.config.permissionsSeen, isTrue);
  });
}
