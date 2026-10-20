import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/config.dart';

void main() {
  group('checkServerUrl', () {
    test('adds https, strips trailing /', () {
      final c = checkServerUrl('  mac.example.ts.net/ ', UrlKind.today);
      expect(c.url, 'https://mac.example.ts.net');
      expect(c.insecureLocal, isFalse);
    });

    test('keeps port and path', () {
      expect(checkServerUrl('https://mac.example.ts.net:8443', UrlKind.ow).url, 'https://mac.example.ts.net:8443');
      expect(checkServerUrl('https://h.example/app/', UrlKind.today).url, 'https://h.example/app');
    });

    test('OW address strips a mistakenly entered /api/v1', () {
      expect(checkServerUrl('https://h.example:8443/api/v1/', UrlKind.ow).url, 'https://h.example:8443');
      // the Today page leaves the path alone
      expect(checkServerUrl('https://h.example/api/v1', UrlKind.today).url, 'https://h.example/api/v1');
    });

    test('public http is rejected; local / LAN http is allowed (flagged as development only)', () {
      expect(checkServerUrl('http://example.com', UrlKind.today).error, 'https');
      for (final h in ['localhost:8130', '127.0.0.1:8200', '192.168.1.20:8100', '10.0.0.5', '172.16.3.4', 'mac.local:8100']) {
        final c = checkServerUrl('http://$h', UrlKind.ow);
        expect(c.isOk, isTrue, reason: h);
        expect(c.insecureLocal, isTrue, reason: h);
      }
      expect(checkServerUrl('http://172.32.0.1', UrlKind.ow).error, 'https');
      expect(checkServerUrl('http://100.64.1.2', UrlKind.ow).error, 'https', reason: 'Tailscale IPs must also use https');
    });

    test('rejects empty, other schemes, credentials, query parameters', () {
      expect(checkServerUrl('', UrlKind.today).error, 'empty');
      expect(checkServerUrl('ftp://x.example', UrlKind.today).error, 'scheme');
      expect(checkServerUrl('https://u:p@x.example', UrlKind.today).error, 'extra');
      expect(checkServerUrl('https://x.example/?token=1', UrlKind.today).error, 'extra');
      expect(checkServerUrl('https://x.example/#a', UrlKind.today).error, 'extra');
    });
  });

  test('isLocalHost', () {
    expect(isLocalHost('LOCALHOST'), isTrue);
    expect(isLocalHost('192.168.0.1'), isTrue);
    expect(isLocalHost('192.169.0.1'), isFalse);
    expect(isLocalHost('999.1.1.1'), isFalse);
    expect(isLocalHost('example.com'), isFalse);
  });

  test('sameOrigin compares scheme, host and port', () {
    final base = Uri.parse('https://mac.example.ts.net/app/');
    expect(sameOrigin(Uri.parse('https://MAC.example.ts.net/v1/me'), base), isTrue);
    expect(sameOrigin(Uri.parse('https://mac.example.ts.net:8444/'), base), isFalse);
    expect(sameOrigin(Uri.parse('http://mac.example.ts.net/'), base), isFalse);
    expect(sameOrigin(Uri.parse('https://claude.ai/code/x'), base), isFalse);
  });

  test('todayEntry: goes to /app/ when only a host is given', () {
    expect(const AppConfig(todayUrl: 'https://h.example').todayEntry.toString(), 'https://h.example/app/');
    expect(const AppConfig(todayUrl: 'https://h.example/app').todayEntry.toString(), 'https://h.example/app');
  });

  group('normalizeInvitationCode', () {
    test('strips spaces and -, uppercases', () {
      expect(normalizeInvitationCode(' abcd-2345 '), 'ABCD2345');
      expect(normalizeInvitationCode('hkmn 7pqr'), 'HKMN7PQR');
    });
    test('rejects wrong length or confusable characters (0 O 1 I)', () {
      expect(normalizeInvitationCode('ABC2345'), isNull);
      expect(normalizeInvitationCode('ABCD23456'), isNull);
      expect(normalizeInvitationCode('ABCD2340'), isNull);
      expect(normalizeInvitationCode('ABCDO345'), isNull);
      expect(normalizeInvitationCode('ABCD1345'), isNull);
    });
  });

  group('parseSetupText', () {
    test('one key=value per line (output of invite.py)', () {
      final s = parseSetupText('today=https://mac.example.ts.net\now=https://mac.example.ts.net:8443\ncode=hkmn7pqr\n');
      expect(s.todayUrl, 'https://mac.example.ts.net');
      expect(s.owHost, 'https://mac.example.ts.net:8443');
      expect(s.code, 'HKMN7PQR');
    });
    test('JSON and "key: value" also work', () {
      final j = parseSetupText('{"today": "https://a.example", "ow": "https://b.example:8443", "code": "ABCD2345"}');
      expect([j.todayUrl, j.owHost, j.code], ['https://a.example', 'https://b.example:8443', 'ABCD2345']);
      final k = parseSetupText('Today: https://a.example\nOW_URL: https://b.example');
      expect([k.todayUrl, k.owHost, k.code], ['https://a.example', 'https://b.example', null]);
    });
    test('only an invitation code', () {
      expect(parseSetupText(' abcd2345 ').code, 'ABCD2345');
    });
    test('unrelated text → empty', () {
      expect(parseSetupText('hello world').isEmpty, isTrue);
      expect(parseSetupText('code=not-a-code').code, isNull);
    });
  });

  group('setup link / QR code (watch2whoop://setup?…)', () {
    test('everything in one: server, invitation code, login code', () {
      final link = 'watch2whoop://setup?today=https%3A%2F%2Fbody.example.com&ow=https%3A%2F%2Fsync.example.com'
          '&code=hkmn7pqr&login=abcd-efgh-jkmn';
      final s = parseSetupText(link);
      expect(s.todayUrl, 'https://body.example.com');
      expect(s.owHost, 'https://sync.example.com');
      expect(s.code, 'HKMN7PQR');
      expect(s.loginCode, 'ABCDEFGHJKMN');
      expect(s.hasServers, isTrue);
    });
    test('buildSetupLink and parsing are inverses', () {
      const i = SetupInfo(todayUrl: 'https://a.example', owHost: 'https://b.example:8443', code: 'HKMN7PQR', loginCode: 'ABCDEFGHJKMN');
      final back = parseSetupText(buildSetupLink(i));
      expect([back.todayUrl, back.owHost, back.code, back.loginCode], [i.todayUrl, i.owHost, i.code, i.loginCode]);
      expect(buildSetupLink(i), startsWith('watch2whoop://setup?'));
    });
    test('the text format can also carry a login code; a malformed login code is dropped', () {
      expect(parseSetupText('today=https://a.example\nlogin=ABCDEFGHJKMN').loginCode, 'ABCDEFGHJKMN');
      expect(parseSetupText('login=SHORT').loginCode, isNull);
      expect(parseSetupText('watch2whoop://setup?login=ABCDEFGHJK0O').loginCode, isNull, reason: '0 / O are not in the alphabet');
    });
  });

  test('normalizeLoginCode', () {
    expect(normalizeLoginCode('abcd efgh-jkmn'), 'ABCDEFGHJKMN');
    expect(normalizeLoginCode('ABCDEFGHJKM'), isNull);
    expect(normalizeLoginCode('ABCDEFGHJKM1'), isNull);
  });

  test('AppConfig JSON round trip', () {
    const c = AppConfig(
      todayUrl: 'https://a.example',
      owHost: 'https://b.example:8443',
      syncDaysBack: 30,
      onboardingDone: true,
      owUserId: 'aaaaaaaa-0000-0000-0000-000000000000',
      healthRequested: true,
      pendingLoginCode: 'ABCDEFGHJKMN',
    );
    final r = AppConfig.fromJson(c.toJson());
    expect(r.toJson(), c.toJson());
    expect(r.owConnected, isTrue);
    expect(r.copyWith(clearOwUserId: true).owConnected, isFalse);
    expect(r.copyWith(clearPendingLoginCode: true).pendingLoginCode, isNull);
    expect(r.copyWith(todayUrl: 'https://c.example').pendingLoginCode, 'ABCDEFGHJKMN');
    expect(AppConfig.fromJson(const {}).syncDaysBack, kDefaultSyncDaysBack);
    expect(kDefaultSyncDaysBack, 90);
  });

  test('only a definite answer from the Today page settles the pending login code', () {
    expect(loginCodeSettled('logged-in'), isTrue);
    expect(loginCodeSettled('login-failed'), isTrue);
    for (final m in ['sync', 'settings', '', 'logged-out']) {
      expect(loginCodeSettled(m), isFalse, reason: m);
    }
  });
}
