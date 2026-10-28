import Flutter
import Foundation

/// Async front for the copied sync files (SyncEngine / RawSync / Backfill, byte-for-byte copies of the Flutter app's,
/// scripts/sync_flutter_sources.sh). They still speak the method-channel shape (`handle(FlutterMethodCall, result)`);
/// this turns that into `await`. N8 (sync center) moves the Dart side (lib/src/sync_engine.dart, raw_sync.dart,
/// backfill.dart: what to read, upload, commit) to Swift on top of this, then gives the three files a native API and
/// removes the shim (docs/ios-native-app.md section 2.3).
enum HealthSyncBridge {
  enum Target { case engine, raw, backfill }

  struct Failure: Error {
    let code: String
    let message: String?
  }

  /// Calls one method (e.g. `.engine, "engineRead", [...]`). Returns the handler's answer; a FlutterError becomes
  /// `Failure`.
  static func call(_ target: Target, _ method: String, _ args: [String: Any] = [:]) async throws -> Any? {
    try await withCheckedThrowingContinuation { cont in
      let call = FlutterMethodCall(methodName: method, arguments: args)
      let done: FlutterResult = { out in
        if let e = out as? FlutterError { cont.resume(throwing: Failure(code: e.code, message: e.message)) }
        else { cont.resume(returning: out) }
      }
      let handled: Bool
      switch target {
      case .engine: handled = SyncEngine.shared.handle(call, done)
      case .raw: handled = RawSync.shared.handle(call, done)
      case .backfill: handled = Backfill.shared.handle(call, done)
      }
      if !handled { cont.resume(throwing: Failure(code: "unknown_method", message: method)) }
    }
  }
}
