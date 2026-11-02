// Data status on the Phone sync page (issue #163): parsing the server's answer, what to offer per type (ask again only
// for types the iOS prompt never covered, otherwise iOS Settings), the last sync on this phone, and the section widget.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/data_status.dart';
import 'package:watch2whoop/src/i18n.dart';
import 'package:watch2whoop/src/pages/data_status_section.dart';
import 'package:watch2whoop/src/raw_sync.dart';
import 'package:watch2whoop/src/sync_stats.dart';

Map<String, Object?> type(String key, String label, String status, {int? since, List<String> notes = const []}) => {
      'key': key,
      'label': label,
      'channel': 'ow',
      'status': status,
      'last_date': since == null ? null : '2026-10-${22 - since}',
      'last_at': null,
      'days_since': since,
      'days': since == null ? 0 : 3,
      'count': since == null ? 0 : 120,
      'sources': since == null ? <String>[] : ['ow'],
      'stale_after_days': 2,
      'notes': notes,
    };

final serverJson = {
  'today': '2026-10-22',
  'window_days': 7,
  'types': [
    type('heart_rate', 'Heart rate', 'ok', since: 0),
    type('hrv', 'HRV', 'never'),
    type('sleep', 'Sleep', 'stale', since: 5),
    type('wrist_temperature', 'Wrist temperature', 'never', notes: ['sdk_unsupported']),
  ],
  'summary': {'ok': 1, 'stale': 1, 'never': 2},
  'channels': {'ow_last_sample_at': '2026-10-22T08:00:00+11:00', 'app_upload_at': null, 'shortcut_upload_at': null},
};

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  systemLanguageCode = () => 'en';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await setLangOverride(null);
  });

  group('fetch', () {
    test('GET /v1/ingest/data-status with the token and the language', () async {
      late http.Request seen;
      final r = await fetchDataStatus(
        serverUrl: 'https://body.example.com/app/',
        token: 'tok',
        client: MockClient((req) async {
          seen = req;
          return http.Response(jsonEncode(serverJson), 200);
        }),
      );
      expect(seen.url.toString(), 'https://body.example.com/v1/ingest/data-status');
      expect(seen.headers['Authorization'], 'Bearer tok');
      expect(seen.headers['X-Body-Lang'], 'en');
      final rep = r.report!;
      expect(rep.types.map((t) => t.state), [DataState.ok, DataState.never, DataState.stale, DataState.never]);
      expect(rep.count(DataState.never), 2);
      expect(rep.types.first.daysSince, 0);
      expect(rep.types.last.unsupported, isTrue);
      expect(rep.owLastSampleAt, isNotNull);
    });

    test('problems', () async {
      Future<DataStatusProblem?> code(int status) async => (await fetchDataStatus(
              serverUrl: 'https://body.example.com', token: 'tok', client: MockClient((_) async => http.Response('{}', status))))
          .problem;
      expect(await code(401), DataStatusProblem.unauthorized);
      expect(await code(404), DataStatusProblem.oldServer, reason: 'a server without the endpoint');
      expect(await code(500), DataStatusProblem.failed);
      expect((await fetchDataStatus(serverUrl: 'https://x', token: null)).problem, DataStatusProblem.noToken);
      expect((await fetchDataStatus(serverUrl: 'https://x', token: null)).problemText, contains('sign in'));
    });
  });

  group('what to offer', () {
    const never = DataTypeStatus(key: 'hrv', label: 'HRV', state: DataState.never);
    test('ok: nothing; never asked: ask again; asked: iOS Settings; unknown: iOS Settings', () {
      expect(fixFor(const DataTypeStatus(key: 'hrv', label: 'HRV', state: DataState.ok), {'hrv': false}), DataFix.none);
      expect(fixFor(never, {'hrv': false}), DataFix.askAgain);
      expect(fixFor(never, {'hrv': true}), DataFix.openSettings);
      expect(fixFor(never, null), DataFix.openSettings);
    });

    test('a permission would not help', () {
      const temp = DataTypeStatus(key: 'wrist_temperature', label: 'T', state: DataState.never, notes: ['sdk_unsupported']);
      expect(fixFor(temp, {'wrist_temperature': false}), DataFix.unsupported);
      const water = DataTypeStatus(key: 'water', label: 'W', state: DataState.never, notes: ['app_direct', 'flag_off']);
      expect(fixFor(water, {'water': false}), DataFix.unsupported);
      expect(reasonsFor(water), ["The app reads this itself when it opens: unlock the phone and open the app", "Water isn't switched on on this server yet"]);
    });

    test('asking again shows a prompt only while some type was never asked', () {
      expect(askWouldShowPrompt(null), isFalse);
      expect(askWouldShowPrompt({for (final k in kAskableKeys) k: true}), isFalse);
      expect(askWouldShowPrompt({for (final k in kAskableKeys) k: k != 'water'}), isTrue);
      expect(askWouldShowPrompt({'wrist_temperature': false}), isFalse, reason: 'the app never asks for wrist temperature');
    });

    test('reasons and steps name the switch as iOS shows it', () {
      expect(reasonsFor(never), contains('Not switched on in the Health permissions'));
      expect(reasonsFor(const DataTypeStatus(key: 'sleep', label: 'S', state: DataState.stale)), contains('The Health permission was switched off'));
      expect(settingsSteps(iosName('vo2max', 'VO₂ max'))[3], 'Turn on Cardio Fitness, or turn them all on');
      expect(settingsSteps().length, 5);
    });

    test('since labels', () {
      expect(sinceLabel(const DataTypeStatus(key: 'a', label: 'a', state: DataState.ok, daysSince: 0)), 'Today');
      expect(sinceLabel(const DataTypeStatus(key: 'a', label: 'a', state: DataState.stale, daysSince: 5)), '5 days ago');
      expect(sinceLabel(never), '—');
    });
  });

  group('last sync on this phone', () {
    final t0 = DateTime(2026, 10, 22, 8);
    test('the latest event wins', () {
      expect(lastSyncOf(SyncStats(), const RawSyncStatus()).at, isNull);
      final ok = lastSyncOf(SyncStats(lastUploadAt: t0, lastErrorAt: t0.subtract(const Duration(hours: 1)), lastError: 'HTTP 500'), const RawSyncStatus());
      expect((ok.ok, ok.at), (true, t0));
      final failed = lastSyncOf(SyncStats(lastUploadAt: t0, lastErrorAt: t0.add(const Duration(minutes: 5)), lastError: 'HTTP 500'), const RawSyncStatus());
      expect((failed.ok, failed.error), (false, 'HTTP 500'));
      final raw = lastSyncOf(SyncStats(lastUploadAt: t0), RawSyncStatus(lastRun: t0.add(const Duration(minutes: 9)), stop: 'failed'));
      expect(raw.ok, isFalse, reason: "the app's direct upload failed after the OW upload");
    });
  });

  group('section', () {
    Future<void> pump(WidgetTester t, {required Map<String, bool>? asked, required List<String> calls}) async {
      await t.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: DataStatusSection(
              load: () async => DataStatusResult.ok(DataStatusReport.fromJson(serverJson)),
              askStatus: () async => asked,
              requestAccess: () async => calls.add('request'),
              openSettings: () async => calls.add('settings'),
            ),
          ),
        ),
      ));
      await t.pumpAndSettle();
    }

    testWidgets('lists every type with its state', (t) async {
      await pump(t, asked: const {}, calls: []);
      expect(find.text('Data on the server'), findsOneWidget);
      expect(find.text('Heart rate'), findsOneWidget);
      expect(find.text('Today'), findsOneWidget);
      expect(find.text('Never received'), findsNWidgets(2));
      expect(find.text('Not received lately'), findsOneWidget);
      expect(find.textContaining('OK 1 · not lately 1 · never 2'), findsOneWidget);
    });

    testWidgets('a type never asked: ask again shows the system prompt', (t) async {
      final calls = <String>[];
      await pump(t, asked: {for (final k in kAskableKeys) k: k != 'hrv'}, calls: calls);
      await t.tap(find.text('HRV'));
      await t.pumpAndSettle();
      expect(find.textContaining('iOS has not asked about this yet'), findsOneWidget);
      await t.tap(find.widgetWithText(FilledButton, 'Ask for Health access again').last);
      await t.pumpAndSettle();
      expect(calls, ['request']);
    });

    testWidgets('a type already asked: steps to iOS Settings, the app changes nothing', (t) async {
      final calls = <String>[];
      await pump(t, asked: {for (final k in kAskableKeys) k: true}, calls: calls);
      await t.tap(find.text('HRV'));
      await t.pumpAndSettle();
      expect(find.textContaining('Turn on Heart Rate Variability'), findsOneWidget);
      expect(find.textContaining('Data Access & Devices'), findsOneWidget);
      await t.tap(find.widgetWithText(FilledButton, 'Open Settings'));
      await t.pumpAndSettle();
      expect(calls, ['settings']);
    });

    testWidgets('ask again with every item asked: no prompt, explains Settings instead', (t) async {
      final calls = <String>[];
      await pump(t, asked: {for (final k in kAskableKeys) k: true}, calls: calls);
      await t.tap(find.text('Ask for Health access again'));
      await t.pumpAndSettle();
      expect(find.textContaining('will not ask again'), findsOneWidget);
      expect(calls, isEmpty);
    });

    testWidgets('wrist temperature: reasons only, no permission button', (t) async {
      await pump(t, asked: const {}, calls: []);
      await t.tap(find.text('Wrist temperature'));
      await t.pumpAndSettle();
      expect(find.textContaining("can't read this yet"), findsOneWidget);
      expect(find.textContaining('Data Access & Devices'), findsNothing);
    });
  });

  test('verdict: "no watch data" is not "sync broken" (sync investigation 2026-10-26)', () {
    DataStatusReport rep(Map<String, String> states) => DataStatusReport(types: [
          for (final e in states.entries)
            DataTypeStatus(key: e.key, label: e.key, state: DataState.values.firstWhere((s) => s.name == e.value)),
        ]);
    const watch = ['heart_rate', 'hrv', 'resting_heart_rate', 'sleep'];
    expect(syncVerdict(rep({for (final k in [...watch, 'steps']) k: 'never'})), SyncVerdict.empty);
    expect(syncVerdict(rep({for (final k in watch) k: 'never', 'steps': 'ok', 'active_energy': 'ok'})), SyncVerdict.noWatch);
    expect(syncVerdict(rep({for (final k in watch) k: 'stale', 'steps': 'stale'})), SyncVerdict.broken);
    expect(syncVerdict(rep({for (final k in watch) k: 'ok', 'steps': 'ok'})), SyncVerdict.ok);
    expect(syncVerdict(rep({'heart_rate': 'ok', 'sleep': 'never', 'steps': 'ok'})), SyncVerdict.ok, reason: 'one missing type is not "no watch"');
    expect(syncVerdictText(SyncVerdict.noWatch), isNotNull);
    expect(syncVerdictText(SyncVerdict.ok), isNull);
  });
}
