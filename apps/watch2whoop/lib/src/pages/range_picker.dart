// Read range picker (「近 90 天」 / 「全部历史」) and the history progress card, used by the first-run permissions step and
// by Phone sync. The logic (what each choice means for the OW SDK, progress) is in ../health_range.dart.
import 'package:flutter/material.dart';

import '../config.dart';
import '../health_range.dart';
import '../i18n.dart';
import '../ui.dart';

class RangePicker extends StatelessWidget {
  const RangePicker({super.key, required this.syncDaysBack, required this.onChanged, this.enabled = true});

  final int syncDaysBack;
  final ValueChanged<int> onChanged;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final all = isAllHistory(syncDaysBack);
    Widget option(bool isAll, String title, String sub) {
      final on = isAll == all;
      return InkWell(
        onTap: enabled && !on ? () => onChanged(isAll ? kAllHistory : kDefaultSyncDaysBack) : null,
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
          option(false, rangeLabel(kDefaultSyncDaysBack), tr('几分钟到十几分钟读完。默认。', 'Done in a few minutes to a quarter of an hour. Default.')),
          option(true, tr('全部历史', 'All history'),
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
