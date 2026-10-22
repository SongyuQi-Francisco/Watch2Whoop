import 'dart:convert';
import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:watch2whoop/src/account_api.dart';

void main() {
  group('inviteStepCode', () {
    test('invite codes are normalized, circle codes are sent as typed', () {
      expect(inviteStepCode(' abcde-23456 '), 'ABCDE23456');
      expect(inviteStepCode('  Example   Lifting Club '), 'Example Lifting Club');
      expect(inviteStepCode('club'), 'club');
    });
    test('too short or too long is refused', () {
      expect(inviteStepCode('a-b c'), isNull);
      expect(inviteStepCode(''), isNull);
      expect(inviteStepCode('x' * 41), isNull);
    });
  });

  group('normalizeInviteCode', () {
    test('accepts spaces, dashes and lowercase', () {
      expect(normalizeInviteCode('abcde-23456'), 'ABCDE23456');
      expect(normalizeInviteCode(' ABCDE 23456 '), 'ABCDE23456');
      expect(normalizeInviteCode('ab-cd-e2-34-56'), 'ABCDE23456');
    });

    test('rejects wrong length and ambiguous characters', () {
      expect(normalizeInviteCode('ABCDE2345'), isNull);
      expect(normalizeInviteCode('ABCDE234567'), isNull);
      expect(normalizeInviteCode('ABCDE2345O'), isNull, reason: 'O is not in the alphabet');
      expect(normalizeInviteCode('ABCDE23451'), isNull, reason: '1 is not in the alphabet');
      expect(normalizeInviteCode(''), isNull);
    });
  });

  test('normalizeEmailCode', () {
    expect(normalizeEmailCode('123 456'), '123456');
    expect(normalizeEmailCode('12345'), isNull);
    expect(normalizeEmailCode('12345a'), isNull);
  });

  test('looksLikeEmail', () {
    expect(looksLikeEmail(' name@example.com '), isTrue);
    expect(looksLikeEmail('name@example'), isFalse);
    expect(looksLikeEmail('name example.com'), isFalse);
  });

  group('nonce', () {
    test('sha256Hex matches the known vector', () {
      expect(sha256Hex('abc'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    });

    test('raw nonce: length, charset, randomness', () {
      final a = generateRawNonce();
      final b = generateRawNonce();
      expect(a.length, 32);
      expect(a, isNot(b));
      expect(RegExp(r'^[0-9A-Za-z\-._]+$').hasMatch(a), isTrue);
      expect(generateRawNonce(8, Random(1)), generateRawNonce(8, Random(1)));
    });
  });

  group('AccountApi with a fake server', () {
    late List<http.Request> seen;
    AccountApi api(http.Response Function(http.Request) handler) {
      seen = [];
      return AccountApi('https://body.example.com', client: MockClient((r) async {
        seen.add(r);
        return handler(r);
      }));
    }

    test('verifyEmail sends the contract body and parses the session', () async {
      final a = api((r) => http.Response(
            jsonEncode({
              'token': 'be_x',
              'status': 'pending',
              'new_user': true,
              'me': {'id': 7, 'display_name': 'A', 'status': 'pending'},
            }),
            200,
          ));
      final s = await a.verifyEmail(' a@example.com ', '123456', device: 'iPhone');
      expect(seen.single.url.toString(), 'https://body.example.com/v1/auth/email/verify');
      expect(jsonDecode(seen.single.body), {'email': 'a@example.com', 'code': '123456', 'client': 'app', 'device': 'iPhone'});
      expect(s.token, 'be_x');
      expect(s.status, AccountStatus.pending);
      expect(s.newUser, isTrue);
      expect(s.displayName, 'A');
    });

    test('Apple sign-in sends the raw nonce, not the hash', () async {
      final a = api((r) => http.Response(jsonEncode({'token': 't', 'status': 'active', 'new_user': false, 'me': {}}), 200));
      final s = await a.signInWithApple(identityToken: 'jwt', rawNonce: 'raw', device: 'd', givenName: 'G');
      final body = jsonDecode(seen.single.body) as Map;
      expect(body['nonce'], 'raw');
      expect(body['given_name'], 'G');
      expect(body.containsKey('family_name'), isFalse);
      expect(s.status, AccountStatus.active);
    });

    test('Bearer header and invite redeem', () async {
      final a = api((r) => http.Response(jsonEncode({'status': 'active', 'provisioning': {'state': 'pending'}}), 200));
      expect(await a.redeemInvite('tok', 'ABCDE23456'), AccountStatus.active);
      expect(seen.single.headers['Authorization'], 'Bearer tok');
      expect(seen.single.url.path, '/v1/account/redeem-invite');
    });

    test('error mapping: 401, 429 with Retry-After, 503, 403 pending', () async {
      Future<AccountApiException> err(int status, String body, [Map<String, String> h = const {}]) async {
        final a = api((r) => http.Response(body, status, headers: h));
        try {
          await a.startEmail('a@example.com', lang: 'en');
        } on AccountApiException catch (e) {
          return e;
        }
        fail('no exception');
      }

      expect((await err(401, '{"detail":"invalid"}')).code, 'invalid');
      final rl = await err(429, '{}', {'retry-after': '42'});
      expect(rl.code, 'rate_limited');
      expect(rl.retryAfter, const Duration(seconds: 42));
      expect((await err(503, '')).code, 'unavailable');
      expect((await err(403, '{"detail":"account_pending"}')).code, 'pending');
      expect((await err(400, '{"detail":"bad email"}')).code, 'bad_request');
      expect((await err(500, 'oops')).code, 'server');
    });

    test('network failure becomes a network error', () async {
      final a = AccountApi('https://body.example.com', client: MockClient((_) async => throw Exception('offline')));
      expect(() => a.account('t'), throwsA(isA<AccountApiException>().having((e) => e.code, 'code', 'network')));
    });

    test('account info', () async {
      final a = api((r) => http.Response(
            jsonEncode({
              'id': 1,
              'display_name': 'A',
              'status': 'pending',
              'identities': [
                {'provider': 'email', 'email': 'a@example.com'},
              ],
              'invite_required': true,
              'provisioning': {'state': 'none', 'steps': {}},
            }),
            200,
          ));
      final i = await a.account('t');
      expect(i.status, AccountStatus.pending);
      expect(i.inviteRequired, isTrue);
      expect(i.provisioningState, 'none');
      expect(seen.single.method, 'GET');
    });

    test('ow-invitation waits while provisioning, then returns the code', () async {
      var calls = 0;
      final a = api((r) {
        calls++;
        return calls < 3
            ? http.Response('{"detail":"provisioning"}', 409)
            : http.Response('{"code":"ABCD2345","ow_url":"https://sync.example.com"}', 200);
      });
      final inv = await a.owInvitationWhenReady('t', step: const Duration(milliseconds: 1));
      expect(calls, 3);
      expect(inv.code, 'ABCD2345');
      expect(inv.owUrl, 'https://sync.example.com');
    });

    test('ow-invitation 503 is not retried; null ow_url is allowed', () async {
      final a = api((r) => http.Response('', 503));
      expect(
        () => a.owInvitationWhenReady('t', step: const Duration(milliseconds: 1)),
        throwsA(isA<AccountApiException>().having((e) => e.code, 'code', 'unavailable')),
      );
      expect(parseOwInvitation({'code': 'ABCD2345', 'ow_url': null}).owUrl, isNull);
    });

    test('web login code and logout', () async {
      final a = api((r) => r.url.path.endsWith('logout')
          ? http.Response('', 204)
          : http.Response('{"code":"ABCDEFGHJKLM","expires_in":600}', 200));
      expect(await a.webLoginCode('t'), 'ABCDEFGHJKLM');
      await a.logout('t');
      expect(seen.map((r) => r.url.path), ['/v1/account/web-login-code', '/v1/account/logout']);
    });

    test('API is called at the server root even when the Today URL has a path', () async {
      seen = [];
      final a = AccountApi('https://h.example:8443/app', client: MockClient((r) async {
        seen.add(r);
        return http.Response('{"sent":true,"expires_in":600}', 200);
      }));
      await a.startEmail('a@example.com', lang: 'zh');
      expect(seen.single.url.toString(), 'https://h.example:8443/v1/auth/email/start');
      expect(jsonDecode(seen.single.body)['lang'], 'zh');
    });
  });

  test('parseSession rejects a missing token', () {
    expect(() => parseSession(200, '{"status":"active"}'), throwsA(isA<AccountApiException>()));
  });
}
