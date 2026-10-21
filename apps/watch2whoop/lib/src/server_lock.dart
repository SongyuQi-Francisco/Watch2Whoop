// Locked builds: `"W2W_LOCK_SERVER": "true"` in config/defaults.json makes the app always use the servers it was built
// for (W2W_DEFAULT_TODAY_URL / W2W_DEFAULT_OW_HOST). There is then no way to type, scan or paste another server, a setup
// link for another server is refused, and the server address is never shown (the screen may be shared). Builds without
// the flag (self-hosting, development) keep the editable server fields.
import 'config.dart';

const bool kLockServer = bool.fromEnvironment('W2W_LOCK_SERVER');

class ServerLock {
  const ServerLock({required this.requested, required this.todayUrl, required this.owHost});

  /// The flag from the build. The lock only takes effect when both built-in addresses are valid ([locked]):
  /// a locked build without them would have no server at all.
  final bool requested;
  final String todayUrl;
  final String owHost;

  /// The build's lock. Tests replace it to exercise both modes.
  static ServerLock current = const ServerLock(requested: kLockServer, todayUrl: kDefaultTodayUrl, owHost: kDefaultOwHost);

  static String? _valid(String s, UrlKind k) {
    final c = checkServerUrl(s, k);
    return c.isOk ? c.url : null;
  }

  String? get _today => _valid(todayUrl, UrlKind.today);
  String? get _ow => _valid(owHost, UrlKind.ow);

  bool get locked => requested && _today != null && _ow != null;

  /// The config to run with. Locked: the servers are always the built-in ones; a config saved for another server
  /// (e.g. by an earlier unlocked build) is replaced by a fresh one, and `reset` is true so the caller drops the old
  /// account and sync sessions.
  ({AppConfig config, bool reset}) enforce(AppConfig c) {
    if (!locked) return (config: c, reset: false);
    bool same(String stored, String builtIn, UrlKind k) {
      if (stored.isEmpty) return true;
      final v = _valid(stored, k);
      return v != null && sameOrigin(Uri.parse(v), Uri.parse(builtIn));
    }

    if (same(c.todayUrl, _today!, UrlKind.today) && same(c.owHost, _ow!, UrlKind.ow)) {
      return (config: c.copyWith(todayUrl: _today, owHost: _ow), reset: false);
    }
    return (config: AppConfig(todayUrl: _today!, owHost: _ow!, syncDaysBack: c.syncDaysBack), reset: true);
  }

  /// Setup info from a link / QR code. Locked: a link for another server is refused (FormatException `other_server`);
  /// a link for this server keeps its invitation code and login code, so an account opened by the admin can still sign in.
  /// The server fields are dropped either way (the built-in ones stay).
  SetupInfo admit(SetupInfo info) {
    if (!locked) return info;
    for (final (url, kind, builtIn) in [(info.todayUrl, UrlKind.today, _today!), (info.owHost, UrlKind.ow, _ow!)]) {
      if (url == null) continue;
      final v = _valid(url, kind);
      if (v == null || !sameOrigin(Uri.parse(v), Uri.parse(builtIn))) throw const FormatException('other_server');
    }
    return SetupInfo(code: info.code, loginCode: info.loginCode);
  }

  /// Text that may contain the server (sync log, error messages): locked builds show "server" instead of any URL / host.
  String redact(String text) {
    if (!locked) return text;
    var s = text.replaceAll(RegExp(r'\b(?:https?|wss?)://[^\s/"<>]+'), 'server');
    for (final h in {Uri.parse(_today!).host, Uri.parse(_ow!).host}) {
      if (h.isNotEmpty) s = s.replaceAll(RegExp(RegExp.escape(h), caseSensitive: false), 'server');
    }
    return s;
  }
}

/// Shorthands for the UI.
bool get serverLocked => ServerLock.current.locked;
String redactServer(String text) => ServerLock.current.redact(text);
