// Config: server addresses, sync range, onboarding progress. Pure Dart (storage lives in ConfigStore) for easy unit testing.
// Config: server URLs, sync range, onboarding state. Pure Dart; persistence lives in ConfigStore.
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// Compile-time defaults (empty in the repo; when building yourself fill them with --dart-define-from-file=config/defaults.json).
/// Build-time defaults. Empty in the repo; set them with --dart-define-from-file.
const String kDefaultTodayUrl = String.fromEnvironment('W2W_DEFAULT_TODAY_URL');
const String kDefaultOwHost = String.fromEnvironment('W2W_DEFAULT_OW_HOST');

/// Days to sync on the first run. export.xml already covered older history, so by default only the last 90 days are synced.
/// Days of history for the first sync (older history is imported from export.xml).
const int kDefaultSyncDaysBack = int.fromEnvironment('W2W_SYNC_DAYS_BACK', defaultValue: 90);

/// OW invitation code: 8 characters, alphabet without the easily confused 0 O 1 I.
/// Open Wearables invitation codes: 8 chars from an unambiguous alphabet.
const String kInvitationAlphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
const int kInvitationLength = 8;

class AppConfig {
  const AppConfig({
    this.todayUrl = '',
    this.owHost = '',
    this.syncDaysBack = kDefaultSyncDaysBack,
    this.onboardingDone = false,
    this.owUserId,
    this.healthRequested = false,
    this.pendingLoginCode,
  });

  /// Address of the Today page (Body Engine), e.g. `https://<mac>.<tailnet>.ts.net`
  final String todayUrl;

  /// Open Wearables API address (host only, the SDK appends /api/v1/... itself), e.g. `https://<mac>.<tailnet>.ts.net:8443`
  final String owHost;
  final int syncDaysBack;
  final bool onboardingDone;

  /// OW user id obtained after redeeming the invitation code (display only; credentials live in the SDK Keychain).
  final String? owUserId;
  final bool healthRequested;

  /// One-time Today page login code carried in the setup info: opened as `/app/#login=code` until the Today page reports
  /// the outcome over the JS channel (see [loginCodeSettled]); kept until then, so an interrupted load retries on the next open.
  final String? pendingLoginCode;

  bool get owConnected => owUserId != null && owUserId!.isNotEmpty;

  /// Address the WebView opens: goes straight to /app/ when only the host is set.
  Uri get todayEntry {
    final u = Uri.parse(todayUrl);
    return (u.path.isEmpty || u.path == '/') ? u.replace(path: '/app/') : u;
  }

  AppConfig copyWith({
    String? todayUrl,
    String? owHost,
    int? syncDaysBack,
    bool? onboardingDone,
    String? owUserId,
    bool clearOwUserId = false,
    bool? healthRequested,
    String? pendingLoginCode,
    bool clearPendingLoginCode = false,
  }) => AppConfig(
    todayUrl: todayUrl ?? this.todayUrl,
    owHost: owHost ?? this.owHost,
    syncDaysBack: syncDaysBack ?? this.syncDaysBack,
    onboardingDone: onboardingDone ?? this.onboardingDone,
    owUserId: clearOwUserId ? null : (owUserId ?? this.owUserId),
    healthRequested: healthRequested ?? this.healthRequested,
    pendingLoginCode: clearPendingLoginCode ? null : (pendingLoginCode ?? this.pendingLoginCode),
  );

  Map<String, Object?> toJson() => {
    'todayUrl': todayUrl,
    'owHost': owHost,
    'syncDaysBack': syncDaysBack,
    'onboardingDone': onboardingDone,
    'owUserId': owUserId,
    'healthRequested': healthRequested,
    'pendingLoginCode': pendingLoginCode,
  };

  factory AppConfig.fromJson(Map<String, Object?> j) => AppConfig(
    todayUrl: (j['todayUrl'] as String?) ?? kDefaultTodayUrl,
    owHost: (j['owHost'] as String?) ?? kDefaultOwHost,
    syncDaysBack: (j['syncDaysBack'] as int?) ?? kDefaultSyncDaysBack,
    onboardingDone: (j['onboardingDone'] as bool?) ?? false,
    owUserId: j['owUserId'] as String?,
    healthRequested: (j['healthRequested'] as bool?) ?? false,
    pendingLoginCode: j['pendingLoginCode'] as String?,
  );
}

/// Messages the Today page posts on the `Watch2Whoop` JS channel once it knows how the login went:
/// `logged-in` (a session works, /v1/web/me answered) or `login-failed` (the server rejected the code: used / expired).
/// Either one settles the pending one-time login code; anything else (network error, page killed) keeps it for a retry.
bool loginCodeSettled(String message) => message == 'logged-in' || message == 'login-failed';

class ConfigStore {
  static const _key = 'w2w.config.v1';

  static Future<AppConfig> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);
    if (raw == null) return const AppConfig(todayUrl: kDefaultTodayUrl, owHost: kDefaultOwHost);
    try {
      return AppConfig.fromJson((jsonDecode(raw) as Map).cast<String, Object?>());
    } catch (_) {
      return const AppConfig(todayUrl: kDefaultTodayUrl, owHost: kDefaultOwHost);
    }
  }

  static Future<void> save(AppConfig c) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, jsonEncode(c.toJson()));
  }
}

// ---------------------------------------------------------------------------- URL validation

enum UrlKind { today, ow }

class UrlCheck {
  const UrlCheck.ok(this.url, {this.insecureLocal = false}) : error = null;
  const UrlCheck.bad(this.error) : url = null, insecureLocal = false;

  /// Normalized address (no trailing /); null on error.
  final String? url;
  final String? error;

  /// http for local / LAN addresses: only for the simulator and development.
  final bool insecureLocal;
  bool get isOk => url != null;
}

/// Validates and normalizes a server address.
/// - Adds https:// when no scheme is given
/// - Must be https; http is only allowed for localhost, 127.0.0.1, *.local and private LAN IPs (development / simulator)
/// - No credentials, query parameters or #
/// - For OW addresses, strips a mistakenly entered /api/v1 and a trailing /
UrlCheck checkServerUrl(String input, UrlKind kind) {
  var s = input.trim();
  if (s.isEmpty) return const UrlCheck.bad('empty');
  if (!s.contains('://')) s = 'https://$s';
  final Uri u;
  try {
    u = Uri.parse(s);
  } on FormatException {
    return const UrlCheck.bad('invalid');
  }
  if (u.scheme != 'https' && u.scheme != 'http') return const UrlCheck.bad('scheme');
  if (u.host.isEmpty) return const UrlCheck.bad('invalid');
  if (u.userInfo.isNotEmpty || u.hasQuery || u.hasFragment) return const UrlCheck.bad('extra');
  final insecure = u.scheme == 'http';
  if (insecure && !isLocalHost(u.host)) return const UrlCheck.bad('https');

  var path = u.path;
  if (kind == UrlKind.ow) {
    path = path.replaceFirst(RegExp(r'/api/v1/?$'), '');
  }
  while (path.endsWith('/')) {
    path = path.substring(0, path.length - 1);
  }
  final normalized = Uri(
    scheme: u.scheme,
    host: u.host,
    port: u.hasPort ? u.port : null,
    path: path,
  ).toString();
  return UrlCheck.ok(normalized, insecureLocal: insecure);
}

/// Local / LAN address (http allowed, development only).
bool isLocalHost(String host) {
  final h = host.toLowerCase();
  if (h == 'localhost' || h == '127.0.0.1' || h == '::1' || h == '[::1]') return true;
  if (h.endsWith('.local')) return true;
  final parts = h.split('.');
  if (parts.length != 4) return false;
  final n = parts.map(int.tryParse).toList();
  if (n.any((x) => x == null || x < 0 || x > 255)) return false;
  final a = n[0]!, b = n[1]!;
  return a == 10 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168);
}

/// Whether two addresses share the same origin (WebView decides whether a link opens in the app or is handed to the system).
bool sameOrigin(Uri a, Uri b) =>
    a.scheme == b.scheme && a.host.toLowerCase() == b.host.toLowerCase() && a.port == b.port;

// ---------------------------------------------------------------------------- invitation code

/// Normalizes an invitation code: strips spaces and -, uppercases; returns null if the format is wrong.
String? normalizeInvitationCode(String input) {
  final c = input.replaceAll(RegExp(r'[\s-]'), '').toUpperCase();
  if (c.length != kInvitationLength) return null;
  for (final ch in c.split('')) {
    if (!kInvitationAlphabet.contains(ch)) return null;
  }
  return c;
}

/// One-time Today page login code (Body Engine `web/login_codes.py`): 12 characters, same alphabet as the invitation code.
const int kLoginCodeLength = 12;

String? normalizeLoginCode(String input) {
  final c = input.replaceAll(RegExp(r'[\s-]'), '').toUpperCase();
  if (c.length != kLoginCodeLength) return null;
  for (final ch in c.split('')) {
    if (!kInvitationAlphabet.contains(ch)) return null;
  }
  return c;
}

// ---------------------------------------------------------------------------- one paste / scan fills everything

/// "Setup info": server address + OW invitation code + one-time Today page login code (generated on the computer with deploy/open-wearables/invite.py).
/// Three formats are supported:
/// - Setup link / QR code: `watch2whoop://setup?today=…&ow=…&code=…&login=…` (scanning with the camera opens the app directly)
/// - JSON：`{"today": …, "ow": …, "code": …, "login": …}`
/// - One `key=value` / `key: value` per line
/// It carries no long-lived token: the invitation code and login code are single-use and expire.
class SetupInfo {
  const SetupInfo({this.todayUrl, this.owHost, this.code, this.loginCode});
  final String? todayUrl;
  final String? owHost;

  /// OW invitation code (8 characters)
  final String? code;

  /// One-time Today page login code (12 characters)
  final String? loginCode;
  bool get isEmpty => todayUrl == null && owHost == null && code == null && loginCode == null;
  bool get hasServers => todayUrl != null && owHost != null;
}

const String kSetupScheme = 'watch2whoop';

/// Builds the setup link (same format as invite.py).
String buildSetupLink(SetupInfo i) => Uri(
  scheme: kSetupScheme,
  host: 'setup',
  queryParameters: {
    if (i.todayUrl != null) 'today': i.todayUrl!,
    if (i.owHost != null) 'ow': i.owHost!,
    if (i.code != null) 'code': i.code!,
    if (i.loginCode != null) 'login': i.loginCode!,
  },
).toString();

SetupInfo parseSetupText(String text) {
  final values = <String, String>{};
  final t = text.trim();
  if (t.toLowerCase().startsWith('$kSetupScheme://')) {
    try {
      Uri.parse(t).queryParameters.forEach((k, v) => values[k.toLowerCase()] = v.trim());
    } catch (_) {}
  } else if (t.startsWith('{')) {
    try {
      final j = (jsonDecode(t) as Map).cast<String, Object?>();
      j.forEach((k, v) {
        if (v is String) values[k.toLowerCase()] = v.trim();
      });
    } catch (_) {
      // Not JSON, read line by line
    }
  }
  if (values.isEmpty) {
    for (final line in t.split(RegExp(r'[\r\n]+'))) {
      final m = RegExp(r'^\s*([A-Za-z_]+)\s*[=:]\s*(.+?)\s*$').firstMatch(line);
      if (m != null) values[m.group(1)!.toLowerCase()] = m.group(2)!;
    }
  }
  String? pick(List<String> keys) {
    for (final k in keys) {
      final v = values[k];
      if (v != null && v.isNotEmpty) return v;
    }
    return null;
  }

  var code = pick(['code', 'invitation_code', 'invite']);
  // Only a single 8-character code was pasted
  if (values.isEmpty) code = normalizeInvitationCode(t) != null ? t : null;
  final login = pick(['login', 'login_code']);
  return SetupInfo(
    todayUrl: pick(['today', 'today_url', 'body', 'body_engine']),
    owHost: pick(['ow', 'ow_host', 'ow_url', 'open_wearables', 'sync']),
    code: code == null ? null : normalizeInvitationCode(code),
    loginCode: login == null ? null : normalizeLoginCode(login),
  );
}
