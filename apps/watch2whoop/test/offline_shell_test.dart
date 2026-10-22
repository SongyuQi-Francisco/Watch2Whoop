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

  test('an older server without the endpoint or no network keeps nothing', () async {
    final old = OfflineShell(dir: () async => dir, client: MockClient((r) async => http.Response('<html></html>', 200)));
    expect(await old.save(entry), isFalse);
    final down = OfflineShell(dir: () async => dir, client: MockClient((r) async => throw const SocketException('offline')));
    expect(await down.save(entry), isFalse);
    expect(await down.load(entry), isNull);
  });
}
