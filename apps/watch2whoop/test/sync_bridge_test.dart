// The Today page's sync calls (enableSync / syncNow / syncStatus, P0 2026-10-26): messages, JS, status and the steps.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:watch2whoop/src/raw_sync.dart';
import 'package:watch2whoop/src/sync_bridge.dart';
import 'package:watch2whoop/src/sync_stats.dart';

final _now = DateTime.utc(2026, 10, 26, 9);

SyncStatusInput input({
  bool supported = true,
  bool signedIn = true,
  bool connected = true,
  bool asked = true,
  bool needsAsk = false,
  SyncStats? ow,
  RawSyncStatus raw = const RawSyncStatus(),
  DeviceSyncState device = const DeviceSyncState(net: 'wifi', backgroundRefresh: 'on', lowPower: false, protectedData: true),
}) =>
    SyncStatusInput(
      supported: supported,
      signedIn: signedIn,
      connected: connected,
      healthAsked: asked,
      healthNeedsAsk: needsAsk,
      ow: ow ?? SyncStats(lastUploadAt: _now.subtract(const Duration(minutes: 5))),
      raw: raw,
      device: device,
      now: _now,
    );

List<String> codes(SyncStatusInput i) => [for (final p in syncProblems(i)) p.code];

void main() {
  test('messages from the page: known verb and an id, nothing else', () {
    expect(parseSyncMessage('sync:enable {"id":"s1_1"}'), (verb: 'enable', id: 's1_1'));
    expect(parseSyncMessage('sync:now {"id":"x"}')?.verb, 'now');
    expect(parseSyncMessage('sync:status {"id":"x"}')?.verb, 'status');
    for (final bad in ['sync:delete {"id":"x"}', 'sync:now', 'sync:now {}', 'sync:now {"id":""}', 'sync:now not-json',
        'sync:now {"id":"${'x' * 65}"}', 'voice:start {"id":"x"}']) {
      expect(parseSyncMessage(bad), isNull, reason: bad);
    }
  });

  test('JS: the API object, the feature flag, replies and progress as JSON', () {
    expect(kSyncBridgeJs, contains('window.Watch2WhoopSync={v:1'));
    for (final f in ['enableSync:', 'syncNow:', 'syncStatus:', '_reply:', '_progress:']) {
      expect(kSyncBridgeJs, contains(f));
    }
    expect(kSyncBridgeJs, contains("{sync:{v:1,enableSync:true,syncNow:true,syncStatus:true}}"));
    expect(kSyncBridgeJs, contains("'watch2whoop:sync'"));
    final r = syncReplyJs('s1', {'ok': true, 'note': '</script>"'});
    expect(r, startsWith('window.Watch2WhoopSync&&window.Watch2WhoopSync._reply("s1",'));
    expect(jsonDecode(r.substring(r.indexOf(',') + 1, r.lastIndexOf(')'))), {'ok': true, 'note': '</script>"'});
    expect(syncProgressJs({'step': 'upload'}), contains('_progress({"step":"upload"})'));
    expect(netFeatureJs('cellular'), contains("{net:'cellular'}"));
    expect(netFeatureJs('unknown'), isNull);
    expect(netFeatureJs("x'});alert(1);//"), isNull);
  });

  test('status: all fine', () {
    final st = buildSyncStatus(input(ow: SyncStats(
      lastUploadAt: _now.subtract(const Duration(minutes: 5)),
      perType: {'HeartRate': 120},
      perTypeAt: {'HeartRate': _now.subtract(const Duration(minutes: 5))},
    )));
    expect(st['ok'], isTrue);
    expect(st['problems'], isEmpty);
    expect(st['connected'], isTrue);
    expect((st['types'] as List).single, {'key': 'HeartRate', 'channel': 'ow', 'count': 120, 'lastUpload': '2026-10-26T08:55:00.000Z'});
    expect(st['lastUpload'], '2026-10-26T08:55:00.000Z');
    expect(jsonDecode(jsonEncode(st)), isA<Map>());
  });

  test('status: each failure has its reason and what fixes it', () {
    // never turned on (skipped in onboarding)
    final off = syncProblems(input(connected: false, asked: false));
    expect([for (final p in off) (p.code, p.severity, p.action)],
        [('not_connected', 'error', 'enable'), ('permission', 'error', 'enable')]);
    // setup-link user without an account: the invitation code on the sync page
    expect(syncProblems(input(connected: false, signedIn: false)).first.action, 'sync_page');
    // 401 from the sync server
    final auth = syncProblems(input(ow: SyncStats(authProblem: true, lastErrorAt: _now, lastError: 'HTTP 401')));
    expect(auth.first.code, 'auth');
    expect(auth.first.action, 'enable');
    expect(buildSyncStatus(input(ow: SyncStats(authProblem: true)))['ok'], isFalse);
    // the app's own upload got 401 / the account token was replaced by the page's
    expect(codes(input(raw: const RawSyncStatus(stop: 'unauthorized'))), contains('auth'));
    // iOS still wants to ask for Health (new types) / HealthKit says never asked
    expect(codes(input(needsAsk: true)), ['permission']);
    expect(codes(input(raw: const RawSyncStatus(stop: 'not_asked'))), ['permission']);
    // network: offline now, or the last pass could not reach the server
    expect(codes(input(device: const DeviceSyncState(net: 'offline'))), ['network']);
    expect(codes(input(raw: const RawSyncStatus(stop: 'offline'))), ['network']);
    // the server refused uploads (not 401)
    expect(codes(input(raw: const RawSyncStatus(stop: 'failed'))), ['server']);
    // Background App Refresh off: only syncs while open, fixed in iOS Settings
    final bg = syncProblems(input(device: const DeviceSyncState(backgroundRefresh: 'off')));
    expect([bg.single.code, bg.single.action], ['background_refresh_off', 'settings']);
    // Low Power Mode / locked phone / background time used up: information
    expect(syncProblems(input(device: const DeviceSyncState(lowPower: true))).single.severity, 'info');
    expect(codes(input(device: const DeviceSyncState(protectedData: false))), ['locked']);
    expect(codes(input(ow: SyncStats(lastUploadAt: _now, lastNotice: SyncNotice.backgroundTimeout, lastNoticeAt: _now))),
        ['timeout']);
    // a notice from yesterday morning is not shown any more
    expect(codes(input(ow: SyncStats(lastUploadAt: _now, lastNotice: SyncNotice.locked,
        lastNoticeAt: _now.subtract(const Duration(hours: 13))))), isEmpty);
    // nothing new for more than a day while it looks fine
    expect(codes(input(ow: SyncStats(lastUploadAt: _now.subtract(const Duration(hours: 30))))), ['stale']);
    // Android: no phone sync
    final android = buildSyncStatus(input(supported: false));
    expect([(android['problems'] as List).single['code'], android['ok']], ['unsupported', false]);
    // every message is a short sentence for people, never a stack trace or a server address
    for (final p in [...off, ...auth, ...bg]) {
      expect(p.message.length, lessThan(80));
      expect(p.message, isNot(contains('http')));
    }
  });

  test('the sync engine\'s uploads are listed too (#219 A1)', () {
    final st = buildSyncStatus(SyncStatusInput(supported: true, signedIn: true, connected: true, healthAsked: true,
        ow: SyncStats(), raw: const RawSyncStatus(), engineAt: {'HKCategoryTypeIdentifierSleepAnalysis': _now}, now: _now));
    expect((st['types'] as List).single, containsPair('channel', 'engine'));
    expect(st['lastUpload'], '2026-10-26T09:00:00.000Z');
    expect(st['engine'], {'stop': null});
  });

  test('the app\'s own uploads per kind are listed with their time', () {
    final st = buildSyncStatus(input(raw: RawSyncStatus(kindsAt: {'heartbeat': _now.toLocal()})));
    expect((st['types'] as List).single, {'key': 'heartbeat', 'channel': 'app', 'lastUpload': '2026-10-26T09:00:00.000Z'});
  });

  group('steps', () {
    late List<String> log;
    late bool connected, reconnect, connectOk, asked;
    late List<Map<String, Object?>> events;
    late Map<String, Object?> statusAnswer;
    Completer<bool>? round;

    SyncBridge bridge() => SyncBridge(
          supported: () => true,
          isConnected: () => connected,
          canUpload: () => connected || asked,
          needsReconnect: () => reconnect,
          connect: (link) async {
            log.add(link?.code == null ? 'connect' : 'connect:${link!.code}');
            if (connectOk) connected = true;
            return SyncConnectOutcome(connected: connectOk, message: connectOk ? null : 'Sync did not connect');
          },
          askHealth: () async => log.add('health'),
          runRound: () async {
            log.add('round');
            return round == null ? true : round!.future;
          },
          status: () async => statusAnswer,
          progress: events.add,
        );

    setUp(() {
      log = [];
      events = [];
      connected = false;
      reconnect = false;
      connectOk = true;
      asked = false;
      round = null;
      statusAnswer = {'ok': true, 'problems': []};
    });

    test('enableSync: connect, Health, first round — with progress', () async {
      final r = await bridge().enable();
      expect(log, ['connect', 'health', 'round']);
      expect(r['ok'], isTrue);
      expect([for (final e in events) e['step']], ['connect', 'permission', 'upload', 'done']);
    });

    test('enableSync when connected: no new code, Health and a round only; a 401 reconnects', () async {
      connected = true;
      await bridge().enable();
      expect(log, ['health', 'round']);
      log.clear();
      reconnect = true;
      await bridge().enable();
      expect(log, ['connect', 'health', 'round']);
    });

    test('enableSync: a failed connect says why; Health and the app\'s own uploads still go', () async {
      connectOk = false;
      final r = await bridge().enable();
      expect(log, ['connect', 'health', 'round']);
      expect(r['ok'], isFalse);
      expect((r['error'] as Map)['code'], 'connect');
      expect((r['error'] as Map)['message'], 'Sync did not connect');
      expect(events.last['step'], 'error');
    });

    test('a problem left after the round is the answer (e.g. still no permission)', () async {
      connected = true;
      statusAnswer = {
        'ok': false,
        'problems': [
          {'code': 'permission', 'severity': 'error', 'message': 'not allowed', 'action': 'enable'},
        ],
      };
      final r = await bridge().now();
      expect(r['ok'], isFalse);
      expect((r['error'] as Map)['code'], 'permission');
    });

    test('syncNow while not connected: the status reason, no round', () async {
      statusAnswer = {
        'ok': false,
        'problems': [
          {'code': 'not_connected', 'severity': 'error', 'message': 'off', 'action': 'enable'},
        ],
      };
      final r = await bridge().now();
      expect(log, isEmpty);
      expect((r['error'] as Map)['action'], 'enable');
      // not connected, but Health was asked: the app's own uploads run
      asked = true;
      await bridge().now();
      expect(log, ['round']);
    });

    test('one operation at a time: a second call gets the first one\'s answer', () async {
      connected = true;
      round = Completer<bool>();
      final b = bridge();
      final a1 = b.now();
      final a2 = b.now();
      expect(b.busy, isTrue);
      round!.complete(true);
      expect(identical(await a1, await a2), isTrue);
      expect(log, ['round']);
      expect(b.busy, isFalse);
    });

    test('the page\'s sync-connect link: its code goes to connect; already connected = no new code', () async {
      await bridge().enable(link: parseSyncConnect(Uri.parse('watch2whoop://sync-connect?ow=https%3A%2F%2Fsync.example.com&code=AB12-CD34')));
      expect(log.first, 'connect:AB12-CD34');
      log.clear();
      await bridge().enable(link: (owHost: null, code: 'AB12-CD34'));
      expect(log, ['health', 'round'], reason: 'connected now: straight to uploading');
    });
  });

  test('the page\'s protocol (docs/sync-onboarding-app-requirements.md): links, features, onApp progress', () {
    expect(parseSyncConnect(Uri.parse('watch2whoop://sync-connect')), (owHost: null, code: null));
    expect(parseSyncConnect(Uri.parse('watch2whoop://sync-connect?ow=javascript:alert(1)&code=<x>')), (owHost: null, code: null));
    expect(parseSyncConnect(Uri.parse('watch2whoop://sync-connect?ow=https://sync.example.com&code=ABCD-2345'))?.owHost,
        'https://sync.example.com');
    expect(parseSyncConnect(Uri.parse('watch2whoop://setup?code=X')), isNull);
    expect(kSyncLinkFeaturesJs, contains('{syncConnect:true,syncNow:true,appSettings:true,healthSettings:true}'));
    expect(onAppEvent({'step': 'connect'}), {'phase': 'connecting', 'error': null});
    expect(onAppEvent({'step': 'upload', 'percent': 142}), {'phase': 'uploading', 'percent': 100, 'error': null});
    expect(onAppEvent({'step': 'error', 'message': 'Health is off'}), {'phase': 'error', 'error': 'Health is off'});
    expect(onAppEvent({'step': 'other'}), isNull);
    expect(appProgressJs({'phase': 'done'}), contains('window.BodySyncStatus.onApp({"phase":"done"})'));
    // the page side, when it is in this checkout
    final page = File('../../body_engine/src/body_engine/web/static/sync-status.js');
    if (page.existsSync()) {
      final js = page.readAsStringSync();
      for (final s in ['onApp', 'syncConnect', 'sync-connect', 'sync-now', 'app-settings', 'health-settings']) {
        expect(js, contains(s), reason: s);
      }
    }
  });

  test('device report (PUT /v1/ingest/sync-device): the phone\'s state, no health values', () async {
    final i = input(
      ow: SyncStats(lastUploadAt: _now.subtract(const Duration(hours: 2)), authProblem: true, lastErrorAt: _now),
      device: const DeviceSyncState(net: 'wifi', backgroundRefresh: 'off', lowPower: true, protectedData: false),
    );
    final r = buildDeviceReport(i, appVersion: '1.0.8', unasked: const ['HKCategoryTypeIdentifierSleepAnalysis'],
        sdkActive: true, sdk: const SdkSyncStatus(sentCount: 120, completedTypes: 3), firstSyncPercent: 12);
    expect(r['background_refresh'], 'denied');
    expect(r['low_power'], isTrue);
    expect(r['health_readable'], isFalse);
    expect(r['last_error'], 'auth');
    expect(r['last_upload_at'], '2026-10-26T07:00:00.000Z');
    expect(r['unasked_types'], ['HKCategoryTypeIdentifierSleepAnalysis']);
    expect((r['sdk_status'] as Map)['sent'], 120);
    expect(r['first_sync_percent'], 12);
    expect(deviceLastError(syncProblems(input())), isNull);
    expect(deviceLastError(syncProblems(input(raw: const RawSyncStatus(stop: 'offline')))), 'network');
    expect(deviceLastError(syncProblems(input(asked: false))), 'health_denied');

    var puts = 0;
    late http.Request seen;
    final rep = DeviceReporter(client: MockClient((req) async {
      puts++;
      seen = req;
      return http.Response('{}', 200);
    }));
    expect(await rep.send('https://body.example.com/app/', 'tok', r), isTrue);
    expect([seen.method, seen.url.toString(), seen.headers['Authorization']],
        ['PUT', 'https://body.example.com/v1/ingest/sync-device', 'Bearer tok']);
    expect(await rep.send('https://body.example.com/app/', 'tok', r), isFalse, reason: 'same content within 10 minutes');
    expect(await rep.send('https://body.example.com/app/', 'tok', {...r, 'low_power': false}), isTrue);
    expect(puts, 2);
    final old = DeviceReporter(client: MockClient((_) async => http.Response('', 404)));
    expect(await old.send('https://body.example.com', 'tok', r), isFalse, reason: 'older server: ignored');
  });
}
