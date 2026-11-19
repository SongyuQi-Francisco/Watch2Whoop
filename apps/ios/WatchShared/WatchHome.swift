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

  static func derive(context c: WatchContext?, today: String, sync: WatchSyncState,
                     credential: WatchVoice.Credential = .none) -> WatchHome {
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
                     voice: WatchVoice.derive(context: c, sync: sync, credential: credential))
  }
}

/// Whether holding the Jarvis core can start talking (plan 2.2 voice meal, 2.3 one question). The one place the rule
/// lives: the home screen draws the core from it and `WatchModel.coreHold()` either starts listening or says why not.
enum WatchVoice: Equatable {
  enum Reason: Equatable {
    /// Nobody is signed in on the phone.
    case signedOut
    /// The account's AI cannot take voice from the watch (subscription AIs via MCP, the butler, none set up), or the
    /// phone's direct-voice flag is off (plan Q6), or the phone has not said (an older phone app).
    case noAI
    /// The fallback AI's terms were not agreed to yet: only on the phone.
    case consent
    /// The watch has no token of its own yet (or lost it): the phone is asked for a code.
    case pairing
    /// The watch's own network failed the last request (voice needs the server; quick log does not).
    case offline
  }

  /// What only the watch knows: its token, its network.
  struct Credential: Equatable {
    /// A token for the phone's current account is in the Keychain (`WatchPairing.usable`).
    var paired: Bool
    /// The watch's own network (not the phone's: `WatchSyncState.offline` is the phone's).
    var online: Bool

    static let none = Credential(paired: false, online: true)
  }

  case available
  case unavailable(Reason)

  var isAvailable: Bool { self == .available }

  static func derive(context: WatchContext?, sync: WatchSyncState, credential: Credential = .none) -> WatchVoice {
    guard context?.signedIn == true else { return .unavailable(.signedOut) }
    guard let offer = context?.voice else { return .unavailable(.noAI) }
    if !offer.on { return .unavailable(offer.why == .consent ? .consent : .noAI) }
    if !credential.paired { return .unavailable(.pairing) }
    if !credential.online { return .unavailable(.offline) }
    return .available
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
  /// A meal Jarvis logged from the watch's voice (#458 `written`): undone through the phone, never as a quick log.
  var voice: [WatchVoiceWrite]? = nil
  /// Voice only: the day's calories after it and the goal, from the server's card (the receipt bar).
  var dayKcal: Double? = nil
  var goalKcal: Double? = nil

  static func meal(_ m: WatchQuickMeal, id: String, day: String, at: Date) -> WatchQuickLog {
    WatchQuickLog(id: id, kind: .meal, day: day, at: at, templateId: m.id, name: m.name, slot: m.slot, kcal: m.kcal,
                  proteinG: m.proteinG)
  }

  static func water(_ ml: Int, id: String, day: String, at: Date) -> WatchQuickLog {
    WatchQuickLog(id: id, kind: .water, day: day, at: at, name: tr("水", "Water"), ml: ml)
  }

  /// A meal Jarvis logged (M3 after a voice meal): what the server's card says, nothing guessed.
  static func voiceMeal(_ m: WatchVoiceMeal, id: String, at: Date) -> WatchQuickLog {
    let foods = m.foods.joined(separator: " + ")
    return WatchQuickLog(id: id, kind: .meal, day: m.date, at: at, templateId: nil,
                         name: foods.isEmpty ? tr("这一餐", "This meal") : foods, slot: WatchVoiceWrite.slotName(m.meal),
                         kcal: m.calories, proteinG: m.proteinG, ml: nil, undone: false, voice: m.writes, dayKcal: m.dayKcal,
                         goalKcal: m.goalKcal)
  }

  /// The event that carries the undo of a voice meal to the phone (it deletes what the server wrote; the watch's token
  /// cannot).
  func voiceUndo(id eventId: String, at: Date) -> WatchSetEvent? {
    guard let w = voice, !w.isEmpty else { return nil }
    var e = WatchSetEvent.quick(.voiceUndo, id: eventId, day: day, at: at, target: id)
    e.writes = w
    return e
  }

  /// The event that carries it to the phone.
  var event: WatchSetEvent {
    kind == .meal ? .quick(.meal, id: id, day: day, at: at, templateId: templateId) : .quick(.water, id: id, day: day, at: at, ml: ml)
  }

  /// The receipt's bar (mockup M3: 「今天 1,180 / 2,300 kcal」, grey = before this meal, ice = this meal): the phone's
  /// intake and goal, corrected only for the watch's own logs the phone had not counted yet (or had counted before they
  /// were undone here). nil = something is unknown: the bar is left out rather than guessed.
  func bar(logs: [WatchQuickLog], quick: WatchQuick?) -> (before: Double, added: Double, goal: Double)? {
    if voice != nil {
      // the server's own totals right after the write (they include this meal)
      guard let added = kcal, let after = dayKcal, let goal = goalKcal, goal > 0 else { return nil }
      return (max(0, after - added), added, goal)
    }
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
  /// M2–M4 / J2–J4 after a hold on the core (listening itself is drawn over home, under the finger).
  case voice
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
