import Foundation

// What the watch-face complications show (docs/watch-app-plan.md 2.5, mockups C1 / C4 on the plan branch), decided by
// the same pure rules as the home screen: `WatchHome.derive` for the numbers, `WatchSyncState.derive` for 「旧了」, and
// the set session's own state machine for the rest countdown. Plain Foundation, so the rules are unit-tested on the
// phone, and the widget extension (its own process) and the app can never disagree. The watch computes no score: a
// number the phone did not send for today is missing, an old one is grey.

/// The three complications. `kind` is the WidgetKit kind (also what the app reloads).
enum FaceComplication: String, CaseIterable {
  /// Circular: recovery number and its arc in the zone colour. Tap = home.
  case recovery = "w2w.face.recovery"
  /// Rectangular: recovery + sleep + strain; during a session the rest countdown and the next set. Tap = home / session.
  case today = "w2w.face.today"
  /// Circular key: 「开始训练」 (a day without training: 「记一餐」). Tap = the start screen / quick log.
  case start = "w2w.face.start"

  var kind: String { rawValue }
}

/// What the circular key draws (the home screen's left capsule, `WatchHome.Key`, plus a running session).
enum FaceKey: Equatable {
  /// Today's plan: the dumbbell, opens the start screen (T1).
  case train
  /// A day without training: the bowl, opens quick log (M5), like home's 「记一餐」.
  case meal
  /// Today's plan has not reached the watch, or nobody is signed in: the dumbbell, dimmed; opens home.
  case unavailable
  /// A session is running: the dumbbell; opens the session.
  case session
}

/// The running session on the rectangular complication (mockup C4): exercise, its sets, the next set's numbers, and the
/// rest countdown while resting (nil = during a set: no countdown).
struct FaceLive: Equatable {
  var exercise: String
  /// Sets of this exercise done so far and planned (「2/4」).
  var done: Int
  var sets: Int
  var weightKg: Double?
  var reps: Int?
  var durationS: Double?
  /// The rest: start … end. The face counts it down by itself (`Text(timerInterval:)`), no entry per second.
  var rest: ClosedRange<Date>?
}

/// One moment of the complications' timeline.
struct FaceEntry: Equatable {
  var date: Date
  /// "zh" / "en": the phone's language, as on the home screen.
  var lang: String
  var signedIn: Bool
  /// Recovery 0–100 for today (nil = missing: 「—」 and an empty track).
  var recovery: Double?
  var tone: WatchTone?
  /// S3's rule: the phone's numbers are old; number and arc are drawn grey.
  var grey: Bool
  var sleep: Double?
  var strain: Double?
  var key: FaceKey
  /// A running session (the rectangular one shows it instead of the three numbers).
  var live: FaceLive?

  /// Same drawing (the date aside): consecutive equal entries are merged, and a store change that leaves every entry
  /// the same does not spend a reload.
  func sameFace(_ o: FaceEntry) -> Bool {
    var a = self
    a.date = o.date
    return a == o
  }
}

/// Builds the timeline: one entry now and one at every moment the drawing changes by itself (numbers turn grey,
/// the day ends, a receipt gives way to the rest, the rest ends, a forgotten session expires). Between those moments
/// nothing changes without a push from the phone or a step of the session, and those reload the timeline.
enum FaceTimeline {
  /// What the complications read from `WatchStore`.
  struct Inputs: Equatable {
    var context: WatchContext?
    var queue: WatchEventQueue
    var session: SavedSession?
  }

  static func entries(_ i: Inputs, now: Date, calendar: Calendar = .current) -> [FaceEntry] {
    let end = horizon(now: now, calendar: calendar)
    let moments = ([now] + changes(i, now: now, calendar: calendar)).filter { $0 >= now && $0 <= end }.sorted()
    var out: [FaceEntry] = []
    for t in moments {
      let e = entry(i, at: t, calendar: calendar)
      if let last = out.last, last.sameFace(e) { continue }
      out.append(e)
    }
    return out
  }

  /// When WidgetKit should ask for the next timeline: just after the next local midnight (the last moment above), a
  /// cheap once-a-day request that also covers a phone that stays silent for days.
  static func horizon(now: Date, calendar: Calendar = .current) -> Date {
    let midnight = calendar.nextDate(after: now, matching: DateComponents(hour: 0, minute: 0, second: 0), matchingPolicy: .nextTime)
      ?? now.addingTimeInterval(86_400)
    return midnight.addingTimeInterval(1)
  }

  /// The drawing at `t`, from what is stored now.
  static func entry(_ i: Inputs, at t: Date, calendar: Calendar = .current) -> FaceEntry {
    let today = WatchDay.string(t, calendar: calendar)
    // reachable: the extension cannot know; it only decides between 「待传」 and 「补交」, neither of which greys
    let sync = WatchSyncState.derive(context: i.context, queue: i.queue, reachable: false, now: t)
    let home = WatchHome.derive(context: i.context, today: today, sync: sync)
    let live = live(i.session, at: t)
    let signedIn = i.context?.signedIn == true
    let key: FaceKey
    if live != nil {
      key = .session
    } else if !signedIn {
      key = .unavailable
    } else {
      switch home.key {
      case .train: key = .train
      case .meal: key = .meal
      case .planMissing: key = .unavailable
      }
    }
    return FaceEntry(date: t, lang: i.context?.lang ?? Lang.code, signedIn: signedIn, recovery: signedIn ? home.recovery : nil,
                     tone: signedIn ? home.tone : nil, grey: home.grey, sleep: signedIn ? home.sleep : nil,
                     strain: signedIn ? home.strain : nil, key: key, live: live)
  }

  /// The session as the app would show it at `t`: the stored one moved on by its own clock (receipt → rest → set).
  /// nil = no session on screen (none, finished, at the summary, or one left too long to be resumed).
  static func live(_ saved: SavedSession?, at t: Date) -> FaceLive? {
    guard let saved, saved.resumable(now: t) else { return nil }
    var s = saved.session
    // the app's tick, twice: a receipt that is over becomes the rest, a rest that is over becomes the set
    _ = s.tick(at: t)
    _ = s.tick(at: t)
    guard let ex = s.current else { return nil }
    let done = s.nextSet.indices.contains(s.exercise) ? s.nextSet[s.exercise] : 0
    var live = FaceLive(exercise: ex.name, done: min(done, ex.sets), sets: ex.sets, weightKg: ex.weighted ? s.weightKg : nil,
                        reps: s.isTimed ? nil : s.reps, durationS: s.isTimed ? s.durationS : nil, rest: nil)
    switch s.phase {
    case .set:
      return live
    case .rest(let r):
      live.rest = r.start...r.start.addingTimeInterval(TimeInterval(r.total))
      return live
    case .logged, .ready, .summary, .finished:
      return nil
    }
  }

  /// Every future moment at which `entry` may change without anything being stored.
  static func changes(_ i: Inputs, now: Date, calendar: Calendar = .current) -> [Date] {
    var out: [Date] = []
    // the day ends: today's numbers and plan are yesterday's
    if let m = calendar.nextDate(after: now, matching: DateComponents(hour: 0, minute: 0, second: 0), matchingPolicy: .nextTime) {
      out.append(m)
    }
    if let c = i.context {
      // the numbers turn grey (WatchSyncState: their age reaches `staleAfterMin`)
      if let since = c.today?.dataAt ?? i.queue.lastContact ?? c.today?.computedAt {
        out.append(since.addingTimeInterval(TimeInterval(WatchParams.staleAfterMin * 60)))
      }
      // the phone's 「同步中」 is no longer fresh
      out.append((i.queue.lastContact ?? c.sentAt).addingTimeInterval(WatchParams.phoneBusyFreshSec))
    }
    if let saved = i.session {
      // just past the resume limit (`SavedSession.resumable` keeps it up to and including the limit)
      out.append(saved.savedAt.addingTimeInterval(WatchParams.resumeWithinHours * 3600 + 1))
      var s = saved.session
      if case .logged(let until) = s.phase {
        out.append(until)
        _ = s.tick(at: max(until, now))
      }
      if case .rest(let r) = s.phase { out.append(r.start.addingTimeInterval(TimeInterval(r.total))) }
    }
    return out
  }

  /// A store change spends a reload only when it changes what the face shows now or later.
  static func needsReload(before: Inputs, after: Inputs, now: Date, calendar: Calendar = .current) -> Bool {
    let a = entries(before, now: now, calendar: calendar), b = entries(after, now: now, calendar: calendar)
    guard a.count == b.count else { return true }
    return zip(a, b).contains { $0.date != $1.date || !$0.sameFace($1) }
  }
}

/// The user's local date (YYYY-MM-DD) in the watch's time zone: the key that says whether the phone's numbers and plan
/// are today's. The app (`WatchModel.day`) and the complications use this one function.
enum WatchDay {
  static func string(_ d: Date, calendar: Calendar = .current) -> String {
    let f = DateFormatter()
    f.calendar = Calendar(identifier: .gregorian)
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = calendar.timeZone
    f.dateFormat = "yyyy-MM-dd"
    return f.string(from: d)
  }
}

/// Where a complication's tap goes. The URL only names the complication; the screen is decided when the app opens, from
/// the app's own state (fresher than the face's last timeline).
enum WatchDeepLink: String, Equatable {
  case home
  case start

  static let scheme = "watch2whoop"
  static let host = "face"

  var url: URL { URL(string: "\(Self.scheme)://\(Self.host)/\(rawValue)")! }

  init?(url: URL) {
    guard url.scheme == Self.scheme, url.host == Self.host else { return nil }
    self.init(rawValue: url.lastPathComponent)
  }

  /// The navigation path for the app's state: a running session is always where a tap lands (the root shows it); the
  /// key follows home's left capsule (today's plan → T1, a day without training → quick log, no plan → home, where the
  /// capsule says 「计划没同步」).
  func route(home: WatchHome, running: Bool, signedIn: Bool) -> [WatchRoute] {
    guard !running, signedIn else { return [] }
    switch self {
    case .home:
      return []
    case .start:
      switch home.key {
      case .train: return [.start]
      case .meal: return [.quick]
      case .planMissing: return []
      }
    }
  }
}
