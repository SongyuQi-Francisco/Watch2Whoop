// Native "phone sync" (手机同步) page: last sync time, per-type counts, failure retry, sync now, and server settings.
// Native sync status page (+ settings). Opened from the today page (Me → Phone sync) or the error screen.
// Locked builds (server_lock.dart): no server rows, no scan / paste of setup info, and the server is redacted from logs and errors.
import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../account_api.dart';
import '../account_flow.dart';
import '../app_state.dart';
import '../config.dart';
import '../debug_seed.dart';
import '../health_sync.dart';
import '../i18n.dart';
import '../setup_flow.dart';
import '../sync_stats.dart';
import '../ui.dart';
import 'scan_page.dart';

class SyncPage extends StatefulWidget {
  const SyncPage({super.key});

  @override
  State<SyncPage> createState() => _SyncPageState();
}

class _SyncPageState extends State<SyncPage> {
  final _sync = HealthSync.instance;
  SdkSyncStatus _status = const SdkSyncStatus();
  Timer? _poll;
  bool _busy = false;
  bool _wake = false;
  String? _version;
  String? _message;

  AppConfig get _config => AppState.instance.config;

  @override
  void initState() {
    super.initState();
    _refresh();
    _poll = Timer.periodic(const Duration(seconds: 2), (_) => _refresh());
    _sync.stats.addListener(_onStats);
    PackageInfo.fromPlatform().then((p) {
      if (mounted) setState(() => _version = '${p.version} (${p.buildNumber})');
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    _sync.stats.removeListener(_onStats);
    if (_wake) WakelockPlus.disable();
    super.dispose();
  }

  void _onStats() {
    if (mounted) setState(() {});
  }

  Future<void> _refresh() async {
    await _sync.drainNativeLog();
    final s = await _sync.status();
    if (!mounted) return;
    // First export unfinished and uploading: keep the screen on (iOS does not allow reading Health data while locked).
    final wake = _config.owConnected && _sync.isSyncActive && !s.initialExportDone;
    if (wake != _wake) {
      _wake = wake;
      wake ? WakelockPlus.enable() : WakelockPlus.disable();
    }
    setState(() => _status = s);
  }

  Future<void> _run(Future<void> Function() f, {String? done}) async {
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await f();
      if (done != null) setState(() => _message = done);
    } catch (e) {
      setState(() => _message = redactServer('$e'));
    } finally {
      if (mounted) setState(() => _busy = false);
      _refresh();
    }
  }

  Future<void> _syncNow() => _run(() async {
    if (!_config.healthRequested) {
      await _sync.requestAuthorization();
      await AppState.instance.update(_config.copyWith(healthRequested: true));
    }
    await _sync.startSync(_config.syncDaysBack);
  }, done: tr('已开始同步', 'Sync started'));

  Future<void> _retry() => _run(() => _sync.retry(_config.syncDaysBack), done: tr('已重试', 'Retrying'));

  // -------------------------------------------------------------------------- settings

  Future<void> _editUrl(UrlKind kind) async {
    final ctrl = TextEditingController(text: kind == UrlKind.today ? _config.todayUrl : _config.owHost);
    String? err;
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setD) => AlertDialog(
          title: Text(kind == UrlKind.today ? tr('今天页地址', 'Today page URL') : tr('同步服务器地址', 'Sync server URL')),
          content: TextField(
            controller: ctrl,
            autofocus: true,
            keyboardType: TextInputType.url,
            autocorrect: false,
            decoration: InputDecoration(errorText: err, errorMaxLines: 2),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: Text(tr('取消', 'Cancel'))),
            TextButton(
              onPressed: () {
                final c = checkServerUrl(ctrl.text, kind);
                if (c.isOk) {
                  Navigator.pop(ctx, c.url);
                } else {
                  setD(() => err = urlError(c.error));
                }
              },
              child: Text(tr('保存', 'Save')),
            ),
          ],
        ),
      ),
    );
    if (result == null) return;
    if (kind == UrlKind.today) {
      await AppState.instance.update(_config.copyWith(todayUrl: result));
    } else if (result != _config.owHost) {
      // Switched OW server: the old connection is void, reconnect with an invitation code from the new server.
      if (_config.owConnected) await _sync.disconnect();
      await AppState.instance.update(_config.copyWith(owHost: result, clearOwUserId: true));
      if (mounted) setState(() => _message = tr('地址已改，请用新的邀请码重新连接', 'Address changed — reconnect with a new code'));
    }
    setState(() {});
  }

  Future<void> _reconnect() async {
    final ctrl = TextEditingController();
    String? err;
    var busy = false;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setD) => AlertDialog(
          title: Text(tr('用邀请码连接', 'Connect with invitation code')),
          content: TextField(
            controller: ctrl,
            autofocus: true,
            autocorrect: false,
            textCapitalization: TextCapitalization.characters,
            // Alphanumeric only: use the ASCII keyboard to avoid the candidate bar of Chinese input methods
            keyboardType: TextInputType.visiblePassword,
            decoration: InputDecoration(hintText: 'ABCD2345', errorText: err, errorMaxLines: 3),
          ),
          actions: [
            TextButton(onPressed: busy ? null : () => Navigator.pop(ctx, false), child: Text(tr('取消', 'Cancel'))),
            TextButton(
              onPressed: busy
                  ? null
                  : () async {
                      final code = normalizeInvitationCode(ctrl.text);
                      if (code == null) {
                        setD(() => err = tr('邀请码是 8 位字母和数字', '8 letters/digits'));
                        return;
                      }
                      setD(() => busy = true);
                      try {
                        final userId = await _sync.connect(owHost: _config.owHost, code: code);
                        await AppState.instance.update(_config.copyWith(owUserId: userId));
                        await _sync.requestAuthorization();
                        await AppState.instance.update(_config.copyWith(healthRequested: true));
                        await _sync.startSync(_config.syncDaysBack);
                        if (ctx.mounted) Navigator.pop(ctx, true);
                      } catch (e) {
                        setD(() {
                          busy = false;
                          err = connectError(e);
                        });
                      }
                    },
              child: Text(tr('连接', 'Connect')),
            ),
          ],
        ),
      ),
    );
    if (ok == true) setState(() => _message = tr('已连接，开始同步', 'Connected — syncing'));
    _refresh();
  }

  bool get _hasAccount => AppState.instance.accountToken != null;

  /// Self-service accounts: connect sync without any code (server-minted OW invitation); fall back to the manual code dialog.
  Future<void> _connectViaAccount() async {
    var manual = false;
    await _run(() async {
      final r = await connectSyncViaAccount();
      if (r.connected) {
        await _sync.requestAuthorization();
        await AppState.instance.update(_config.copyWith(healthRequested: true));
        await _sync.startSync(_config.syncDaysBack);
        setState(() => _message = tr('已连接，开始同步', 'Connected — syncing'));
      } else if (r.manualNeeded) {
        manual = true;
      } else {
        final e = r.error;
        setState(() => _message = e is AccountApiException ? accountError(e) : connectError(e ?? 'server'));
      }
    });
    if (manual && mounted && _config.owHost.isNotEmpty) await _reconnect();
  }

  Future<void> _signOut() async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(tr('退出登录？', 'Sign out?')),
        content: Text(tr('这台 iPhone 停止同步并退出账号；服务器上的数据不受影响，之后用同一个 Apple ID / 邮箱登录即可。',
            'This iPhone stops syncing and signs out. Your data on the server is kept; sign in again with the same Apple ID / e-mail.')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(tr('取消', 'Cancel'))),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(tr('退出', 'Sign out'))),
        ],
      ),
    );
    if (yes != true || !mounted) return;
    final nav = Navigator.of(context);
    await _run(() => signOutToOnboarding());
    if (nav.mounted) nav.popUntil((r) => r.isFirst);
  }

  Future<void> _disconnect() async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(tr('断开同步？', 'Disconnect sync?')),
        content: Text(tr('这台 iPhone 不再上传健康数据；服务器上已有的数据不受影响。之后要用新的邀请码重新连接。',
            'This iPhone stops uploading. Data already on the server is kept. Reconnecting needs a new code.')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(tr('取消', 'Cancel'))),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(tr('断开', 'Disconnect'))),
        ],
      ),
    );
    if (yes != true) return;
    await _run(() async {
      await _sync.disconnect();
      await AppState.instance.update(_config.copyWith(clearOwUserId: true));
    }, done: tr('已断开', 'Disconnected'));
  }

  /// Applies new setup info (scan / paste) in one go: update the server, reconnect sync, log back in to the Today page.
  Future<void> _setupFrom(Future<String?> Function() source) async {
    final text = await source();
    if (text == null) return;
    final info = parseSetupText(text);
    if (info.isEmpty) {
      setState(() => _message = tr('没有读到设置信息', 'No setup info found'));
      return;
    }
    await _run(() async {
      try {
        final r = await applySetup(info);
        if (r.owConnected) {
          await _sync.requestAuthorization();
          await AppState.instance.update(_config.copyWith(healthRequested: true));
          await _sync.startSync(_config.syncDaysBack);
        }
        setState(() => _message = r.owError != null
            ? connectError(r.owError!)
            : tr('已更新${r.owConnected ? '，同步已重新连接' : ''}${r.loginCodeSaved ? '，回到今天页会自动登录' : ''}',
                'Updated${r.owConnected ? ', sync reconnected' : ''}${r.loginCodeSaved ? ', the today page will sign in' : ''}'));
      } on FormatException catch (e) {
        setState(() => _message = urlError(e.message));
      }
    });
  }

  Future<String?> _scanText() => Navigator.of(context).push<String>(MaterialPageRoute(builder: (_) => const ScanPage()));
  Future<String?> _clipboardText() async => (await Clipboard.getData(Clipboard.kTextPlain))?.text;

  Future<void> _seed() => _run(() async {
    final r = await seedHealthTestData();
    setState(() => _message = r);
  });

  // -------------------------------------------------------------------------- UI

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final stats = _sync.stats.value;
    final connected = _config.owConnected && _sync.isSignedIn;
    final active = connected && _sync.isSyncActive;
    final failedAfterSuccess = stats.lastErrorAt != null &&
        (stats.lastUploadAt == null || stats.lastErrorAt!.isAfter(stats.lastUploadAt!));

    final (String headline, Color dot) = !connected
        ? (tr('未连接', 'Not connected'), cs.onSurfaceVariant)
        : stats.authProblem
        ? (tr('连接已失效', 'Connection expired'), cs.error)
        : !active
        ? (tr('后台同步未开启', 'Background sync off'), Colors.orange)
        : _status.isSyncing
        ? (tr('正在同步', 'Syncing'), const Color(0xFF34C759))
        : (tr('后台同步已开启', 'Background sync on'), const Color(0xFF34C759));

    return Scaffold(
      appBar: AppBar(title: Text(tr('手机同步', 'Phone sync'))),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 40),
          children: [
            // ---- status
            Card(
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Container(width: 10, height: 10, decoration: BoxDecoration(color: dot, shape: BoxShape.circle)),
                        const SizedBox(width: 8),
                        Text(headline, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
                      ],
                    ),
                    const SizedBox(height: 14),
                    _kv(tr('上次上传', 'Last upload'), ago(stats.lastUploadAt)),
                    _kv(tr('上次完整同步', 'Last full pass'), ago(stats.lastCompleteAt)),
                    if (connected && !_status.initialExportDone)
                      _kv(tr('首次同步', 'First sync'), tr('进行中 · 已发 ${_status.sentCount} 条', 'in progress · ${_status.sentCount} sent'))
                    else if (_status.isSyncing)
                      _kv(tr('本轮', 'This pass'), tr('已发 ${_status.sentCount} 条', '${_status.sentCount} sent')),
                    if (failedAfterSuccess) ...[
                      const SizedBox(height: 8),
                      Hint(
                        redactServer(tr('上次失败（${ago(stats.lastErrorAt)}）：${stats.lastError}', 'Last failure (${ago(stats.lastErrorAt)}): ${stats.lastError}')),
                        icon: Icons.error_outline,
                        color: cs.error,
                      ),
                    ],
                    if (stats.lastNotice != null && stats.lastNoticeAt != null && DateTime.now().difference(stats.lastNoticeAt!).inHours < 24) ...[
                      const SizedBox(height: 8),
                      Hint(_noticeText(stats.lastNotice!, stats.lastNoticeAt!), icon: Icons.lock_clock_outlined),
                    ],
                    const SizedBox(height: 12),
                    Hint(limitsHint),
                    if (_wake) ...[
                      const SizedBox(height: 6),
                      Hint(tr('首次同步期间这页会保持屏幕常亮，传完再离开更快。', 'The screen stays on during the first sync.'),
                          icon: Icons.light_mode_outlined),
                    ],
                  ],
                ),
              ),
            ),
            const SizedBox(height: 14),
            if (connected && !stats.authProblem) ...[
              BigButton(tr('立即同步', 'Sync now'), onPressed: _syncNow, busy: _busy),
              if (failedAfterSuccess || _status.hasResumableSession) ...[
                const SizedBox(height: 6),
                BigButton(tr('失败重试', 'Retry'), secondary: true, onPressed: _busy ? null : _retry),
              ],
            ] else
              _hasAccount
                  ? BigButton(tr('连接同步', 'Connect sync'), onPressed: _connectViaAccount, busy: _busy)
                  : BigButton(tr('用邀请码连接', 'Connect with invitation code'), onPressed: _config.owHost.isEmpty ? null : _reconnect),
            if (_message != null) ...[
              const SizedBox(height: 10),
              Hint(_message!, icon: Icons.check_circle_outline),
            ],
            const SizedBox(height: 24),

            // ---- per-type counts
            Group(
              title: tr('已上传（按类型）', 'Uploaded by type'),
              footer: stats.perType.isEmpty
                  ? null
                  : tr('从这次连接起累计 ${stats.total} 条。手腕温度、HRV RMSSD 暂时不在这里（SDK 还不支持），由快捷指令补。',
                      '${stats.total} records since connecting. Wrist temperature and HRV RMSSD are not supported by the SDK yet.'),
              rows: stats.perType.isEmpty
                  ? [Row2(tr('还没有上传记录', 'Nothing uploaded yet'))]
                  : [for (final e in stats.sortedTypes) Row2(typeLabel(e.key), value: '${e.value}')],
            ),

            // ---- settings
            Group(
              title: tr('设置', 'Settings'),
              rows: [
                if (!serverLocked) ...[
                  Row2(tr('扫描设置二维码', 'Scan setup code'), onTap: _busy ? null : () => _setupFrom(_scanText)),
                  Row2(tr('粘贴设置信息', 'Paste setup text'), onTap: _busy ? null : () => _setupFrom(_clipboardText)),
                  Row2(tr('今天页', 'Today page'), value: shortUrl(_config.todayUrl), onTap: () => _editUrl(UrlKind.today)),
                  Row2(tr('同步服务器', 'Sync server'), value: shortUrl(_config.owHost), onTap: () => _editUrl(UrlKind.ow)),
                ],
                Row2(tr('首次同步范围', 'First sync range'), value: tr('${_config.syncDaysBack} 天', '${_config.syncDaysBack} days')),
                if (connected) Row2(tr('同步账号', 'Sync account'), value: '${_config.owUserId!.substring(0, 8)}…'),
                if (connected) Row2(tr('重新连接', 'Reconnect'), onTap: _reconnect),
                if (connected) Row2(tr('断开同步', 'Disconnect sync'), danger: true, onTap: _disconnect),
                if (_hasAccount) Row2(tr('退出登录', 'Sign out'), danger: true, onTap: _busy ? null : _signOut),
              ],
              footer: tr('健康权限在「健康」app → 右上角头像 → App → Watch2Whoop 里改。',
                  'Change Health permissions in Health → profile → Apps → Watch2Whoop.'),
            ),

            if (kDebugMode)
              Group(
                title: tr('开发（只在调试版出现）', 'Development (debug builds only)'),
                rows: [Row2(tr('往模拟器的健康 app 写测试数据', 'Write test data to simulator Health'), onTap: _busy ? null : _seed)],
              ),

            // ---- logs
            Card(
              clipBehavior: Clip.antiAlias,
              child: ExpansionTile(
                title: Text(tr('同步日志', 'Sync log'), style: const TextStyle(fontSize: 16)),
                shape: const Border(),
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                    child: SelectableText(
                      redactServer(_sync.recentLog.reversed.take(80).join('\n')),
                      style: TextStyle(fontSize: 11, fontFamily: 'Menlo', color: cs.onSurfaceVariant),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            Center(
              child: Text('Watch2Whoop ${_version ?? ''}', style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
            ),
          ],
        ),
      ),
    );
  }

  String _noticeText(SyncNotice n, DateTime at) => switch (n) {
    SyncNotice.locked => tr('${ago(at)} 因为锁屏暂停，解锁后自动继续。', 'Paused ${ago(at)} because the phone was locked.'),
    SyncNotice.offline => tr('${ago(at)} 断网，联网后自动继续。', 'Offline ${ago(at)}; resumes when online.'),
    SyncNotice.backgroundTimeout => tr('${ago(at)} 后台时间用完，下次唤醒继续。', 'Background time ran out ${ago(at)}; continues next time.'),
  };

  Widget _kv(String k, String v) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 3),
    child: Row(
      children: [
        Expanded(child: Text(k, style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant))),
        Text(v, style: const TextStyle(fontFeatures: [FontFeature.tabularFigures()])),
      ],
    ),
  );
}
