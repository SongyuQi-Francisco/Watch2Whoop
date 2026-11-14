import Foundation

/// The 2.1.0 fields of the training endpoints (PR #452) as the 记录 pages draw them. Pure, so tests feed them values
/// (WireFieldsTests). A field an older server does not send leaves the page as it was (its empty state), never a guess.

/// The training timer of today's session (screen 11, and under the set ring of 记录 → 训练).
enum TrainingTimer {
  /// The `started_at` to send with the draft PUT: the one already queued, else the server's when it is a ticked set's,
  /// else — once a set is ticked — when the phone saw the first tick (or now). Nil before any tick, and always nil for a
  /// server that would refuse the field (`accepts`).
  static func startToSend(accepts: Bool, queued: String?, server: String?, serverFrom: String?, hasDone: Bool,
                          firstTick: Date?, now: Date) -> String? {
    guard accepts else { return nil }
    if let queued { return queued }
    if serverFrom == "first_set", let server { return server }
    guard hasDone else { return nil }
    return ISO8601DateFormatter.localWithOffset.string(from: firstTick ?? now)
  }

  /// The time the timer shows: from the start to the end of a submitted training, else to now. Nil without a start,
  /// before the first ticked set (a draft saved without a tick is not a training under way), or when the span is not
  /// believable (in the future, or longer than RecordParams.timerMaxHours: a timer left running from the morning).
  static func elapsed(start: String?, startedFrom: String?, submittedAt: String?, submitted: Bool, doneSets: Int,
                      now: Date) -> TimeInterval? {
    guard let start, let from = parse(start) else { return nil }
    guard doneSets > 0 || submitted else { return nil }
    let end = submitted ? (submittedAt.flatMap(parse) ?? now) : now
    let span = end.timeIntervalSince(from)
    guard span >= 0, span <= RecordParams.timerMaxHours * 3600 else { return nil }
    return span
  }

  /// 「22:14」, 「1:02:09」.
  static func text(_ seconds: TimeInterval) -> String {
    let s = Int(seconds)
    let h = s / 3600
    let m = (s % 3600) / 60
    let sec = s % 60
    if h > 0 { return String(format: "%d:%02d:%02d", h, m, sec) }
    return String(format: "%02d:%02d", m, sec)
  }

  static func parse(_ s: String) -> Date? { FeedFormat.parseDate(s) }
}

/// The history cards of screen 14: the day's heart-rate zones and its muscles.
enum HistoryCardWire {
  /// The five zones' shares of the day's zone minutes (sum 1); nil without minutes (an older server, or a day without a
  /// heart-rate workout: the card keeps its intensity bar).
  static func zoneShares(_ minutes: [Double]?) -> [Double]? {
    guard let minutes, minutes.count == 5 else { return nil }
    let total = minutes.reduce(0) { $0 + max(0, $1) }
    guard total > 0 else { return nil }
    return minutes.map { max(0, $0) / total }
  }

  /// The bar's segment widths (sum 1): a zone with minutes is never thinner than `minShare`, the others give way in
  /// proportion; a zone without minutes has no segment (0).
  static func segments(_ shares: [Double], minShare: Double = RecordParams.zoneMinShare) -> [Double] {
    let lifted = shares.map { $0 > 0 ? max($0, minShare) : 0 }
    let total = lifted.reduce(0, +)
    guard total > 0 else { return shares }
    return lifted.map { $0 / total }
  }

  /// Body regions of the day's muscles (the server's keys, as in muscle-load); empty when none maps onto the body.
  static func regions(_ muscles: [String]?) -> Set<String> {
    var out = Set<String>()
    for m in muscles ?? [] {
      for r in MuscleLoad.bodyRegions[m] ?? [] { out.insert(r) }
    }
    return out
  }

  /// A server day's 2.1.0 fields laid over the shown day (the phone's own calendar has no muscles; zone minutes come from
  /// whichever has them).
  static func overlay(_ shown: TrainingMonth.Day, server: TrainingMonth.Day?) -> TrainingMonth.Day {
    var d = shown
    guard let server, server.date == shown.date else { return d }
    if d.hrZoneMin == nil { d.hrZoneMin = server.hrZoneMin }
    if d.muscles == nil { d.muscles = server.muscles }
    if d.muscleGroups == nil { d.muscleGroups = server.muscleGroups }
    return d
  }
}

/// Starred exercises (screen 15): the server's flag with this phone's pending stars over it.
enum ExerciseFavorites {
  /// Kind of the local-first write (`PUT` / `DELETE /training/exercises/{slug}/favorite`).
  static let kind = "exercise_favorite"

  static func path(_ slug: String) -> String { "/v1/users/me/training/exercises/\(slug)/favorite" }

  /// The star shown: what was just tapped, else a queued change, else the server's flag (nil = no star on this server).
  static func shown(server: Bool?, pending: Bool?, tapped: Bool?) -> Bool? {
    if server == nil && pending == nil && tapped == nil { return nil }
    return tapped ?? pending ?? server
  }

  /// The pending stars by slug (the newest write of each; a refused one is not shown as starred).
  static func pending(_ rows: [PendingRecord]) -> [String: Bool] {
    var out: [String: Bool] = [:]
    for r in rows.sorted(by: { $0.createdAt < $1.createdAt }) where r.kind == kind && !r.needsAttention {
      if let slug = r.json["slug"] as? String, let on = r.json["favorite"] as? Bool { out[slug] = on }
    }
    return out
  }

  /// The 收藏 filter with the phone's changes applied: unstarred ones leave, starred ones that the list holds stay.
  static func filtered(_ list: [LibraryExercise], stars: (LibraryExercise) -> Bool?) -> [LibraryExercise] {
    list.filter { stars($0) == true }
  }
}
