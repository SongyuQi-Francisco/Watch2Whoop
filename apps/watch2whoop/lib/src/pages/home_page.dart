// Main screen = full-screen embedded Today page (the Body Engine PWA). Login reuses the Today page own token → httpOnly cookie.
// - The Today page handles the safe area (viewport-fit=cover + env(safe-area-inset-*)) and pull-to-refresh itself, so no SafeArea here in full screen.
// - Same-origin links open inside the app; other links ("Ask the butler" → Claude app, SparkyFitness) are handed to the system.
// - Inside the app the Today page sees window.Watch2Whoop (JS channel) and uses it to show the "phone sync" entry, which navigates to watch2whoop://sync.
// - Self-service accounts (Bearer token in the Keychain): when the page is not logged in, the app mints a fresh one-time login code
//   (POST /v1/account/web-login-code) and opens /app/#login=<code>. After the page deletes the account it navigates to
//   watch2whoop://account-deleted: sync stops, the token and config are cleared, and the app returns to onboarding.
//
// Home = the Body Engine today page, full screen in a WebView.
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart';

import '../account_flow.dart';
import '../app_state.dart';
import '../config.dart';
import '../health_sync.dart';
import '../i18n.dart';
import '../setup_flow.dart';
import '../ui.dart';
import 'sync_page.dart';

/// Address the Today page uses to open the native sync page.
const String kSyncLink = 'watch2whoop://sync';

/// The Today page navigates here after deleting the account (「我」→「删除账号」).
const String kAccountDeletedHost = 'account-deleted';

/// Bridge message prefix for the login check of self-service accounts.
const String _kMePrefix = 'w2w-me:';

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> with WidgetsBindingObserver {
  late final WebViewController _web;
  String? _loadedUrl;
  String? _loadedCode;
  DateTime _loadedAt = DateTime.fromMillisecondsSinceEpoch(0);
  String? _error;
  bool _loading = true;

  /// Self-service accounts: check once per page load whether the Today page is logged in; mint a fresh code at most once per app run.
  bool _checkLogin = false;
  bool _webLoginTried = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    final params = WebKitWebViewControllerCreationParams(
      allowsInlineMediaPlayback: true,
      // Media types that can play without a user gesture: none
      mediaTypesRequiringUserAction: const {PlaybackMediaTypes.audio, PlaybackMediaTypes.video},
    );
    _web = WebViewController.fromPlatformCreationParams(params)
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..addJavaScriptChannel('Watch2Whoop', onMessageReceived: (m) => _onBridge(m.message))
      ..setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: _onNavigation,
          onPageStarted: (_) => setState(() => _loading = true),
          onPageFinished: (_) {
            setState(() => _loading = false);
            _maybeCheckLogin();
          },
          // A same-page navigation that only changes the # fragment (one-time login code) may not fire onPageFinished, so fall back on progress
          onProgress: (p) {
            if (p >= 100 && _loading) setState(() => _loading = false);
          },
          onUrlChange: (_) {
            if (_loading) setState(() => _loading = false);
          },
          onWebResourceError: (e) {
            if (e.isForMainFrame ?? true) {
              setState(() {
                _error = e.description;
                _loading = false;
              });
            }
          },
          onHttpError: (e) {
            // The Today page handles its own API 401s etc.; we only care when the main document fails to open (e.g. 502: service not up).
            if (e.request?.uri.toString() == _loadedUrl && (e.response?.statusCode ?? 0) >= 500) {
              setState(() => _error = 'HTTP ${e.response?.statusCode}');
            }
          },
        ),
      );
    final wk = _web.platform;
    if (wk is WebKitWebViewController) {
      wk.setAllowsBackForwardNavigationGestures(false);
      // Debug builds can inspect the page from the Safari "Develop" menu on the Mac.
      wk.setInspectable(kDebugMode);
    }
    AppState.instance.addListener(_onConfig);
    SetupLinks.instance.pending.addListener(_onLink);
    _load();
    WidgetsBinding.instance.addPostFrameCallback((_) => _onLink());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    AppState.instance.removeListener(_onConfig);
    SetupLinks.instance.pending.removeListener(_onLink);
    super.dispose();
  }

  void _onConfig() {
    // Reload when the Today page address changed or a new one-time login code arrived.
    final c = AppState.instance.config;
    if (c.todayEntry.toString() != _loadedUrl || (c.pendingLoginCode != null && c.pendingLoginCode != _loadedCode)) _load();
  }

  /// Already set up, then scanned another setup QR code (e.g. switching phone number / re-invited): ask before applying it.
  Future<void> _onLink() async {
    final info = SetupLinks.instance.pending.value;
    if (info == null || !mounted) return;
    SetupLinks.instance.pending.value = null;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(tr('使用新的设置信息？', 'Use the new setup info?')),
        content: Text(serverLocked
            ? tr('会${info.code != null ? '重新连接同步' : ''}${info.code != null && info.loginCode != null ? '、' : ''}${info.loginCode != null ? '重新登录今天页' : ''}。',
                'This${info.code != null ? ' reconnects sync' : ''}${info.code != null && info.loginCode != null ? ' and' : ''}${info.loginCode != null ? ' signs in again' : ''}.')
            : tr('会更新服务器地址${info.code != null ? '、重新连接同步' : ''}${info.loginCode != null ? '、重新登录今天页' : ''}。',
                'This updates the servers${info.code != null ? ', reconnects sync' : ''}${info.loginCode != null ? ' and signs in again' : ''}.')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(tr('取消', 'Cancel'))),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(tr('使用', 'Use'))),
        ],
      ),
    );
    if (ok != true) return;
    try {
      final r = await applySetup(info);
      if (r.owConnected) {
        await HealthSync.instance.requestAuthorization();
        await HealthSync.instance.startSync(AppState.instance.config.syncDaysBack);
      }
      if (r.owError != null && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(connectError(r.owError!))));
      }
    } on FormatException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(urlError(e.message))));
    }
  }

  @override
  void didChangePlatformBrightness() => _syncBackground();

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Back in the foreground: fetch the logs buffered during background sync (the Today page refreshes itself on demand).
    if (state != AppLifecycleState.resumed) return;
    HealthSync.instance.drainNativeLog();
    // The login code is still pending: the last load was interrupted (e.g. the Health permission sheet came up, the page never
    // ran its script), so try again, but not while a load that just started may still be redeeming the code.
    if (AppState.instance.config.pendingLoginCode != null && DateTime.now().difference(_loadedAt) > const Duration(seconds: 20)) _load();
  }

  void _syncBackground() {
    final dark = WidgetsBinding.instance.platformDispatcher.platformBrightness == Brightness.dark;
    _web.setBackgroundColor(dark ? Colors.black : Colors.white);
  }

  Future<void> _load() async {
    _syncBackground();
    final config = AppState.instance.config;
    final url = config.todayEntry.toString();
    final code = config.pendingLoginCode;
    setState(() {
      _loadedUrl = url;
      _loadedCode = code;
      _loadedAt = DateTime.now();
      _error = null;
      _loading = true;
    });
    _checkLogin = false;
    if (code == null && AppState.instance.accountToken != null) {
      // Self-service account: right after sign-in, log the page in with a fresh one-time code; otherwise check after loading.
      if (AppState.instance.webLoginNeeded) {
        AppState.instance.webLoginNeeded = false;
        _webLoginTried = true;
        final entry = await freshWebLoginEntry();
        if (entry != null) {
          await _web.loadRequest(entry);
          return;
        }
      }
      _checkLogin = !_webLoginTried;
    }
    if (code != null) {
      // The one-time login code goes after the # (kept out of server logs); the Today page swaps it for a cookie and erases it
      // from the address bar right away. It stays saved until the page reports `logged-in` / `login-failed` (_onBridge).
      await _web.loadRequest(config.todayEntry.replace(fragment: 'login=$code'));
    } else {
      await _web.loadRequest(Uri.parse(url));
    }
  }

  void _onBridge(String message) {
    if (message == 'sync' || message == 'settings') _openSync();
    final c = AppState.instance.config;
    if (loginCodeSettled(message) && c.pendingLoginCode != null) {
      AppState.instance.update(c.copyWith(clearPendingLoginCode: true));
    }
    if (message.startsWith(_kMePrefix)) _onLoginStatus(int.tryParse(message.substring(_kMePrefix.length)) ?? 0);
    // Profile language (after login) or the language picked on the login page: the native screens follow it.
    final lang = parseLangMessage(message);
    if (lang != null && lang.lang != langOverride.value) setLangOverride(lang.lang);
  }

  /// Asks the page (same-origin cookie) whether it is logged in; the answer comes back over the JS channel.
  Future<void> _maybeCheckLogin() async {
    if (!_checkLogin) return;
    _checkLogin = false;
    try {
      await _web.runJavaScript(
        "fetch('/v1/web/me',{credentials:'same-origin'})"
        ".then(function(r){Watch2Whoop.postMessage('$_kMePrefix'+r.status)})"
        ".catch(function(){Watch2Whoop.postMessage('${_kMePrefix}0')});",
      );
    } catch (e) {
      debugPrint('login check failed: $e');
    }
  }

  Future<void> _onLoginStatus(int status) async {
    if (status != 401 || _webLoginTried || AppState.instance.accountToken == null) return;
    _webLoginTried = true;
    final entry = await freshWebLoginEntry();
    if (entry != null && mounted) await _web.loadRequest(entry);
  }

  Future<void> _onAccountDeleted() => signOutToOnboarding(revoke: false);

  Future<NavigationDecision> _onNavigation(NavigationRequest req) async {
    final uri = Uri.tryParse(req.url);
    if (uri == null) return NavigationDecision.prevent;
    if (uri.scheme == 'watch2whoop') {
      if (uri.host == 'sync' || uri.host == 'settings') _openSync();
      if (uri.host == kAccountDeletedHost) _onAccountDeleted();
      return NavigationDecision.prevent;
    }
    if (uri.scheme == 'about' || uri.scheme == 'blob' || uri.scheme == 'data') return NavigationDecision.navigate;
    if ((uri.scheme == 'http' || uri.scheme == 'https') && sameOrigin(uri, AppState.instance.config.todayEntry)) {
      return NavigationDecision.navigate;
    }
    // External links: "Ask the butler" (claude.ai → universal link to the Claude app), SparkyFitness, etc.
    if (req.isMainFrame) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
    return NavigationDecision.prevent;
  }

  void _openSync() {
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SyncPage()));
  }

  @override
  Widget build(BuildContext context) {
    final dark = MediaQuery.platformBrightnessOf(context) == Brightness.dark;
    return Scaffold(
      backgroundColor: dark ? Colors.black : Colors.white,
      body: Stack(
        children: [
          Positioned.fill(child: WebViewWidget(controller: _web)),
          if (_loading && _error == null)
            const Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: SafeArea(child: LinearProgressIndicator(minHeight: 2)),
            ),
          if (_error != null) Positioned.fill(child: _errorView(context)),
        ],
      ),
    );
  }

  Widget _errorView(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final config = AppState.instance.config;
    return ColoredBox(
      color: Theme.of(context).scaffoldBackgroundColor,
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Icon(Icons.cloud_off_outlined, size: 44, color: cs.onSurfaceVariant),
              const SizedBox(height: 16),
              Text(
                tr('连不上今天页', 'Cannot reach the today page'),
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
              ),
              const SizedBox(height: 8),
              Text(
                serverLocked
                    ? tr('检查网络后重试。', 'Check your connection and retry.')
                    : tr('检查网络；服务器只在内网时要先连 VPN。\n${shortUrl(config.todayUrl)}',
                        'Check your connection (connect the VPN if the server is private).\n${shortUrl(config.todayUrl)}'),
                textAlign: TextAlign.center,
                style: TextStyle(color: cs.onSurfaceVariant, height: 1.4),
              ),
              const SizedBox(height: 6),
              Text(redactServer(_error ?? ''), textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
              const SizedBox(height: 28),
              BigButton(tr('重试', 'Retry'), onPressed: _load),
              const SizedBox(height: 6),
              BigButton(tr('同步状态和设置', 'Sync status & settings'), secondary: true, onPressed: _openSync),
            ],
          ),
        ),
      ),
    );
  }
}
