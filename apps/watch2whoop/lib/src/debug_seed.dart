// Simulator debugging only: writes a batch of test data to the simulator Health app (the native code is under #if DEBUG && simulator in AppDelegate,
// not compiled into release or device builds; calls then return "unsupported"). Test data carries metadata W2WTestData=1, and weight 99.9 kg makes it easy to spot.
import 'package:flutter/services.dart';

const _channel = MethodChannel('watch2whoop/debug');

/// Runs the history backfill's background-task body once (Backfill.swift runSlices); simulator debug builds only.
Future<String> runDebugBackfillTask() async {
  try {
    return '${await _channel.invokeMethod<bool>('runBackfillTask')}';
  } on MissingPluginException {
    return 'not available in this build';
  } on PlatformException catch (e) {
    return 'failed: ${e.message}';
  }
}

Future<String> seedHealthTestData() async {
  try {
    final r = await _channel.invokeMethod<String>('seedHealthData');
    return r ?? 'ok';
  } on MissingPluginException {
    return 'not available in this build';
  } on PlatformException catch (e) {
    return 'failed: ${e.message}';
  }
}
