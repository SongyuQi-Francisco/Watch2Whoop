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
import 'package:flutter/services.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart';

import '../account_api.dart' show AccountApiException;
import '../account_flow.dart';
import '../android_bridge.dart';
import '../app_state.dart';
import '../apple_bridge.dart';
import '../config.dart';
import '../health_range.dart';
import '../health_sync.dart';
import '../i18n.dart';
import '../offline_shell.dart';
import '../page_events.dart';
import '../platform_features.dart';
import '../push.dart';
import '../raw_sync.dart';
import '../resync.dart';
import '../resync_flow.dart';
import '../setup_flow.dart';
import '../sync_bridge.dart';
import '../sync_on_open.dart';
import '../sync_repair.dart';
import '../sync_stats.dart' show SdkSyncStatus;
import '../ui.dart';
import '../voice_bridge.dart';
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

/// The automatic sync repairs of the last days (sync_repair.dart).
const String _kRepairKey = 'w2w.syncRepair.v1';

/// Bridge message: open this app's page in iOS Settings (the sync status's `settings` action, e.g. Background App Refresh).
const String kAppSettingsMessage = 'app-settings';

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
      await HealthSync.instance.startSync(Resync.instance.effectiveDaysBack(AppState.instance.config.syncDaysBack));
    },
    isSyncing: () async => (await HealthSync.instance.status()).isSyncing,
    onDone: _afterSyncRound,
  );

  /// Open Wearables rejects this phone (HTTP 401 in the SDK log): reconnect by itself, at most 3 times a day (sync_repair.dart).
  late final SyncAutoRepair _autoRepair = SyncAutoRepair(
    needsRepair: () => HealthSync.instance.stats.value.authProblem,
    canRepair: () {
      final c = AppState.instance.config;
      return mounted && _foreground && hasHealthSync && AppState.instance.accountToken != null && c.owConnected;
    },
    repair: _repairSync,
    load: () async => (await SharedPreferences.getInstance()).getString(_kRepairKey),
    save: (v) async => (await SharedPreferences.getInstance()).setString(_kRepairKey, v),
  );

  Future<RepairOutcome> _repairSync() async {
    final r = await connectSyncViaAccount();
    if (!r.connected) {
      await HealthSync.instance.note('Auto reconnect failed: ${r.manualNeeded ? 'needs a code' : r.error}');
      return r.manualNeeded ? RepairOutcome.notPossible : RepairOutcome.failed;
    }
    // Same steps as 「连接同步」 on the sync page: the SDK forgets its tracked types when it signs out; a sheet that did
    // not come up is asked again. Then one round right away (sync investigation 2026-10-26: after a reconnect the phone
    // sent nothing until the next open).
    await HealthSync.instance.note('Auto reconnect: connected again');
    await _askHealthChecked();
    unawaited(_syncOnOpen.trigger(force: true));
    return RepairOutcome.connected;
  }

  /// The Health sheet(s), then whether iOS still wants to ask about the core types: a sheet swallowed while another
  /// view was closing (#214; seen 2026-10-26: accounts connected that never uploaded) is asked once more. Returns the
  /// core types iOS still has not asked about.
  Future<List<String>> _askHealthChecked() async {
    if (!hasHealthSync) return const [];
    await HealthSync.instance.requestAuthorization();
    final c = AppState.instance.config;
    await AppState.instance.update(c.copyWith(healthRequested: true, permissionsSeen: true));
    var unasked = await healthUnasked(kCoreHealthTypes);
    if (unasked.isNotEmpty) {
      await HealthSync.instance.note('Health sheet: ${unasked.length} core types still not asked; asking again');
      await Future<void>.delayed(const Duration(milliseconds: 1200));
      await HealthSync.instance.requestAuthorization();
      unasked = await healthUnasked(kCoreHealthTypes);
    }
    _unasked = unasked;
    return unasked;
  }

  /// Core Health types iOS has not asked about yet (last check; null = not checked in this app run).
  List<String>? _unasked;

  void _onSyncStats() => unawaited(_autoRepair.trigger());

  /// The Today page's 「开启同步」 / 「立即同步」 / 「同步状态」 (sync_bridge.dart).
  late final SyncBridge _syncBridge = SyncBridge(
    supported: () => hasHealthSync,
    isConnected: () => AppState.instance.config.owConnected && HealthSync.instance.isSignedIn,
    canUpload: () =>
        hasHealthSync &&
        (AppState.instance.config.healthRequested || (AppState.instance.config.owConnected && HealthSync.instance.isSignedIn)),
    needsReconnect: () => HealthSync.instance.stats.value.authProblem,
    connect: _bridgeConnect,
    askHealth: _askHealthChecked,
    runRound: _bridgeRound,
    status: _syncStatus,
    progress: _bridgeProgress,
  );

  /// The last progress for the page's `onApp` (doc §3), sent again after the page reloads (within 10 minutes).
  Map<String, Object?>? _lastOnApp;
  DateTime? _lastOnAppAt;

  void _bridgeProgress(Map<String, Object?> e) {
    _web.runJavaScript(syncProgressJs(e)).catchError((_) {});
    final app = onAppEvent(e);
    if (app == null) return;
    _lastOnApp = app;
    _lastOnAppAt = DateTime.now();
    _web.runJavaScript(appProgressJs(app)).catchError((_) {});
    if (e['step'] == 'done' || e['step'] == 'error') unawaited(_reportDevice());
  }

  void _resendProgress() {
    final app = _lastOnApp, at = _lastOnAppAt;
    if (app == null || at == null || DateTime.now().difference(at) > const Duration(minutes: 10)) return;
    _web.runJavaScript(appProgressJs(app)).catchError((_) {});
  }

  /// Connects the background sync: the account's own server-minted invitation (like 「连接同步」), or the one-time code
  /// the page's `sync-connect` link carried (setup-link users without an account token).
  Future<SyncConnectOutcome> _bridgeConnect(({String? owHost, String? code})? link) async {
    if (AppState.instance.accountToken == null && link?.code != null) {
      try {
        final r = await applySetup(SetupInfo(owHost: link!.owHost, code: link.code));
        if (r.owConnected) {
          await HealthSync.instance.note('Sync connected from the Today page (code)');
          return const SyncConnectOutcome(connected: true);
        }
        return SyncConnectOutcome(message: r.owError == null ? null : connectError(r.owError!));
      } on FormatException catch (e) {
        return SyncConnectOutcome(message: urlError(e.message));
      }
    }
    if (AppState.instance.accountToken == null) {
      return SyncConnectOutcome(
          manualNeeded: true,
          message: tr('这个账号要用设置链接里的邀请码连接同步：打开「手机同步」', 'This account connects sync with the invitation code from its setup link: open Phone sync'));
    }
    final r = await connectSyncViaAccount();
    if (r.connected) await HealthSync.instance.note('Sync connected from the Today page');
    final e = r.error;
    return SyncConnectOutcome(
      connected: r.connected,
      manualNeeded: r.manualNeeded,
      message: e == null ? null : (e is AccountApiException ? accountError(e) : connectError(e)),
    );
  }

  /// One round now: the SDK round (no 3-minute throttle), then the app's own uploads and the page refresh
  /// (_afterSyncRound). Progress (items sent, types done) goes to the page every 2 seconds meanwhile.
  Future<bool> _bridgeRound() async {
    final tick = Timer.periodic(const Duration(seconds: 2), (_) async {
      final st = await HealthSync.instance.status();
      if (st.isSyncing) {
        // the first export: how far it got (types done of the types read); afterwards no percentage
        final p = st.initialExportDone ? null : HistoryProgress.from(st, requestedTypes.length, DateTime.now()).percent;
        _bridgeProgress({
          'step': 'upload',
          'message': tr('正在上传…', 'Uploading…'),
          'sent': st.sentCount,
          'types': st.completedTypes,
          'percent': ?p,
        });
      }
    });
    try {
      if (!_syncOnOpen.canSync()) {
        await _ownUploads(force: true); // background sync not connected: the app's own uploads still go
        return true;
      }
      if (_syncOnOpen.running) {
        while (_syncOnOpen.running) {
          await Future<void>.delayed(const Duration(seconds: 1));
        }
        return true;
      }
      return await _syncOnOpen.trigger(force: true);
    } finally {
      tick.cancel();
    }
  }

  Future<Map<String, Object?>> _syncStatus() async {
    final c = AppState.instance.config;
    var ask = 'unknown';
    SdkSyncStatus? sdk;
    if (hasHealthSync) {
      await HealthSync.instance.drainNativeLog();
      try {
        ask = await RawSync.instance.source.authStatus();
      } catch (_) {}
      if (c.owConnected) sdk = await HealthSync.instance.status();
      if (c.healthRequested) _unasked = await healthUnasked(kCoreHealthTypes);
    }
    return buildSyncStatus(await _statusInput(ask: ask, sdk: sdk));
  }

  Future<SyncStatusInput> _statusInput({String ask = 'unknown', SdkSyncStatus? sdk}) async {
    final c = AppState.instance.config;
    return SyncStatusInput(
      supported: hasHealthSync,
      signedIn: AppState.instance.accountToken != null,
      connected: c.owConnected && HealthSync.instance.isSignedIn,
      healthAsked: c.healthRequested,
      healthNeedsAsk: ask == 'shouldRequest' || (_unasked?.isNotEmpty ?? false),
      running: _syncOnOpen.running || (sdk?.isSyncing ?? false),
      firstSyncDone: sdk?.initialExportDone,
      ow: HealthSync.instance.stats.value,
      raw: RawSync.instance.status.value,
      device: await DeviceSyncState.read(),
      now: DateTime.now(),
    );
  }

  /// The phone's sync state for the server (doc §4, PUT /v1/ingest/sync-device): after every round, on the page's
  /// sync calls and on return to the foreground; the same content at most every 10 minutes.
  final DeviceReporter _deviceReporter = DeviceReporter();

  Future<void> _reportDevice() async {
    if (!hasHealthSync || !mounted) return;
    try {
      final c = AppState.instance.config;
      final token = AppState.instance.accountToken ?? await RawSync.instance.ingestToken();
      if (token == null) return;
      final sdk = c.owConnected ? await HealthSync.instance.status() : null;
      if (c.healthRequested) _unasked = await healthUnasked(kCoreHealthTypes);
      String? version;
      try {
        version = (await PackageInfo.fromPlatform()).version;
      } catch (_) {}
      final input = await _statusInput(sdk: sdk);
      final report = buildDeviceReport(
        input,
        appVersion: version,
        unasked: _unasked ?? const [],
        foreground: _foreground,
        sdkActive: c.owConnected ? HealthSync.instance.isSyncActive : null,
        sdk: sdk,
        firstSyncPercent: sdk == null || sdk.initialExportDone
            ? null
            : HistoryProgress.from(sdk, requestedTypes.length, DateTime.now()).percent,
      );
      await _deviceReporter.send(c.todayUrl, token, report);
    } catch (e) {
      debugPrint('device report failed: $e');
    }
  }

  Future<void> _onSyncCall(({String verb, String id}) m) async {
    Map<String, Object?> res;
    try {
      res = switch (m.verb) {
        'enable' => await _syncBridge.enable(),
        'now' => await _syncBridge.now(),
        _ => {'ok': true, 'status': await _syncStatus()},
      };
    } catch (e) {
      res = {'ok': false, 'error': {'code': 'failed', 'message': redactServer('$e'), 'action': 'retry'}};
    }
    if (!mounted) return;
    await _web.runJavaScript(syncReplyJs(m.id, res)).catchError((_) {});
  }

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
    await stepResync(pageToken: _pageIngestToken); // a forced re-sync still running (resync_flow.dart): continue it
    await _reportDevice();
  }

  /// The app's own uploads when the background sync is not connected (skipped in onboarding, no account, OW refused):
  /// they do not depend on it, so they run on open / resume as well (at most every 3 minutes) and on the page's syncNow.
  DateTime? _ownAt;
  bool _ownRunning = false;

  Future<void> _ownUploads({bool force = false}) async {
    final now = DateTime.now();
    if (_ownRunning || !hasHealthSync || !AppState.instance.config.healthRequested) return;
    if (!force && _ownAt != null && now.difference(_ownAt!) < const Duration(minutes: 3)) return;
    _ownRunning = true;
    _ownAt = now;
    try {
      for (var i = 0; i < 10 && mounted && (_loading || _error != null); i++) {
        await Future<void>.delayed(const Duration(seconds: 1)); // the page may still be loading on a cold start
      }
      await _rawSync();
      if (mounted && _error == null) {
        await _web.runJavaScript("window.BodySync && window.BodySync.refresh('app');").catchError((_) {});
      }
      await _reportDevice();
    } finally {
      _ownRunning = false;
    }
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
    final PlatformWebViewControllerCreationParams params = isAndroid
        ? AndroidWebViewControllerCreationParams()
        : WebKitWebViewControllerCreationParams(
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
    if (wk is AndroidWebViewController) {
      // Android (#253): file inputs (meal photos, videos, avatar, CSV import) need the app to pick the files
      wk.setOnShowFileSelector(_pickFiles);
      wk.setMediaPlaybackRequiresUserGesture(true);
      // Debug builds can inspect the page from chrome://inspect.
      AndroidWebViewController.enableDebugging(kDebugMode);
    }
    // Jarvis voice input on the phone (voice_bridge.dart, #53): the recogniser's events go to the page as they come
    VoiceBridge.instance
      ..attach()
      ..onEvent = (e) => _web.runJavaScript(voiceEventJs(e)).catchError((_) {});
    AppState.instance.addListener(_onConfig);
    SetupLinks.instance.pending.addListener(_onLink);
    PushService.instance.opened.addListener(_openTapped);
    PushService.instance.received.addListener(_onPushReceived);
    HealthSync.instance.stats.addListener(_onSyncStats);
    _load();
    _rangeTimer = Timer.periodic(const Duration(seconds: 30), (_) => _reportRange());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _onLink();
      _syncOnOpen.trigger(); // cold start
      if (!_syncOnOpen.canSync()) _ownUploads(); // not connected: the app's own uploads still go
      _onSyncStats(); // a 401 kept from the last run
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
    HealthSync.instance.stats.removeListener(_onSyncStats);
    VoiceBridge.instance.onEvent = null;
    VoiceBridge.instance.cancelAll();
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
        await _askHealthChecked();
        unawaited(_syncOnOpen.trigger(force: true)); // a round right away after connecting
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
    if (state == AppLifecycleState.paused) VoiceBridge.instance.cancelAll(); // the microphone never stays on in the background
    if (state != AppLifecycleState.resumed) return;
    HealthSync.instance.drainNativeLog();
    _tellPageForeground();
    _announceNet();
    _reportDevice(); // e.g. Background App Refresh or Low Power Mode changed meanwhile
    PushService.instance.refresh(); // allowed in iOS Settings meanwhile, or a new device token: register again
    _syncOnOpen.trigger(); // back in the foreground: fresh data (at most every 3 minutes, never two rounds at once)
    if (!_syncOnOpen.canSync()) _ownUploads();
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

  /// Tells the loaded page which native features the app has (`window.Watch2WhoopFeatures`, web_mic.dart, voice_bridge.dart).
  Future<void> _announceFeatures() async {
    if (_onShell) return; // the kept copy cannot reach the server, so no voice there
    try {
      await _web.runJavaScript(featuresJs(
          mic: !isAndroid || await AndroidBridge.hasMic(),
          haptics: !isAndroid, // the native side is iOS only (VoiceInput.swift); Android: no voice either (no channel)
          voiceLangs: await VoiceBridge.instance.langs()));
      // Sync from the page (sync_bridge.dart): 「开启同步」 / 「立即同步」 / 「同步状态」
      if (hasHealthSync) {
        await _web.runJavaScript(kSyncBridgeJs);
        await _web.runJavaScript(kSyncLinkFeaturesJs); // the page's 一键开启 / 立即上传 / 去设置 / 去授权 (doc §3, §5)
        _resendProgress();
      }
      // Sign in with / bind Apple from the page's own screens (apple_bridge.dart)
      if (await appleAvailable()) await _web.runJavaScript(kAppleFeaturesJs);
      await _announceNet();
    } catch (e) {
      debugPrint('features call failed: $e');
    }
  }

  /// `Watch2WhoopFeatures.net` (wifi / cellular / constrained = Low Data Mode / offline …): the page skips its idle
  /// prefetch on cellular and in Low Data Mode (app.js prefetchNetOk). Again on every return to the foreground.
  Future<void> _announceNet() async {
    if (_onShell || _loading || _error != null) return;
    try {
      final net = netFeatureJs((await DeviceSyncState.read()).net);
      if (net != null) await _web.runJavaScript(net);
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
    var ok = allowMediaCapture(
        types: req.types, pageUrl: page, todayEntry: AppState.instance.config.todayEntry, onShell: _onShell);
    // Android: the app itself needs RECORD_AUDIO first (system dialog); declined = the page offers the settings
    if (ok && isAndroid) ok = await AndroidBridge.requestMic();
    await (ok ? req.grant() : req.deny());
  }

  /// Android file inputs: the system picker (camera offered next to it, or straight to the camera for `capture`), only
  /// for our own Today page.
  Future<List<String>> _pickFiles(FileSelectorParams p) async {
    if (p.mode == FileSelectorMode.save) return const [];
    String? page;
    try {
      page = await _web.currentUrl();
    } catch (_) {}
    final u = page == null ? null : Uri.tryParse(page);
    if (u == null || !sameOrigin(u, AppState.instance.config.todayEntry)) return const [];
    return AndroidBridge.pickFiles(
        acceptTypes: p.acceptTypes, multiple: p.mode == FileSelectorMode.openMultiple, capture: p.isCaptureEnabled);
  }

  /// Android back button / gesture: back in the page's history first, then leave the app.
  Future<void> _onBack() async {
    if (_error == null && await _web.canGoBack()) {
      await _web.goBack();
    } else {
      await SystemNavigator.pop();
    }
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
    // Jarvis voice input and haptics (voice_bridge.dart): only for the real page, never the kept offline copy
    final voice = parseVoiceMessage(message);
    if (voice != null) {
      if (!_onShell) VoiceBridge.instance.command(voice);
      return;
    }
    final haptic = parseHapticMessage(message);
    if (haptic != null) {
      VoiceBridge.instance.haptic(haptic);
      return;
    }
    // Sync from the page (sync_bridge.dart): the answer goes back with the call's id
    final sync = parseSyncMessage(message);
    if (sync != null) {
      _onSyncCall(sync);
      return;
    }
    if (message == kAppSettingsMessage) {
      openAppSettings();
      return;
    }
    if (message == kAppleSignInMessage && !_onShell) {
      _appleSignIn();
      return;
    }
    if (message == kAppleLinkMessage && !_onShell) {
      _appleLink();
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

  bool _appleBusy = false;

  /// The page's 「通过 Apple 登录」: native sheet -> /v1/auth/apple -> session in the Keychain -> page logged in with a
  /// fresh one-time code (apple_bridge.dart). Cancelling does nothing; errors show as a short message.
  Future<void> _appleSignIn() async {
    if (_appleBusy) return;
    _appleBusy = true;
    try {
      final c = await appleCredential();
      if (c == null) return;
      final server = AppState.instance.config.todayUrl;
      final s = await accountApiFor(server).signInWithApple(
          identityToken: c.identityToken,
          rawNonce: c.rawNonce,
          device: deviceLabel,
          givenName: c.givenName,
          familyName: c.familyName);
      await saveSession(s, server);
      _webLoginTried = false;
      await _load();
    } on AccountApiException catch (e) {
      _snack(switch (e.code) {
        'invalid' => tr('Apple 登录验证没通过，请再试一次', 'The Apple sign-in could not be verified — try again'),
        'unavailable' => tr('这个服务器没有开通「通过 Apple 登录」，请用邮箱登录', 'This server has no Sign in with Apple — use e-mail'),
        _ => accountError(e),
      });
    } catch (e) {
      _snack(tr('通过 Apple 登录没有成功', 'Sign in with Apple did not work'));
      debugPrint('apple sign-in failed: $e');
    } finally {
      _appleBusy = false;
    }
  }

  /// The page's 「绑定 Apple 登录」: native sheet, then the page binds the token with its own session.
  Future<void> _appleLink() async {
    if (_appleBusy) return;
    _appleBusy = true;
    AppleCredential? c;
    try {
      c = await appleCredential(name: false);
    } catch (e) {
      debugPrint('apple link failed: $e');
    } finally {
      _appleBusy = false;
    }
    await _web.runJavaScript(appleResultJs(c)).catchError((_) {});
  }

  void _snack(String text) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _onAccountDeleted() => signOutToOnboarding(revoke: false);

  Future<NavigationDecision> _onNavigation(NavigationRequest req) async {
    final uri = Uri.tryParse(req.url);
    if (uri == null) return NavigationDecision.prevent;
    if (uri.scheme == 'watch2whoop') {
      if (uri.host == 'sync' || uri.host == 'settings') _openSync();
      if (uri.host == 'sync-center') _openSync(center: true); // the Today page's one row for sync (resync.dart)
      if (uri.host == kAccountDeletedHost) _onAccountDeleted();
      // The page's sync buttons (sync_bridge.dart, docs/sync-onboarding-app-requirements.md): no extra dialog, the tap
      // on the page was the confirmation
      if (hasHealthSync && !_onShell) {
        final connect = parseSyncConnect(uri);
        if (connect != null) _syncBridge.enable(link: connect);
        if (uri.host == 'sync-now') _syncBridge.now();
        if (uri.host == 'app-settings') openAppSettings();
        if (uri.host == 'health-settings') _healthSettings();
      }
      // 「手机同步已断开 → 重新连接」 on the Today page (web/static/sync-repair.js): its sync part, asked once like a scanned link
      final setup = uri.host == 'setup' ? pageSetupLink(req.url) : null;
      if (setup != null) SetupLinks.instance.pending.value = setup;
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

  /// The page's 「去授权」: the Health sheet when iOS still has types to ask (core types, the app's own), otherwise this
  /// app's page in iOS Settings (Health is there on iOS 18+); the new state goes to the server either way.
  Future<void> _healthSettings() async {
    final unasked = await healthUnasked(kCoreHealthTypes);
    var raw = 'unknown';
    try {
      raw = await RawSync.instance.source.authStatus();
    } catch (_) {}
    if (unasked.isNotEmpty || raw == 'shouldRequest') {
      await _askHealthChecked();
    } else {
      await openAppSettings();
    }
    await _reportDevice();
  }

  void _openSync({bool center = false}) {
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => SyncPage(center: center)));
  }

  @override
  Widget build(BuildContext context) {
    final dark = MediaQuery.platformBrightnessOf(context) == Brightness.dark;
    final page = Scaffold(
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
    if (!isAndroid) return page;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _onBack();
      },
      child: page,
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
