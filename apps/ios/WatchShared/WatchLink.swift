import Foundation

// What the phone and the watch say to each other over WatchConnectivity (docs/watch-app-plan.md sections 4.4 and 4.6).
// Plain Foundation only: this folder is compiled into the iPhone app (where the unit tests run) and the watch app.
//
//   phone → watch  application context (only the newest copy counts): `WatchContext` = signed in? + today's plan
//   watch → phone  user-info transfers (queued, delivered once, in order): one `WatchSetEvent` each
//
// The watch never writes to the server: the phone merges the events into the day's draft and its own offline queue
// sends them, so the server sees one writer (section 4.6).

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

/// The phone's latest state for the watch (application context).
struct WatchContext: Codable, Equatable {
  var signedIn: Bool
  /// nil = no plan today (rest day, plan not loaded yet).
  var plan: WatchPlan?
  /// "zh" / "en": the watch shows the phone's language.
  var lang: String
  var sentAt: Date
}

/// One thing that happened on the watch. Every event has its own id; the phone drops ids it has seen.
struct WatchSetEvent: Codable, Equatable, Identifiable {
  enum Kind: String, Codable {
    /// A set was done (or a done set was corrected: same exercise and set index, later event wins).
    case set
    /// The session ended on the watch: the phone submits the day's training.
    case end
  }

  var id: String
  var kind: Kind
  /// The plan day the session belongs to (a session that runs past midnight keeps its day).
  var day: String
  var slug: String?
  /// 0-based set index within the exercise.
  var setIndex: Int?
  var reps: Int?
  var weightKg: Double?
  var durationS: Double?
  /// When the set was done (merge order on the phone).
  var at: Date
  /// Heart rate when the set ended (kept on the phone only in phase 1; plan 4.8).
  var hrEnd: Int?
  /// Rest actually taken before this set, seconds (phone only in phase 1).
  var restBeforeS: Int?
}

/// Encoding for WatchConnectivity: the dictionaries hold one JSON `Data` value, which is a valid property-list type.
enum WatchCodec {
  static let contextKey = "w2w.watch.context.v1"
  static let eventKey = "w2w.watch.event.v1"
  /// `sendMessage` from the watch: 「send me the plan again」 (the 「在手机上打开一次」 screen retries with it).
  static let requestKey = "w2w.watch.request.v1"

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
