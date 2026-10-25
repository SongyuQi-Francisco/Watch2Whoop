// Sync center (server flag sync_center): the forced re-sync and the per-type "on the phone / on the server" comparison.
//
// Forced re-sync of the last 7 / 30 / 90 days, all history, or a custom number of days:
// 1. POST {Today page}/v1/ingest/resync {days | null} (sync/resync.py): one job per account, rate limited by the server.
// 2. The phone part, all resumable:
//    - Open Wearables SDK (checked in its 0.15 source): startBackgroundSync(syncDaysBack: N) stores N and resetAnchors()
//      clears every type's anchor and starts a full export from the start of the day N days ago (0 = all history),
//      newest first, saving its progress. While the job runs every other startSync of the app uses N too
//      ([effectiveDaysBack]), so a sync on open does not cut the export back to the usual range; afterwards the usual
//      range is stored again (the anchors then only see newer samples, nothing is read twice).
//    - RawSync reads every kind again with its own anchor set "resync" (raw_sync.dart), so the regular anchors do not move.
//    Progress goes to PUT /v1/ingest/resync; when both are done, phone_done.
// 3. The server then imports the range again and recomputes the scores from its start (sync-refresh-run); the page shows
//    its percent from GET.
// All history reads years of data: the app asks first and warns that it takes long and uses battery; RawSync stops after
// its time budget and continues on the next step, so the app never blocks.
// The state is kept in shared_preferences, so a closed app continues on the next open (home_page.dart → step()).
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'data_status.dart';
import 'i18n.dart';

/// The range choices; null = all history.
const List<int?> kResyncChoices = [7, 30, 90, null];
const int kResyncMaxDays = 3650;

/// Days back for the native readers: 0 = all history (health_range.dart kAllHistory).
int daysBackOf(int? days) => days ?? 0;

String resyncRangeLabel(int? days) => days == null ? tr('全部', 'All') : tr('$days 天', '$days days');

/// A custom number of days typed by the user, or null when it is not 1–3650.
int? parseCustomDays(String text) {
  final n = int.tryParse(text.trim());
  return n != null && n >= 1 && n <= kResyncMaxDays ? n : null;
}

/// The phone part of a running re-sync (kept across app starts).
class ResyncState {
  const ResyncState({this.days, required this.startedAt, this.owDone = false, this.rawDone = false, this.reported = 0,
      this.phoneDone = false, this.engineDone = true, this.fromPage = false});

  final int? days;
  final DateTime startedAt;
  final bool owDone, rawDone, phoneDone;
  final int reported;

  /// The app's own engine read the range too (backfill.dart, Backfill.swift); true when the engine is not on.
  final bool engineDone;

  /// Started by the Today page's 「补全历史」 (its progress also goes to the page's onApp with kind backfill).
  final bool fromPage;

  ResyncState copyWith({bool? owDone, bool? rawDone, bool? phoneDone, int? reported, bool? engineDone}) => ResyncState(
      days: days,
      startedAt: startedAt,
      owDone: owDone ?? this.owDone,
      rawDone: rawDone ?? this.rawDone,
      phoneDone: phoneDone ?? this.phoneDone,
      reported: reported ?? this.reported,
      engineDone: engineDone ?? this.engineDone,
      fromPage: fromPage);

  Map<String, Object?> toJson() => {'days': days, 'started_at': startedAt.toIso8601String(), 'ow_done': owDone,
        'raw_done': rawDone, 'phone_done': phoneDone, 'reported': reported, 'engine_done': engineDone, 'from_page': fromPage};

  static ResyncState? fromJson(Object? j) {
    if (j is! Map) return null;
    final at = DateTime.tryParse('${j['started_at']}');
    if (at == null) return null;
    return ResyncState(days: (j['days'] as num?)?.toInt(), startedAt: at, owDone: j['ow_done'] == true,
        rawDone: j['raw_done'] == true, phoneDone: j['phone_done'] == true, reported: (j['reported'] as num?)?.toInt() ?? 0,
        engineDone: j['engine_done'] != false, fromPage: j['from_page'] == true);
  }
}

/// Phone percent: the OW export (its own percent) and the app's direct reads, half each.
int phonePercent(ResyncState s, int owPercent, {int enginePercent = 100}) {
  final parts = [s.owDone ? 100 : owPercent.clamp(0, 100), s.rawDone ? 100 : 0, if (!s.engineDone) enginePercent.clamp(0, 100)];
  return (parts.reduce((a, b) => a + b) / parts.length).round();
}

/// GET / POST / PUT / DELETE {server}/v1/ingest/resync with the account or upload-only token.
class ResyncApi {
  ResyncApi(this.baseUrl, this.token, {this.client});
  final String baseUrl, token;
  final http.Client? client;

  Uri get _uri {
    final b = Uri.parse(baseUrl);
    return Uri(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null, path: '/v1/ingest/resync');
  }

  /// The JSON answer, or null (network, 404 = flag off / older server).
  Future<Map<String, Object?>?> _send(String method, [Map<String, Object?>? body]) async {
    final c = client ?? http.Client();
    try {
      final req = http.Request(method, _uri)
        ..headers.addAll({'Authorization': 'Bearer $token', 'Accept': 'application/json', 'X-Body-Lang': currentLang});
      if (body != null) {
        req.headers['Content-Type'] = 'application/json';
        req.body = jsonEncode(body);
      }
      final res = await http.Response.fromStream(await c.send(req)).timeout(const Duration(seconds: 20));
      if (res.statusCode >= 300) return null;
      final j = jsonDecode(utf8.decode(res.bodyBytes));
      return j is Map ? j.map((k, v) => MapEntry('$k', v)) : null;
    } catch (_) {
      return null;
    } finally {
      if (client == null) c.close();
    }
  }

  Future<Map<String, Object?>?> status() => _send('GET');
  Future<Map<String, Object?>?> start(int? days) => _send('POST', {'days': days});
  Future<Map<String, Object?>?> progress({int? percent, Map<String, int>? counts, bool done = false}) =>
      _send('PUT', {'phone_percent': ?percent, 'counts': ?counts, if (done) 'phone_done': true});
  Future<Map<String, Object?>?> cancel() => _send('DELETE');
}

/// Why the server did not start a job, as one line.
String resyncRefusal(Map<String, Object?>? r) {
  final min = ((r?['retry_after'] as num? ?? 60) / 60).ceil();
  return switch (r?['reason']) {
      'busy' => tr('已经有一个重新同步在进行', 'A re-sync is already running'),
      'throttled' => tr('刚同步过，$min 分钟后再试', 'Just synced; try again in $min min'),
      'consent' => tr('先在今天页确认同意书', 'Accept the consent on the Today page first'),
      _ => tr('服务器暂时不能重新同步，稍后再试', 'The server cannot re-sync right now; try again later'),
    };
}

/// The server job as one line: phase and percent.
String resyncServerLine(Map<String, Object?>? j) {
  if (j == null) return '—';
  final p = j['percent'];
  return switch (j['status']) {
    'phone' => tr('手机上传中 ${p ?? 0}%', 'Uploading from the phone ${p ?? 0}%'),
    'queued' || 'importing' => tr('服务器重新导入中 ${p ?? 0}%', 'Server re-importing ${p ?? 0}%'),
    'scoring' => tr('服务器重算分数中 ${p ?? 0}%', 'Server recomputing scores ${p ?? 0}%'),
    'done' => tr('已完成', 'Done'),
    'error' => tr('出错：${j['error'] ?? '-'}', 'Failed: ${j['error'] ?? '-'}'),
    'cancelled' => tr('已停止', 'Stopped'),
    _ => '—',
  };
}

// ------------------------------------------------------------------------------ phone vs server per type

enum TypeVerdict { synced, notSynced, notCollected, unknown }

/// "Not collected or not synced": no sample on the phone = not collected (or not allowed to read); a newer day on the
/// phone than on the server = not synced; else synced. Days compare in local time.
TypeVerdict verdictOf({DateTime? phone, DateTime? serverAt, String? serverDate}) {
  if (phone == null) return TypeVerdict.notCollected;
  final p = DateTime(phone.year, phone.month, phone.day);
  final s = serverAt != null ? DateTime(serverAt.year, serverAt.month, serverAt.day) : DateTime.tryParse(serverDate ?? '');
  if (s == null) return TypeVerdict.notSynced;
  return p.isAfter(s) ? TypeVerdict.notSynced : TypeVerdict.synced;
}

String verdictLabel(TypeVerdict v) => switch (v) {
      TypeVerdict.synced => tr('已同步', 'Synced'),
      TypeVerdict.notSynced => tr('没同步', 'Not synced'),
      TypeVerdict.notCollected => tr('没采集', 'Not recorded'),
      TypeVerdict.unknown => '—',
    };

/// One extra line for a type: VO₂ max is only estimated outdoors; "not recorded" may also be a missing permission.
String? verdictNote(String key, TypeVerdict v) {
  if (key == 'vo2max' && v != TypeVerdict.synced) {
    return tr('只在户外步行 / 跑步 / 徒步时生成', 'Only made during outdoor walks / runs / hikes');
  }
  if (v == TypeVerdict.notCollected) return tr('手机上没有（或没有读取权限）', 'None on the phone (or no read permission)');
  return null;
}

/// "MM-DD" of a local date, or —.
String shortDay(DateTime? d) =>
    d == null ? '—' : '${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

/// The newest phone sample of a data-status type (any of its HealthKit types).
DateTime? phoneLatestOf(String key, Map<String, DateTime> latest, Map<String, List<String>> phoneTypes) {
  DateTime? best;
  for (final t in phoneTypes[key] ?? const <String>[]) {
    final v = latest[t];
    if (v != null && (best == null || v.isAfter(best))) best = v;
  }
  return best;
}

class TypeRow {
  const TypeRow(this.type, this.phone, this.verdict);
  final DataTypeStatus type;
  final DateTime? phone;
  final TypeVerdict verdict;
}

List<TypeRow> compareTypes(List<DataTypeStatus> types, Map<String, DateTime> latest, Map<String, List<String>> phoneTypes) => [
      for (final t in types)
        () {
          final p = phoneLatestOf(t.key, latest, phoneTypes);
          return TypeRow(t, p, phoneTypes.containsKey(t.key)
              ? verdictOf(phone: p, serverAt: t.lastAt, serverDate: t.lastDate)
              : TypeVerdict.unknown);
        }(),
    ];

// ------------------------------------------------------------------------------ the running job

class Resync {
  Resync._();
  static final Resync instance = Resync._();
  static const _key = 'w2w.resync.v1';

  final ValueNotifier<ResyncState?> state = ValueNotifier(null);

  Future<void> load() async {
    try {
      state.value = ResyncState.fromJson(jsonDecode((await SharedPreferences.getInstance()).getString(_key) ?? 'null'));
    } catch (_) {}
  }

  Future<void> save(ResyncState? s) async {
    state.value = s;
    final prefs = await SharedPreferences.getInstance();
    if (s == null) {
      await prefs.remove(_key);
    } else {
      await prefs.setString(_key, jsonEncode(s.toJson()));
    }
  }

  /// Days back every startSync should use: the re-sync's range while its phone part runs, else the usual one.
  int effectiveDaysBack(int usual) {
    final s = state.value;
    return s != null && !s.owDone ? daysBackOf(s.days) : usual;
  }
}
