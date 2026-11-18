import Foundation

/// What the watch's home (「今天一眼」, plan 2.4, mockups D1 / D2 / S1–S7) shows, decided from the phone's context alone.
/// Pure, so the rules are unit-tested on the phone: the watch draws exactly this and computes no score. A number the
/// phone did not send for today is missing (「—」), never yesterday's and never made up.
struct WatchHome: Equatable {
  /// The left capsule under the readings.
  enum Key: Equatable {
    /// Today's plan: its name; tap / Double Tap = the training start screen (T1).
    case train(String)
    /// A day without training: 「记一餐」 = the quick-log tiles (D2).
    case meal
    /// Today's plan has not reached the watch: 「计划没同步」; tap = ask the phone again (was the S9 wall in phase 1).
    case planMissing
  }

  /// Recovery 0–100 (nil = missing).
  var recovery: Double?
  var tone: WatchTone?
  /// The usual lane, 0–100 at both ends.
  var usual: ClosedRange<Double>?
  var verdict: String?
  /// Why recovery is missing (from the phone), shown in its place.
  var reason: String?
  var sleep: Double?
  var strain: Double?
  /// S3: drawn grey, not passed off as current.
  var grey: Bool
  var key: Key
  /// The quick-log screen has something to offer (a quick meal or the water tile).
  var canQuickLog: Bool
  /// Hold on the Jarvis core = talk (plan 2.2 / 2.3). Unavailable = the core is dimmed (mockup S7).
  var voice: WatchVoice

  static func derive(context c: WatchContext?, today: String, sync: WatchSyncState) -> WatchHome {
    let t = c?.today.flatMap { $0.day == today ? $0 : nil }
    let key: Key
    if let p = c?.plan, p.day == today, !p.exercises.isEmpty {
      key = .train(p.title)
    } else if c?.planFor == today {
      key = .meal
    } else {
      key = .planMissing
    }
    let q = c?.quick
    var usual: ClosedRange<Double>?
    if let lo = t?.usualLo, let hi = t?.usualHi { usual = min(lo, hi)...max(lo, hi) }
    return WatchHome(recovery: t?.recovery, tone: t?.recovery == nil ? nil : t?.tone, usual: usual, verdict: t?.recovery == nil ? nil : t?.verdict,
                     reason: t?.recovery == nil ? t?.reason : nil, sleep: t?.sleep, strain: t?.strain, grey: sync.greysNumbers,
                     key: key, canQuickLog: !(q?.meals.isEmpty ?? true) || q?.waterMl != nil,
                     voice: WatchVoice.derive(context: c, sync: sync))
  }
}

/// Whether holding the Jarvis core can start talking (plan 2.2 voice meal, 2.3 one question). The one place the rule
/// lives, so the voice phase (2b) only changes `derive`; the home screen draws the core from it and
/// `WatchModel.coreHold()` either starts listening or plays the refusal.
enum WatchVoice: Equatable {
  enum Reason: Equatable {
    /// Phase 2a: voice is not on the watch yet.
    case notYet
    /// Nobody is signed in on the phone.
    case signedOut
    /// No network for the request (voice needs the server; quick log does not).
    case offline
    /// The account's AI cannot take voice from the watch (subscription AIs via MCP, plan 2.2).
    case noAI
  }

  case available
  case unavailable(Reason)

  var isAvailable: Bool { self == .available }

  /// Phase 2a: always `.notYet`. Phase 2b reads the phone's answer about the user's AI (a new optional field of the
  /// context), the watch's own token and the watch's network here, and returns the reasons above.
  static func derive(context: WatchContext?, sync: WatchSyncState) -> WatchVoice {
    guard context?.signedIn == true else { return .unavailable(.signedOut) }
    return .unavailable(.notYet)
  }
}

/// One quick log made on this watch, remembered for its receipt (M3), its undo (M4) and the receipt's intake bar.
struct WatchQuickLog: Codable, Equatable, Identifiable {
  enum Kind: String, Codable { case meal, water }

  /// The event's id (also the phone's write key).
  var id: String
  var kind: Kind
  var day: String
  var at: Date
  var templateId: Int?
  var name: String
  /// 「午餐」 for a meal.
  var slot: String?
  var kcal: Double?
  var proteinG: Double?
  var ml: Int?
  var undone = false

  static func meal(_ m: WatchQuickMeal, id: String, day: String, at: Date) -> WatchQuickLog {
    WatchQuickLog(id: id, kind: .meal, day: day, at: at, templateId: m.id, name: m.name, slot: m.slot, kcal: m.kcal,
                  proteinG: m.proteinG)
  }

  static func water(_ ml: Int, id: String, day: String, at: Date) -> WatchQuickLog {
    WatchQuickLog(id: id, kind: .water, day: day, at: at, name: tr("水", "Water"), ml: ml)
  }

  /// The event that carries it to the phone.
  var event: WatchSetEvent {
    kind == .meal ? .quick(.meal, id: id, day: day, at: at, templateId: templateId) : .quick(.water, id: id, day: day, at: at, ml: ml)
  }

  /// The receipt's bar (mockup M3: 「今天 1,180 / 2,300 kcal」, grey = before this meal, ice = this meal): the phone's
  /// intake and goal, corrected only for the watch's own logs the phone had not counted yet (or had counted before they
  /// were undone here). nil = something is unknown: the bar is left out rather than guessed.
  func bar(logs: [WatchQuickLog], quick: WatchQuick?) -> (before: Double, added: Double, goal: Double)? {
    guard kind == .meal, let added = kcal, let q = quick, q.day == day, let intake = q.kcal, let goal = q.kcalGoal, goal > 0 else {
      return nil
    }
    var before = intake
    for l in logs where l.kind == .meal && l.day == day {
      let counted = q.results[l.id]
      if l.id == id {
        if counted == true { before -= added }
        continue
      }
      guard let k = l.kcal else {
        if !l.undone && counted == nil { return nil }   // a log the phone has not counted and whose size we do not know
        continue
      }
      if !l.undone && counted == nil { before += k }
      if l.undone && counted == true { before -= k }
    }
    return (max(0, before), added, goal)
  }
}

/// The quick logs this watch remembers (the last day's), stored as one value.
struct WatchQuickLogs: Codable, Equatable {
  private(set) var items: [WatchQuickLog] = []

  mutating func add(_ l: WatchQuickLog) {
    guard !items.contains(where: { $0.id == l.id }) else { return }
    items.append(l)
  }

  mutating func markUndone(_ id: String) {
    guard let i = items.firstIndex(where: { $0.id == id }) else { return }
    items[i].undone = true
  }

  func log(_ id: String) -> WatchQuickLog? { items.first { $0.id == id } }

  /// Drops the ones older than `WatchParams.quickLogsKeptHours`.
  mutating func prune(now: Date) {
    items.removeAll { now.timeIntervalSince($0.at) > WatchParams.quickLogsKeptHours * 3600 }
  }

  func data() -> Data? { WatchCodec.encode(self) }
  static func load(_ data: Data?) -> WatchQuickLogs { WatchCodec.decode(WatchQuickLogs.self, data) ?? WatchQuickLogs() }
}

/// Where the watch is (plan 2.4: 「一个首屏 + 当前场景」): home is the root whenever no session runs; every screen above
/// it has the system back key, and the end of each flow returns to home.
enum WatchRoute: Hashable {
  /// T1, from home's left capsule.
  case start
  /// M5, from the Jarvis core or 「记一餐」.
  case quick
  /// M3, after a tile: the receipt of quick log `id` (it replaces the tiles, so back goes home).
  case receipt(String)
  /// M4, from the receipt's 「撤销」: the question and its two keys.
  case undo(String)
}

/// A saved training session, so a running one comes back after the app was closed or the watch restarted.
struct SavedSession: Codable, Equatable {
  var session: SetSession
  var savedAt: Date

  /// Resumed on launch only while it is running and was touched within `WatchParams.resumeWithinHours`.
  func resumable(now: Date) -> Bool {
    switch session.phase {
    case .ready, .finished: return false
    default: return now.timeIntervalSince(savedAt) <= WatchParams.resumeWithinHours * 3600
    }
  }
}
