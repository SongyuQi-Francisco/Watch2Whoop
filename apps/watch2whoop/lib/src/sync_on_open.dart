// Sync on open: on cold start and every return to the foreground, one incremental sync round (the phone is unlocked then,
// so HealthKit can be read), at most once every [minGap] and never while a round is running. When the round has finished,
// [onDone] runs: the Today page then asks the server to pull the new data and recompute the scores (web/static/sync.js).
// Pure Dart with injected callbacks, so the throttle and the wait are unit-tested (test/sync_on_open_test.dart).
import 'dart:async';

class SyncOnOpen {
  SyncOnOpen({
    required this.canSync,
    required this.startRound,
    required this.isSyncing,
    required this.onDone,
    DateTime Function()? now,
    this.minGap = const Duration(minutes: 3),
    this.pollEvery = const Duration(seconds: 2),
    this.startWait = const Duration(seconds: 6),
    this.maxWait = const Duration(minutes: 3),
  }) : _now = now ?? DateTime.now;

  /// Sync is connected and Health was asked for (nothing to upload otherwise).
  final bool Function() canSync;

  /// Starts one round right away (HealthSync.startSync).
  final Future<void> Function() startRound;

  /// Whether the SDK is in the middle of a round (getSyncStatus).
  final Future<bool> Function() isSyncing;

  /// After the round (or after [maxWait]): tell the Today page.
  final Future<void> Function() onDone;

  final DateTime Function() _now;
  final Duration minGap, pollEvery, startWait, maxWait;

  DateTime? _lastStart;
  bool _running = false;

  bool get running => _running;

  /// Runs a round unless one ran less than [minGap] ago or is still running. Returns whether it ran.
  Future<bool> trigger() async {
    final now = _now();
    if (_running || !canSync()) return false;
    if (_lastStart != null && now.difference(_lastStart!) < minGap) return false;
    _running = true;
    _lastStart = now;
    try {
      if (await isSyncing()) {
        await _waitUntilIdle(); // the SDK is already uploading (e.g. woken in the background): wait for that round
      } else {
        await startRound();
        await _waitForRound();
      }
      await onDone();
      return true;
    } catch (_) {
      return false; // a failed round is visible on the sync page; the next open tries again
    } finally {
      _running = false;
    }
  }

  /// The round may start a moment after startSync and may also be over before the first check.
  Future<void> _waitForRound() async {
    final t0 = _now();
    var started = false;
    while (_now().difference(t0) < startWait) {
      if (await isSyncing()) {
        started = true;
        break;
      }
      await Future<void>.delayed(pollEvery);
    }
    if (started) await _waitUntilIdle();
  }

  Future<void> _waitUntilIdle() async {
    final t0 = _now();
    while (_now().difference(t0) < maxWait && await isSyncing()) {
      await Future<void>.delayed(pollEvery);
    }
  }
}
