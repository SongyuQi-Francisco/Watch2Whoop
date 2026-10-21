// Sync on open (issue: fresh data after waking up): one round per open / resume, throttled, then the Today page is told.
import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/sync_on_open.dart';

class _Fake {
  DateTime now = DateTime(2026, 10, 22, 7, 40);
  bool can = true;
  bool sdkSyncing = false;
  int roundTicks = 0; // how many status checks a started round stays "syncing"
  int rounds = 0, done = 0;
  final List<String> log = [];

  late final SyncOnOpen s = SyncOnOpen(
    canSync: () => can,
    startRound: () async {
      rounds++;
      log.add('start');
    },
    isSyncing: () async {
      now = now.add(const Duration(seconds: 1)); // each status check takes a moment (the fake clock moves)
      if (roundTicks > 0) {
        roundTicks--;
        return true;
      }
      return sdkSyncing;
    },
    onDone: () async {
      done++;
      log.add('done');
    },
    now: () => now,
    pollEvery: Duration.zero,
    startWait: const Duration(seconds: 6),
  );
}

void main() {
  test('open: starts a round, waits until it is over, then tells the page', () async {
    final f = _Fake();
    f.roundTicks = 0;
    // the round shows up as "syncing" for 3 checks after startRound
    final s = SyncOnOpen(
      canSync: () => true,
      startRound: () async {
        f.rounds++;
        f.roundTicks = 3;
      },
      isSyncing: () async => f.roundTicks-- > 0,
      onDone: () async => f.done++,
      now: () => f.now,
      pollEvery: Duration.zero,
    );
    expect(await s.trigger(), isTrue);
    expect(f.rounds, 1);
    expect(f.done, 1);
    expect(f.roundTicks, lessThanOrEqualTo(0), reason: 'onDone only after the round reported idle');
  });

  test('throttled: not again within 3 minutes; again after', () async {
    final f = _Fake();
    final t0 = f.now;
    expect(await f.s.trigger(), isTrue);
    f.now = t0.add(const Duration(minutes: 2, seconds: 59));
    expect(await f.s.trigger(), isFalse);
    expect(f.rounds, 1);
    f.now = f.now.add(const Duration(seconds: 2));
    expect(await f.s.trigger(), isTrue);
    expect(f.rounds, 2);
    expect(f.done, 2);
  });

  test('not while a round is running (two resumes at once)', () async {
    final f = _Fake();
    final first = f.s.trigger();
    expect(f.s.running, isTrue);
    expect(await f.s.trigger(), isFalse);
    expect(await first, isTrue);
    expect(f.rounds, 1);
  });

  test('the SDK is already uploading (woken in the background): no second round, wait for it', () async {
    final f = _Fake()..sdkSyncing = true;
    final t = f.s.trigger();
    await Future<void>.delayed(Duration.zero);
    f.sdkSyncing = false;
    expect(await t, isTrue);
    expect(f.rounds, 0);
    expect(f.done, 1);
  });

  test('nothing to sync (not connected / Health never asked): no round, no call', () async {
    final f = _Fake()..can = false;
    expect(await f.s.trigger(), isFalse);
    expect(f.rounds, 0);
    expect(f.done, 0);
  });

  test('a failing round does not block the next open', () async {
    var n = 0;
    var now = DateTime(2026, 10, 22, 7, 40);
    final s = SyncOnOpen(
      canSync: () => true,
      startRound: () async {
        n++;
        if (n == 1) throw StateError('SDK');
      },
      isSyncing: () async => false,
      onDone: () async {},
      now: () => now,
      pollEvery: Duration.zero,
      startWait: Duration.zero,
    );
    expect(await s.trigger(), isFalse);
    expect(s.running, isFalse);
    now = now.add(const Duration(minutes: 4));
    expect(await s.trigger(), isTrue);
  });
}
