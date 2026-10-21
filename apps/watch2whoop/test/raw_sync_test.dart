import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/raw_sync.dart';

/// What RawSync.swift hands over for one page (test samples: the simulator has no ECG).
Map<Object?, Object?> nativePage({bool more = false, String anchor = 'A1'}) => {
      'items': [
        {
          'uuid': '0f8fad5b-d9cb-469f-a165-70867728950e',
          'start': '2026-10-20T01:00:00.000Z',
          'end': '2026-10-20T01:01:00.000Z',
          'source': 'Apple Watch',
          'beats': [0.0, 0.98, 2.0, 2.98],
          'gaps': [false, false, true, false],
        },
      ],
      'deleted': ['7c9e6679-7425-40de-944b-e07fc1f90ae7'],
      'anchor': anchor,
      'more': more,
    };

class FakeSource implements RawSource {
  FakeSource(this.pages);

  /// kind -> pages returned in order (an extra read returns an empty page with no anchor)
  final Map<String, List<RawPage>> pages;
  final reads = <String>[];
  final commits = <String>[];
  final limits = <int>[];
  final _pos = <String, int>{};

  @override
  Future<bool> requestAuth() async => true;

  @override
  Future<RawPage> read(String kind, int daysBack, int limit) async {
    reads.add(kind);
    limits.add(limit);
    final i = _pos[kind] ?? 0;
    final list = pages[kind] ?? const [];
    return i < list.length ? list[i] : const RawPage();
  }

  /// The anchor only moves on commit, like the native side.
  @override
  Future<void> commit(String kind, String anchor) async {
    commits.add('$kind:$anchor');
    _pos[kind] = (_pos[kind] ?? 0) + 1;
  }

  @override
  Future<void> reset() async => _pos.clear();
}

/// Records whether the "asked" flag was already stored when the native sheet was requested.
class AuthProbe extends FakeSource {
  AuthProbe() : super(const {});
  bool? askedBeforeCall;

  @override
  Future<bool> requestAuth() async {
    askedBeforeCall = (await SharedPreferences.getInstance()).getBool('w2w.raw.authAsked.v1');
    return true;
  }
}

RawPage hb(String anchor, {bool more = false}) => RawPage.fromMap(nativePage(anchor: anchor, more: more));

RawPage ecgPage(String anchor) => RawPage(items: [
      {'uuid': 'e1', 'start': '2026-10-20T02:00:00.000Z', 'sample_hz': 512.0, 'voltage_b64': 'AAA=', 'classification': 'sinusRhythm'},
    ], anchor: anchor);

void main() {
  test('the automatic permission ask is remembered before the native call (no crash on every open)', () async {
    SharedPreferences.setMockInitialValues({});
    final probe = AuthProbe();
    RawSync.instance.source = probe;
    await RawSync.instance.requestAuth();
    expect(probe.askedBeforeCall, isTrue);
  });

  test('native page parses into the server batch format', () {
    final p = RawPage.fromMap(nativePage(more: true));
    expect(p.more, isTrue);
    expect(p.anchor, 'A1');
    final b = buildBatch('heartbeat', p, batchId: 'b1', device: 'iPhone', appVersion: '1.0.0+2');
    expect(b.keys, containsAll(['batch_id', 'device', 'app_version', 'heartbeats', 'deleted']));
    expect((b['heartbeats'] as List).single['gaps'], [false, false, true, false]);
    expect(buildBatch('ecg', ecgPage('E'), batchId: 'b2').containsKey('ecgs'), isTrue);
    // JSON-encodable as is
    expect(() => jsonEncode(b), returnsNormally);
  });

  test('uploads page by page and moves the anchor only after the server accepted it', () async {
    final src = FakeSource({
      'heartbeat': [hb('H1', more: true), hb('H2')],
      'ecg': [ecgPage('E1')],
    });
    final sent = <Map<String, Object?>>[];
    final res = await RawSyncRunner(
      source: src,
      upload: (b) async {
        sent.add(b);
        return RawUploadResult(RawUpload.ok, {
          'heartbeats': b.containsKey('heartbeats') ? 1 : 0,
          'ecgs': b.containsKey('ecgs') ? 1 : 0,
          'deleted': 1,
          'status': {'heartbeats': 2, 'ecgs': 1},
        });
      },
    ).run(90);
    expect(res.stop, isNull);
    expect(res.heartbeats, 2);
    expect(res.ecgs, 1);
    expect(res.serverStatus, {'heartbeats': 2, 'ecgs': 1});
    expect(src.commits, ['heartbeat:H1', 'heartbeat:H2', 'ecg:E1']);
    expect(sent.map((b) => b['batch_id']).toSet().length, 3, reason: 'a new batch id per page');
  });

  test('a failed, refused or unauthorized upload keeps the anchor', () async {
    for (final (outcome, stop) in [
      (RawUpload.failed, 'failed'),
      (RawUpload.consent, 'consent'),
      (RawUpload.unauthorized, 'unauthorized'),
    ]) {
      final src = FakeSource({'heartbeat': [hb('H1')]});
      final res = await RawSyncRunner(source: src, upload: (_) async => RawUploadResult(outcome)).run(0);
      expect(res.stop, stop);
      expect(src.commits, isEmpty);
    }
  });

  test('too large: the same page is read again in smaller pieces', () async {
    final src = FakeSource({'heartbeat': [hb('H1')]});
    var n = 0;
    final res = await RawSyncRunner(
      source: src,
      upload: (_) async => (n++ == 0) ? const RawUploadResult(RawUpload.tooLarge) : const RawUploadResult(RawUpload.ok, {}),
    ).run(90);
    expect(res.stop, isNull);
    expect(src.limits.take(2), [kRawPageSize['heartbeat'], kRawPageSize['heartbeat']! ~/ 2]);
    expect(src.commits.first, 'heartbeat:H1');

    // Over the byte limit before sending: smaller page, nothing sent for the big one
    final src2 = FakeSource({'heartbeat': [hb('H1')]});
    var sent = 0;
    await RawSyncRunner(source: src2, maxBytes: 10, upload: (_) async {
      sent++;
      return const RawUploadResult(RawUpload.ok, {});
    }).run(90);
    expect(src2.limits.first, kRawPageSize['heartbeat']);
    expect(src2.limits, contains(1));
    expect(sent, 1, reason: 'sent once the page cannot get smaller');
  });

  test('empty page with a new anchor is committed; budget stops early', () async {
    final src = FakeSource({'heartbeat': [const RawPage(anchor: 'H0')]});
    final res = await RawSyncRunner(source: src, upload: (_) async => const RawUploadResult(RawUpload.ok)).run(90);
    expect(src.commits, ['heartbeat:H0']);
    expect(res.batches, 0);

    final many = FakeSource({'heartbeat': [hb('H1', more: true), hb('H2', more: true), hb('H3')]});
    final r2 = await RawSyncRunner(source: many, maxBatches: 2, upload: (_) async => const RawUploadResult(RawUpload.ok, {}))
        .run(90);
    expect(r2.stop, 'budget');
    expect(many.commits, ['heartbeat:H1', 'heartbeat:H2']);
  });

  test('uploader: path, bearer token and status codes', () async {
    late http.Request seen;
    var code = 200;
    final up = RawUploader('https://body.example.com/app/', client: MockClient((req) async {
      seen = req;
      return http.Response(jsonEncode({'heartbeats': 1}), code);
    }));
    final ok = await up.upload('tok', {'batch_id': 'x'});
    expect(seen.url.toString(), 'https://body.example.com/v1/ingest/healthkit');
    expect(seen.headers['Authorization'], 'Bearer tok');
    expect(ok.outcome, RawUpload.ok);
    expect(ok.body, {'heartbeats': 1});
    for (final (c, o) in [(401, RawUpload.unauthorized), (409, RawUpload.consent), (413, RawUpload.tooLarge), (500, RawUpload.failed)]) {
      code = c;
      expect((await up.upload('tok', {})).outcome, o);
    }
    final down = RawUploader('https://body.example.com', client: MockClient((_) => throw Exception('offline')));
    expect((await down.upload('tok', {})).outcome, RawUpload.failed);
  });

  test('helpers', () {
    final u = newUuid();
    expect(RegExp(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$').hasMatch(u), isTrue);
    expect(needsAnchorReset(90, 0), isTrue);
    expect(needsAnchorReset(0, 90), isFalse);
    expect(needsAnchorReset(null, 0), isFalse);
    expect(needsAnchorReset(90, 90), isFalse);
    final s = RawSyncStatus(heartbeats: 3, ecgs: 1, lastUpload: DateTime(2026, 10, 21, 8), stop: 'consent');
    final back = RawSyncStatus.fromJson(jsonDecode(jsonEncode(s.toJson())) as Map<String, Object?>);
    expect(back.heartbeats, 3);
    expect(back.ecgs, 1);
    expect(back.lastUpload, DateTime(2026, 10, 21, 8));
    expect(back.problem, isNotNull);
    expect(const RawSyncStatus().problem, isNull);
  });
}
