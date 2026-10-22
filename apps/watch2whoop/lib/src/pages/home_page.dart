// Main screen = full-screen embedded Today page (the Body Engine PWA). Login reuses the Today page own token → httpOnly cookie.
// - The Today page handles the safe area (viewport-fit=cover + env(safe-area-inset-*)) and pull-to-refresh itself, so no SafeArea here in full screen.
// - Same-origin links open inside the app; other links ("Ask the butler" → Claude app, SparkyFitness) are handed to the system.
// - Inside the app the Today page sees window.Watch2Whoop (JS channel) and uses it to show the "phone sync" entry, which navigates to watch2whoop://sync.
// - Self-service accounts (Bearer token in the Keychain): when the page is not logged in, the app mints a fresh one-time login code
//   (POST /v1/account/web-login-code) and opens /app/#login=<code>. After the page deletes the account it navigates to
//   watch2whoop://account-deleted: sync stops, the token and config are cleared, and the app returns to onboarding.
//
// Home = the Body Engine today page, full screen in a WebView.
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart';

import '../account_flow.dart';
import '../app_state.dart';
import '../config.dart';
import '../health_range.dart';
import '../health_sync.dart';
import '../i18n.dart';
import '../offline_shell.dart';
import '../page_events.dart';
import '../push.dart';
import '../raw_sync.dart';
import '../setup_flow.dart';
import '../sync_on_open.dart';
import '../ui.dart';
import '../web_mic.dart';
import 'permissions_page.dart';
import 'sync_page.dart';

/// Address the Today page uses to open the native sync page.
const String kSyncLink = 'watch2whoop://sync';

/// The Today page navigates here after deleting the account (「我」→「删除账号」).
const String kAccountDeletedHost = 'account-deleted';

/// Bridge message prefix for the login check of self-service accounts.
const String _kMePrefix = 'w2w-me:';

/// Bridge message prefix for the upload-only token the page mints for setup-link users (raw_sync.dart).
const String _kIngestPrefix = 'w2w-ingest:';

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

  /// The kept copy of the page for a cold start without network (offline_shell.dart); _onShell = it is on screen now.
  final OfflineShell _shell = OfflineShell();
  bool _onShell = false;

  /// Sync on open (sync_on_open.dart): one round on cold start and on every return to the foreground (throttled), then the
  /// Today page asks the server for the new data (web/static/sync.js) and shows 「同步中… / 已更新」.
  late final SyncOnOpen _syncOnOpen = SyncOnOpen(
    canSync: () {
      final c = AppState.instance.config;
      return c.owConnected && c.healthRequested && HealthSync.instance.isSignedIn;
    },
    startRound: () async {
      await HealthSync.instance.startSync(AppState.instance.config.syncDaysBack);
    },
    isSyncing: () async => (await HealthSync.instance.status()).isSyncing,
    onDone: _afterSyncRound,
  );

  /// Read range and history progress for the server (health_range.dart), through the page (it has the login cookie):
  /// every 30 s while the app is in the foreground, after the page loads and after each sync round; only when it changed.
  late final RangeReporter _rangeReporter = RangeReporter((r) async {
    final res = await _web.runJavaScriptReturningResult(
        'window.BodyHealthRange ? (window.BodyHealthRange.report(${jsonEncode(r)}), 1) : 0');
    return res.toString() == '1';
  });
  Timer? _rangeTimer;
  bool _foreground = true;

  Future<void> _reportRange() async {
    final c = AppState.instance.config;
    if (!mounted || !_foreground || _loading || _error != null || !c.owConnected) return;
    try {
      final p = HistoryProgress.from(await HealthSync.instance.status(), requestedTypes.length, DateTime.now());
      await _rangeReporter.maybeSend(rangeReport(c, p));
    } catch (e) {
      debugPrint('range report failed: $e');
    }
  }

  Future<void> _afterSyncRound() async {
    await HealthSync.instance.drainNativeLog();
    for (var i = 0; i < 10 && mounted && (_loading || _error != null); i++) {
      await Future<void>.delayed(const Duration(seconds: 1)); // the page may still be loading on a cold start
    }
    if (!mounted || _error != null) return;
    try {
      await _web.runJavaScript("window.BodySync && window.BodySync.refresh('app');");
    } catch (e) {
      debugPrint('sync refresh call failed: $e');
    }
    await _reportRange();
    await _rawSync();
  }

  /// Beat-to-beat HRV series and ECG recordings, straight to Body Engine (raw_sync.dart), after each sync-on-open round.
  Future<void> _rawSync() async {
    final c = AppState.instance.config;
    if (!mounted || !c.healthRequested) return;
    String? version;
    try {
      final p = await PackageInfo.fromPlatform();
      version = '${p.version}+${p.buildNumber}';
    } catch (_) {}
    await RawSync.instance.run(
      serverUrl: c.todayUrl,
      daysBack: c.syncDaysBack,
      accountToken: AppState.instance.accountToken,
      pageToken: _pageIngestToken,
      appVersion: version,
    );
  }

  /// Setup-link users have no account token: the logged-in page mints an upload-only token (cookie + X-Body-Web) and
  /// passes it back over the JS channel.
  Completer<String?>? _ingestWait;

  Future<String?> _pageIngestToken() async {
    if (!mounted || _loading || _error != null) return null;
    final wait = _ingestWait = Completer<String?>();
    try {
      await _web.runJavaScript(
        "fetch('/v1/web/ingest-token',{method:'POST',credentials:'same-origin',"
        "headers:{'Content-Type':'application/json','X-Body-Web':'1'},body:JSON.stringify({device:'iPhone'})})"
        ".then(function(r){return r.ok?r.json():null})"
        ".then(function(j){Watch2Whoop.postMessage('$_kIngestPrefix'+((j&&j.token)||''))})"
        ".catch(function(){Watch2Whoop.postMessage('$_kIngestPrefix')});",
      );
      return await wait.future.timeout(const Duration(seconds: 15), onTimeout: () => null);
    } catch (e) {
      debugPrint('ingest token failed: $e');
      return null;
    } finally {
      _ingestWait = null;
    }
  }

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
    // Hold-to-talk: WebKit asks the app before the page may use the microphone (web_mic.dart, #171)
    _web = WebViewController.fromPlatformCreationParams(params, onPermissionRequest: _onMediaCapture)
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..addJavaScriptChannel('Watch2Whoop', onMessageReceived: (m) => _onBridge(m.message))
      // Without these WKWebView shows nothing and confirm() is always false (js_dialogs.dart)
      ..setOnJavaScriptAlertDialog((r) => showJsAlert(context, r.message))
      ..setOnJavaScriptConfirmDialog((r) => showJsConfirm(context, r.message))
      ..setOnJavaScriptTextInputDialog((r) => showJsPrompt(context, r.message, r.defaultText))
      ..setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: _onNavigation,
          onPageStarted: (_) => setState(() => _loading = true),
          onPageFinished: (_) {
            setState(() => _loading = false);
            _announceFeatures();
            _maybeCheckLogin();
            Future<void>.delayed(const Duration(seconds: 3), _reportRange);
          },
          // A same-page navigation that only changes the # fragment (one-time login code) may not fire onPageFinished, so fall back on progress
          onProgress: (p) {
            if (p >= 100 && _loading) setState(() => _loading = false);
          },
          onUrlChange: (_) {
            if (_loading) setState(() => _loading = false);
          },
          onWebResourceError: (e) {
            if (e.isForMainFrame ?? true) _mainFrameFailed(e.description);
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
    PushService.instance.opened.addListener(_openTapped);
    PushService.instance.received.addListener(_onPushReceived);
    _load();
    _rangeTimer = Timer.periodic(const Duration(seconds: 30), (_) => _reportRange());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _onLink();
      _syncOnOpen.trigger(); // cold start
      // Someone who was never asked for Health (e.g. updated from a build without the permissions step): show it once
      if (shouldShowPermissions(AppState.instance.config)) {
        Navigator.of(context).push(MaterialPageRoute(builder: (_) => const PermissionsPage()));
      }
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _rangeTimer?.cancel();
    AppState.instance.removeListener(_onConfig);
    SetupLinks.instance.pending.removeListener(_onLink);
    PushService.instance.opened.removeListener(_openTapped);
    PushService.instance.received.removeListener(_onPushReceived);
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
    _foreground = state == AppLifecycleState.resumed;
    if (state != AppLifecycleState.resumed) return;
    HealthSync.instance.drainNativeLog();
    _tellPageForeground();
    PushService.instance.refresh(); // allowed in iOS Settings meanwhile, or a new device token: register again
    _syncOnOpen.trigger(); // back in the foreground: fresh data (at most every 3 minutes, never two rounds at once)
    if (_onShell) {
      _load(); // showing the kept copy of the page: try the real one again
      return;
    }
    // The login code is still pending: the last load was interrupted (e.g. the Health permission sheet came up, the page never
    // ran its script), so try again, but not while a load that just started may still be redeeming the code.
    if (AppState.instance.config.pendingLoginCode != null && DateTime.now().difference(_loadedAt) > const Duration(seconds: 20)) _load();
  }

  /// The page picks up an in-page chat answer that was cut off while the app was in the background (page_events.dart, #114).
  Future<void> _tellPageForeground() async {
    if (!mounted || _loading || _error != null) return;
    try {
      await _web.runJavaScript(kForegroundJs);
    } catch (e) {
      debugPrint('foreground event failed: $e');
    }
  }

  /// The page did not open (no network, server unreachable): the kept copy of the page when there is one for this server
  /// (the page then shows what it kept and queues new records), otherwise the error view.
  Future<void> _mainFrameFailed(String description) async {
    final entry = AppState.instance.config.todayEntry;
    final html = _onShell ? null : await _shell.load(entry);
    if (!mounted) return;
    if (html == null) {
      setState(() {
        _error = description;
        _loading = false;
      });
      return;
    }
    setState(() {
      _onShell = true;
      _error = null;
      _loading = false;
    });
    await _web.loadHtmlString(html, baseUrl: entry.removeFragment().toString());
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
      _onShell = false;
    });
    _checkLogin = false;
    if (code == null && AppState.instance.accountToken != null) {
      // Self-service account: right after sign-in, log the page in with a fresh one-time code; otherwise check after loading.
      if (AppState.instance.webLoginNeeded) {
        AppState.instance.webLoginNeeded = false;
        _webLoginTried = true;
        final entry = await freshWebLoginEntry();
        if (entry != null) {
          await _web.loadRequest(withLangFragment(entry));
          return;
        }
      }
      _checkLogin = !_webLoginTried;
    }
    if (code != null) {
      // The one-time login code goes after the # (kept out of server logs); the Today page swaps it for a cookie and erases it
      // from the address bar right away. It stays saved until the page reports `logged-in` / `login-failed` (_onBridge).
      await _web.loadRequest(withLangFragment(config.todayEntry.replace(fragment: 'login=$code')));
    } else {
      // A tapped notification that started the app opens its page right away (#175)
      final tapped = _takeTapped();
      await _web.loadRequest(tapped ?? withLangFragment(Uri.parse(url))); // #lang=…: the language picked on the native sign-in screen
    }
  }

  /// Tells the loaded page which native features the app has (`window.Watch2WhoopFeatures`, web_mic.dart).
  Future<void> _announceFeatures() async {
    if (_onShell) return; // the kept copy cannot reach the server, so no voice there
    try {
      await _web.runJavaScript(kFeaturesJs);
    } catch (e) {
      debugPrint('features call failed: $e');
    }
  }

  /// WebKit's media capture request: the microphone for our own Today page only; everything else is denied.
  Future<void> _onMediaCapture(WebViewPermissionRequest req) async {
    String? page;
    try {
      page = await _web.currentUrl();
    } catch (_) {}
    final ok = allowMediaCapture(
        types: req.types, pageUrl: page, todayEntry: AppState.instance.config.todayEntry, onShell: _onShell);
    await (ok ? req.grant() : req.deny());
  }

  /// The page of a tapped notification (a path on the server, checked by [notificationTarget]), or null.
  Uri? _takeTapped() {
    final path = PushService.instance.opened.value;
    if (path == null) return null;
    PushService.instance.opened.value = null;
    return notificationTarget(AppState.instance.config.todayEntry, path);
  }

  /// A notification tapped while the app was running: open its page (a change of query reloads, of # only scrolls the page).
  void _openTapped() {
    if (!mounted || _onShell || AppState.instance.config.pendingLoginCode != null) return; // _load() picks it up
    final target = _takeTapped();
    if (target != null) _web.loadRequest(target);
  }

  void _onPushReceived() {
    _web.runJavaScript(kPushReceivedJs).catchError((_) {});
  }

  void _onBridge(String message) {
    if (message.startsWith(_kIngestPrefix)) {
      final t = message.substring(_kIngestPrefix.length);
      final w = _ingestWait;
      if (w != null && !w.isCompleted) w.complete(t.isEmpty ? null : t);
      return;
    }
    if (message == 'sync' || message == 'settings') _openSync();
    if (message == kMicSettingsMessage) openAppSettings(); // microphone declined: this app's page in iOS Settings
    // Push (#175): the page asks on the first Friends visit / in its notification settings; it also reports the unread count
    if (message == kPushAskMessage) {
      PushService.instance.ask().then((s) => _web.runJavaScript(pushStateJs(s))).catchError((_) {});
      return;
    }
    final badge = parseBadgeMessage(message);
    if (badge != null) {
      PushService.instance.setBadge(badge);
      return;
    }
    // Offline use (docs/offline-plan.md): keep / drop the copy of the page; the copy asks for the real page once online
    if (message == 'offline:on' && !_onShell) _shell.save(AppState.instance.config.todayEntry);
    if (message == 'offline:off') _shell.forget();
    if (message == 'reload' && _onShell) _load();
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
    if (!_checkLogin || _onShell) return; // the kept copy of the page sends nothing, so it cannot tell
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
    if (entry != null && mounted) await _web.loadRequest(withLangFragment(entry));
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
