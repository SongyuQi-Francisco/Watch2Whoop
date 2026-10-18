// Sync status: counts uploaded items per type and the last success / failure from the OW iOS SDK (0.15) log lines.
// The SDK only reports "how many items were sent this round", with no cumulative per-type count, so we parse its logs:
//   "Sending 812 KB, 1500 items (HeartRate: 1200, sleep: 40, workouts: 2, ...)"   <- payload summary before upload
//   "upload=sync req=… bytes=… sent=…/… status=200"                              <- result of this upload
//   "Sync complete: 1500 samples across 12 types"                                 <- end of a sync round
// A changed log format only makes the counts inaccurate and does not affect sync itself. Pure Dart for easy unit testing.
//
// Sync status parsed from the OW iOS SDK log stream (per-type upload counts, last success / failure).
import 'dart:convert';

class SyncStats {
  SyncStats({
    Map<String, int>? perType,
    this.lastUploadAt,
    this.lastCompleteAt,
    this.lastErrorAt,
    this.lastError,
    this.lastNoticeAt,
    this.lastNotice,
    this.authProblem = false,
  }) : perType = perType ?? <String, int>{};

  /// Cumulative successfully uploaded items per type (since app install / reconnect).
  final Map<String, int> perType;
  DateTime? lastUploadAt;
  DateTime? lastCompleteAt;
  DateTime? lastErrorAt;
  String? lastError;

  /// Not a failure but worth surfacing: paused by lock screen, offline, background time used up.
  DateTime? lastNoticeAt;
  SyncNotice? lastNotice;

  /// 401 / refresh token rejected: reconnect with a new invitation code.
  bool authProblem;

  int get total => perType.values.fold(0, (a, b) => a + b);

  /// Sorted by item count, descending.
  List<MapEntry<String, int>> get sortedTypes =>
      perType.entries.toList()..sort((a, b) => b.value != a.value ? b.value.compareTo(a.value) : a.key.compareTo(b.key));

  Map<String, Object?> toJson() => {
    'perType': perType,
    'lastUploadAt': lastUploadAt?.toIso8601String(),
    'lastCompleteAt': lastCompleteAt?.toIso8601String(),
    'lastErrorAt': lastErrorAt?.toIso8601String(),
    'lastError': lastError,
    'lastNoticeAt': lastNoticeAt?.toIso8601String(),
    'lastNotice': lastNotice?.name,
    'authProblem': authProblem,
  };

  static SyncStats fromJson(Map<String, Object?> j) {
    DateTime? d(String k) => j[k] == null ? null : DateTime.tryParse(j[k] as String);
    return SyncStats(
      perType: ((j['perType'] as Map?) ?? const {}).map((k, v) => MapEntry(k as String, (v as num).toInt())),
      lastUploadAt: d('lastUploadAt'),
      lastCompleteAt: d('lastCompleteAt'),
      lastErrorAt: d('lastErrorAt'),
      lastError: j['lastError'] as String?,
      lastNoticeAt: d('lastNoticeAt'),
      lastNotice: SyncNotice.values.where((n) => n.name == j['lastNotice']).firstOrNull,
      authProblem: (j['authProblem'] as bool?) ?? false,
    );
  }

  String encode() => jsonEncode(toJson());
  static SyncStats decode(String? s) {
    if (s == null) return SyncStats();
    try {
      return fromJson((jsonDecode(s) as Map).cast<String, Object?>());
    } catch (_) {
      return SyncStats();
    }
  }
}

enum SyncNotice { locked, offline, backgroundTimeout }

final _sending = RegExp(r'^Sending \d+ KB, (\d+) items \((.*)\)$');
final _uploadStage = RegExp(r'^upload=(\S+)');
final _uploadStatus = RegExp(r'\bstatus=(\d{3})\b');
final _uploadError = RegExp(r'\berror=(\S+)');
final _complete = RegExp(r'^Sync complete: (\d+) samples');

/// Feeds in one log line and updates [stats]; returns whether the status changed (only then does it need saving).
class SyncLogParser {
  SyncLogParser(this.stats);
  final SyncStats stats;

  /// Batches sent but with no result yet (per type).
  Map<String, int>? _pending;

  bool handle(String raw, DateTime now) {
    final line = raw.trim();

    final s = _sending.firstMatch(line);
    if (s != null) {
      _pending = parseBreakdown(s.group(2)!);
      return false;
    }

    if (line.startsWith('upload=')) {
      final stage = _uploadStage.firstMatch(line)?.group(1) ?? '';
      final status = int.tryParse(_uploadStatus.firstMatch(line)?.group(1) ?? '');
      final error = _uploadError.firstMatch(line)?.group(1);
      final ok = status != null && status >= 200 && status < 300 && error == null;
      if (ok) {
        // Background-session retries (outbox-retry) do not know which batch they belong to; only the time is recorded.
        if (stage != 'outbox-retry' && _pending != null) {
          _pending!.forEach((k, v) => stats.perType[k] = (stats.perType[k] ?? 0) + v);
        }
        _pending = null;
        stats.lastUploadAt = now;
        stats.authProblem = false;
        return true;
      }
      _pending = null;
      stats.lastErrorAt = now;
      stats.lastError = status != null ? 'HTTP $status' : (error ?? 'upload failed');
      if (status == 401) stats.authProblem = true;
      return true;
    }

    if (_complete.hasMatch(line)) {
      stats.lastCompleteAt = now;
      return true;
    }

    if (line.contains('protected data inaccessible')) return _notice(SyncNotice.locked, now);
    if (line == 'Network lost') return _notice(SyncNotice.offline, now);
    if (line.startsWith('Background time low') || line.startsWith('Background task expired')) {
      return _notice(SyncNotice.backgroundTimeout, now);
    }

    if (line.startsWith('Token refresh rejected') ||
        line.startsWith('Auth error: HTTP') ||
        line.contains('auth is invalid')) {
      stats.authProblem = true;
      stats.lastErrorAt = now;
      stats.lastError = line;
      return true;
    }
    if (line == 'Sync failed to start') {
      stats.lastErrorAt = now;
      stats.lastError = line;
      return true;
    }
    return false;
  }

  bool _notice(SyncNotice n, DateTime now) {
    stats.lastNotice = n;
    stats.lastNoticeAt = now;
    return true;
  }
}

/// "HeartRate: 1200, sleep: 40" → {HeartRate: 1200, sleep: 40}
Map<String, int> parseBreakdown(String s) {
  final out = <String, int>{};
  for (final part in s.split(',')) {
    final i = part.lastIndexOf(':');
    if (i <= 0) continue;
    final n = int.tryParse(part.substring(i + 1).trim());
    final k = part.substring(0, i).trim();
    if (n != null && k.isNotEmpty) out[k] = (out[k] ?? 0) + n;
  }
  return out;
}

/// Result of SDK getSyncStatus() (iOS).
class SdkSyncStatus {
  const SdkSyncStatus({
    this.hasResumableSession = false,
    this.sentCount = 0,
    this.completedTypes = 0,
    this.isFullExport = false,
    this.initialExportDone = false,
    this.isSyncing = false,
    this.createdAt,
  });

  final bool hasResumableSession;
  final int sentCount;
  final int completedTypes;
  final bool isFullExport;
  final bool initialExportDone;
  final bool isSyncing;
  final DateTime? createdAt;

  factory SdkSyncStatus.fromMap(Map<dynamic, dynamic> m) {
    int i(Object? v) => v is num ? v.toInt() : int.tryParse('$v') ?? 0;
    bool b(Object? v) => v == true || v == 1 || v == 'true';
    final c = m['createdAt'];
    return SdkSyncStatus(
      hasResumableSession: b(m['hasResumableSession']),
      sentCount: i(m['sentCount']),
      completedTypes: i(m['completedTypes']),
      isFullExport: b(m['isFullExport']),
      initialExportDone: b(m['initialExportDone']),
      isSyncing: b(m['isSyncing']),
      createdAt: c is String ? DateTime.tryParse(c) : null,
    );
  }
}
