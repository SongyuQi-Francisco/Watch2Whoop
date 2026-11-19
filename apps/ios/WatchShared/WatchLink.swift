import Foundation

// What the phone and the watch say to each other over WatchConnectivity (docs/watch-app-plan.md sections 4.4 and 4.6).
// Plain Foundation only: this folder is compiled into the iPhone app (where the unit tests run) and the watch app.
//
//   phone → watch  application context (only the newest copy counts): `WatchContext` = signed in? + today's plan +
//                  today's numbers as the phone computed them + the quick-log tiles
//   watch → phone  user-info transfers (queued, delivered once, in order): one `WatchSetEvent` each (a set, the end of
//                  a session, a quick meal, a drink, the undo of one of those)
//
// The watch never writes to the server and never computes a score: the phone merges the events into its local store and
// its own offline queue sends them, so the server sees one writer (section 4.6).

/// One exercise of today's plan as the watch needs it: names, the target of the next set, rest, the last session.
struct WatchExercise: Codable, Equatable, Identifiable {
  var slug: String
  /// Short name in the phone's language (the status row of the set screen).
  var name: String
  /// Planned sets (the server's `target.sets`, already fewer on a red / yellow day).
  var sets: Int
  /// Sets already ticked on the phone today: a session started on the watch continues after them.
  var done: Int
  var reps: Int?
  var weightKg: Double?
  /// Timed exercise (plank, carries): the second number is seconds instead of reps.
  var durationS: Double?
  var weighted: Bool
  var restSec: Int
  /// One crown detent on the weight.
  var weightStepKg: Double
  /// The last session's sets as the server writes them (`"57.5×8"` or `"8"`), first set first.
  var last: [String]

  var id: String { slug }
}

/// Today's plan as the phone last saw it.
struct WatchPlan: Codable, Equatable {
  /// The user's local date (YYYY-MM-DD) the plan is for.
  var day: String
  /// The session names (「上肢 A」).
  var title: String
  var exercises: [WatchExercise]

  var totalSets: Int { exercises.reduce(0) { $0 + $1.sets } }
}

/// The phone's latest state for the watch (application context). Every field after `sentAt` came with phase 2a; a
/// context stored by an older build decodes with them nil.
///
/// How it grows (voice availability in phase 2b, what the watch-face widgets show in phase 3): each new block is an
/// optional field with its own Codable type, decoded leniently (see `init(from:)`): an older watch ignores a field it
/// does not know, and a block it cannot read (a newer enum case) becomes nil instead of losing the whole context. Keep
/// the context small (WatchConnectivity keeps only the newest copy; no secrets: a token never travels in it).
struct WatchContext: Codable, Equatable {
  var signedIn: Bool
  /// nil = no plan for `planFor` (rest day), or the phone has not loaded today's plan (then `planFor` is not today).
  var plan: WatchPlan?
  /// "zh" / "en": the watch shows the phone's language.
  var lang: String
  var sentAt: Date
  /// The day (YYYY-MM-DD) the phone's answer about training is for, also when it has no plan: `plan == nil` and
  /// `planFor == today` is a day without training; `planFor != today` means today's plan has not reached the watch.
  var planFor: String?
  /// Today's numbers, computed on the phone (the watch draws them and computes nothing).
  var today: WatchToday?
  /// The quick-log tiles and the day's intake (mockup M5, M3).
  var quick: WatchQuick?
  /// Voice on the watch for this account (phase 2b). nil = the phone has not said (an older phone): no voice key.
  var voice: WatchVoiceOffer?

  init(signedIn: Bool, plan: WatchPlan?, lang: String, sentAt: Date, planFor: String? = nil, today: WatchToday? = nil,
       quick: WatchQuick? = nil, voice: WatchVoiceOffer? = nil) {
    self.signedIn = signedIn
    self.plan = plan
    self.lang = lang
    self.sentAt = sentAt
    self.planFor = planFor ?? plan?.day
    self.today = today
    self.quick = quick
    self.voice = voice
  }

  /// The four phase-1 fields are required; every later block is optional and lenient (one unreadable block = nil).
  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    signedIn = try c.decode(Bool.self, forKey: .signedIn)
    lang = try c.decode(String.self, forKey: .lang)
    sentAt = try c.decode(Date.self, forKey: .sentAt)
    plan = (try? c.decodeIfPresent(WatchPlan.self, forKey: .plan)) ?? nil
    planFor = (try? c.decodeIfPresent(String.self, forKey: .planFor)) ?? nil
    today = (try? c.decodeIfPresent(WatchToday.self, forKey: .today)) ?? nil
    quick = (try? c.decodeIfPresent(WatchQuick.self, forKey: .quick)) ?? nil
    voice = (try? c.decodeIfPresent(WatchVoiceOffer.self, forKey: .voice)) ?? nil
  }
}

/// A state colour by name (the phone's `Tone`: recovery zones, set by the phone).
enum WatchTone: String, Codable, Equatable { case good, mid, bad }

/// Today at a glance (plan 2.4, mockups D1 / D2): the numbers of the phone's Today, as the phone last had them.
struct WatchToday: Codable, Equatable {
  /// The user's local date the numbers are for; numbers of another day are not shown as today's.
  var day: String
  /// Recovery 0–100; nil = no score today (drawn as missing).
  var recovery: Double?
  /// The recovery's zone colour as the phone draws it.
  var tone: WatchTone?
  /// Your usual recovery (the phone's 30-day P25–P75), the grey lane inside the dial; nil in the first two weeks.
  var usualLo: Double?
  var usualHi: Double?
  /// The word under the number (≤ 4 characters / 2 English words), in the phone's language.
  var verdict: String?
  /// Why there is no recovery (≤ 6 characters, e.g. 「昨晚没戴表」), when the phone knows.
  var reason: String?
  /// Sleep score 0–100.
  var sleep: Double?
  /// Strain so far today, 0–21.
  var strain: Double?
  /// When the phone built these numbers.
  var computedAt: Date
  /// When the newest data behind them arrived (the phone's 「2分前」); nil = unknown.
  var dataAt: Date?
  /// The phone's own sync capsule when it sent this (the same states the phone shows).
  var phone: WatchPhoneSync
}

/// The phone's sync capsule (TodayGlance `SyncCapsuleState`), carried to the watch's sync mark.
struct WatchPhoneSync: Codable, Equatable {
  enum Kind: String, Codable, Equatable {
    case ok, busy, stale, offline, behind, off, failed
  }

  var kind: Kind
  /// The capsule's short label (an age, 「缺2天」, 「需处理」); nil for ok / busy.
  var label: String?

  init(_ kind: Kind, _ label: String? = nil) {
    self.kind = kind
    self.label = label
  }
}

/// The quick-log tiles (plan 2.2, mockup M5) and what the receipt (M3) needs from the phone.
struct WatchQuick: Codable, Equatable {
  /// The day the intake below is for.
  var day: String
  /// At most `WatchParams.quickMealsMax` quick meals, most used first (made and ordered on the phone).
  var meals: [WatchQuickMeal]
  /// Millilitres of the water tile; nil = no water tile (water is off for this account).
  var waterMl: Int?
  /// The day's intake and goal, kcal, as the phone has them (its queued writes included); nil = unknown.
  var kcal: Double?
  var kcalGoal: Double?
  /// The watch's quick logs the phone has handled: id → whether it is in `kcal` now (false: undone or refused).
  var results: [String: Bool] = [:]
}

/// One quick meal of the phone (`MealTemplate`).
struct WatchQuickMeal: Codable, Equatable, Identifiable {
  /// The server's template id.
  var id: Int
  var name: String
  /// The meal it is logged to, in the phone's language (「午餐」).
  var slot: String
  var kcal: Double?
  var proteinG: Double?
}

/// One thing that happened on the watch. Every event has its own id; the phone drops ids it has seen. Phase 1 had only
/// training (`set`, `end`); phase 2a adds the quick logs (`meal`, `water`) and their `undo`.
struct WatchSetEvent: Codable, Equatable, Identifiable {
  enum Kind: String, Codable {
    /// A set was done (or a done set was corrected: same exercise and set index, later event wins).
    case set
    /// The session ended on the watch: the phone submits the day's training.
    case end
    /// A quick meal was tapped (`templateId`): the phone logs it like its own quick-meal strip.
    case meal
    /// The water tile was tapped (`ml`).
    case water
    /// The quick log `target` was taken back on the watch (after the confirm screen).
    case undo
    /// A meal Jarvis logged from the watch's voice (`target` = its receipt) is taken back: the phone deletes `writes`.
    case voiceUndo
  }

  var id: String
  var kind: Kind
  /// The plan day the session belongs to (a session that runs past midnight keeps its day); for a quick log the user's
  /// local date when it was tapped.
  var day: String
  var slug: String?
  /// 0-based set index within the exercise.
  var setIndex: Int?
  var reps: Int?
  var weightKg: Double?
  var durationS: Double?
  /// When the set was done / the tile was tapped (merge order on the phone).
  var at: Date
  /// Heart rate when the set ended (kept on the phone only in phase 1; plan 4.8).
  var hrEnd: Int?
  /// Rest actually taken before this set, seconds (phone only in phase 1).
  var restBeforeS: Int?
  /// `meal`: the quick meal's server id.
  var templateId: Int? = nil
  /// `water`: millilitres.
  var ml: Int? = nil
  /// `undo`: the id of the quick log it takes back.
  var target: String? = nil
  /// `voiceUndo`: what the server wrote (log id or entry ids, the date).
  var writes: [WatchVoiceWrite]? = nil

  var isTraining: Bool { kind == .set || kind == .end }

  /// A quick meal, a drink or an undo (the training events have their own initialiser above).
  static func quick(_ kind: Kind, id: String, day: String, at: Date, templateId: Int? = nil, ml: Int? = nil,
                    target: String? = nil) -> WatchSetEvent {
    WatchSetEvent(id: id, kind: kind, day: day, slug: nil, setIndex: nil, reps: nil, weightKg: nil, durationS: nil, at: at,
                  hrEnd: nil, restBeforeS: nil, templateId: templateId, ml: ml, target: target)
  }
}

/// Encoding for WatchConnectivity: the dictionaries hold one JSON `Data` value, which is a valid property-list type.
enum WatchCodec {
  static let contextKey = "w2w.watch.context.v1"
  static let eventKey = "w2w.watch.event.v1"
  /// `sendMessage` from the watch: 「send me the plan and today's numbers again」 (the 「计划没同步」 key and the sync mark
  /// retry with it).
  static let requestKey = "w2w.watch.request.v1"
  /// Watch → phone: 「I have no token of my own: a code, please」 (sendMessage, or a queued transfer when away).
  static let pairRequestKey = "w2w.watch.pairrequest.v1"
  /// Phone → watch: a `WatchPairCode` (sendMessage or transferUserInfo, never the application context: a code is a key).
  static let pairKey = "w2w.watch.pair.v1"
  /// Watch → phone: J4 「在手机上继续」, the conversation id (queued transfer).
  static let continueKey = "w2w.watch.continue.v1"

  static func message(_ code: WatchPairCode) -> [String: Any] {
    guard let d = encode(code) else { return [:] }
    return [pairKey: d]
  }

  static func pairCode(_ dict: [String: Any]) -> WatchPairCode? { decode(WatchPairCode.self, dict[pairKey] as? Data) }

  static func continueMessage(conversation: Int) -> [String: Any] { [continueKey: conversation] }
  static func continued(_ dict: [String: Any]) -> Int? { (dict[continueKey] as? NSNumber)?.intValue ?? dict[continueKey] as? Int }

  private static let encoder: JSONEncoder = {
    let e = JSONEncoder()
    e.dateEncodingStrategy = .millisecondsSince1970
    e.outputFormatting = [.sortedKeys]
    return e
  }()

  private static let decoder: JSONDecoder = {
    let d = JSONDecoder()
    d.dateDecodingStrategy = .millisecondsSince1970
    return d
  }()

  static func encode<T: Encodable>(_ value: T) -> Data? { try? encoder.encode(value) }
  static func decode<T: Decodable>(_ type: T.Type, _ data: Data?) -> T? {
    guard let data else { return nil }
    return try? decoder.decode(type, from: data)
  }

  static func message(_ context: WatchContext) -> [String: Any] {
    guard let d = encode(context) else { return [:] }
    return [contextKey: d]
  }

  static func context(_ dict: [String: Any]) -> WatchContext? { decode(WatchContext.self, dict[contextKey] as? Data) }

  static func message(_ event: WatchSetEvent) -> [String: Any] {
    guard let d = encode(event) else { return [:] }
    return [eventKey: d]
  }

  static func event(_ dict: [String: Any]) -> WatchSetEvent? { decode(WatchSetEvent.self, dict[eventKey] as? Data) }
}

/// The last session's sets (`"57.5×8"`, `"8"`) as numbers.
struct LastSet: Equatable {
  var weightKg: Double?
  var reps: Int?

  init(weightKg: Double?, reps: Int?) {
    self.weightKg = weightKg
    self.reps = reps
  }

  init?(_ s: String) {
    let parts = s.replacingOccurrences(of: "x", with: "×").split(separator: "×").map { $0.trimmingCharacters(in: .whitespaces) }
    if parts.count == 2 {
      guard let w = Double(parts[0]), let r = Int(parts[1]) else { return nil }
      self.init(weightKg: w, reps: r)
    } else if parts.count == 1, let r = Int(parts[0]) {
      self.init(weightKg: nil, reps: r)
    } else {
      return nil
    }
  }
}

/// Number formatting shared by both sides: `60`, `62.5` (no trailing zeros, at most one decimal).
enum WatchNum {
  static func kg(_ v: Double?) -> String {
    guard let v else { return "—" }
    let r = (v * 10).rounded() / 10
    return r == r.rounded() ? String(Int(r)) : String(format: "%.1f", r)
  }
}
