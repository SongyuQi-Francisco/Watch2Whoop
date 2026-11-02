import Flutter
import Foundation

/// Async front for the copied sync files (byte-for-byte copies of the Flutter app's, scripts/sync_flutter_sources.sh).
/// - SyncEngine.swift and Backfill.swift have had a native API since #344 (no Flutter, no Open Wearables): `engine` /
///   `backfill` below wrap their callbacks into `await`.
/// - RawSync.swift (beat-to-beat series, ECGs, drinks; permission sheet; newest sample per type) still speaks the method-
///   channel shape `handle(FlutterMethodCall, result)`, so it goes through `raw(_:_:)` and the FlutterShim package until
///   the Flutter original is split the same way (then the shim goes, docs/ios-native-app.md section 2.3).
enum HealthSyncBridge {
  struct Failure: Error, Equatable {
    let code: String
    let message: String?
    /// FlutterError.details as text (RawSync puts HealthKit's error code there: "5" = never asked).
    var details: String? = nil
  }

  // MARK: - RawSync (method-channel shape)

  /// Calls one RawSync method (`rawRead`, `rawCommit`, `rawRequestAuth`, `healthLatest` …). A FlutterError becomes `Failure`.
  static func raw(_ method: String, _ args: [String: Any] = [:]) async throws -> Any? {
    try await withCheckedThrowingContinuation { cont in
      let call = FlutterMethodCall(methodName: method, arguments: args)
      let done: FlutterResult = { out in
        if let e = out as? FlutterError {
          cont.resume(throwing: Failure(code: e.code, message: e.message, details: e.details.map { "\($0)" }))
        } else {
          cont.resume(returning: out)
        }
      }
      if !RawSync.shared.handle(call, done) { cont.resume(throwing: Failure(code: "unknown_method", message: method)) }
    }
  }

  // MARK: - SyncEngine (native API)

  enum Engine {
    static func configure(_ args: [String: Any]) async -> [String: Any] {
      await withCheckedContinuation { c in SyncEngine.shared.configureAsync(args) { c.resume(returning: $0) } }
    }

    static func run(_ args: [String: Any], trigger: String) async -> [String: Any] {
      await withCheckedContinuation { c in SyncEngine.shared.runForeground(args, trigger: trigger) { c.resume(returning: $0) } }
    }

    static func status() async -> [String: Any] {
      await withCheckedContinuation { c in SyncEngine.shared.status { c.resume(returning: $0) } }
    }

    static func startRange(days: Int) async -> [String: Any]? {
      await withCheckedContinuation { c in SyncEngine.shared.startRange(days: days) { c.resume(returning: $0) } }
    }

    static func reset() async {
      await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in SyncEngine.shared.reset { c.resume() } }
    }
  }

  // MARK: - Backfill (native API)

  enum History {
    static func start(_ args: [String: Any]) async -> [String: Any]? {
      await withCheckedContinuation { c in Backfill.shared.start(args) { c.resume(returning: $0) } }
    }

    static func status() async -> [String: Any] {
      await withCheckedContinuation { c in Backfill.shared.status { c.resume(returning: $0) } }
    }

    static func pump(seconds: Double) async -> [String: Any] {
      await withCheckedContinuation { c in Backfill.shared.pump(seconds: seconds) { c.resume(returning: $0) } }
    }

    static func cancel() async -> [String: Any] {
      await withCheckedContinuation { c in Backfill.shared.cancel { c.resume(returning: $0) } }
    }
  }
}
