import BackgroundTasks
import Compression
import Foundation
import HealthKit

/// History backfill for the app's own sync engine (#219; decision 2026-10-26: the first sync reads only the last 14
/// days, older history is filled in afterwards, in the background, without holding up new data). Dart
/// (lib/src/backfill.dart) starts it with the types the server takes; from then on it runs natively, also while the app
/// is suspended:
/// - it walks back one day at a time from the start of the first-sync window ([to]) to the target ([from]: 90 days ago,
///   or the oldest sample Health allows for "all"); each day is one batch of all types (HKSampleQuery by time range, no
///   anchors), gzip JSON, the engine's columnar format with `phase: "backfill"` (docs/apple-sync-engine-app-contract.md);
/// - each batch is written to a file and uploaded with a **background URLSession** upload task: iOS carries on with the
///   upload when the app is suspended or killed by the system, and relaunches the app in the background when it is done;
/// - the cursor (the oldest day already accepted) moves only after the server answered 200; a batch that failed is sent
///   again with the same batch_id (the server answers the first result again), so an interruption anywhere is safe;
/// - a BGProcessingTask (`watch2whoop.sync.backfill`, network required, power optional) continues while the phone is idle;
///   in the foreground Dart calls `backfillPump` after the newest data went (new data first, history after);
/// - the state (target, cursor, days done, last error) is kept in UserDefaults; the upload token in the Keychain
///   (after first unlock, this device only). Nothing of the health data is logged.
/// Channel watch2whoop/native, methods backfill*: start {server, token, types, days (0 = all), to, appVersion,
/// powerOnly}, status, pump {seconds}, cancel.
final class Backfill: NSObject, URLSessionDataDelegate {
  static let shared = Backfill()
  static let taskId = "watch2whoop.sync.backfill"
  static let sessionId = "watch2whoop.sync.backfill.upload"

  private let store = HKHealthStore()
  private let stateKey = "w2w.backfill.state.v1"
  private let queue = DispatchQueue(label: "watch2whoop.backfill")
  private var responses: [Int: Data] = [:]
  private var building = false
  /// Called by the system when the background session's events were delivered (AppDelegate).
  var sessionCompletion: (() -> Void)?

  private lazy var session: URLSession = {
    let c = URLSessionConfiguration.background(withIdentifier: Self.sessionId)
    c.sessionSendsLaunchEvents = true
    c.isDiscretionary = false
    c.allowsCellularAccess = false            // history goes over Wi-Fi only (contract §5)
    c.timeoutIntervalForRequest = 60
    return URLSession(configuration: c, delegate: self, delegateQueue: nil)
  }()

  // MARK: - state

  struct State: Codable {
    var active = false
    var server = ""
    var appVersion = ""
    var types: [[String: String]] = []      // [{hk, kind, unit}]
    var target = 90                         // days; 0 = all history
    var from: Double = 0                    // oldest bound (UTC seconds)
    var to: Double = 0                      // newest bound = start of the first-sync window
    var cursor: Double = 0                  // everything in [cursor, to) is on the server
    var windowSeconds: Double = 86400
    var pendingBatch: String?               // batch_id of the upload in flight (same id when it is sent again)
    var pendingFile: String?
    var pendingStart: Double = 0
    var batches = 0
    var samples = 0
    var error: String?
    var errorAt: Double = 0
    var startedAt: Double = 0
    var updatedAt: Double = 0
    var done = false
    var powerOnly = false
  }

  private func load() -> State {
    guard let d = UserDefaults.standard.data(forKey: stateKey), let s = try? JSONDecoder().decode(State.self, from: d) else {
      return State()
    }
    return s
  }

  private func save(_ s: State) {
    var s = s
    s.updatedAt = Date().timeIntervalSince1970
    if let d = try? JSONEncoder().encode(s) { UserDefaults.standard.set(d, forKey: stateKey) }
  }

  private func statusMap(_ s: State) -> [String: Any] {
    let total = max(1, s.to - s.from)
    let percent = s.done ? 100 : Int(min(99, max(0, (s.to - s.cursor) / total * 100)))
    return [
      "active": s.active, "done": s.done, "target": s.target, "percent": percent,
      "from": s.from, "to": s.to, "cursor": s.cursor, "batches": s.batches, "samples": s.samples,
      "error": s.error ?? NSNull(), "errorAt": s.errorAt, "startedAt": s.startedAt, "updatedAt": s.updatedAt,
      "uploading": s.pendingBatch != nil, "powerOnly": s.powerOnly,
    ]
  }

  // MARK: - API for the host (no Flutter here: the channel methods backfill* are in SyncEngineChannel.swift)

  func status(_ done: @escaping ([String: Any]) -> Void) {
    queue.async { let s = self.load(); DispatchQueue.main.async { done(self.statusMap(s)) } }
  }

  func cancel(_ done: @escaping ([String: Any]) -> Void) {
    queue.async {
      var s = self.load()
      if let f = s.pendingFile { try? FileManager.default.removeItem(atPath: f) }
      s.active = false
      s.pendingBatch = nil
      s.pendingFile = nil
      self.save(s)
      self.session.getAllTasks { $0.forEach { $0.cancel() } }
      Keychain.delete()
      DispatchQueue.main.async { done(self.statusMap(s)) }
    }
  }

  /// Starts (or widens) the backfill: {server, token, types, days (0 = all), to, appVersion, powerOnly}; nil = bad args.
  func start(_ args: [String: Any], _ done: @escaping ([String: Any]?) -> Void) {
    guard let server = args["server"] as? String, let token = args["token"] as? String, !server.isEmpty, !token.isEmpty,
          let types = args["types"] as? [[String: String]], !types.isEmpty else {
      done(nil); return
    }
    let days = args["days"] as? Int ?? 90
    let to = args["to"] as? Double ?? Date().addingTimeInterval(-14 * 86400).timeIntervalSince1970
    Keychain.write(token)
    queue.async {
      var s = self.load()
      let from: Double = days > 0
        ? Date().addingTimeInterval(-Double(days) * 86400).timeIntervalSince1970
        : (self.store.earliestPermittedSampleDate().timeIntervalSince1970)
      // the same or a smaller target while one is running / done: keep its progress, just take the new token / types
      if (s.active || s.done) && s.server == server && s.to == to && from >= s.from {
        s.types = types
        s.active = !s.done || from < s.from
      } else if (s.active || s.done) && s.server == server && s.to == to {
        s.from = from              // a larger target (90 days -> all): go on from where the cursor is
        s.target = days
        s.active = true
        s.done = false
        s.types = types
      } else {
        if let f = s.pendingFile { try? FileManager.default.removeItem(atPath: f) }
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        s = State(active: true, server: server, appVersion: args["appVersion"] as? String ?? version, types: types, target: days,
                  from: from, to: to, cursor: to, startedAt: Date().timeIntervalSince1970)
      }
      s.powerOnly = args["powerOnly"] as? Bool ?? false
      s.error = nil
      self.save(s)
      Self.schedule(powerOnly: s.powerOnly)
      DispatchQueue.main.async { done(self.statusMap(s)) }
      // start right away while the app is open (the user just asked); the background session carries on when it is not
      self.next(deadline: Date().addingTimeInterval(25)) { _ in }
    }
  }

  // MARK: - the work

  /// Builds and sends batches until [seconds] are used or a batch is in flight; returns the status.
  func pump(seconds: Double, _ done: @escaping ([String: Any]) -> Void) {
    let deadline = Date().addingTimeInterval(seconds)
    queue.async { self.next(deadline: deadline, done) }
  }

  private func next(deadline: Date, _ done: @escaping ([String: Any]) -> Void) {
    var s = load()
    guard s.active, !building else { done(statusMap(s)); return }
    if s.pendingBatch != nil {
      // in flight (or lost when the app was killed before the session had it): make sure the session has a task
      session.getAllTasks { tasks in
        self.queue.async {
          if tasks.isEmpty, let file = s.pendingFile, FileManager.default.fileExists(atPath: file), let batch = s.pendingBatch {
            self.send(file: file, batch: batch, server: s.server)
          } else if tasks.isEmpty {
            s.pendingBatch = nil
            s.pendingFile = nil
            self.save(s)
            if Date() < deadline { self.next(deadline: deadline, done); return }
          }
          done(self.statusMap(self.load()))
        }
      }
      return
    }
    if s.cursor <= s.from {
      s.active = false
      s.done = true
      save(s)
      Keychain.delete()
      done(statusMap(s))
      return
    }
    if Date() >= deadline { done(statusMap(s)); return }
    let end = s.cursor, start = max(s.from, s.cursor - s.windowSeconds)
    building = true
    read(types: s.types, start: start, end: end) { body, count in
      self.queue.async {
        self.building = false
        var s = self.load()
        guard s.active, s.cursor == end else { done(self.statusMap(s)); return }
        guard let body, count > 0 else {
          // nothing that day: no request; jump straight to the day of the next older sample (years of "all history"
          // may be empty)
          self.building = true
          self.newestBefore(types: s.types, date: start) { latest in
            self.queue.async {
              self.building = false
              var s = self.load()
              guard s.active, s.cursor == end else { done(self.statusMap(s)); return }
              if let latest, latest > s.from {
                s.cursor = min(start, latest + 1)
              } else {
                s.cursor = s.from
              }
              self.save(s)
              self.next(deadline: deadline, done)
            }
          }
          return
        }
        let batch = UUID().uuidString.lowercased()
        var json = body
        json["batch_id"] = batch
        json["app_version"] = s.appVersion
        guard let data = try? JSONSerialization.data(withJSONObject: json), let gz = Gzip.compress(data) else {
          s.error = "encode"
          self.save(s)
          done(self.statusMap(s))
          return
        }
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
          .appendingPathComponent("backfill", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("\(batch).json.gz")
        do {
          try gz.write(to: file, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        } catch {
          s.error = "file"
          self.save(s)
          done(self.statusMap(s))
          return
        }
        s.pendingBatch = batch
        s.pendingFile = file.path
        s.pendingStart = start
        s.samples += count
        self.save(s)
        self.send(file: file.path, batch: batch, server: s.server)
        done(self.statusMap(s))
      }
    }
  }

  private func send(file: String, batch: String, server: String) {
    guard let token = Keychain.read(), let base = URL(string: server), var c = URLComponents(url: base, resolvingAgainstBaseURL: false)
    else { return }
    c.path = "/v1/ingest/healthkit"
    c.query = nil
    c.fragment = nil
    guard let url = c.url else { return }
    var req = URLRequest(url: url)
    req.httpMethod = "POST"
    req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.setValue("gzip", forHTTPHeaderField: "Content-Encoding")
    req.setValue("application/json", forHTTPHeaderField: "Accept")
    let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
    req.setValue(version, forHTTPHeaderField: "X-App-Version")
    let task = session.uploadTask(with: req, fromFile: URL(fileURLWithPath: file))
    task.taskDescription = batch
    task.resume()
  }

  /// One time range of every type as one engine batch body (without batch_id); nil when nothing is in it.
  private func read(types: [[String: String]], start: Double, end: Double, _ done: @escaping ([String: Any]?, Int) -> Void) {
    let predicate = HKQuery.predicateForSamples(withStart: Date(timeIntervalSince1970: start), end: Date(timeIntervalSince1970: end),
                                                options: [.strictStartDate])
    var samples: [[String: Any]] = [], categories: [[String: Any]] = []
    var sources: [[String: Any]] = []
    var sourceIndex: [String: Int] = [:]
    var count = 0
    let lock = NSLock()
    let group = DispatchGroup()
    var workouts: [[String: Any]] = [], stats: [[String: Any]] = []
    let sharedSources = EngineSources()
    for t in types {
      guard let id = t["hk"], let kind = t["kind"] else { continue }
      let upload = t["upload"] ?? "raw"
      let type: HKSampleType?
      var unit: HKUnit?
      if kind == "quantity", let q = HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: id)) {
        let u = HKUnit(from: t["unit"] ?? "count")
        guard q.is(compatibleWith: u) else { continue }
        type = q
        unit = u
        if upload.contains("stats") {
          // the day's 15-minute buckets (A3; activity / basal energy go only this way, D1)
          let b0 = (start / EngineLimits.bucket).rounded(.down) * EngineLimits.bucket
          let n = Int(((end - b0) / EngineLimits.bucket).rounded(.up))
          group.enter()
          EngineHK.buckets(store, q, unit: u, start: b0, count: n) { values in
            lock.lock()
            if let values, values.contains(where: { $0 > 0 }) {
              stats.append(["type": id, "unit": t["unit"] ?? "", "bucket_s": Int(EngineLimits.bucket), "start": Int(b0), "values": values])
              count += values.count
            }
            lock.unlock()
            group.leave()
          }
        }
        if !upload.contains("raw") { continue }
      } else if kind == "category" {
        type = HKCategoryType.categoryType(forIdentifier: HKCategoryTypeIdentifier(rawValue: id))
      } else if kind == "workout" {
        // workouts with their statistics, events and metadata (A3), sources in the batch's table
        group.enter()
        let q = HKSampleQuery(sampleType: HKObjectType.workoutType(), predicate: predicate, limit: HKObjectQueryNoLimit,
                              sortDescriptors: nil) { _, list, _ in
          lock.lock()
          defer { lock.unlock(); group.leave() }
          for case let w as HKWorkout in list ?? [] {
            let i = sharedSources.add(w)
            if let m = EngineWorkout.map(w, src: i) { workouts.append(m) }
          }
        }
        store.execute(q)
        continue
      } else {
        type = nil
      }
      guard let type else { continue }
      group.enter()
      let q = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, list, _ in
        let page = SyncEngine.shared.page(list ?? [], unit: unit, deleted: [], anchor: nil, more: false)
        lock.lock()
        defer { lock.unlock(); group.leave() }
        let uuids = page["uuid"] as? [String] ?? []
        guard !uuids.isEmpty else { return }
        // re-index this type's sources into the batch's table
        let local = page["sources"] as? [[String: Any]] ?? []
        let map: [Int] = local.map { src in
          let key = ["bundle", "product", "device_name", "device_model", "os_version"].map { "\(src[$0] ?? "")" }.joined(separator: "|")
          if let i = sourceIndex[key] { return i }
          sources.append(src)
          sourceIndex[key] = sources.count - 1
          return sources.count - 1
        }
        let src = (page["src"] as? [Int] ?? []).map { map[$0] }
        let tz = page["tz"] as? [Any] ?? []
        // at most 5000 per group (the server's group limit)
        var i = 0
        while i < uuids.count {
          let j = min(uuids.count, i + 5000)
          var g: [String: Any] = [
            "type": id, "unit": kind == "category" ? "" : (t["unit"] ?? ""),
            "uuid": Array(uuids[i..<j]),
            "start": Array((page["start"] as? [Double] ?? [])[i..<j]),
            "end": Array((page["end"] as? [Double] ?? [])[i..<j]),
            "value": Array((page["value"] as? [Double] ?? [])[i..<j]),
            "src": Array(src[i..<j]),
          ]
          if tz.contains(where: { !($0 is NSNull) }) { g["tz"] = Array(tz[i..<j]) }
          if kind == "category" { categories.append(g) } else { samples.append(g) }
          i = j
        }
        count += uuids.count
      }
      store.execute(q)
    }
    group.notify(queue: queue) {
      // the workouts' sources go after the samples' ones
      let offset = sources.count
      sources += sharedSources.list
      for i in workouts.indices { workouts[i]["src"] = (workouts[i]["src"] as? Int ?? 0) + offset }
      count += workouts.count
      guard count > 0 else { done(nil, 0); return }
      var body: [String: Any] = ["device": "iPhone", "phase": "backfill", "tz": TimeZone.current.identifier,
                                 "sources": sources, "deleted": []]
      if !samples.isEmpty { body["samples"] = samples }
      if !categories.isEmpty { body["categories"] = categories }
      if !workouts.isEmpty { body["workouts"] = workouts }
      if !stats.isEmpty { body["stats"] = stats }
      done(body, count)
    }
  }

  /// The start (UTC seconds) of the newest sample of any of [types] that starts before [date]; nil when there is none.
  private func newestBefore(types: [[String: String]], date: Double, _ done: @escaping (Double?) -> Void) {
    let predicate = HKQuery.predicateForSamples(withStart: nil, end: Date(timeIntervalSince1970: date), options: [])
    let lock = NSLock()
    var best: Double?
    let group = DispatchGroup()
    for t in types {
      guard let id = t["hk"], let kind = t["kind"] else { continue }
      let type: HKSampleType? = kind == "quantity"
        ? HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: id))
        : (kind == "category" ? HKCategoryType.categoryType(forIdentifier: HKCategoryTypeIdentifier(rawValue: id)) : nil)
      guard let type else { continue }
      group.enter()
      let q = HKSampleQuery(sampleType: type, predicate: predicate, limit: 1,
                            sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: false)]) { _, list, _ in
        lock.lock()
        if let s = list?.first?.startDate.timeIntervalSince1970, s < date, best == nil || s > best! { best = s }
        lock.unlock()
        group.leave()
      }
      store.execute(q)
    }
    group.notify(queue: queue) { done(best) }
  }

  // MARK: - URLSession (background)

  func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
    queue.async { self.responses[dataTask.taskIdentifier, default: Data()].append(data) }
  }

  func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
    let code = (task.response as? HTTPURLResponse)?.statusCode ?? 0
    let retryAfter = Double((task.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Retry-After") ?? "") ?? 0
    queue.async {
      let body = self.responses.removeValue(forKey: task.taskIdentifier) ?? Data()
      var s = self.load()
      guard s.pendingBatch == task.taskDescription else { return }   // an old task (cancelled backfill)
      let detail = ((try? JSONSerialization.jsonObject(with: body)) as? [String: Any])?["detail"] as? String
      let now = Date().timeIntervalSince1970
      var again = false
      switch code {
      case 200:
        s.cursor = s.pendingStart
        s.batches += 1
        s.error = nil
        if let f = s.pendingFile { try? FileManager.default.removeItem(atPath: f) }
        s.pendingBatch = nil
        s.pendingFile = nil
        again = true
      case 413:
        // too large: the same range in smaller windows (down to an hour)
        if let f = s.pendingFile { try? FileManager.default.removeItem(atPath: f) }
        s.pendingBatch = nil
        s.pendingFile = nil
        s.windowSeconds = max(3600, s.windowSeconds / 2)
        again = true
      case 400, 415, 422:
        // a contract problem with that day: skip it, keep going (visible as the error)
        if let f = s.pendingFile { try? FileManager.default.removeItem(atPath: f) }
        s.cursor = s.pendingStart
        s.pendingBatch = nil
        s.pendingFile = nil
        s.error = "contract"
        s.errorAt = now
        again = true
      case 401, 403:
        s.error = "auth"            // stop; Dart starts it again with a fresh token
        s.errorAt = now
        s.active = false
        s.pendingBatch = nil
        Keychain.delete()
      case 409:
        s.error = detail ?? "refused" // engine_disabled / type_paused / consent_required: Dart restarts with the server's types
        s.errorAt = now
        s.active = false
        s.pendingBatch = nil
      default:
        // 429 / 5xx / network: keep the file and batch_id, send the same batch later (BG task / next pump)
        s.error = code == 429 ? "rate_limited" : (code == 0 ? "network" : "server")
        s.errorAt = now + retryAfter
      }
      self.save(s)
      if again && s.active {
        self.next(deadline: Date().addingTimeInterval(25)) { _ in }
      }
    }
  }

  func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
    DispatchQueue.main.async {
      self.sessionCompletion?()
      self.sessionCompletion = nil
    }
  }

  /// Recreates the background session at launch so iOS can deliver the events of uploads that finished meanwhile.
  func attach() {
    _ = session
  }

  // MARK: - BGProcessingTask

  static func register() {
    BGTaskScheduler.shared.register(forTaskWithIdentifier: taskId, using: nil) { task in
      guard let task = task as? BGProcessingTask else { return }
      Backfill.shared.run(task)
    }
  }

  /// Open Wearables' stopBackgroundSync cancels every BGTask request of the app: ask for ours again while it runs.
  func rescheduleIfActive() {
    queue.async {
      let s = self.load()
      guard s.active else { return }
      BGTaskScheduler.shared.getPendingTaskRequests { reqs in
        if !reqs.contains(where: { $0.identifier == Self.taskId }) { Self.schedule(powerOnly: s.powerOnly) }
      }
    }
  }

  static func schedule(powerOnly: Bool) {
    let r = BGProcessingTaskRequest(identifier: taskId)
    r.requiresNetworkConnectivity = true
    r.requiresExternalPower = powerOnly
    r.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
    do {
      try BGTaskScheduler.shared.submit(r)
    } catch {
      NSLog("Backfill: BGProcessingTask not scheduled: %@", "\(error)")   // e.g. the simulator (not supported there)
    }
  }

  private func run(_ task: BGProcessingTask) {
    var expired = false
    task.expirationHandler = {
      // an upload in flight goes on in the background session; the cursor is only moved by its answer
      expired = true
      task.setTaskCompleted(success: false)
    }
    runSlices(expired: { expired }) { task.setTaskCompleted(success: $0) }
  }

  /// The body of the background task: slices of work until the backfill is done, the system ends the task, or 60
  /// slices; reschedules itself while there is work left. Also reachable from the debug channel (simulator builds,
  /// where BGTaskScheduler does not run tasks).
  func runSlices(expired: @escaping () -> Bool, completed: @escaping (Bool) -> Void) {
    func loop(_ rounds: Int) {
      pump(seconds: 25) { status in
        if expired() { return }
        let active = status["active"] as? Bool ?? false
        if !active || rounds <= 0 {
          if active { Self.schedule(powerOnly: status["powerOnly"] as? Bool ?? false) }
          completed(true)
          return
        }
        // wait for the upload in flight, then the next day
        DispatchQueue.global().asyncAfter(deadline: .now() + 5) { loop(rounds - 1) }
      }
    }
    loop(60)
  }
}

/// gzip (RFC 1952) of raw DEFLATE from the Compression framework, with the CRC-32 trailer the server checks.
enum Gzip {
  static func compress(_ data: Data) -> Data? {
    guard let deflated = try? (data as NSData).compressed(using: .zlib) as Data else { return nil }
    var out = Data([0x1f, 0x8b, 0x08, 0x00, 0, 0, 0, 0, 0x00, 0xff])
    out.append(deflated)
    var crc = crc32(data).littleEndian
    var size = UInt32(truncatingIfNeeded: data.count).littleEndian
    out.append(Data(bytes: &crc, count: 4))
    out.append(Data(bytes: &size, count: 4))
    return out
  }

  private static let table: [UInt32] = (0..<256).map { i -> UInt32 in
    var c = UInt32(i)
    for _ in 0..<8 { c = (c & 1) != 0 ? 0xEDB8_8320 ^ (c >> 1) : c >> 1 }
    return c
  }

  static func crc32(_ data: Data) -> UInt32 {
    var c: UInt32 = 0xFFFF_FFFF
    data.withUnsafeBytes { (p: UnsafeRawBufferPointer) in
      for b in p { c = table[Int((c ^ UInt32(b)) & 0xFF)] ^ (c >> 8) }
    }
    return c ^ 0xFFFF_FFFF
  }
}

/// The upload token for background uploads: Keychain, readable after the first unlock, this device only.
enum Keychain {
  private static let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                            kSecAttrService as String: "w2w.backfill", kSecAttrAccount as String: "token"]

  static func write(_ token: String) {
    delete()
    var q = base
    q[kSecValueData as String] = Data(token.utf8)
    q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    SecItemAdd(q as CFDictionary, nil)
  }

  static func read() -> String? {
    var q = base
    q[kSecReturnData as String] = true
    q[kSecMatchLimit as String] = kSecMatchLimitOne
    var out: AnyObject?
    guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
    return String(data: d, encoding: .utf8)
  }

  static func delete() {
    SecItemDelete(base as CFDictionary)
  }
}
