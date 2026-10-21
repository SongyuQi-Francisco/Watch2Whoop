// Read range (近 90 天 / 全部历史) and the history progress (health_range.dart, pages/range_picker.dart).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:watch2whoop/src/config.dart';
import 'package:watch2whoop/src/health_range.dart';
import 'package:watch2whoop/src/i18n.dart';
import 'package:watch2whoop/src/pages/range_picker.dart';
import 'package:watch2whoop/src/sync_stats.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  systemLanguageCode = () => 'en';
  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await setLangOverride(null);
  });

  test('0 = all history; labels follow the language', () async {
    expect(isAllHistory(kAllHistory), isTrue);
    expect(isAllHistory(90), isFalse);
    expect(rangeLabel(90), 'Last 90 days');
    expect(rangeLabel(0), 'All history');
    await setLangOverride('zh');
    expect(rangeLabel(90), '近 90 天');
    expect(rangeLabel(0), '全部历史');
  });

  group('HistoryProgress', () {
    final t0 = DateTime(2026, 10, 22, 7, 0);
    test('types finished / types read, with a time estimate once 10% is done', () {
      final p = HistoryProgress.from(
          SdkSyncStatus(isFullExport: true, hasResumableSession: true, completedTypes: 5, sentCount: 120000, createdAt: t0),
          20, t0.add(const Duration(minutes: 10)));
      expect(p.running, isTrue);
      expect(p.done, isFalse);
      expect(p.percent, 25);
      expect(p.typesDone, 5);
      expect(p.eta, const Duration(minutes: 30)); // 10 min for 25 % → 30 min for the rest
      expect(etaLabel(p.eta), 'about 30 min');
    });

    test('no estimate at the very start; never 100 % before the SDK says so', () {
      final start = HistoryProgress.from(SdkSyncStatus(isFullExport: true, createdAt: t0), 20, t0.add(const Duration(minutes: 1)));
      expect(start.percent, 0);
      expect(start.eta, isNull);
      expect(etaLabel(null), 'estimating');
      final almost = HistoryProgress.from(SdkSyncStatus(isFullExport: true, completedTypes: 25, createdAt: t0), 20, t0);
      expect(almost.percent, 99);
    });

    test('export complete', () {
      final p = HistoryProgress.from(const SdkSyncStatus(initialExportDone: true, sentCount: 9), 20, t0);
      expect(p.done, isTrue);
      expect(p.percent, 100);
    });
  });

  test('report to the server: the choice, and for all history the upload progress and "done"', () {
    final t0 = DateTime(2026, 10, 22);
    final running = HistoryProgress.from(SdkSyncStatus(isFullExport: true, completedTypes: 10, sentCount: 5, createdAt: t0), 20, t0);
    expect(rangeReport(const AppConfig(syncDaysBack: 90), running), {'scope': 'days90'});
    expect(rangeReport(const AppConfig(syncDaysBack: 0), running),
        {'scope': 'all', 'upload_percent': 50, 'sent': 5, 'upload_done': false});
    final done = HistoryProgress.from(const SdkSyncStatus(initialExportDone: true, sentCount: 7), 20, t0);
    expect(rangeReport(const AppConfig(syncDaysBack: 0), done)['upload_done'], isTrue);
  });

  test('reporter: only changes are sent; a report the page did not take is tried again', () async {
    final sent = <Map<String, Object?>>[];
    var pageReady = false;
    final r = RangeReporter((m) async {
      if (!pageReady) return false;
      sent.add(m);
      return true;
    });
    expect(await r.maybeSend({'scope': 'all', 'upload_percent': 10}), isFalse);
    pageReady = true;
    expect(await r.maybeSend({'scope': 'all', 'upload_percent': 10}), isTrue);
    expect(await r.maybeSend({'scope': 'all', 'upload_percent': 10}), isFalse);
    expect(await r.maybeSend({'scope': 'all', 'upload_percent': 20}), isTrue);
    expect(sent.length, 2);
  });

  testWidgets('picker: two choices, tapping the other one reports it', (t) async {
    final picked = <int>[];
    await t.pumpWidget(MaterialApp(home: Scaffold(body: RangePicker(syncDaysBack: 90, onChanged: picked.add))));
    expect(find.text('Last 90 days'), findsOneWidget);
    expect(find.text('All history'), findsOneWidget);
    await t.tap(find.text('Last 90 days'));
    expect(picked, isEmpty, reason: 'already chosen');
    await t.tap(find.text('All history'));
    expect(picked, [kAllHistory]);
  });

  testWidgets('progress card: percent, records, types, time left', (t) async {
    final p = HistoryProgress.from(
        SdkSyncStatus(isFullExport: true, completedTypes: 5, sentCount: 123456, createdAt: DateTime(2026, 10, 22, 7)),
        20, DateTime(2026, 10, 22, 7, 10));
    await t.pumpWidget(MaterialApp(home: Scaffold(body: HistoryProgressCard(p))));
    expect(find.text('Reading all history'), findsOneWidget);
    expect(find.text('25%'), findsOneWidget);
    expect(find.textContaining('123,456 records · 5/20 types · about 30 min left'), findsOneWidget);
  });
}
