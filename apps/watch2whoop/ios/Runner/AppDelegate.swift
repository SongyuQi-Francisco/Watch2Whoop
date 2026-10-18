import Flutter
import UIKit
import OpenWearablesHealthSDK
import open_wearables_health_sdk
#if DEBUG && targetEnvironment(simulator)
import HealthKit
#endif

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    // Start the OW SDK natively first, without waiting for Dart:
    // - the SDK registers its BGTask during initialization, which must happen before launch finishes;
    // - when woken by a HealthKit background push (no UI, Dart may not be running) it must re-attach observers and continue syncing.
    // Start the OW SDK natively: BGTask handlers must be registered before launch finishes,
    // and background launches (HealthKit delivery) must restore observers without waiting for Dart.
    let sdk = OpenWearablesHealthSDK.shared
    sdk.logLevel = .always
    SyncLogBuffer.shared.attach(to: sdk)
    let creds = sdk.getStoredCredentials()
    if let host = (creds["host"] ?? nil) as? String, !host.isEmpty, sdk.isSyncActive {
      sdk.configure(host: host)
    }
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    // Plugin registration replaces the SDK onLog, so wrap it again here and logs still reach the buffer.
    SyncLogBuffer.shared.attach(to: OpenWearablesHealthSDK.shared)

    guard let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "Watch2WhoopNative") else { return }
    let native = FlutterMethodChannel(name: "watch2whoop/native", binaryMessenger: registrar.messenger())
    native.setMethodCallHandler { call, result in
      switch call.method {
      case "drainLog": result(SyncLogBuffer.shared.drain())
      case "takeLinks": result(LinkInbox.shared.take())
      default: result(FlutterMethodNotImplemented)
      }
    }
    // watch2whoop://setup?… (opened by scanning the setup QR code with the camera): store it first, Dart picks it up later
    LinkInbox.shared.channel = native
    registrar.addSceneDelegate(LinkInbox.shared)
    #if DEBUG && targetEnvironment(simulator)
    let debug = FlutterMethodChannel(name: "watch2whoop/debug", binaryMessenger: registrar.messenger())
    debug.setMethodCallHandler { call, result in
      if call.method == "seedHealthData", #available(iOS 16.0, *) {
        DebugHealthSeeder.seed { result($0) }
      } else {
        result(FlutterMethodNotImplemented)
      }
    }
    #endif
  }

  override func application(
    _ application: UIApplication,
    handleEventsForBackgroundURLSession identifier: String,
    completionHandler: @escaping () -> Void
  ) {
    OpenWearablesHealthSdkPlugin.setBackgroundCompletionHandler(completionHandler)
  }
}

/// Receives watch2whoop:// links (cold start: in connectionOptions of willConnect; while running: via openURLContexts).
final class LinkInbox: NSObject, FlutterSceneLifeCycleDelegate {
  static let shared = LinkInbox()
  var channel: FlutterMethodChannel?  // strong reference: nobody else holds the channel object
  private var pending: [String] = []

  func scene(_ scene: UIScene, willConnectTo session: UISceneSession,
             options connectionOptions: UIScene.ConnectionOptions?) -> Bool {
    connectionOptions?.urlContexts.forEach { receive($0.url) }
    return false
  }

  func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) -> Bool {
    var handled = false
    for c in URLContexts where c.url.scheme == "watch2whoop" {
      receive(c.url)
      handled = true
    }
    return handled
  }

  private func receive(_ url: URL) {
    guard url.scheme == "watch2whoop" else { return }
    pending.append(url.absoluteString)
    channel?.invokeMethod("linksPending", arguments: nil)
  }

  func take() -> [String] {
    defer { pending.removeAll() }
    return pending
  }
}

/// Local buffer for SDK logs: Dart may not be running during background sync, so logs are stored here and Dart collects them on app open to count entries per type.
/// Stored on the device only (UserDefaults), at most 3000 lines.
/// Native buffer for SDK log lines so background syncs are counted even when Dart is not running.
final class SyncLogBuffer {
  static let shared = SyncLogBuffer()
  private let key = "w2w.nativeSyncLog"
  private let maxLines = 3000
  private let queue = DispatchQueue(label: "watch2whoop.synclog")
  private let formatter = ISO8601DateFormatter()

  func attach(to sdk: OpenWearablesHealthSDK) {
    let forward = sdk.onLog
    sdk.onLog = { [weak self] message in
      self?.append(message)
      forward?(message)
    }
  }

  func append(_ message: String) {
    let line = "\(formatter.string(from: Date()))\t\(message)"
    queue.async {
      var lines = UserDefaults.standard.stringArray(forKey: self.key) ?? []
      lines.append(line)
      if lines.count > self.maxLines { lines.removeFirst(lines.count - self.maxLines) }
      UserDefaults.standard.set(lines, forKey: self.key)
    }
  }

  /// Takes all buffered logs ("ISO8601 time\tmessage").
  func drain() -> [String] {
    queue.sync {
      let lines = UserDefaults.standard.stringArray(forKey: key) ?? []
      UserDefaults.standard.removeObject(forKey: key)
      return lines
    }
  }
}

#if DEBUG && targetEnvironment(simulator)
/// Simulator debug builds only: writes a batch of easily recognizable test data to Health (metadata W2WTestData=1, weight 99.9 kg).
@available(iOS 16.0, *)
enum DebugHealthSeeder {
  static let store = HKHealthStore()

  static func seed(_ done: @escaping (String) -> Void) {
    let q = { (id: HKQuantityTypeIdentifier) in HKQuantityType(id) }
    let types: Set<HKSampleType> = [
      q(.heartRate), q(.restingHeartRate), q(.heartRateVariabilitySDNN), q(.stepCount),
      q(.activeEnergyBurned), q(.oxygenSaturation), q(.respiratoryRate), q(.bodyMass),
      HKCategoryType(.sleepAnalysis), HKObjectType.workoutType(),
    ]
    store.requestAuthorization(toShare: types, read: nil) { ok, error in
      guard ok else { DispatchQueue.main.async { done("auth failed: \(error?.localizedDescription ?? "-")") }; return }
      let now = Date()
      let meta: [String: Any] = ["W2WTestData": 1]
      var samples: [HKSample] = []
      func qs(_ id: HKQuantityTypeIdentifier, _ unit: HKUnit, _ v: Double, _ start: Date, _ end: Date? = nil) {
        samples.append(HKQuantitySample(type: q(id), quantity: HKQuantity(unit: unit, doubleValue: v),
                                        start: start, end: end ?? start, metadata: meta))
      }
      let bpm = HKUnit.count().unitDivided(by: .minute())
      // One heart rate sample every 10 minutes for the last 24 hours
      for i in 0..<144 {
        let t = now.addingTimeInterval(-Double(i) * 600)
        qs(.heartRate, bpm, 58 + Double((i * 7) % 30), t)
      }
      for d in 0..<3 {
        let day = now.addingTimeInterval(-Double(d) * 86400)
        qs(.restingHeartRate, bpm, 55 + Double(d), day.addingTimeInterval(-3600))
        qs(.heartRateVariabilitySDNN, .secondUnit(with: .milli), 48 + Double(d * 3), day.addingTimeInterval(-7200))
        qs(.oxygenSaturation, .percent(), 0.97, day.addingTimeInterval(-10800))
        qs(.respiratoryRate, bpm, 14.5, day.addingTimeInterval(-10800))
      }
      for h in 1...12 {
        let s = now.addingTimeInterval(-Double(h) * 3600)
        qs(.stepCount, .count(), Double(300 + h * 25), s, s.addingTimeInterval(3000))
        qs(.activeEnergyBurned, .kilocalorie(), Double(20 + h), s, s.addingTimeInterval(3000))
      }
      qs(.bodyMass, .gramUnit(with: .kilo), 99.9, now.addingTimeInterval(-1800))
      // Last night sleep: from 01:30, alternating core / deep / REM / awake
      let cal = Calendar.current
      let bed = cal.date(bySettingHour: 1, minute: 30, second: 0, of: now)!.addingTimeInterval(now < cal.date(bySettingHour: 9, minute: 0, second: 0, of: now)! ? -86400 : 0)
      let stages: [(HKCategoryValueSleepAnalysis, Double)] = [
        (.asleepCore, 50), (.asleepDeep, 40), (.asleepREM, 25), (.awake, 5),
        (.asleepCore, 60), (.asleepDeep, 30), (.asleepREM, 35), (.asleepCore, 50), (.awake, 5),
      ]
      var t = bed
      for (stage, minutes) in stages {
        let end = t.addingTimeInterval(minutes * 60)
        samples.append(HKCategorySample(type: HKCategoryType(.sleepAnalysis), value: stage.rawValue, start: t, end: end, metadata: meta))
        t = end
      }
      // A 50-minute strength workout yesterday at 18:00
      let ws = cal.date(bySettingHour: 18, minute: 0, second: 0, of: now.addingTimeInterval(-86400))!
      let cfg = HKWorkoutConfiguration()
      cfg.activityType = .traditionalStrengthTraining
      let builder = HKWorkoutBuilder(healthStore: store, configuration: cfg, device: nil)
      store.save(samples) { ok, error in
        guard ok else { DispatchQueue.main.async { done("save failed: \(error?.localizedDescription ?? "-")") }; return }
        builder.beginCollection(withStart: ws) { _, _ in
          let kcal = HKQuantitySample(type: q(.activeEnergyBurned), quantity: HKQuantity(unit: .kilocalorie(), doubleValue: 260),
                                      start: ws, end: ws.addingTimeInterval(3000), metadata: meta)
          builder.add([kcal]) { _, _ in
            builder.addMetadata(meta) { _, _ in
              builder.endCollection(withEnd: ws.addingTimeInterval(3000)) { _, _ in
                builder.finishWorkout { w, error in
                  DispatchQueue.main.async {
                    done("saved \(samples.count) samples + \(w == nil ? 0 : 1) workout\(error.map { " (\($0.localizedDescription))" } ?? "")")
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
#endif
