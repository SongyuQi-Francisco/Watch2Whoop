// Secondary onboarding path ("I have a setup link"; the default first screen is the account sign-in in onboarding_page.dart):
// for accounts opened by an admin. Scan the "setup QR code" / paste setup info → fills in the server, connects sync, keeps the login code
// → allow reading health data → open the Today page (auto login if there is a login code). Manual entry is tucked under "Manual entry" (手动填写).
// Onboarding: scan / paste the setup info (servers + invitation code + one-time login code) → Health → today page.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app_state.dart';
import '../config.dart';
import '../health_sync.dart';
import '../i18n.dart';
import '../setup_flow.dart';
import '../ui.dart';
import 'scan_page.dart';

class SetupLinkOnboarding extends StatefulWidget {
  const SetupLinkOnboarding({super.key, this.onBack});

  /// Back to the account sign-in screen (the default first screen).
  final VoidCallback? onBack;

  @override
  State<SetupLinkOnboarding> createState() => _SetupLinkOnboardingState();
}

class _SetupLinkOnboardingState extends State<SetupLinkOnboarding> {
  int _step = 0;
  bool _busy = false;
  bool _manual = false;
  String? _error;
  String? _todayError, _owError;
  bool _insecure = false;
  late final TextEditingController _today, _ow, _code;

  AppConfig get _config => AppState.instance.config;

  @override
  void initState() {
    super.initState();
    _today = TextEditingController(text: _config.todayUrl);
    _ow = TextEditingController(text: _config.owHost);
    _code = TextEditingController();
    SetupLinks.instance.pending.addListener(_onLink);
    WidgetsBinding.instance.addPostFrameCallback((_) => _onLink());
  }

  @override
  void dispose() {
    SetupLinks.instance.pending.removeListener(_onLink);
    _today.dispose();
    _ow.dispose();
    _code.dispose();
    super.dispose();
  }

  /// Setup link brought in by opening the app via camera QR scan.
  void _onLink() {
    final info = SetupLinks.instance.pending.value;
    if (info == null || _busy) return;
    SetupLinks.instance.pending.value = null;
    _apply(info);
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final info = parseSetupText(data?.text ?? '');
    if (info.isEmpty) {
      setState(() => _error = tr('剪贴板里没有设置信息', 'No setup info on the clipboard'));
      return;
    }
    await _apply(info);
  }

  Future<void> _scan() async {
    final text = await Navigator.of(context).push<String>(MaterialPageRoute(builder: (_) => const ScanPage()));
    if (text == null) return;
    await _apply(parseSetupText(text));
  }

  Future<void> _apply(SetupInfo info) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final r = await applySetup(info);
      _today.text = _config.todayUrl;
      _ow.text = _config.owHost;
      if (_config.todayUrl.isEmpty || _config.owHost.isEmpty) {
        setState(() {
          _manual = true;
          _error = tr('设置信息里缺服务器地址，请手动填写', 'The setup info has no server addresses — fill them in');
        });
        return;
      }
      if (r.owConnected) {
        setState(() => _step = 2);
      } else {
        setState(() {
          _step = 1;
          if (r.owError != null) _error = connectError(r.owError!);
        });
      }
    } on FormatException catch (e) {
      setState(() {
        _manual = true;
        _error = urlError(e.message);
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _saveServers() async {
    final t = checkServerUrl(_today.text, UrlKind.today);
    final o = checkServerUrl(_ow.text, UrlKind.ow);
    setState(() {
      _todayError = t.isOk ? null : urlError(t.error);
      _owError = o.isOk ? null : urlError(o.error);
      _insecure = t.insecureLocal || o.insecureLocal;
    });
    if (!t.isOk || !o.isOk) return;
    _today.text = t.url!;
    _ow.text = o.url!;
    await AppState.instance.update(_config.copyWith(todayUrl: t.url, owHost: o.url));
    setState(() {
      _error = null;
      _step = 1;
    });
  }

  Future<void> _connect() async {
    final code = normalizeInvitationCode(_code.text);
    if (code == null) {
      setState(() => _error = tr('邀请码是 8 位字母和数字', 'The invitation code has 8 letters/digits'));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final userId = await HealthSync.instance.connect(owHost: _config.owHost, code: code);
      await AppState.instance.update(_config.copyWith(owUserId: userId));
      _code.clear();
      setState(() => _step = 2);
    } catch (e) {
      setState(() => _error = connectError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _requestHealth() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final ok = await HealthSync.instance.requestAuthorization();
      await AppState.instance.update(_config.copyWith(healthRequested: true));
      if (!ok) {
        setState(() => _error = tr(
          '没有允许任何类型。可以在「健康」app → 右上角头像 → App → Watch2Whoop 里打开。',
          'Nothing was allowed. Change it in Health → profile → Apps → Watch2Whoop.',
        ));
      }
      await HealthSync.instance.startSync(_config.syncDaysBack);
      setState(() => _step = 3);
    } catch (e) {
      setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _finish() => AppState.instance.update(_config.copyWith(onboardingDone: true));

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final steps = [_setupStep, _owStep, _healthStep, _doneStep];
    return Scaffold(
      body: SafeArea(
        child: GestureDetector(
          onTap: () => FocusScope.of(context).unfocus(),
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 28, 20, 28),
            children: [
              Row(
                children: [
                  for (var i = 0; i < steps.length; i++)
                    Expanded(
                      child: Container(
                        height: 3,
                        margin: const EdgeInsets.symmetric(horizontal: 2),
                        decoration: BoxDecoration(
                          color: i <= _step ? cs.primary : cs.onSurfaceVariant.withValues(alpha: 0.2),
                          borderRadius: BorderRadius.circular(2),
                        ),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 28),
              ...steps[_step](),
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

  Widget _title(String t, String sub) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(t, style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w700, letterSpacing: -0.5)),
      const SizedBox(height: 8),
      Text(sub, style: TextStyle(fontSize: 15, color: Theme.of(context).colorScheme.onSurfaceVariant, height: 1.4)),
      const SizedBox(height: 24),
    ],
  );

  List<Widget> _setupStep() => [
    _title(
      tr('开始使用', 'Get started'),
      tr('家人会给你一个「设置二维码」：用 iPhone 相机扫一下就能自动设置好。也可以在这里扫码，或粘贴设置信息。',
          'You will get a setup QR code: scan it with the iPhone Camera and everything is set up. You can also scan it here or paste the setup text.'),
    ),
    BigButton(tr('扫描设置二维码', 'Scan setup code'), onPressed: _busy ? null : _scan, busy: _busy),
    const SizedBox(height: 10),
    OutlinedButton.icon(
      onPressed: _busy ? null : _paste,
      icon: const Icon(Icons.content_paste, size: 18),
      label: Text(tr('粘贴设置信息', 'Paste setup text')),
      style: OutlinedButton.styleFrom(
        minimumSize: const Size.fromHeight(48),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    const SizedBox(height: 6),
    BigButton(
      _manual ? tr('收起手动填写', 'Hide manual setup') : tr('手动填写服务器', 'Enter servers manually'),
      secondary: true,
      onPressed: _busy ? null : () => setState(() => _manual = !_manual),
    ),
    if (_manual) ...[
      const SizedBox(height: 12),
      UrlField(
        controller: _today,
        label: tr('今天页地址', 'Today page URL'),
        hint: 'https://body.example.com',
        kind: UrlKind.today,
        error: _todayError,
      ),
      const SizedBox(height: 14),
      UrlField(
        controller: _ow,
        label: tr('同步服务器地址', 'Sync server URL'),
        hint: 'https://sync.example.com',
        kind: UrlKind.ow,
        error: _owError,
      ),
      if (_insecure) ...[
        const SizedBox(height: 10),
        Hint(tr('http 只适合模拟器 / 开发；手机上请用 https。', 'http is for the simulator / development only.')),
      ],
      const SizedBox(height: 18),
      BigButton(tr('下一步', 'Next'), onPressed: _saveServers),
    ],
    const SizedBox(height: 22),
    Hint(vpnHint, icon: Icons.vpn_lock_outlined),
    if (widget.onBack != null) ...[
      const SizedBox(height: 18),
      BigButton(tr('改用账号登录', 'Sign in with an account instead'), secondary: true, onPressed: _busy ? null : widget.onBack),
    ],
  ];

  List<Widget> _owStep() => [
    _title(
      tr('连接同步', 'Connect sync'),
      tr('输入 8 位邀请码（在设置信息里，或问家人要）。连接后 app 会在后台把健康数据同步到服务器。',
          'Enter the 8-character invitation code. The app then syncs your Health data in the background.'),
    ),
    TextField(
      controller: _code,
      autocorrect: false,
      enableSuggestions: false,
      textCapitalization: TextCapitalization.characters,
      // Alphanumeric only: use the ASCII keyboard to avoid the candidate bar of Chinese input methods
      keyboardType: TextInputType.visiblePassword,
      style: const TextStyle(fontSize: 22, letterSpacing: 4, fontWeight: FontWeight.w600),
      decoration: InputDecoration(
        labelText: tr('邀请码', 'Invitation code'),
        hintText: 'ABCD2345',
        border: const OutlineInputBorder(borderRadius: BorderRadius.all(Radius.circular(12))),
      ),
      onSubmitted: (_) => _connect(),
    ),
    const SizedBox(height: 24),
    BigButton(tr('连接', 'Connect'), onPressed: _connect, busy: _busy),
    const SizedBox(height: 6),
    BigButton(
      tr('跳过，只看今天页', 'Skip — today page only'),
      secondary: true,
      onPressed: _busy ? null : () => setState(() => _step = 3),
    ),
    BigButton(tr('上一步', 'Back'), secondary: true, onPressed: _busy ? null : () => setState(() => _step = 0)),
  ];

  List<Widget> _healthStep() => [
    _title(
      tr('允许读取健康数据', 'Allow Health access'),
      tr('接下来会弹出系统的健康权限窗口，请点「全部打开」，再点「允许」。只读取、不写入。首次会同步最近 ${_config.syncDaysBack} 天。',
          'Next, iOS asks for Health access — tap "Turn On All", then "Allow". Read-only. The first sync covers the last ${_config.syncDaysBack} days.'),
    ),
    Hint(limitsHint),
    const SizedBox(height: 24),
    BigButton(tr('继续', 'Continue'), onPressed: _requestHealth, busy: _busy),
  ];

  List<Widget> _doneStep() {
    final auto = _config.pendingLoginCode != null;
    return [
      _title(
        tr('设置好了', 'All set'),
        auto
            ? tr('打开今天页会自动登录。以后同步状态在「我」→「手机同步」里看。',
                'The today page signs you in automatically. Sync status lives under Me → Phone sync.')
            : tr('打开今天页后，输入家人给你的 token 登录（只需一次）。以后同步状态在「我」→「手机同步」里看。',
                'On the today page, sign in once with the token you were given. Sync status lives under Me → Phone sync.'),
      ),
      if (_config.owConnected) Hint(tr('首次同步可能要几分钟，保持 app 打开会更快。', 'The first sync can take a few minutes; keep the app open.')),
      const SizedBox(height: 24),
      BigButton(tr('打开今天页', 'Open today page'), onPressed: _finish),
    ];
  }
}
