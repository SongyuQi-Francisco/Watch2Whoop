import Flutter
import Foundation
import OpenWearablesHealthSDK

/// The Flutter side of the app's own sync engine (SyncEngine.swift) and the history backfill (Backfill.swift): a thin
/// layer over their APIs on the channel watch2whoop/native, plus what the host does on the mode `app` (stop Open
/// Wearables). SyncEngine.swift and Backfill.swift import neither Flutter nor Open Wearables (#340 reuses them).
///
/// Methods: engineConfigure / engineRun {server, token, appVersion, daysBack, engine, registry, trigger} /
/// engineStatus / engineSetCellular {on} / engineRangeStart {days} / engineRangeCancel / engineRearm / engineStopOw /
/// engineReset / engineDebugRound {trigger} (debug builds); backfillStart / backfillStatus / backfillPump {seconds} /
/// backfillCancel.
enum SyncEngineChannel {
  /// Connects the engine to the host: in the mode `app` Open Wearables stops (contract §7).
  static func attach() {
    SyncEngine.shared.onModeApp = { stopOpenWearables() }
  }

  /// OW's stopBackgroundSync disables background delivery of its types and cancels *every* BGTask request of the app, so
  /// the engine (and Backfill) arm themselves again right after.
  static func stopOpenWearables() {
    let sdk = OpenWearablesHealthSDK.shared
    if sdk.isSyncActive { sdk.stopBackgroundSync() }
  }

  /// false = not one of these methods.
  static func handle(_ call: FlutterMethodCall, _ result: @escaping FlutterResult) -> Bool {
    let args = call.arguments as? [String: Any] ?? [:]
    let engine = SyncEngine.shared, backfill = Backfill.shared
    switch call.method {
    case "engineConfigure":
      engine.configureAsync(args) { result($0) }
    case "engineRun":
      engine.runForeground(args, trigger: args["trigger"] as? String ?? "foreground") { result($0) }
    case "engineStatus":
      engine.status { result($0) }
    case "engineSetCellular":
      engine.setCellularHistory(args["on"] as? Bool ?? false)
      result(nil)
    case "engineRangeStart":
      engine.startRange(days: args["days"] as? Int ?? 0) { r in
        result(r ?? FlutterError(code: "off", message: "engine off", details: nil))
      }
    case "engineRangeCancel":
      engine.cancelRange()
      result(nil)
    case "engineRearm":
      engine.rearmAsync()
      result(nil)
    case "engineStopOw":
      stopOpenWearables()
      engine.rearmAsync()
      result(nil)
    case "engineReset":
      engine.reset { result(nil) }
    #if DEBUG
    case "engineDebugRound":
      engine.runBackgroundStyle(trigger: args["trigger"] as? String ?? "debug") { result($0) }
    #endif
    case "backfillStart":
      backfill.start(args) { r in
        DispatchQueue.main.async { result(r ?? FlutterError(code: "bad_args", message: "server, token, types", details: nil)) }
      }
    case "backfillStatus":
      backfill.status { result($0) }
    case "backfillPump":
      backfill.pump(seconds: args["seconds"] as? Double ?? 20) { s in DispatchQueue.main.async { result(s) } }
    case "backfillCancel":
      backfill.cancel { result($0) }
    default:
      return false
    }
    return true
  }
}
