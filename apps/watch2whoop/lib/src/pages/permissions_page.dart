// Permissions step of the first-run flow (issue #60), also re-openable from Phone sync → Permissions.
// Only what the app really uses: Health (read). The app sends no notifications; iOS asks for the microphone only when the
// mic button on the Today page is first held (hold-to-talk, #171) and for the camera only when a setup QR code is scanned
// (unlocked builds) or a photo / video is taken for the AI, so none of those get a card here.
// Per permission: why, what happens if declined, a button that shows the system prompt, and (once asked) a button that opens
// this app's page in iOS Settings. Nothing is switched on for the user. iOS never tells an app whether Health *read* access
// was granted, so "asked" is all that can be shown.
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../config.dart';
import '../health_sync.dart';
import '../i18n.dart';
import '../ui.dart';

/// The step shows on its own once: first run (inside onboarding), or after an update for someone who was never asked.
bool shouldShowPermissions(AppConfig c) => c.onboardingDone && !c.permissionsSeen && !c.healthRequested;

/// Opens this app's page in iOS Settings (UIApplication.openSettingsURLString).
Future<void> openAppSettings() => launchUrl(Uri.parse('app-settings:'), mode: LaunchMode.externalApplication);

/// The permission cards. [onRequestHealth] shows the Health prompt (the onboarding flows connect sync first);
/// default: ask and start syncing when sync is connected.
class PermissionsPanel extends StatefulWidget {
  const PermissionsPanel({super.key, this.onRequestHealth, this.openSettings = openAppSettings});
  final Future<void> Function()? onRequestHealth;
  final Future<void> Function() openSettings;

  @override
  State<PermissionsPanel> createState() => _PermissionsPanelState();
}

class _PermissionsPanelState extends State<PermissionsPanel> {
  bool _busy = false;

  AppConfig get _config => AppState.instance.config;

  Future<void> _defaultRequest() async {
    await HealthSync.instance.requestAuthorization();
    await AppState.instance.update(_config.copyWith(healthRequested: true));
    if (_config.owConnected) await HealthSync.instance.startSync(_config.syncDaysBack);
  }

  Future<void> _ask() async {
    setState(() => _busy = true);
    try {
      await (widget.onRequestHealth ?? _defaultRequest)();
    } catch (e) {
      if (mounted) ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(content: Text(redactServer('$e'))));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final asked = _config.healthRequested;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.favorite_outline, color: cs.primary),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(tr('健康数据（只读）', 'Health data (read only)'),
                          style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
                    ),
                    if (asked)
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(color: cs.primary.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(8)),
                        child: Text(tr('已请求', 'Asked'), style: TextStyle(fontSize: 12, color: cs.primary)),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                _line(Icons.help_outline, tr('为什么要', 'Why'),
                    tr('恢复、负荷和睡眠分数都从手表的心率、HRV、睡眠和训练算出来。只读取，不写入。',
                        'Recovery, strain and sleep scores come from your watch\'s heart rate, HRV, sleep and workouts. Read only, never written.')),
                const SizedBox(height: 8),
                _line(Icons.block_outlined, tr('不允许会怎样', 'If you decline'),
                    tr('不会同步健康数据，今天页没有分数。随时可以在设置里打开。',
                        'Nothing is synced and Today shows no scores. You can turn it on in Settings any time.')),
                const SizedBox(height: 14),
                if (!asked)
                  BigButton(tr('允许读取健康数据', 'Allow Health access'), onPressed: _ask, busy: _busy)
                else ...[
                  Text(
                    tr('iOS 不会告诉 app 你打开了哪些。没有数据的话，在「健康」app → 右上角头像 → App → Watch2Whoop 里检查，或者打开设置。',
                        'iOS does not tell apps what you allowed. If no data arrives, check Health → profile → Apps → Watch2Whoop, or open Settings.'),
                    style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant, height: 1.4),
                  ),
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    onPressed: () => widget.openSettings(),
                    icon: const Icon(Icons.settings_outlined, size: 18),
                    label: Text(tr('打开设置', 'Open Settings')),
                    style: OutlinedButton.styleFrom(
                      minimumSize: const Size.fromHeight(46),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                    ),
                  ),
                  TextButton(onPressed: _busy ? null : _ask, child: Text(tr('再显示一次系统弹窗', 'Show the iOS prompt again'))),
                ],
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        Hint(
          serverLocked
              ? tr('这个 app 不发通知；麦克风只在你按住 🎤 和 AI 说话时由 iOS 询问。',
                  'This app sends no notifications; iOS asks for the microphone only when you hold 🎤 to talk to your AI.')
              : tr('这个 app 不发通知；麦克风只在你按住 🎤 和 AI 说话时、相机只在扫设置二维码时由 iOS 询问。',
                  'This app sends no notifications; iOS asks for the microphone only when you hold 🎤 to talk to your AI, and for the camera only when you scan a setup code.'),
          icon: Icons.info_outline,
        ),
      ],
    );
  }

  Widget _line(IconData icon, String title, String text) {
    final cs = Theme.of(context).colorScheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 18, color: cs.onSurfaceVariant),
        const SizedBox(width: 8),
        Expanded(
          child: Text.rich(
            TextSpan(children: [
              TextSpan(text: '$title  ', style: const TextStyle(fontWeight: FontWeight.w600)),
              TextSpan(text: text, style: TextStyle(color: cs.onSurfaceVariant)),
            ]),
            style: const TextStyle(fontSize: 14, height: 1.4),
          ),
        ),
      ],
    );
  }
}

/// Stand-alone page: shown once after an update (HomePage) and from Phone sync → Permissions. "Done" marks it seen.
class PermissionsPage extends StatefulWidget {
  const PermissionsPage({super.key, this.openSettings = openAppSettings});
  final Future<void> Function() openSettings;

  @override
  State<PermissionsPage> createState() => _PermissionsPageState();
}

class _PermissionsPageState extends State<PermissionsPage> {
  @override
  void initState() {
    super.initState();
    langOverride.addListener(_redraw);
    AppState.instance.addListener(_redraw);
  }

  @override
  void dispose() {
    langOverride.removeListener(_redraw);
    AppState.instance.removeListener(_redraw);
    super.dispose();
  }

  void _redraw() {
    if (mounted) setState(() {});
  }

  Future<void> _done() async {
    final nav = Navigator.of(context);
    await AppState.instance.update(AppState.instance.config.copyWith(permissionsSeen: true));
    if (nav.canPop()) nav.pop();
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      onPopInvokedWithResult: (didPop, _) {
        if (didPop && !AppState.instance.config.permissionsSeen) {
          AppState.instance.update(AppState.instance.config.copyWith(permissionsSeen: true)); // closing = skipped
        }
      },
      child: Scaffold(
        appBar: AppBar(title: Text(tr('权限', 'Permissions'))),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
          children: [
            Text(
              tr('Watch2Whoop 只需要读取健康数据。下面说明为什么要、不允许会怎样；开关由你在 iOS 里点。',
                  'Watch2Whoop only needs to read your Health data. Here is why, and what happens if you decline; you make the choice in iOS.'),
              style: TextStyle(fontSize: 15, color: Theme.of(context).colorScheme.onSurfaceVariant, height: 1.4),
            ),
            const SizedBox(height: 16),
            PermissionsPanel(openSettings: widget.openSettings),
            const SizedBox(height: 20),
            BigButton(tr('完成', 'Done'), onPressed: _done),
          ],
        ),
      ),
    );
  }
}
