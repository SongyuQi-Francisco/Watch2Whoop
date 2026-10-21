// Apple Health read range: "Last 90 days" (default) or "All history" (replaces the manual export upload).
//
// How the OW SDK (iOS 0.15) handles it, checked in its source:
// - syncDaysBack is stored by the SDK; 0 (or below) = no start date = the whole HealthKit history.
// - The first export (and a full re-export) reads newest first in chunks per type, saves its progress after every chunk
//   (state.json: per-type cursors) and resumes there after the app is closed / killed / out of background time; chunks
//   are smaller in the background. Health data cannot be read while the phone is locked, so it pauses then.
// - Afterwards incremental anchored queries only see samples newer than the anchor. Going from 90 days to "All history"
//   therefore needs resetAnchors() + a full export (HealthSync.changeRange); it re-sends the last 90 days once, which
//   Open Wearables stores only once (unique per source, type and time), so nothing is duplicated in the data.
// - The SDK does not expose a total, so progress is "types finished / types read" (+ records sent and a rough ETA).
import 'config.dart';
import 'i18n.dart';
import 'sync_stats.dart';

/// syncDaysBack value meaning the whole history.
const int kAllHistory = 0;

bool isAllHistory(int syncDaysBack) => syncDaysBack <= 0;

String rangeLabel(int syncDaysBack) =>
    isAllHistory(syncDaysBack) ? tr('全部历史', 'All history') : tr('近 $syncDaysBack 天', 'Last $syncDaysBack days');

/// Progress of the history read (the SDK's first / full export).
class HistoryProgress {
  const HistoryProgress({required this.running, required this.done, required this.percent, required this.sent,
      required this.typesDone, required this.typesTotal, this.eta});

  final bool running;
  final bool done;
  final int percent; // 0–100 (99 until the SDK says the export is complete)
  final int sent;
  final int typesDone, typesTotal;
  final Duration? eta;

  /// [typesTotal] = the types the app reads; [now] for the elapsed time since the session started.
  factory HistoryProgress.from(SdkSyncStatus s, int typesTotal, DateTime now) {
    if (s.initialExportDone) {
      return HistoryProgress(running: false, done: true, percent: 100, sent: s.sentCount, typesDone: typesTotal, typesTotal: typesTotal);
    }
    final total = typesTotal <= 0 ? 1 : typesTotal;
    final doneTypes = s.completedTypes.clamp(0, total);
    final f = doneTypes / total;
    Duration? eta;
    if (s.createdAt != null && f >= 0.1 && f < 1) {
      final elapsed = now.difference(s.createdAt!);
      if (elapsed > Duration.zero) eta = Duration(seconds: (elapsed.inSeconds * (1 - f) / f).round());
    }
    return HistoryProgress(
      running: s.isFullExport || s.hasResumableSession || s.isSyncing,
      done: false,
      percent: (f * 100).floor().clamp(0, 99),
      sent: s.sentCount,
      typesDone: doneTypes,
      typesTotal: total,
      eta: eta,
    );
  }
}

String etaLabel(Duration? d) {
  if (d == null) return tr('正在估算', 'estimating');
  if (d.inMinutes < 1) return tr('不到 1 分钟', 'under a minute');
  if (d.inMinutes < 90) return tr('约 ${d.inMinutes} 分钟', 'about ${d.inMinutes} min');
  return tr('约 ${(d.inMinutes / 60).toStringAsFixed(1)} 小时', 'about ${(d.inMinutes / 60).toStringAsFixed(1)} h');
}

/// What the app tells the server (PUT /v1/users/me/health-history through the Today page): the choice and, for
/// "All history", the upload progress and whether the whole history is in (the server then backfills the scores).
Map<String, Object?> rangeReport(AppConfig c, HistoryProgress p) {
  final all = isAllHistory(c.syncDaysBack);
  return {
    'scope': all ? 'all' : 'days90',
    if (all) 'upload_percent': p.percent,
    if (all) 'sent': p.sent,
    if (all) 'upload_done': p.done,
  };
}

/// Sends a report only when it changed (the page is asked at most every 30 s). [send] returns whether the page took it
/// (false: the page is not loaded / not logged in yet; the same report is tried again next time).
class RangeReporter {
  RangeReporter(this.send);
  final Future<bool> Function(Map<String, Object?> report) send;
  String? _last;

  Future<bool> maybeSend(Map<String, Object?> report) async {
    final key = report.toString();
    if (key == _last) return false;
    if (!await send(report)) return false;
    _last = key;
    return true;
  }
}
