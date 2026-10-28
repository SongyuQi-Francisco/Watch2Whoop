import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:watch2whoop/src/offline_shell.dart';

// The kept copy of the Today page for a cold start without network (offline_shell.dart, docs/offline-plan.md).
void main() {
  late Directory dir;
  setUp(() async => dir = await Directory.systemTemp.createTemp('w2w-shell'));
  tearDown(() async => dir.delete(recursive: true));

  final entry = Uri.parse('https://body.example.com/app/#lang=en');
  const page = '<html><head><script>window.W2W_SHELL = "v2.87";</script></head><body></body></html>';

  test('the copy sits next to the page', () {
    expect(OfflineShell.shellUri(entry).toString(), 'https://body.example.com/app/offline-shell.html');
  });

  test('keeps the copy, only for the same server, once per run, and forgets it', () async {
    var calls = 0;
    final shell = OfflineShell(
      dir: () async => dir,
      client: MockClient((r) async {
        calls++;
        return http.Response(page, 200, headers: {'content-type': 'text/html; charset=utf-8'});
      }),
    );
    expect(await shell.load(entry), isNull);
    expect(await shell.save(entry), isTrue);
    expect(await shell.save(entry), isTrue);
    expect(calls, 1); // once per app run
    expect(await shell.load(entry), page);
    expect(await shell.load(Uri.parse('https://other.example.com/app/')), isNull);
    // a new app run reads the same files
    expect(await OfflineShell(dir: () async => dir).load(entry), page);
    await shell.forget();
    expect(await shell.load(entry), isNull);
  });

  test('asks with the ETag once per run: 304 keeps the copy, a new page replaces it and counts as changed', () async {
    var body = page, etag = '"a"';
    final asked = <String?>[];
    MockClient server() => MockClient((r) async {
          asked.add(r.headers['If-None-Match']);
          if (r.headers['If-None-Match'] == etag) return http.Response('', 304, headers: {'etag': etag});
          return http.Response(body, 200, headers: {'content-type': 'text/html; charset=utf-8', 'etag': etag});
        });
    expect(await OfflineShell(dir: () async => dir, client: server()).save(entry), isTrue);
    expect(asked, [null]); // nothing kept yet: no ETag sent
    // the next run: unchanged on the server = 304, the copy stays and nothing changed
    final run2 = OfflineShell(dir: () async => dir, client: server());
    expect(await run2.save(entry), isTrue);
    expect(asked.last, '"a"');
    expect(run2.changed, isFalse);
    expect(await run2.load(entry), page);
    // a deploy: the next run gets the new page; it is newer than the one on screen until it is opened
    body = page.replaceFirst('v2.87', 'v2.88');
    etag = '"b"';
    final run3 = OfflineShell(dir: () async => dir, client: server());
    expect(await run3.save(entry), isTrue);
    expect(run3.changed, isTrue);
    expect(OfflineShell.versionOf((await run3.load(entry))!), 'v2.88');
    expect(run3.changed, isFalse); // opened
    // another server's copy is never sent its ETag
    final other = Uri.parse('https://other.example.com/app/');
    expect(await OfflineShell(dir: () async => dir, client: server()).save(other), isTrue);
    expect(asked.last, isNull);
  });

  test('local-first start: only while the page said so, for the same server, as the W2W_LOCAL page', () async {
    final shell = OfflineShell(
      dir: () async => dir,
      client: MockClient((r) async => http.Response(page, 200, headers: {'content-type': 'text/html; charset=utf-8'})),
    );
    await shell.save(entry);
    expect(await shell.loadLocal(entry), isNull); // never told: off (first run of a new app, older server)
    await shell.setLocalFirst(true);
    final local = await shell.loadLocal(entry);
    expect(local, contains('<script>window.W2W_LOCAL = "v2.87";</script>'));
    expect(local, isNot(contains('W2W_SHELL')));
    expect(await shell.loadLocal(Uri.parse('https://other.example.com/app/')), isNull);
    expect(await OfflineShell(dir: () async => dir).localFirst(), isTrue); // remembered for the next run
    await shell.setLocalFirst(false); // kill switch local_first_start_off
    expect(await shell.loadLocal(entry), isNull);
    expect(await shell.load(entry), page); // the offline copy stays
    // logout / offline use off: copy and switch both go
    await shell.setLocalFirst(true);
    await shell.forget();
    expect(await shell.localFirst(), isFalse);
    expect(await shell.loadLocal(entry), isNull);
  });

  test('a copy without the marker is never opened as the local page', () {
    expect(OfflineShell.asLocal('<html><script>window.W2W_SHELL="x"</script></html>'), isNull);
    expect(OfflineShell.asLocal(page), '<html><head><script>window.W2W_LOCAL = "v2.87";</script></head><body></body></html>');
    expect(OfflineShell.versionOf(page), 'v2.87');
  });

  test('the page messages for the switch', () {
    expect(parseLocalFirst('local-first:true'), isTrue);
    expect(parseLocalFirst('local-first:false'), isFalse);
    expect(parseLocalFirst('offline:on'), isNull);
    expect(parseLocalFirst('local-first:'), isNull);
  });

  test('an older server without the endpoint or no network keeps nothing', () async {
    final old = OfflineShell(dir: () async => dir, client: MockClient((r) async => http.Response('<html></html>', 200)));
    expect(await old.save(entry), isFalse);
    final down = OfflineShell(dir: () async => dir, client: MockClient((r) async => throw const SocketException('offline')));
    expect(await down.save(entry), isFalse);
    expect(await down.load(entry), isNull);
  });
}
