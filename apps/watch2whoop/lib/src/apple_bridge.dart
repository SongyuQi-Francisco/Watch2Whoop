// Sign in with Apple for the Today page inside the app (#295 A7). The page shows 「通过 Apple 登录」 on its login screen
// (web/static/account.js) and 「绑定 Apple 登录」 under Me → 登录方式 (web/static/identities.js) only when the app says it
// can (`window.Watch2WhoopFeatures.appleSignIn` / `.appleLink`); a WKWebView cannot run Apple's web flow itself.
// - page -> app `apple-sign-in`: the native sheet, then POST /v1/auth/apple (as the onboarding screen does), the session is
//   kept in the Keychain and the page is logged in with a fresh one-time code (a pending account then sees the page's
//   invite-code step);
// - page -> app `apple-link`: the native sheet, then the page gets `window.BodyIdentities.appleResult({identity_token,
//   nonce})` (the raw nonce; Apple got its sha256) and binds it with its own cookie session; `appleResult(null)` = cancelled.
// iOS only (Android has no Sign in with Apple); the features are announced only when Apple says it is available.
import 'dart:convert';

import 'package:sign_in_with_apple/sign_in_with_apple.dart';

import 'account_api.dart';
import 'platform_features.dart';

const String kAppleSignInMessage = 'apple-sign-in';
const String kAppleLinkMessage = 'apple-link';

/// JS declaring both Apple features (sent only when [appleAvailable]).
const String kAppleFeaturesJs =
    'window.Watch2WhoopFeatures=Object.assign(window.Watch2WhoopFeatures||{},{appleSignIn:true,appleLink:true});';

/// JS handing the binding credential to the page (null = cancelled).
String appleResultJs(AppleCredential? c) =>
    'window.BodyIdentities&&window.BodyIdentities.appleResult('
    '${c == null ? 'null' : jsonEncode({'identity_token': c.identityToken, 'nonce': c.rawNonce})});';

/// Whether this phone can show the native Apple sheet.
Future<bool> appleAvailable() async {
  if (isAndroid) return false;
  try {
    return await SignInWithApple.isAvailable();
  } catch (_) {
    return false;
  }
}

/// An Apple ID credential with the raw nonce the server checks; null when the person cancelled.
class AppleCredential {
  const AppleCredential(this.identityToken, this.rawNonce, {this.givenName, this.familyName});
  final String identityToken, rawNonce;
  final String? givenName, familyName;
}

Future<AppleCredential?> appleCredential({bool name = true}) async {
  final raw = generateRawNonce();
  try {
    final cred = await SignInWithApple.getAppleIDCredential(
      scopes: name ? const [AppleIDAuthorizationScopes.email, AppleIDAuthorizationScopes.fullName] : const [],
      nonce: sha256Hex(raw),
    );
    final idToken = cred.identityToken;
    if (idToken == null || idToken.isEmpty) throw AccountApiException('bad_response');
    return AppleCredential(idToken, raw, givenName: cred.givenName, familyName: cred.familyName);
  } on SignInWithAppleAuthorizationException catch (e) {
    if (e.code == AuthorizationErrorCode.canceled) return null;
    rethrow;
  }
}
