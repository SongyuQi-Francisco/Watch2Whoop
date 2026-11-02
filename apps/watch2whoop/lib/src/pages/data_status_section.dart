// Phone sync page → "Data on the server" and "Health permissions" (issue #163). The status per type comes from the
// server (data_status.dart); tapping a type that is stale or never received shows the likely reasons and the one thing
// that can help: ask again (only when the iOS prompt never covered that type) or the steps in iOS Settings. Nothing is
// switched on for the user: the buttons show the system prompt or open Settings, the user taps the rest.
// Everything external is injected, so the logic is tested without HealthKit or a server (test/data_status_test.dart).
import 'package:flutter/material.dart';

import '../data_status.dart';
import '../i18n.dart';
import '../ui.dart';

class DataStatusSection extends StatefulWidget {
  const DataStatusSection({
    super.key,
    required this.load,
    required this.askStatus,
    required this.requestAccess,
    required this.openSettings,
    this.reloadToken = 0,
    this.showTypes = true,
  });

  /// false in the sync center, which lists the types with the phone side next to them (sync_center_section.dart).
  final bool showTypes;

  final Future<DataStatusResult> Function() load;
  final Future<Map<String, bool>?> Function() askStatus;

  /// Shows the iOS prompts (OW + RawSync), then the caller starts a sync round.
  final Future<void> Function() requestAccess;
  final Future<void> Function() openSettings;

  /// Changing it reloads (pull to refresh, back from Settings).
  final int reloadToken;

  @override
  State<DataStatusSection> createState() => _DataStatusSectionState();
}

class _DataStatusSectionState extends State<DataStatusSection> {
  DataStatusResult? _result;
  Map<String, bool>? _asked;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void didUpdateWidget(DataStatusSection old) {
    super.didUpdateWidget(old);
    if (old.reloadToken != widget.reloadToken) _reload();
  }

  Future<void> _reload() async {
    final (r, a) = await (widget.load(), widget.askStatus()).wait;
    if (!mounted) return;
    setState(() {
      _result = r;
      _asked = a;
    });
  }

  /// "Ask again": the sheet only appears for types never asked; otherwise explain where the switches are.
  Future<void> _askAgain() async {
    final asked = await widget.askStatus();
    if (!mounted) return;
    if (!askWouldShowPrompt(asked)) {
      await _stepsDialog(null, intro: tr('每一项都已经问过了，iOS 不会再弹窗。要改的话在系统设置里改：',
          'iOS has already asked about every item and will not ask again. Change them in Settings:'));
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.requestAccess();
    } catch (e) {
      if (mounted) ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(content: Text(redactServer('$e'))));
    } finally {
      if (mounted) setState(() => _busy = false);
      _reload();
    }
  }

  Future<void> _stepsDialog(String? name, {String? intro}) => showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(tr('在系统设置里打开', 'Turn it on in Settings')),
          content: SingleChildScrollView(child: _StepsText(name: name, intro: intro)),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: Text(tr('关闭', 'Close'))),
            TextButton(
              onPressed: () {
                Navigator.pop(ctx);
                widget.openSettings();
              },
              child: Text(tr('打开系统设置', 'Open Settings')),
            ),
          ],
        ),
      );

  void _detail(DataTypeStatus t, int windowDays) {
    final fix = fixFor(t, _asked);
    final reasons = reasonsFor(t);
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) {
        final muted = Theme.of(ctx).colorScheme.onSurfaceVariant;
        return SafeArea(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(t.label, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
                const SizedBox(height: 10),
                _kv(ctx, tr('状态', 'Status'), stateLabel(t.state)),
                _kv(ctx, tr('最近收到', 'Last received'), t.lastAt != null ? ago(t.lastAt) : (t.lastDate ?? '—')),
                _kv(ctx, tr('近 $windowDays 天', 'Last $windowDays days'), tr('${t.days} 天 · ${t.count} 条', '${t.days} days · ${t.count} records')),
                if (t.sources.isNotEmpty) _kv(ctx, tr('来源', 'Sources'), t.sources.join(' · ')),
                if (reasons.isNotEmpty) ...[
                  const SizedBox(height: 14),
                  Text(tr('可能的原因', 'Possible reasons'), style: TextStyle(fontWeight: FontWeight.w600, color: muted)),
                  for (final r in reasons) Padding(padding: const EdgeInsets.only(top: 4), child: Text('• $r', style: TextStyle(color: muted, height: 1.35))),
                ],
                if (fix == DataFix.askAgain) ...[
                  const SizedBox(height: 16),
                  Text(tr('iOS 还没问过这一项，点下面会弹出系统窗口，由你选择。', 'iOS has not asked about this yet; the button shows the system prompt and you choose.'),
                      style: TextStyle(color: muted, height: 1.35)),
                  const SizedBox(height: 10),
                  BigButton(tr('重新申请健康权限', 'Ask for Health access again'), onPressed: () {
                    Navigator.pop(ctx);
                    _askAgain();
                  }),
                ],
                if (fix == DataFix.openSettings) ...[
                  const SizedBox(height: 16),
                  _StepsText(name: iosName(t.key, t.label)),
                  const SizedBox(height: 12),
                  BigButton(tr('打开系统设置', 'Open Settings'), onPressed: () {
                    Navigator.pop(ctx);
                    widget.openSettings();
                  }),
                ],
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _kv(BuildContext ctx, String k, String v) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(children: [
          Expanded(child: Text(k, style: TextStyle(color: Theme.of(ctx).colorScheme.onSurfaceVariant))),
          Flexible(child: Text(v, textAlign: TextAlign.right, style: const TextStyle(fontWeight: FontWeight.w600))),
        ]),
      );

  @override
  Widget build(BuildContext context) {
    final r = _result;
    final report = r?.report;
    final rows = <Widget>[
      if (r == null) Row2(tr('读取中…', 'Loading…'))
      else if (report == null) Row2(r.problemText ?? '—')
      else ...[
        // "no watch data" and "sync broken" are different things (sync investigation 2026-10-26)
        if (syncVerdictText(syncVerdict(report)) case final v?)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: Text(v,
                style: TextStyle(
                    fontWeight: FontWeight.w600,
                    height: 1.35,
                    color: syncVerdict(report) == SyncVerdict.noWatch
                        ? Theme.of(context).colorScheme.onSurface
                        : Theme.of(context).colorScheme.error)),
          ),
        for (final t in report.types)
          Row2(t.label,
              value: sinceLabel(t),
              trailing: _StateTag(t.state),
              onTap: () => _detail(t, report.windowDays)),
      ],
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (widget.showTypes) Group(
          title: tr('服务器收到的数据', 'Data on the server'),
          rows: rows,
          footer: report == null
              ? tr('iOS 不告诉 app 你允许了哪些，所以这里看服务器实际收到了什么。',
                  'iOS does not tell apps what you allowed, so this shows what the server actually received.')
              : tr('正常 ${report.count(DataState.ok)} · 很久没收到 ${report.count(DataState.stale)} · 从没收到 ${report.count(DataState.never)}。点一项看原因和怎么打开。',
                  'OK ${report.count(DataState.ok)} · not lately ${report.count(DataState.stale)} · never ${report.count(DataState.never)}. Tap one for reasons and how to turn it on.'),
        ),
        Group(
          title: tr('健康权限', 'Health permissions'),
          rows: [
            Row2(tr('重新申请健康权限', 'Ask for Health access again'), onTap: _busy ? null : _askAgain),
            Row2(tr('打开系统设置', 'Open Settings'), onTap: () => _stepsDialog(null)),
          ],
          footer: tr('只有还没问过的项目会弹系统窗口；已经选过的要在系统设置里改，app 不能替你改。',
              'Only items never asked show the iOS prompt; choices already made are changed in Settings, the app cannot change them.'),
        ),
      ],
    );
  }
}

class _StateTag extends StatelessWidget {
  const _StateTag(this.state);
  final DataState state;

  @override
  Widget build(BuildContext context) {
    final c = switch (state) {
      DataState.ok => const Color(0xFF34C759),
      DataState.stale => Colors.orange,
      DataState.never => Theme.of(context).colorScheme.error,
    };
    return Container(
      margin: const EdgeInsets.only(left: 8),
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(6)),
      child: Text(stateLabel(state), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: c)),
    );
  }
}

class _StepsText extends StatelessWidget {
  const _StepsText({this.name, this.intro});
  final String? name, intro;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final steps = settingsSteps(name);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (intro != null) Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(intro!, style: const TextStyle(height: 1.35))),
        for (var i = 0; i < steps.length; i++)
          Padding(padding: const EdgeInsets.only(top: 4), child: Text('${i + 1}. ${steps[i]}', style: const TextStyle(height: 1.35))),
        const SizedBox(height: 8),
        Text(settingsAltPath, style: TextStyle(fontSize: 12.5, color: muted, height: 1.35)),
      ],
    );
  }
}
