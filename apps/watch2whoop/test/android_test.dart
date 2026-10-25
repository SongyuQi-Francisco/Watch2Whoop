// Android build (#253): Huawei phones on HarmonyOS 4.x and older, no Google services. Health sync, push and Sign in with
// Apple are off and degrade; file inputs, the microphone and setup links go through MainActivity.kt. iOS is unchanged.
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/android_bridge.dart';
import 'package:watch2whoop/src/app_state.dart';
import 'package:watch2whoop/src/config.dart';
import 'package:watch2whoop/src/health_sync.dart';
import 'package:watch2whoop/src/i18n.dart';
import 'package:watch2whoop/src/pages/permissions_page.dart';
import 'package:watch2whoop/src/pages/sync_page.dart';
import 'package:watch2whoop/src/platform_features.dart';
import 'package:watch2whoop/src/push.dart';
import 'package:watch2whoop/src/raw_sync.dart';
import 'package:watch2whoop/src/setup_flow.dart';
import 'package:watch2whoop/src/ui.dart';
import 'package:watch2whoop/src/web_mic.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  systemLanguageCode = () => 'en';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await setLangOverride(null);
    androidDevice = () => true;
    AppState.instance.config = const AppConfig(todayUrl: 'https://body.example.com', owHost: 'https://sync.example.com');
    AppState.instance.accountToken = null;
  });
  tearDown(() => androidDevice = () => false);

  group('features', () {
    test('Android: no health sync, no push; the test host (iOS rules) has both', () {
      expect(isAndroid, isTrue);
      expect(hasHealthSync, isFalse);
      expect(hasPush, isFalse);
      androidDevice = () => false;
      expect(hasHealthSync, isTrue);
      expect(hasPush, isTrue);
    });

    test('the Health permissions step never shows on Android', () {
      const c = AppConfig(onboardingDone: true);
      expect(shouldShowPermissions(c), isFalse);
      androidDevice = () => false;
      expect(shouldShowPermissions(c), isTrue);
    });
  });

  group('health sync degrades without touching the SDK', () {
    test('status, authorization, sync and connect', () async {
      final h = HealthSync.instance;
      await h.start(AppState.instance.config);
      expect(h.isSignedIn, isFalse);
      expect(h.isSyncActive, isFalse);
      expect((await h.status()).isSyncing, isFalse);
      expect(await h.requestAuthorization(), isFalse);
      expect(await h.startSync(90), isFalse);
      await h.changeRange(0);
      await h.resyncFrom(7);
      await h.retry(90);
      await h.disconnect();
      await expectLater(h.connect(owHost: 'https://sync.example.com', code: 'ABCD2345'),
          throwsA(isA<ConnectException>().having((e) => e.code, 'code', 'unsupported')));
      expect(connectError(ConnectException('unsupported')), watchDataPending);
    });

    test('beat-to-beat / ECG reading is off', () async {
      expect(await RawSync.instance.requestAuth(), isFalse);
      final r = await RawSync.instance.run(
          serverUrl: 'https://body.example.com', daysBack: 90, accountToken: 't', pageToken: () async => null);
      expect(r, isNull);
    });

    test('a setup link keeps the servers and the login code, and leaves the sync code unused', () async {
      final r = await applySetup(const SetupInfo(
          todayUrl: 'https://body2.example.com', owHost: 'https://sync2.example.com', code: 'ABCD2345', loginCode: 'LOGIN123'));
      expect(r.serversSet, isTrue);
      expect(r.loginCodeSaved, isTrue);
      expect(r.owConnected, isFalse);
      expect(r.owError, isNull, reason: 'not an error: there is simply no sync to connect on Android yet');
      expect(AppState.instance.config.todayUrl, 'https://body2.example.com');
      expect(AppState.instance.config.pendingLoginCode, 'LOGIN123');
    });
  });

  test('push answers "unsupported" without a native side', () async {
    AppState.instance.accountToken = 'token';
    expect(await PushService.instance.status(), 'unsupported');
    expect(await PushService.instance.ask(), 'unsupported');
  });

  group('file inputs', () {
    test('accept types are split, trimmed and lower-cased', () {
      expect(normalizeAcceptTypes(['image/*']), ['image/*']);
      expect(normalizeAcceptTypes(['.csv,text/csv']), ['.csv', 'text/csv']);
      expect(normalizeAcceptTypes([' Image/PNG , image/jpeg', 'image/png', '']), ['image/png', 'image/jpeg']);
      expect(normalizeAcceptTypes(const []), isEmpty);
    });

    test('the camera is offered for photos or videos only', () {
      expect(cameraKinds(['image/*']), (photo: true, video: false));
      expect(cameraKinds(['image/png', 'image/heic']), (photo: true, video: false));
      expect(cameraKinds(['video/*']), (photo: false, video: true));
      expect(cameraKinds(['.csv', 'text/csv']), (photo: false, video: false));
      expect(cameraKinds(const []), (photo: true, video: false), reason: 'no accept = any file');
      expect(cameraKinds(['*/*']), (photo: true, video: false));
    });

    test('the request for the native picker (the Today page inputs)', () {
      // Jarvis camera button: <input accept="image/*" capture="environment">
      expect(pickRequest(acceptTypes: ['image/*'], multiple: false, capture: true),
          {'accept': ['image/*'], 'multiple': false, 'capture': true, 'photo': true, 'video': false});
      // AI chat photos: <input accept="image/*" multiple>
      expect(pickRequest(acceptTypes: ['image/*'], multiple: true, capture: false)['multiple'], isTrue);
      // CSV import: capture makes no sense there and is dropped
      expect(pickRequest(acceptTypes: ['.csv,text/csv'], multiple: false, capture: true)['capture'], isFalse);
    });

    test('without the native side nothing is picked and the microphone stays off', () async {
      expect(await AndroidBridge.pickFiles(acceptTypes: ['image/*'], multiple: false, capture: false), isEmpty);
      expect(await AndroidBridge.hasMic(), isFalse);
      expect(await AndroidBridge.requestMic(), isFalse);
      await AndroidBridge.openAppSettings(); // no throw
    });

    test('mic off = the page keeps the keyboard dictation key', () {
      expect(kFeaturesNoMicJs, contains('{mic:false,syncCenter:true,syncRepair:true}'));
      expect(kFeaturesNoMicJs.replaceAll('mic:false', 'mic:true'), kFeaturesJs);
    });
  });

  testWidgets('the sync page says the watch data is in development and keeps the settings', (t) async {
    PackageInfo.setMockInitialValues(
        appName: 'Watch2Whoop', packageName: 'org.watch2whoop.app', version: '1.0.7', buildNumber: '7', buildSignature: '');
    AppState.instance.accountToken = 'token';
    await t.pumpWidget(const MaterialApp(home: SyncPage()));
    await t.pump();
    expect(find.text('Huawei watch data support is in development'), findsOneWidget);
    expect(find.text('Scan setup code'), findsOneWidget);
    expect(find.text('Paste setup text'), findsOneWidget);
    expect(find.text('Sign out'), findsOneWidget);
    expect(find.text('Sync now'), findsNothing);
    expect(find.text('Read range'), findsNothing);
  });

  group('Android project', () {
    String read(String p) => File(p).readAsStringSync();

    test('manifest: network, microphone, setup links, no backups, no health permissions of the OW plugin', () {
      final m = read('android/app/src/main/AndroidManifest.xml');
      for (final s in [
        'android.permission.INTERNET',
        'android.permission.RECORD_AUDIO',
        'android.permission.CAMERA',
        'android:scheme="watch2whoop"',
        'android:allowBackup="false"',
        '<meta-data android:name="flutter_deeplinking_enabled" android:value="false" />',
        'androidx.core.content.FileProvider',
      ]) {
        expect(m, contains(s));
      }
      for (final p in ['FOREGROUND_SERVICE_HEALTH', 'POST_NOTIFICATIONS', 'health.READ_HEART_RATE', 'health.READ_SLEEP',
          'health.READ_HEALTH_DATA_IN_BACKGROUND']) {
        expect(m, contains('android.permission.$p" tools:node="remove"'), reason: p);
      }
    });

    test('MainActivity answers the channels the Dart side uses', () {
      final k = read('android/app/src/main/kotlin/org/watch2whoop/app/MainActivity.kt');
      for (final s in ['"watch2whoop/native"', '"takeLinks"', '"linksPending"', '"watch2whoop/android"', '"hasMic"',
          '"requestMic"', '"openAppSettings"', '"pickFiles"', r'"$packageName.files"']) {
        expect(k, contains(s));
      }
    });

    test('identity and signing are placeholders / come from outside git', () {
      final props = read('android/Watch2Whoop.properties');
      expect(props, contains('W2W_APPLICATION_ID=org.watch2whoop.app'));
      final gradle = read('android/app/build.gradle.kts');
      expect(gradle, contains('Watch2Whoop.local.properties'));
      expect(gradle, contains('W2W_REQUIRE_RELEASE_SIGNING'));
      expect(gradle, isNot(contains('storePassword = "')), reason: 'no password in the build file');
      final ignore = read('android/.gitignore');
      for (final s in ['/Watch2Whoop.local.properties', '**/*.jks', '**/*.keystore', 'key.properties']) {
        expect(ignore, contains(s));
      }
    });
  });
}
