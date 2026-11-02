// The app's own sync engine in the sync center (#219 A4): mode, first-sync progress, the last round and background
// rounds, the cellular switch for history, per type the newest sample on the phone next to the newest on the server
// (GET /v1/ingest/sync-status: "not recorded or not synced"), and the engine's run log. Shown only while the server
// has the engine on for this user (modes shadow / app). Times, counts and codes only: no health values.
import 'dart:async';

import 'package:flutter/material.dart';

import '../app_state.dart';
import '../backfill.dart';
import '../i18n.dart';
import '../raw_sync.dart';
import '../resync.dart';
import '../sync_engine.dart';
import '../ui.dart';
import 'sync_center_section.dart' show VerdictTag;

class EngineSection extends StatefulWidget {
  const EngineSection({super.key, this.reloadToken = 0});
  final int reloadToken;

  @override
  State<EngineSection> createState() => _EngineSectionState();
}

class _EngineSectionState extends State<EngineSection> {
  EngineStatus? _status;
  EngineServerStatus? _server;
  Map<String, DateTime> _phone = const {};
  List<EngineType> _registry = const [];
  Map<String, Object?>? _backfill;
  bool _busy = false;
  String? _message;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _reload();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) => _tick());
  }

  @override
  void didUpdateWidget(EngineSection old) {
    super.didUpdateWidget(old);
    if (old.reloadToken != widget.reloadToken) _reload();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<String?> _token() async => AppState.instance.accountToken ?? await RawSync.instance.ingestToken();

  Future<void> _tick() async {
    final s = await SyncEngine.instance.status();
    final b = await BackfillService.instance.engineStatus();
    if (!mounted) return;
    setState(() {
      _status = s;
      _backfill = b;
    });
  }

  Future<void> _reload() async {
    final reg = await SyncEngine.instance.registry();
    final token = await _token();
    final (s, server, phone) = await (
      SyncEngine.instance.status(),
      token == null ? Future<EngineServerStatus?>.value() : fetchEngineServerStatus(AppState.instance.config.todayUrl, token),
      phoneLatest([for (final t in reg) if (t.engine) t.hk]),
    ).wait;
    final b = await BackfillService.instance.engineStatus();
    if (!mounted) return;
    setState(() {
      _registry = reg;
      _status = s;
      _server = server;
      _phone = phone;
      _backfill = b;
    });
  }

  Future<void> _syncNow() async {
    setState(() {
      _busy = true;
      _message = null;
    });
    final r = await SyncEngine.instance.runNow();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = r == null
          ? null
          : (engineStopText(r.stop) ??
              tr('已上传 ${r.samples} 条（${r.uploaded.length} 类）', 'Uploaded ${r.samples} items (${r.uploaded.length} types)'));
    });
    await _reload();
  }

  Future<void> _cellular(bool on) async {
    await SyncEngine.instance.setCellularHistory(on);
    await _tick();
  }

  String _modeLabel(String m) => switch (m) {
        'app' => tr('主同步（自建）', 'Main sync (own engine)'),
        'shadow' => tr('影子对比（和旧同步并行）', 'Shadow (alongside the old sync)'),
        _ => tr('未开启', 'Off'),
      };

  String _runLine(Map<String, Object?>? r) {
    if (r == null) return '—';
    final at = r['started'] is num ? DateTime.fromMillisecondsSinceEpoch(((r['started'] as num) * 1000).round()) : null;
    final result = '${r['result'] ?? ''}';
    final label = switch (result) {
      'ok' => tr('成功', 'OK'),
      'nothing' => tr('没有新数据', 'Nothing new'),
      'partial' => tr('部分完成', 'Partly'),
      'locked' => tr('锁屏暂停', 'Locked'),
      'offline' => tr('没网', 'Offline'),
      'expired' => tr('后台时间用完', 'Time ran out'),
      _ => tr('失败', 'Failed'),
    };
    return '${ago(at)} · $label';
  }

  @override
  Widget build(BuildContext context) {
    final s = _status;
    if (s == null || !s.active) return const SizedBox.shrink();
    final cs = Theme.of(context).colorScheme;
    final rows = engineRows(_registry, const {}, _phone, _server?.latest ?? const {})
        .where((r) => r.phone != null || r.server != null)
        .toList();
    final notSynced = rows.where((r) => r.verdict == TypeVerdict.notSynced).length;
    final range = s.range;
    final backfill = _backfill;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Group(
          title: tr('自建同步', 'Own sync engine'),
          rows: [
            Row2(tr('模式', 'Mode'), value: _modeLabel(s.mode)),
            Row2(tr('首次同步', 'First sync'),
                value: s.firstSyncDone
                    ? tr('完成', 'Done')
                    : tr('${s.firstDone}/${s.firstTypes} 类 · ${s.firstPercent}%', '${s.firstDone}/${s.firstTypes} types · ${s.firstPercent}%')),
            if (!s.firstSyncDone)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
                child: LinearProgressIndicator(value: s.firstPercent / 100),
              ),
            if (backfill != null)
              Row2(tr('历史补传', 'History'),
                  value: backfill['done'] == true ? tr('完成', 'Done') : '${backfill['percent'] ?? 0}%'),
            if (range != null) Row2(tr('重新同步', 'Re-sync'), value: '${range['percent'] ?? 0}%'),
            Row2(tr('上次同步', 'Last round'), value: _runLine(s.lastRun)),
            Row2(tr('上次后台同步', 'Last background round'), value: ago(s.lastBackgroundAt)),
            if (_server?.openFailure != null)
              Row2(tr('服务器拒收', 'Refused by the server'), value: _server!.openFailure),
            SwitchListTile(
              title: Text(tr('用蜂窝数据补历史', 'Use cellular data for history'), style: const TextStyle(fontSize: 16)),
              value: s.cellularHistory,
              onChanged: _cellular,
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
              child: BigButton(tr('立即同步', 'Sync now'), onPressed: _busy || s.running ? null : _syncNow, busy: _busy || s.running),
            ),
          ],
          footer: [
            ?_message,
            ?engineStopText(s.lastStop),
            tr('新数据在任何网络都会传；首次同步和历史默认只用 Wi-Fi。', 'New data goes on any network; the first sync and history use Wi-Fi unless allowed.'),
          ].join('\n'),
        ),
        Group(
          title: notSynced == 0
              ? tr('手机上 / 服务器上（每类最新）', 'On the phone / on the server (newest per type)')
              : tr('手机上 / 服务器上（$notSynced 类没同步）', 'On the phone / on the server ($notSynced not synced)'),
          rows: _server == null
              ? [Row2(tr('读取中…', 'Loading…'))]
              : rows.isEmpty
                  ? [Row2(tr('手机上还没有这些数据', 'No data on the phone yet'))]
                  : [
                      for (final r in rows)
                        Row2(engineTypeLabel(r.type),
                            value: '${shortDay(r.phone)} / ${shortDay(r.server)}', trailing: VerdictTag(r.verdict)),
                    ],
          footer: tr('手机上也没有 = 没采集（或没给读取权限）；手机上比服务器新 = 没同步，点「立即同步」或用下面的「立即重新同步」。',
              'None on the phone = not recorded (or not allowed); newer on the phone than on the server = not synced: tap Sync now, or use Re-sync now below.'),
        ),
        Card(
          clipBehavior: Clip.antiAlias,
          child: ExpansionTile(
            title: Text(tr('自建同步日志', 'Engine log'), style: const TextStyle(fontSize: 16)),
            shape: const Border(),
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                child: SelectableText(
                  engineLogForDisplay(s.log).reversed.take(60).join('\n'),
                  style: TextStyle(fontSize: 11, fontFamily: 'Menlo', color: cs.onSurfaceVariant),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 24),
      ],
    );
  }
}
