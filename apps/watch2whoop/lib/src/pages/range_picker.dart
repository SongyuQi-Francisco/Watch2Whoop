// Read range picker (「近 90 天」 / 「全部历史」) and the history progress card, used by the first-run permissions step and
// by Phone sync. The logic (what each choice means for the OW SDK, progress) is in ../health_range.dart.
import 'package:flutter/material.dart';

import '../health_range.dart';
import '../i18n.dart';
import '../sync_params.dart';
import '../ui.dart';

class RangePicker extends StatelessWidget {
  const RangePicker({super.key, required this.syncDaysBack, required this.onChanged, this.enabled = true});

  final int syncDaysBack;
  final ValueChanged<int> onChanged;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    // the stored range, or the nearest option (a build with another default)
    final current = isAllHistory(syncDaysBack) ? kAllHistory : (syncDaysBack > kFirstSyncDays ? kBackfillDays : kFirstSyncDays);
    Widget option(int days, String title, String sub) {
      final on = days == current;
      return InkWell(
        onTap: enabled && !on ? () => onChanged(days) : null,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 4),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(on ? Icons.radio_button_checked : Icons.radio_button_unchecked, color: on ? cs.primary : cs.onSurfaceVariant),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 2),
                    Text(sub, style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant, height: 1.35)),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
    }

    return Semantics(
      label: tr('读取范围', 'Read range'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          option(kFirstSyncDays, rangeLabel(kFirstSyncDays),
              tr('几分钟读完，今天页最快有数据。默认；以后可以再补更早的。', 'Read in a few minutes, so the Today page has data soonest. Default; older history can be added later.')),
          option(kBackfillDays, rangeLabel(kBackfillDays),
              tr('先传最近 $kFirstSyncDays 天，更早的在后台补上（手机空闲、连着 Wi-Fi 时）。', 'The last $kFirstSyncDays days first, the rest in the background (while the phone is idle on Wi-Fi).')),
          option(kAllHistory, tr('全部历史', 'All history'),
              tr('从「健康」里最早的一条开始读。数据多的话要几十分钟到几小时，中断了会接着读。',
                  'From the oldest record in Health. With a lot of data this takes tens of minutes to a few hours; it resumes if interrupted.')),
        ],
      ),
    );
  }
}

/// Progress of reading the whole history: bar, records sent, types done, a rough time left.
class HistoryProgressCard extends StatelessWidget {
  const HistoryProgressCard(this.p, {super.key});
  final HistoryProgress p;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    if (p.done) {
      return Row2(tr('全部历史', 'All history'), value: tr('已读完', 'Read'));
    }
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(tr('正在读取全部历史', 'Reading all history'), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600))),
              Text('${p.percent}%', style: const TextStyle(fontFeatures: [FontFeature.tabularFigures()])),
            ],
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: LinearProgressIndicator(value: p.percent / 100, minHeight: 6),
          ),
          const SizedBox(height: 8),
          Text(
            tr('已读 ${_n(p.sent)} 条 · ${p.typesDone}/${p.typesTotal} 类 · 还要${etaLabel(p.eta)}',
                '${_n(p.sent)} records · ${p.typesDone}/${p.typesTotal} types · ${etaLabel(p.eta)} left'),
            style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
          ),
          const SizedBox(height: 4),
          Text(
            tr('保持 app 打开、手机解锁会更快；中断了下次打开接着读。', 'Faster with the app open and the phone unlocked; it resumes next time if interrupted.'),
            style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
          ),
        ],
      ),
    );
  }

  static String _n(int v) => v.toString().replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ',');
}
