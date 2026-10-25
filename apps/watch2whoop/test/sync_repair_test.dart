// Automatic sync repair after Open Wearables rejected the phone (401): when it runs, backoff, at most 3 a day, kept across
// restarts; and the watch2whoop://setup link the Today page opens (only its sync part is taken).
import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/sync_repair.dart';

class _Fake {
  DateTime now = DateTime(2026, 10, 25, 8, 0);
  bool needs = true, can = true;
  RepairOutcome outcome = RepairOutcome.failed;
  String? stored;
  int repairs = 0;

  SyncAutoRepair make() => SyncAutoRepair(
        needsRepair: () => needs,
        canRepair: () => can,
        repair: () async {
          repairs++;
          return outcome;
        },
        load: () async => stored,
        save: (v) async => stored = v,
        now: () => now,
      );
}

void main() {
  test('nothing to repair, or not possible now: no attempt is used up', () async {
    final f = _Fake()..needs = false;
    final r = f.make();
    expect(await r.trigger(), isNull);
    f
      ..needs = true
      ..can = false;
    expect(await r.trigger(), isNull);
    expect(f.repairs, 0);
    expect(f.stored, isNull);
  });

  test('401: repairs at once, then backs off, at most 3 a day', () async {
    final f = _Fake();
    final r = f.make();
    expect(await r.trigger(), RepairOutcome.failed);
    expect(await r.trigger(), isNull); // within 15 minutes
    f.now = f.now.add(const Duration(minutes: 16));
    expect(await r.trigger(), RepairOutcome.failed);
    f.now = f.now.add(const Duration(hours: 1));
    expect(await r.trigger(), isNull); // the third waits 2 hours
    f.now = f.now.add(const Duration(hours: 1, minutes: 1));
    expect(await r.trigger(), RepairOutcome.failed);
    f.now = f.now.add(const Duration(hours: 5));
    expect(await r.trigger(), isNull); // 3 today
    expect(f.repairs, 3);
    // the next day starts again
    f.now = DateTime(2026, 10, 26, 0, 5);
    expect(await r.trigger(), RepairOutcome.failed);
    expect(f.repairs, 4);
  });

  test('the attempts survive a restart; a thrown repair counts as failed', () async {
    final f = _Fake();
    await f.make().trigger();
    final again = SyncAutoRepair(
      needsRepair: () => true,
      canRepair: () => true,
      repair: () async => throw StateError('network'),
      load: () async => f.stored,
      save: (v) async => f.stored = v,
      now: () => f.now.add(const Duration(minutes: 5)),
    );
    expect(await again.trigger(), isNull); // a new process still waits for the backoff
    expect((await again.attemptsToday()).length, 1);
    final later = SyncAutoRepair(
      needsRepair: () => true,
      canRepair: () => true,
      repair: () async => throw StateError('network'),
      load: () async => f.stored,
      save: (v) async => f.stored = v,
      now: () => f.now.add(const Duration(minutes: 20)),
    );
    expect(await later.trigger(), RepairOutcome.failed);
  });

  test('a broken store does not block the repair', () async {
    final f = _Fake()
      ..stored = 'not json'
      ..outcome = RepairOutcome.connected;
    expect(await f.make().trigger(), RepairOutcome.connected);
  });

  test('one at a time', () async {
    final f = _Fake();
    late final SyncAutoRepair r;
    RepairOutcome? inner;
    r = SyncAutoRepair(
      needsRepair: () => true,
      canRepair: () => true,
      repair: () async {
        inner = await r.trigger() ?? RepairOutcome.notPossible; // a stats change while repairing
        return RepairOutcome.connected;
      },
      load: () async => f.stored,
      save: (v) async => f.stored = v,
      now: () => f.now,
    );
    expect(await r.trigger(), RepairOutcome.connected);
    expect(inner, RepairOutcome.notPossible);
  });

  group('pageSetupLink', () {
    test('takes the OW address and the code only', () {
      final s = pageSetupLink('watch2whoop://setup?ow=https%3A%2F%2Fsync.example.org&code=abcd2345&today=https://evil.example&login=ABCDEFGHJKLM');
      expect(s, isNotNull);
      expect(s!.code, 'ABCD2345');
      expect(s.owHost, 'https://sync.example.org');
      expect(s.todayUrl, isNull);
      expect(s.loginCode, isNull);
      expect(pageSetupLink('watch2whoop://setup?code=ABCD2345')!.owHost, isNull);
    });
    test('no code or another link: nothing', () {
      expect(pageSetupLink('watch2whoop://setup?ow=https://sync.example.org'), isNull);
      expect(pageSetupLink('watch2whoop://sync'), isNull);
      expect(pageSetupLink('https://example.org/?code=ABCD2345'), isNull);
    });
  });
}
