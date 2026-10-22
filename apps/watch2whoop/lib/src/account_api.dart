// Self-service account API of Body Engine (sign in with Apple / e-mail code, invite code, sync provisioning).
// Pure Dart + package:http so it can be unit-tested with MockClient; the Bearer token is passed in, storage lives in AccountStore.
import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;

/// Invite codes (admin-issued, beta gate): 10 characters, same unambiguous alphabet as the other codes.
const String kInviteAlphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
const int kInviteLength = 10;

/// Normalizes an invite code: strips spaces and dashes, uppercases; null if the format is wrong.
String? normalizeInviteCode(String input) {
  final c = input.replaceAll(RegExp(r'[\s\-‐-―]'), '').toUpperCase();
  if (c.length != kInviteLength) return null;
  for (final ch in c.split('')) {
    if (!kInviteAlphabet.contains(ch)) return null;
  }
  return c;
}

/// E-mail login code: 6 digits (spaces allowed while typing / pasting).
String? normalizeEmailCode(String input) {
  final c = input.replaceAll(RegExp(r'\s'), '');
  return RegExp(r'^\d{6}$').hasMatch(c) ? c : null;
}

/// Very loose e-mail check (the server validates for real).
bool looksLikeEmail(String s) => RegExp(r'^[^\s@]+@[^\s@]+\.[^\s@]+$').hasMatch(s.trim());

/// Random raw nonce for Sign in with Apple (URL-safe characters, from a secure RNG).
String generateRawNonce([int length = 32, Random? random]) {
  // 0-9, A-Z, a-z, '-', '.', '_'
  final chars = [
    for (var c = 0x30; c <= 0x39; c++) c,
    for (var c = 0x41; c <= 0x5a; c++) c,
    for (var c = 0x61; c <= 0x7a; c++) c,
    0x2d, 0x2e, 0x5f,
  ].map(String.fromCharCode).toList(growable: false);
  final r = random ?? Random.secure();
  return List.generate(length, (_) => chars[r.nextInt(chars.length)]).join();
}

/// sha256 hex of the raw nonce: this goes into the Apple request; the server compares it with the token's nonce claim.
String sha256Hex(String input) => sha256.convert(utf8.encode(input)).toString();

// ---------------------------------------------------------------------------- results

enum AccountStatus { pending, active, disabled, unknown }

AccountStatus parseStatus(Object? s) => switch (s) {
  'pending' => AccountStatus.pending,
  'active' => AccountStatus.active,
  'disabled' => AccountStatus.disabled,
  _ => AccountStatus.unknown,
};

class AccountSession {
  const AccountSession({required this.token, required this.status, required this.newUser, this.displayName, this.userId});
  final String token;
  final AccountStatus status;
  final bool newUser;
  final String? displayName;
  final Object? userId;
}

class AccountInfo {
  const AccountInfo({required this.status, this.displayName, this.inviteRequired = false, this.provisioningState = 'none'});
  final AccountStatus status;
  final String? displayName;
  final bool inviteRequired;

  /// none / pending / done / failed
  final String provisioningState;
}

class OwInvitation {
  const OwInvitation(this.code, this.owUrl);
  final String code;
  final String? owUrl;
}

class AccountApiException implements Exception {
  AccountApiException(this.code, {this.status, this.retryAfter, this.detail});

  /// network / rate_limited / bad_request / invalid / pending / provisioning / unavailable / server / bad_response
  final String code;
  final int? status;
  final Duration? retryAfter;
  final String? detail;

  @override
  String toString() => 'AccountApiException($code${status == null ? '' : ' $status'}${detail == null ? '' : ': $detail'})';
}

// ---------------------------------------------------------------------------- client

class AccountApi {
  AccountApi(this.baseUrl, {this.client, this.timeout = const Duration(seconds: 20)});

  /// Today page / Body Engine base URL, e.g. https://body.example.com (no trailing /).
  final String baseUrl;
  /// Injected for tests; null = a new client per request.
  final http.Client? client;
  final Duration timeout;

  /// The API lives at the server root (the Today page itself calls absolute /v1/… paths), whatever path the Today URL has.
  Uri _u(String path) {
    final b = Uri.parse(baseUrl);
    return Uri(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null, path: path);
  }

  Future<http.Response> _send(String method, String path, {String? token, Object? body}) async {
    final c = client ?? http.Client();
    try {
      final req = http.Request(method, _u(path));
      req.headers['Accept'] = 'application/json';
      if (token != null) req.headers['Authorization'] = 'Bearer $token';
      if (body != null) {
        req.headers['Content-Type'] = 'application/json';
        req.body = jsonEncode(body);
      }
      final streamed = await c.send(req).timeout(timeout);
      return await http.Response.fromStream(streamed).timeout(timeout);
    } on AccountApiException {
      rethrow;
    } catch (e) {
      throw AccountApiException('network', detail: '$e');
    } finally {
      if (client == null) c.close();
    }
  }

  Future<void> startEmail(String email, {required String lang}) async {
    final r = await _send('POST', '/v1/auth/email/start', body: {'email': email.trim(), 'lang': lang});
    _json(r);
  }

  Future<AccountSession> verifyEmail(String email, String code, {required String device}) async {
    final r = await _send('POST', '/v1/auth/email/verify',
        body: {'email': email.trim(), 'code': code, 'client': 'app', 'device': device});
    return parseSession(r.statusCode, r.body, r.headers);
  }

  Future<AccountSession> signInWithApple({
    required String identityToken,
    required String rawNonce,
    required String device,
    String? givenName,
    String? familyName,
  }) async {
    final r = await _send('POST', '/v1/auth/apple', body: {
      'identity_token': identityToken,
      'client': 'app',
      'device': device,
      'nonce': rawNonce,
      if (givenName != null && givenName.isNotEmpty) 'given_name': givenName,
      if (familyName != null && familyName.isNotEmpty) 'family_name': familyName,
    });
    return parseSession(r.statusCode, r.body, r.headers);
  }

  Future<AccountInfo> account(String token) async {
    final r = await _send('GET', '/v1/account', token: token);
    return parseAccount(_json(r));
  }

  Future<AccountStatus> redeemInvite(String token, String code) async {
    final r = await _send('POST', '/v1/account/redeem-invite', token: token, body: {'code': code});
    return parseStatus(_json(r)['status']);
  }

  Future<OwInvitation> owInvitation(String token) async {
    final r = await _send('POST', '/v1/account/ow-invitation', token: token);
    return parseOwInvitation(_json(r));
  }

  /// Asks for an OW invitation code, waiting while the server is still creating the sync account (409 provisioning).
  Future<OwInvitation> owInvitationWhenReady(
    String token, {
    Duration maxWait = const Duration(seconds: 60),
    Duration step = const Duration(seconds: 4),
  }) async {
    final deadline = DateTime.now().add(maxWait);
    while (true) {
      try {
        return await owInvitation(token);
      } on AccountApiException catch (e) {
        if (e.code != 'provisioning' || DateTime.now().add(step).isAfter(deadline)) rethrow;
        await Future<void>.delayed(step);
      }
    }
  }

  Future<String> webLoginCode(String token) async {
    final r = await _send('POST', '/v1/account/web-login-code', token: token);
    final code = _json(r)['code'];
    if (code is! String || code.isEmpty) throw AccountApiException('bad_response');
    return code;
  }

  /// Registers this phone's APNs device token for push notifications (#175); only ever for the signed-in account.
  Future<void> registerPushDevice(String token, {required String deviceToken, required String environment,
      String? appVersion, String? lang}) async {
    final r = await _send('POST', '/v1/users/me/push-devices', token: token, body: {
      'token': deviceToken,
      'environment': environment,
      'app_version': ?appVersion,
      'lang': ?lang,
    });
    if (r.statusCode == 404) throw AccountApiException('unavailable', status: 404); // server without notify/
    _json(r);
  }

  /// Stops pushes to this phone (sign-out). Without [deviceToken]: every device registered with [token].
  Future<void> removePushDevice(String token, {String? deviceToken}) async {
    final r = await _send('DELETE', '/v1/users/me/push-devices', token: token,
        body: deviceToken == null ? null : {'token': deviceToken});
    if (r.statusCode == 401 || r.statusCode == 404) return; // already signed out / server without notify/
    _json(r);
  }

  Future<void> logout(String token) async {
    final r = await _send('POST', '/v1/account/logout', token: token);
    if (r.statusCode == 401) return; // already revoked
    if (r.statusCode >= 300) throw errorFor(r.statusCode, r.body, r.headers);
  }
}

// ---------------------------------------------------------------------------- parsing (pure, unit-tested)

Map<String, Object?> _json(http.Response r) => parseJsonOk(r.statusCode, r.body, r.headers);

Map<String, Object?> parseJsonOk(int status, String body, [Map<String, String> headers = const {}]) {
  if (status < 200 || status >= 300) throw errorFor(status, body, headers);
  if (body.trim().isEmpty) return const {};
  try {
    return (jsonDecode(body) as Map).cast<String, Object?>();
  } catch (_) {
    throw AccountApiException('bad_response', status: status);
  }
}

AccountApiException errorFor(int status, String body, [Map<String, String> headers = const {}]) {
  String? detail;
  try {
    final d = (jsonDecode(body) as Map)['detail'];
    if (d is String) detail = d;
  } catch (_) {}
  Duration? retry;
  final ra = int.tryParse((headers['retry-after'] ?? headers['Retry-After'] ?? '').trim());
  if (ra != null) retry = Duration(seconds: ra);
  final code = switch (status) {
    400 || 422 => 'bad_request',
    401 => 'invalid',
    403 when detail == 'account_pending' => 'pending',
    403 => 'forbidden',
    409 when detail == 'provisioning' => 'provisioning',
    409 => 'conflict',
    429 => 'rate_limited',
    503 => 'unavailable',
    _ => 'server',
  };
  return AccountApiException(code, status: status, retryAfter: retry, detail: detail);
}

AccountSession parseSession(int status, String body, [Map<String, String> headers = const {}]) {
  final j = parseJsonOk(status, body, headers);
  final token = j['token'];
  if (token is! String || token.isEmpty) throw AccountApiException('bad_response', status: status);
  final me = j['me'] is Map ? (j['me'] as Map).cast<String, Object?>() : const <String, Object?>{};
  return AccountSession(
    token: token,
    status: parseStatus(j['status'] ?? me['status']),
    newUser: j['new_user'] == true,
    displayName: me['display_name'] as String?,
    userId: me['id'],
  );
}

AccountInfo parseAccount(Map<String, Object?> j) {
  final prov = j['provisioning'] is Map ? (j['provisioning'] as Map) : const {};
  return AccountInfo(
    status: parseStatus(j['status']),
    displayName: j['display_name'] as String?,
    inviteRequired: j['invite_required'] == true,
    provisioningState: (prov['state'] as String?) ?? 'none',
  );
}

OwInvitation parseOwInvitation(Map<String, Object?> j) {
  final code = j['code'];
  if (code is! String || code.isEmpty) throw AccountApiException('bad_response');
  final url = j['ow_url'];
  return OwInvitation(code, url is String && url.isNotEmpty ? url : null);
}
