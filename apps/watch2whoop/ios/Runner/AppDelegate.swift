import Flutter
import UIKit
import UserNotifications
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
    // Push notifications (#175): set before launch finishes so a tap that cold-starts the app reaches didReceive
    UNUserNotificationCenter.current().delegate = self
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
      default:
        // raw*: beat-to-beat HRV series and ECG read for Body Engine (RawSync.swift)
        if !RawSync.shared.handle(call, result) { result(FlutterMethodNotImplemented) }
      }
    }
    // watch2whoop://setup?… (opened by scanning the setup QR code with the camera): store it first, Dart picks it up later
    LinkInbox.shared.channel = native
    registrar.addSceneDelegate(LinkInbox.shared)
    // Push notifications (#175): permission, the APNs device token, taps (PushBridge below)
    let push = FlutterMethodChannel(name: "watch2whoop/push", binaryMessenger: registrar.messenger())
    PushBridge.shared.channel = push
    push.setMethodCallHandler { call, result in PushBridge.shared.handle(call, result) }
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

  override func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
    PushBridge.shared.received(token: deviceToken)
    super.application(application, didRegisterForRemoteNotificationsWithDeviceToken: deviceToken)
  }

  override func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
    PushBridge.shared.channel?.invokeMethod("error", arguments: error.localizedDescription)
    super.application(application, didFailToRegisterForRemoteNotificationsWithError: error)
  }

  // A notification while the app is open: show it as a banner too, and let the page refresh its unread count
  override func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                       withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
    PushBridge.shared.channel?.invokeMethod("received", arguments: nil)
    completionHandler([.banner, .list, .sound, .badge])
  }

  // Tapped: open its page in the Today page (the payload's "url", a path on the server)
  override func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                       withCompletionHandler completionHandler: @escaping () -> Void) {
    PushBridge.shared.opened(response.notification.request.content.userInfo)
    completionHandler()
  }
}

/// Push notifications for Dart (lib/src/push.dart) over the channel "watch2whoop/push":
/// status / request / register / takeOpened / setBadge; calls back token / error / opened / received.
final class PushBridge {
  static let shared = PushBridge()
  var channel: FlutterMethodChannel?
  private var pendingOpened: [String] = []
  private var lastToken: [String: String]?

  func handle(_ call: FlutterMethodCall, _ result: @escaping FlutterResult) {
    switch call.method {
    case "status": status { result($0) }
    case "request":
      // iOS shows its dialog only while the user never decided; afterwards this just answers granted / denied
      UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { granted, _ in
        DispatchQueue.main.async {
          if granted { UIApplication.shared.registerForRemoteNotifications() }
          self.status { result($0) }
        }
      }
    case "register":
      // Already allowed: ask APNs for the (possibly new) device token again; it arrives through "token"
      status { s in
        if s == "granted" { UIApplication.shared.registerForRemoteNotifications() }
        result(s)
      }
    case "takeToken": result(lastToken)
    case "takeOpened":
      result(pendingOpened)
      pendingOpened.removeAll()
    case "setBadge":
      let n = (call.arguments as? Int) ?? 0
      if #available(iOS 16.0, *) {
        UNUserNotificationCenter.current().setBadgeCount(n) { _ in }
      } else {
        UIApplication.shared.applicationIconBadgeNumber = n
      }
      result(nil)
    default: result(FlutterMethodNotImplemented)
    }
  }

  func status(_ done: @escaping (String) -> Void) {
    UNUserNotificationCenter.current().getNotificationSettings { s in
      let v: String
      switch s.authorizationStatus {
      case .authorized, .provisional, .ephemeral: v = "granted"
      case .denied: v = "denied"
      default: v = "unknown"
      }
      DispatchQueue.main.async { done(v) }
    }
  }

  func received(token: Data) {
    let t = ["token": token.map { String(format: "%02x", $0) }.joined(), "environment": PushBridge.environment()]
    lastToken = t
    channel?.invokeMethod("token", arguments: t)
  }

  func opened(_ userInfo: [AnyHashable: Any]) {
    pendingOpened.append((userInfo["url"] as? String) ?? "/app/#notifications")
    channel?.invokeMethod("opened", arguments: nil)
  }

  /// APNs environment of this build: Xcode / development builds embed a provisioning profile whose aps-environment is
  /// "development" (sandbox); TestFlight and App Store builds have none, ad hoc ones say "production".
  static func environment() -> String {
    guard let path = Bundle.main.path(forResource: "embedded", ofType: "mobileprovision"),
          let data = FileManager.default.contents(atPath: path),
          let text = String(data: data, encoding: .isoLatin1),
          let start = text.range(of: "<?xml"), let end = text.range(of: "</plist>") else { return "production" }
    let xml = Data(text[start.lowerBound..<end.upperBound].utf8)
    guard let plist = try? PropertyListSerialization.propertyList(from: xml, format: nil) as? [String: Any],
          let ent = plist["Entitlements"] as? [String: Any],
          let env = ent["aps-environment"] as? String else { return "production" }
    return env == "development" ? "sandbox" : "production"
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
    let extra = writableRegistry()
    let types: Set<HKSampleType> = Set<HKSampleType>([
      q(.heartRate), q(.restingHeartRate), q(.heartRateVariabilitySDNN), q(.stepCount),
      q(.activeEnergyBurned), q(.oxygenSaturation), q(.respiratoryRate), q(.bodyMass), q(.dietaryWater),
      HKCategoryType(.sleepAnalysis), HKObjectType.workoutType(), HKSeriesType.heartbeat(),
    ]).union(extra.map { $0.type })
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
      // Two drinks today (330 mL, 0.5 L), for RawSync's water upload (#135)
      qs(.dietaryWater, .literUnit(with: .milli), 330, now.addingTimeInterval(-5400))
      qs(.dietaryWater, .liter(), 0.5, now.addingTimeInterval(-2700))
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
                  heartbeats(now) { n in
                    registry(extra, now) { r in
                      DispatchQueue.main.async {
                        done("saved \(samples.count) samples + \(w == nil ? 0 : 1) workout + \(n) heartbeat series\(error.map { " (\($0.localizedDescription))" } ?? ""); \(r)")
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
  }

  /// The quantity types RawSync reads itself (lib/src/hk_types.dart), each with a plausible value in its registry unit.
  static let registryValues: [(id: String, unit: String, value: Double)] = [
    ("HKQuantityTypeIdentifierAppleStandTime", "min", 6),
    ("HKQuantityTypeIdentifierAppleExerciseTime", "min", 12),
    ("HKQuantityTypeIdentifierTimeInDaylight", "min", 18),
    ("HKQuantityTypeIdentifierPhysicalEffort", "kcal/hr·kg", 3.2),
    ("HKQuantityTypeIdentifierAppleSleepingWristTemperature", "degC", 34.6),
    ("HKQuantityTypeIdentifierEnvironmentalAudioExposure", "dBASPL", 64),
    ("HKQuantityTypeIdentifierHeadphoneAudioExposure", "dBASPL", 71),
    ("HKQuantityTypeIdentifierWalkingHeartRateAverage", "count/min", 96),
    ("HKQuantityTypeIdentifierStairAscentSpeed", "m/s", 0.42),
    ("HKQuantityTypeIdentifierStairDescentSpeed", "m/s", 0.51),
    ("HKQuantityTypeIdentifierAppleWalkingSteadiness", "%", 0.88),
  ]

  /// The registry types this simulator lets an app write. The Apple-computed ones (stand / exercise minutes, wrist
  /// temperature, walking heart rate, walking steadiness …) are read-only: asking to share them raises an Objective-C
  /// exception, so each is probed on its own first (no sheet) and left out.
  static func writableRegistry() -> [(type: HKQuantityType, unit: HKUnit, value: Double)] {
    registryValues.compactMap { r in
      guard let t = HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: r.id)) else { return nil }
      let answered = DispatchSemaphore(value: 0)
      var failed = false
      let raised = W2WCatchException {
        store.getRequestStatusForAuthorization(toShare: [t], read: []) { _, error in
          failed = error != nil
          answered.signal()
        }
      }
      if raised != nil || answered.wait(timeout: .now() + 2) == .timedOut || failed { return nil }
      return (t, HKUnit(from: r.unit), r.value)
    }
  }

  /// Two samples a day of each writable registry type for the last 3 days, the newest one hour ago (the sync center's
  /// "on the phone" column), saved per type so a type HealthKit refuses does not hold back the others.
  static func registry(_ types: [(type: HKQuantityType, unit: HKUnit, value: Double)], _ now: Date,
                       _ done: @escaping (String) -> Void) {
    let group = DispatchGroup()
    let lock = NSLock()
    var saved: [String] = [], failed: [String] = []
    for (type, unit, value) in types {
      var samples: [HKQuantitySample] = []
      for d in 0..<3 {
        for (k, hours) in [1.0, 3.0].enumerated() {
          let start = now.addingTimeInterval(-Double(d) * 86400 - hours * 3600)
          let v = value * (1 + 0.05 * Double(d) - 0.02 * Double(k))
          samples.append(HKQuantitySample(type: type, quantity: HKQuantity(unit: unit, doubleValue: v), start: start,
                                          end: start.addingTimeInterval(600), metadata: ["W2WTestData": 1]))
        }
      }
      let name = type.identifier.replacingOccurrences(of: "HKQuantityTypeIdentifier", with: "")
      group.enter()
      store.save(samples) { ok, error in
        lock.lock()
        if ok { saved.append(name) } else { failed.append("\(name) (\(error?.localizedDescription ?? "-"))") }
        lock.unlock()
        group.leave()
      }
    }
    group.notify(queue: .global()) {
      let readOnly = registryValues.count - types.count
      done("registry types: \(saved.count) saved [\(saved.sorted().joined(separator: ", "))], \(readOnly) read-only"
           + (failed.isEmpty ? "" : ", failed: \(failed.joined(separator: "; "))"))
    }
  }

  /// Three beat-to-beat series (60 beats, RR alternating 980 / 1020 ms, RMSSD 40 ms) 1–3 hours ago, for RawSync.
  static func heartbeats(_ now: Date, _ done: @escaping (Int) -> Void) {
    func one(_ i: Int, _ n: Int) {
      guard i < 3 else { done(n); return }
      let start = now.addingTimeInterval(-Double(i + 1) * 3600)
      let b = HKHeartbeatSeriesBuilder(healthStore: store, device: nil, start: start)
      var t = 0.0
      func beat(_ k: Int) {
        guard k < 60 else {
          b.addMetadata(["W2WTestData": 1]) { _, _ in
            b.finishSeries { s, _ in one(i + 1, n + (s == nil ? 0 : 1)) }
          }
          return
        }
        b.addHeartbeatWithTimeInterval(sinceSeriesStartDate: t, precededByGap: false) { _, _ in
          t += k % 2 == 0 ? 0.98 : 1.02
          beat(k + 1)
        }
      }
      beat(0)
    }
    one(0, 0)
  }
}
#endif
