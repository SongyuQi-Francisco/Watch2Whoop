// First-run onboarding (self-service): server address → Sign in with Apple / e-mail code → invite code (pending accounts only)
// → permissions step (permissions_page.dart: Health; sync connected silently with a server-minted OW invitation code)
// → Today page logged in with a fresh one-time code (the Today page then shows its intro once, intro.js).
// "I have a setup link" keeps the admin-issued setup link / QR / paste path (setup_link_page.dart) for existing users.
// Locked builds (server_lock.dart) show no server and no "I have a setup link": only a camera-opened link for this server still works.
// A 中文 / English switch sits top right on every step (ui.dart LangSwitch); a change redraws the page in place, inputs kept.
import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:sign_in_with_apple/sign_in_with_apple.dart';

import '../account_api.dart';
import '../account_flow.dart';
import '../app_state.dart';
import '../backfill.dart';
import '../config.dart';
import '../health_sync.dart';
import '../i18n.dart';
import '../platform_features.dart';
import '../setup_flow.dart';
import '../ui.dart';
import 'permissions_page.dart';
import 'range_picker.dart';
import 'setup_link_page.dart';

enum _Step { welcome, email, code, invite, health, done }

class OnboardingPage extends StatefulWidget {
  const OnboardingPage({super.key});

  @override
  State<OnboardingPage> createState() => _OnboardingPageState();
}

class _OnboardingPageState extends State<OnboardingPage> {
  _Step _step = _Step.welcome;
  bool _legacy = false;
  bool _busy = false;
  bool _editServer = false;
  bool _apple = false;
  String? _error;
  String? _info;
  String? _serverError;
  String? _progress;
  int _cooldown = 0;
  Timer? _timer;
  late final TextEditingController _server, _email, _code, _invite;

  AppState get _state => AppState.instance;
  AppConfig get _config => _state.config;

  @override
  void initState() {
    super.initState();
    _server = TextEditingController(text: _config.todayUrl);
    _editServer = !serverLocked && !checkServerUrl(_config.todayUrl, UrlKind.today).isOk;
    _email = TextEditingController();
    _code = TextEditingController();
    _invite = TextEditingController();
    SetupLinks.instance.pending.addListener(_onLink);
    langOverride.addListener(_onLang);
    _checkApple();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _onLink();
      if (_state.accountToken != null) _resume();
    });
  }

  @override
  void dispose() {
    SetupLinks.instance.pending.removeListener(_onLink);
    langOverride.removeListener(_onLang);
    _timer?.cancel();
    _server.dispose();
    _email.dispose();
    _code.dispose();
    _invite.dispose();
    super.dispose();
  }

  void _onLang() {
    if (mounted) setState(() {});
  }

  /// A setup link opened the app (camera QR scan): hand over to the setup-link flow, which consumes it.
  void _onLink() {
    if (SetupLinks.instance.pending.value != null && !_legacy && mounted) setState(() => _legacy = true);
  }

  Future<void> _checkApple() async {
    if (kIsWeb || !Platform.isIOS) return;
    try {
      final ok = await SignInWithApple.isAvailable();
      if (mounted) setState(() => _apple = ok);
    } catch (_) {}
  }

  /// Signed in earlier but onboarding was interrupted (app closed): continue where it stopped.
  Future<void> _resume() async {
    await _run(() async {
      final info = await accountApiFor(_config.todayUrl).account(_state.accountToken!);
      _afterStatus(info.status);
    }, silentInvalid: true);
  }

  // -------------------------------------------------------------------------- helpers

  Future<void> _run(Future<void> Function() f, {String Function(AccountApiException)? onError, bool silentInvalid = false}) async {
    setState(() {
      _busy = true;
      _error = null;
      _info = null;
    });
    try {
      await f();
    } on AccountApiException catch (e) {
      if (e.code == 'invalid' && silentInvalid) {
        await _state.setAccountToken(null); // stored token no longer valid: sign in again
      } else if (mounted) {
        setState(() => _error = onError?.call(e) ?? accountError(e));
      }
    } on SignInWithAppleAuthorizationException catch (e) {
      if (e.code != AuthorizationErrorCode.canceled && mounted) {
        setState(() => _error = tr('通过 Apple 登录没有成功：${e.message}', 'Sign in with Apple failed: ${e.message}'));
      }
    } catch (e) {
      if (mounted) setState(() => _error = redactServer('$e'));
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _progress = null;
        });
      }
    }
  }

  /// Validates the server field; null (with the error shown) if it is wrong.
  String? _serverUrl() {
    if (serverLocked) return _config.todayUrl;   // the built-in server (AppState enforces it)
    final c = checkServerUrl(_server.text, UrlKind.today);
    setState(() {
      _serverError = c.isOk ? null : urlError(c.error);
      if (!c.isOk) _editServer = true;
    });
    if (c.isOk) _server.text = c.url!;
    return c.url;
  }

  void _afterStatus(AccountStatus s) {
    if (!mounted) return;
    setState(() {
      switch (s) {
        case AccountStatus.pending:
          _step = _Step.invite;
        case AccountStatus.disabled:
          _step = _Step.welcome;
          _error = tr('这个账号已停用，请联系管理员。', 'This account is disabled. Contact the administrator.');
        case AccountStatus.active:
        case AccountStatus.unknown:
          _step = hasHealthSync ? _Step.health : _Step.done; // Android (#253): no Health step yet
      }
    });
  }

  Future<void> _signedIn(AccountSession s, String server) async {
    await saveSession(s, server);
    var status = s.status;
    if (status == AccountStatus.unknown) {
      status = (await accountApiFor(server).account(s.token)).status;
    }
    if (status == AccountStatus.disabled) await _state.setAccountToken(null);
    _afterStatus(status);
  }

  void _startCooldown([Duration? d]) {
    _timer?.cancel();
    setState(() => _cooldown = (d ?? const Duration(seconds: 60)).inSeconds.clamp(1, 3600));
    _timer = Timer.periodic(const Duration(seconds: 1), (t) {
      if (!mounted) return t.cancel();
      setState(() => _cooldown--);
      if (_cooldown <= 0) t.cancel();
    });
  }

  // -------------------------------------------------------------------------- actions

  Future<void> _signInWithApple() async {
    final server = _serverUrl();
    if (server == null) return;
    await _run(() async {
      final raw = generateRawNonce();
      final cred = await SignInWithApple.getAppleIDCredential(
        scopes: const [AppleIDAuthorizationScopes.email, AppleIDAuthorizationScopes.fullName],
        nonce: sha256Hex(raw),
      );
      final idToken = cred.identityToken;
      if (idToken == null || idToken.isEmpty) throw AccountApiException('bad_response');
      final s = await accountApiFor(server).signInWithApple(
        identityToken: idToken,
        rawNonce: raw,
        device: deviceLabel,
        givenName: cred.givenName,
        familyName: cred.familyName,
      );
      await _signedIn(s, server);
    }, onError: (e) => switch (e.code) {
      'invalid' => tr('Apple 登录验证没通过，请再试一次', 'The Apple sign-in could not be verified — try again'),
      'unavailable' => tr('这个服务器没有开通「通过 Apple 登录」，请用邮箱登录', 'This server has no Sign in with Apple — use e-mail'),
      _ => accountError(e),
    });
  }

  void _toEmail() {
    if (_serverUrl() == null) return;
    setState(() {
      _error = null;
      _step = _Step.email;
    });
  }

  Future<void> _sendCode() async {
    final email = _email.text.trim();
    if (!looksLikeEmail(email)) {
      setState(() => _error = tr('邮箱格式不对', 'Invalid e-mail address'));
      return;
    }
    await _run(() async {
      await accountApiFor(_server.text).startEmail(email, lang: isZh ? 'zh' : 'en');
      _code.clear();
      if (mounted) {
        setState(() {
          _step = _Step.code;
          _info = tr('验证码已发到 $email，10 分钟内有效', 'Code sent to $email (valid for 10 minutes)');
        });
      }
      _startCooldown();
    }, onError: (e) {
      if (e.code == 'rate_limited') _startCooldown(e.retryAfter);
      return switch (e.code) {
        'bad_request' => tr('邮箱格式不对', 'Invalid e-mail address'),
        'unavailable' => tr('这个服务器还不能发邮件，请用其他方式登录', 'This server cannot send e-mail yet — use another way'),
        _ => accountError(e),
      };
    });
  }

  Future<void> _verifyCode() async {
    final code = normalizeEmailCode(_code.text);
    if (code == null) {
      setState(() => _error = tr('验证码是 6 位数字', 'The code has 6 digits'));
      return;
    }
    final server = _server.text;
    await _run(() async {
      final s = await accountApiFor(server).verifyEmail(_email.text.trim(), code, device: deviceLabel);
      await _signedIn(s, server);
    }, onError: (e) => e.code == 'invalid'
        ? tr('验证码不对或已过期', 'Wrong or expired code')
        : accountError(e));
  }

  Future<void> _redeem() async {
    final code = inviteStepCode(_invite.text);
    if (code == null) {
      setState(() => _error = tr('激活码是 10 位字母和数字，或团体给的圈子激活码', 'Enter the 10-character invite code, or the circle code your group gave you'));
      return;
    }
    await _run(() async {
      final status = await accountApiFor(_config.todayUrl).redeemInvite(_state.accountToken!, code);
      _invite.clear();
      _afterStatus(status == AccountStatus.unknown ? AccountStatus.active : status);
    }, onError: (e) => switch (e.code) {
          'invalid' => tr('激活码无效、已用完或已过期', 'Invite code is invalid, used up or expired'),
          // a circle activation code (server friends/codes.py): 410 used up / disabled / expired, 409 circle full
          'gone' => tr('这个圈子激活码已用完、已停用或已过期', 'This circle code is used up, disabled or expired'),
          'conflict' => tr('这个圈子已满', 'This circle is full'),
          _ => accountError(e),
        });
  }

  /// Sync connected (server-minted invitation code); a note for the user when it could not be.
  Future<bool> _ensureSync(void Function(String) onNote) async {
    if (_config.owConnected) return true;
    if (mounted) setState(() => _progress = tr('正在准备同步账号…', 'Preparing your sync account…'));
    final r = await connectSyncViaAccount();
    if (!r.connected) {
      onNote(r.manualNeeded || r.error == null
          ? tr('同步还没连上：之后在「我」→「手机同步」里连接。', 'Sync is not connected yet: connect it later under Me → Phone sync.')
          : (r.error is AccountApiException ? accountError(r.error as AccountApiException) : connectError(r.error!)));
    }
    return r.connected;
  }

  /// Health card button: connect sync first (that configures the SDK), then the iOS prompt, then start syncing.
  Future<void> _allowHealth() async {
    String? note;
    await _run(() async {
      final connected = await _ensureSync((n) => note = n);
      if (mounted) setState(() => _progress = tr('等待健康权限…', 'Waiting for Health access…'));
      final ok = await HealthSync.instance.requestAuthorization();
      await _state.update(_config.copyWith(healthRequested: true));
      if (!ok) {
        note = tr('没有允许任何类型。可以在「健康」app → 右上角头像 → App → Watch2Whoop 里打开。',
            'Nothing was allowed. Change it in Health → profile → Apps → Watch2Whoop.');
      }
      if (connected) await HealthSync.instance.startSync(_config.syncDaysBack);
    });
    if (mounted) setState(() => _info = note);
  }

  /// Continue / skip the permissions step: sync is connected even before Health is allowed; the step counts as seen.
  Future<void> _afterPermissions() async {
    String? note;
    await _run(() async {
      await _ensureSync((n) => note = n);
      await _state.update(_config.copyWith(permissionsSeen: true));
      if (mounted) setState(() => _step = _Step.done);
    });
    if (note != null && mounted) setState(() => _info = note);
  }

  Future<void> _switchAccount() => _run(() async {
    await signOutToOnboarding();
    if (mounted) setState(() => _step = _Step.welcome);
  });

  Future<void> _finish() => _state.update(_config.copyWith(onboardingDone: true));

  // -------------------------------------------------------------------------- UI

  @override
  Widget build(BuildContext context) {
    if (_legacy) {
      return SetupLinkOnboarding(onBack: () => setState(() => _legacy = false));
    }
    final cs = Theme.of(context).colorScheme;
    final body = switch (_step) {
      _Step.welcome => _welcome(),
      _Step.email => _emailStep(),
      _Step.code => _codeStep(),
      _Step.invite => _inviteStep(),
      _Step.health => _healthStep(),
      _Step.done => _doneStep(),
    };
    return Scaffold(
      body: SafeArea(
        child: GestureDetector(
          onTap: () => FocusScope.of(context).unfocus(),
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 28),
            children: [
              const Align(alignment: Alignment.centerRight, child: LangSwitch()),
              const SizedBox(height: 4),
              ...body,
              if (_progress != null) ...[
                const SizedBox(height: 14),
                Hint(_progress!, icon: Icons.hourglass_top),
              ],
              if (_info != null) ...[
                const SizedBox(height: 14),
                Hint(_info!, icon: Icons.check_circle_outline),
              ],
              if (_error != null) ...[
                const SizedBox(height: 14),
                Hint(_error!, icon: Icons.error_outline, color: cs.error),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _title(String t, [String? sub]) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(t, style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w700, letterSpacing: -0.5)),
      if (sub != null) ...[
        const SizedBox(height: 8),
        Text(sub, style: TextStyle(fontSize: 15, color: Theme.of(context).colorScheme.onSurfaceVariant, height: 1.4)),
      ],
      const SizedBox(height: 24),
    ],
  );

  Widget _outlined(String label, IconData icon, VoidCallback? onPressed) => OutlinedButton.icon(
    onPressed: onPressed,
    icon: Icon(icon, size: 18),
    label: Text(label, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
    style: OutlinedButton.styleFrom(
      minimumSize: const Size.fromHeight(50),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
    ),
  );

  List<Widget> _welcome() {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return [
      _title('Watch2Whoop', hasHealthSync
          ? tr('登录后开始同步 Apple 健康数据。', 'Sign in to start syncing your Apple Health data.')
          : tr('登录后打开你的今天页。', 'Sign in to open your Today page.')),
      if (serverLocked)
        const SizedBox.shrink()
      else if (_editServer)
        UrlField(
          controller: _server,
          label: tr('服务器地址', 'Server address'),
          hint: 'https://body.example.com',
          kind: UrlKind.today,
          error: _serverError,
        )
      else
        Row2(
          tr('服务器', 'Server'),
          value: shortUrl(_server.text),
          trailing: TextButton(onPressed: _busy ? null : () => setState(() => _editServer = true), child: Text(tr('更改', 'Change'))),
        ),
      const SizedBox(height: 20),
      if (_apple) ...[
        SizedBox(
          height: 50,
          child: SignInWithAppleButton(
            text: tr('通过 Apple 登录', 'Sign in with Apple'),
            style: dark ? SignInWithAppleButtonStyle.white : SignInWithAppleButtonStyle.black,
            borderRadius: const BorderRadius.all(Radius.circular(14)),
            onPressed: _busy ? () {} : _signInWithApple,
          ),
        ),
        const SizedBox(height: 10),
      ],
      _outlined(tr('用邮箱登录', 'Sign in with e-mail'), Icons.mail_outline, _busy ? null : _toEmail),
      if (_busy) ...[
        const SizedBox(height: 14),
        const Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.2))),
      ],
      if (!serverLocked) ...[
        const SizedBox(height: 22),
        BigButton(tr('我有设置链接', 'I have a setup link'), secondary: true, onPressed: _busy ? null : () => setState(() => _legacy = true)),
      ],
    ];
  }

  List<Widget> _emailStep() => [
    _title(tr('用邮箱登录', 'Sign in with e-mail'), tr('我们会发一个 6 位验证码到你的邮箱，不用设密码。', 'We e-mail you a 6-digit code — no password.')),
    TextField(
      controller: _email,
      autofocus: true,
      keyboardType: TextInputType.emailAddress,
      autocorrect: false,
      enableSuggestions: false,
      autofillHints: const [AutofillHints.email],
      textInputAction: TextInputAction.send,
      decoration: InputDecoration(
        labelText: tr('邮箱', 'E-mail'),
        hintText: 'name@example.com',
        border: const OutlineInputBorder(borderRadius: BorderRadius.all(Radius.circular(12))),
      ),
      onSubmitted: (_) => _sendCode(),
    ),
    const SizedBox(height: 24),
    BigButton(
      _cooldown > 0 ? tr('发送验证码（${_cooldown}s）', 'Send code (${_cooldown}s)') : tr('发送验证码', 'Send code'),
      onPressed: _cooldown > 0 ? null : _sendCode,
      busy: _busy,
    ),
    BigButton(tr('上一步', 'Back'), secondary: true, onPressed: _busy ? null : () => setState(() {
      _error = null;
      _step = _Step.welcome;
    })),
  ];

  List<Widget> _codeStep() => [
    _title(tr('输入验证码', 'Enter the code'), _email.text.trim()),
    TextField(
      controller: _code,
      autofocus: true,
      keyboardType: TextInputType.number,
      autofillHints: const [AutofillHints.oneTimeCode],
      inputFormatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(6)],
      style: const TextStyle(fontSize: 26, letterSpacing: 8, fontWeight: FontWeight.w600),
      decoration: InputDecoration(
        labelText: tr('6 位验证码', '6-digit code'),
        border: const OutlineInputBorder(borderRadius: BorderRadius.all(Radius.circular(12))),
      ),
      onChanged: (v) {
        if (v.length == 6 && !_busy) _verifyCode();
      },
      onSubmitted: (_) => _verifyCode(),
    ),
    const SizedBox(height: 24),
    BigButton(tr('登录', 'Sign in'), onPressed: _verifyCode, busy: _busy),
    BigButton(
      _cooldown > 0 ? tr('重新发送（${_cooldown}s）', 'Resend (${_cooldown}s)') : tr('重新发送', 'Resend'),
      secondary: true,
      onPressed: _busy || _cooldown > 0 ? null : _sendCode,
    ),
    BigButton(tr('换个邮箱', 'Use another e-mail'), secondary: true, onPressed: _busy ? null : () => setState(() {
      _error = null;
      _info = null;
      _step = _Step.email;
    })),
  ];

  List<Widget> _inviteStep() => [
    _title(tr('输入激活码', 'Enter invite code'), tr('内测期间需要激活码，向邀请你的人要；团体给的圈子激活码也可以。', 'During the beta you need an invite code from whoever invited you, or the circle code your group gave you.')),
    TextField(
      controller: _invite,
      autofocus: true,
      autocorrect: false,
      enableSuggestions: false,
      textCapitalization: TextCapitalization.characters,
      // Alphanumeric only: the ASCII keyboard avoids the candidate bar of Chinese input methods
      keyboardType: TextInputType.visiblePassword,
      style: const TextStyle(fontSize: 22, letterSpacing: 3, fontWeight: FontWeight.w600),
      decoration: InputDecoration(
        labelText: tr('激活码', 'Invite code'),
        hintText: 'ABCDE-23456',
        border: const OutlineInputBorder(borderRadius: BorderRadius.all(Radius.circular(12))),
      ),
      onSubmitted: (_) => _redeem(),
    ),
    const SizedBox(height: 24),
    BigButton(tr('激活', 'Activate'), onPressed: _redeem, busy: _busy),
    BigButton(tr('换个账号登录', 'Use another account'), secondary: true, onPressed: _busy ? null : _switchAccount),
  ];

  /// Read range (health_range.dart): stored with the config; if syncing already started, the SDK follows at once.
  Future<void> _pickRange(int days) async {
    // Open Wearables, RawSync and the app's own engine follow (backfill.dart); before connecting it is just stored
    if (_config.owConnected && _config.healthRequested) {
      await BackfillService.instance.setRange(days);
    } else {
      await AppState.instance.update(_config.copyWith(syncDaysBack: days));
    }
    if (mounted) setState(() {});
  }

  /// The permissions step (pages/permissions_page.dart), the same cards as Phone sync → Permissions.
  List<Widget> _healthStep() => [
    _title(
      tr('开权限', 'Permissions'),
      tr('同步只需要读取健康数据。先选读多少，再允许读取；开关由你在 iOS 里点。',
          'Syncing only needs to read your Health data. Pick how much to read, then allow it; you make the choice in iOS.'),
    ),
    Text(tr('读取范围', 'Read range'), style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
    RangePicker(syncDaysBack: _config.syncDaysBack, onChanged: _pickRange, enabled: !_busy),
    const SizedBox(height: 12),
    PermissionsPanel(onRequestHealth: _allowHealth),
    const SizedBox(height: 12),
    Hint(limitsHint),
    const SizedBox(height: 24),
    BigButton(_config.healthRequested ? tr('继续', 'Continue') : tr('跳过，以后再说', 'Skip for now'),
        onPressed: _afterPermissions, busy: _busy),
  ];

  List<Widget> _doneStep() => [
    _title(
      tr('设置好了', 'All set'),
      hasHealthSync ? tr('以后同步状态在「我」→「手机同步」里看。', 'Sync status lives under Me → Phone sync.') : watchDataPendingHint,
    ),
    if (_config.owConnected) Hint(tr('首次同步可能要几分钟，保持 app 打开会更快。', 'The first sync can take a few minutes; keep the app open.')),
    const SizedBox(height: 24),
    BigButton(tr('打开今天页', 'Open today page'), onPressed: _finish),
  ];
}
