// Sync center parts of the phone sync page (server flag sync_center; resync.dart): the forced re-sync with its range
// (7 / 30 / 90 days, all, custom) and progress, and per data type the newest sample on the phone next to the newest on
// the server, which answers "not recorded or not synced".
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../app_state.dart';
import '../data_status.dart';
import '../hk_types.dart';
import '../i18n.dart';
import '../raw_sync.dart';
import '../resync.dart';
import '../resync_flow.dart';
import '../ui.dart';

class SyncCenterSection extends StatefulWidget {
  const SyncCenterSection({super.key, required this.load, this.reloadToken = 0});

  /// The data status (extended list from the server).
  final Future<DataStatusResult> Function() load;
  final int reloadToken;

  @override
  State<SyncCenterSection> createState() => _SyncCenterSectionState();
}

class _SyncCenterSectionState extends State<SyncCenterSection> {
  int _choice = 0; // index into kResyncChoices, or kResyncChoices.length = custom
  final _custom = TextEditingController(text: '14');
  Map<String, Object?>? _job;
  DataStatusResult? _status;
  Map<String, DateTime> _phone = const {};
  String? _message;
  bool _busy = false;
  bool _wake = false;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _reload();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) => _tick());
    Resync.instance.state.addListener(_onState);
  }

  @override
  void didUpdateWidget(SyncCenterSection old) {
    super.didUpdateWidget(old);
    if (old.reloadToken != widget.reloadToken) _reload();
  }

  @override
  void dispose() {
    _timer?.cancel();
    Resync.instance.state.removeListener(_onState);
    if (_wake) WakelockPlus.disable();
    _custom.dispose();
    super.dispose();
  }

  void _onState() {
    if (!mounted) return;
    // the phone part reads Health, which iOS blocks while locked: keep the screen on while it runs here
    final wake = Resync.instance.state.value != null;
    if (wake != _wake) {
      _wake = wake;
      wake ? WakelockPlus.enable() : WakelockPlus.disable();
    }
    setState(() {});
  }

  Future<String?> _token() async => AppState.instance.accountToken ?? await RawSync.instance.ingestToken();

  Future<void> _loadJob() async {
    final t = await _token();
    final j = t == null ? null : await ResyncApi(AppState.instance.config.todayUrl, t).status();
    if (mounted) setState(() => _job = j);
  }

  Future<void> _reload() async {
    final types = {for (final l in kPhoneTypes.values) ...l};
    final (s, p) = await (widget.load(), phoneLatest(types)).wait;
    if (!mounted) return;
    setState(() {
      _status = s;
      _phone = p;
    });
    await _loadJob();
    _onState();
  }

  Future<void> _tick() async {
    if (Resync.instance.state.value != null) await stepResync();
    if (Resync.instance.state.value != null || _job?['active'] == true) await _loadJob();
  }

  int? get _days => _choice < kResyncChoices.length ? kResyncChoices[_choice] : parseCustomDays(_custom.text);

  Future<void> _start() async {
    final custom = _choice == kResyncChoices.length;
    final days = _days;
    if (custom && days == null) {
      setState(() => _message = tr('天数要在 1–$kResyncMaxDays 之间', 'Days must be 1–$kResyncMaxDays'));
      return;
    }
    final all = days == null;
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(tr('重新同步${resyncRangeLabel(days)}？', 'Re-sync ${resyncRangeLabel(days)}?')),
        content: Text(all
            ? tr('从「健康」里最早的一条开始全部重新上传，数据多的话要几十分钟到几小时，比较耗电；请保持 app 打开、手机解锁并充电。中断了下次打开接着传，服务器之后会重算全部分数。',
                'Uploads everything again from the oldest record in Health. With a lot of data this takes tens of minutes to a few hours and uses battery: keep the app open, the phone unlocked and charging. It resumes if interrupted; the server then recomputes all scores.')
            : tr('把这段时间手机上的数据重新上传一遍，服务器再重新导入并重算分数。保持 app 打开会更快。',
                'Uploads this period from the phone again; the server then re-imports it and recomputes the scores. Faster with the app open.')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(tr('取消', 'Cancel'))),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(tr('开始', 'Start'))),
        ],
      ),
    );
    if (yes != true || !mounted) return;
    setState(() {
      _busy = true;
      _message = null;
    });
    final why = await startResync(days);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = why ?? tr('已开始重新同步', 'Re-sync started');
    });
    await _loadJob();
  }

  Future<void> _cancel() async {
    setState(() => _busy = true);
    await cancelResync();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = tr('已停止', 'Stopped');
    });
    await _loadJob();
  }

  Widget _choices() {
    final labels = [for (final d in kResyncChoices) resyncRangeLabel(d), tr('自定义', 'Custom')];
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      child: Wrap(spacing: 8, runSpacing: 6, children: [
        for (var i = 0; i < labels.length; i++)
          ChoiceChip(label: Text(labels[i]), selected: _choice == i, onSelected: (_) => setState(() => _choice = i)),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final local = Resync.instance.state.value;
    final running = local != null || _job?['active'] == true;
    final pct = (_job?['percent'] as num?)?.toInt();
    final refresh = _job?['refresh'] is Map ? _job!['refresh'] as Map : null;
    final updated = DateTime.tryParse('${refresh?['scored_at'] ?? refresh?['imported_at'] ?? ''}')?.toLocal();
    final upd = updated == null ? '—' : ago(updated);
    final report = _status?.report;
    final rows = report == null ? const <TypeRow>[] : compareTypes(report.types, _phone, kPhoneTypes);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Group(
          title: tr('立即重新同步', 'Re-sync now'),
          rows: [
            if (!running) ...[
              _choices(),
              if (_choice == kResyncChoices.length)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                  child: TextField(
                    controller: _custom,
                    keyboardType: TextInputType.number,
                    decoration: InputDecoration(labelText: tr('最近几天（1–$kResyncMaxDays）', 'Last how many days (1–$kResyncMaxDays)')),
                  ),
                ),
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
                child: BigButton(tr('立即重新同步', 'Re-sync now'), onPressed: _busy ? null : _start, busy: _busy),
              ),
            ] else ...[
              Row2(tr('范围', 'Range'), value: resyncRangeLabel(local?.days ?? (_job?['days'] as num?)?.toInt())),
              if (local != null)
                Row2(tr('手机', 'Phone'), value: '${_part(tr('健康同步', 'Health sync'), local.owDone)} · '
                    '${_part(tr('直传', 'Direct'), local.rawDone)}'),
              Row2(tr('进度', 'Progress'), value: resyncServerLine(_job)),
              if (pct != null)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
                  child: LinearProgressIndicator(value: pct / 100),
                ),
              Row2(tr('停止', 'Stop'), danger: true, onTap: _busy ? null : _cancel),
            ],
          ],
          footer: [
            ?_message,
            tr('服务器数据更新于 $upd', 'Server data updated $upd'),
            if (!running && _job?['status'] == 'done') tr('上次重新同步：已完成', 'Last re-sync: done'),
          ].join('\n'),
        ),
        Group(
          title: tr('手机上 / 服务器上（最新一条）', 'On the phone / on the server (newest)'),
          rows: report == null
              ? [Row2(_status == null ? tr('读取中…', 'Loading…') : (_status!.problemText ?? '—'))]
              : [
                  for (final r in rows)
                    Row2(r.type.label,
                        value: tr('手机 ${shortDay(r.phone)} · 服务器 ${shortDay(r.type.lastAt ?? DateTime.tryParse(r.type.lastDate ?? ''))}',
                            'Phone ${shortDay(r.phone)} · server ${shortDay(r.type.lastAt ?? DateTime.tryParse(r.type.lastDate ?? ''))}'),
                        trailing: _VerdictTag(r.verdict),
                        onTap: verdictNote(r.type.key, r.verdict) == null
                            ? null
                            : () => ScaffoldMessenger.maybeOf(context)
                                ?.showSnackBar(SnackBar(content: Text(verdictNote(r.type.key, r.verdict)!)))),
                ],
          footer: tr('手机上也没有 = 没采集；手机上有、服务器没有 = 没同步，点上面「立即重新同步」。VO₂max 只在户外步行 / 跑步 / 徒步时生成。',
              'None on the phone = not recorded; on the phone but not on the server = not synced: use "Re-sync now" above. VO₂ max is only made during outdoor walks / runs / hikes.'),
        ),
        if (report?.strength != null) _strength(report!.strength!),
      ],
    );
  }

  String _part(String name, bool done) => '$name ${done ? tr('完成', 'done') : tr('进行中', 'running')}';

  Widget _strength(Map<String, Object?> st) {
    final s = (st['sessions'] as Map?) ?? const {};
    final weeks = st['weeks'] ?? '-', watch = s['watch'] ?? 0, db = s['sparky'] ?? '—', hevy = s['hevy'] ?? 0;
    return Group(
      title: tr('力量训练记录（近 $weeks 周）', 'Strength training (last $weeks weeks)'),
      rows: [
        Row2(tr('天数', 'Days'), value: '${st['days'] ?? 0}'),
        Row2(tr('来源（次）', 'Sources (sessions)'),
            value: tr('手表 $watch · 底层数据库 $db · Hevy $hevy', 'Watch $watch · database $db · Hevy $hevy')),
      ],
    );
  }
}

class _VerdictTag extends StatelessWidget {
  const _VerdictTag(this.v);
  final TypeVerdict v;

  @override
  Widget build(BuildContext context) {
    final c = switch (v) {
      TypeVerdict.synced => const Color(0xFF34C759),
      TypeVerdict.notSynced => Colors.orange,
      TypeVerdict.notCollected => Theme.of(context).colorScheme.onSurfaceVariant,
      TypeVerdict.unknown => Theme.of(context).colorScheme.onSurfaceVariant,
    };
    return Container(
      margin: const EdgeInsets.only(left: 8),
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(6)),
      child: Text(verdictLabel(v), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: c)),
    );
  }
}
