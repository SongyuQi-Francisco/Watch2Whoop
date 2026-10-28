import Foundation

/// 记录 → 日记: the day's behaviours, numbers and supplement check-ins. Each answer is local first, then
/// `PUT` / `DELETE /journal/{day}/{key}` through the offline queue (last answer wins, section 5.4).
@MainActor
final class JournalStore: LocalPageStore {
  @Published private(set) var day: String
  @Published private(set) var today: String
  @Published private(set) var journal: JournalDay?

  init(session: AccountSession) {
    let t = session.db.userToday()
    today = t
    day = t
    super.init(api: session.api, db: session.db, queue: session.sync.queue)
    observeDay()
  }

  private func observeDay() {
    observe(keys: [RecordDocs.journalItems, RecordDocs.journalFrequent, RecordDocs.journal(day)], kinds: [RecordKind.journal])
  }

  override func changed() {
    journal = JournalDay.build(items: view.list(RecordDocs.journalItems), frequent: view.list(RecordDocs.journalFrequent),
                               entries: view.list(RecordDocs.journal(day)), day: day, pending: view.pending)
  }

  func select(_ d: String) {
    let d = min(d, today)
    guard d != day else { return }
    day = d
    observeDay()
    changed()
    Task { await refresh() }
  }

  func refresh() async {
    today = db.userToday()
    await send()
    await pull([DocFetch(RecordDocs.journalItems, RecordAPI.journalItems), DocFetch(RecordDocs.journalFrequent, RecordAPI.journalFrequent),
                DocFetch(RecordDocs.journal(day), RecordAPI.journal(day))])
  }

  /// Store (value) or clear (nil) one item of the shown day.
  @discardableResult
  func set(_ key: String, _ value: Double?) async -> WriteOutcome {
    await JournalWrite.set(self, day: day, key: key, value: value)
  }
}

/// One journal answer, local first (shared by 日记 and the supplement check-ins of 健康档案).
enum JournalWrite {
  @MainActor
  static func set(_ store: LocalPageStore, day: String, key: String, value: Double?) async -> WriteOutcome {
    let path = RecordAPI.journalEntry(day, key)
    let overlay: [String: Any] = ["key": key, "value": value.map { $0 as Any } ?? NSNull()]
    let out: WriteOutcome
    if let value {
      out = await store.write(kind: RecordKind.journal, date: day, group: "journal:\(day):\(key)", method: .put, path: path,
                              body: ["value": value], overlay: overlay, supersede: true)
    } else {
      out = await store.write(kind: RecordKind.journal, date: day, group: "journal:\(day):\(key)", method: .delete, path: path,
                              body: nil, overlay: overlay, clientIdInBody: false, supersede: true)
    }
    if out.sent { await store.pull([DocFetch(RecordDocs.journal(day), RecordAPI.journal(day))]) }
    return out
  }
}

/// 记录 → 训练: today's plan with set-by-set logging, the week, restrictions, the muscle load map and the history
/// calendar. The day's draft is one whole-day write (`PUT /training/session/{day}`), last one wins; 「提交」 queues
/// after it (`POST …/submit`).
@MainActor
final class TrainingStore: LocalPageStore {
  @Published private(set) var today: String
  @Published private(set) var month: String
  @Published private(set) var session: TrainingSession?
  @Published private(set) var week: TrainingWeek?
  @Published private(set) var calendar: TrainingMonth?
  @Published private(set) var muscle: MuscleLoad?
  @Published private(set) var rules: [ExerciseRule] = []
  /// The rows being edited (the draft), kept here so typing is not overwritten by a refresh.
  @Published var rows: [String: [SetRow]] = [:]
  @Published private(set) var saveState = ""
  private var editing = false
  private var saveTask: Task<Void, Never>?
  let muscleOn: Bool

  init(session: AccountSession) {
    let t = session.db.userToday()
    today = t
    month = String(t.prefix(7))
    muscleOn = session.flags.contains("muscle_load")
    super.init(api: session.api, db: session.db, queue: session.sync.queue)
    observeAll()
  }

  var monday: String { RecordDay.monday(today) }

  private func observeAll() {
    var keys = [RecordDocs.session(today), RecordDocs.week(monday), RecordDocs.calendar(month), RecordDocs.calendar(String(monday.prefix(7))),
                RecordDocs.rules]
    if muscleOn { keys.append(RecordDocs.muscle) }
    observe(keys: keys, kinds: RecordDocs.kinds)
  }

  override func changed() {
    let s = TrainingSession.build(view.json(RecordDocs.session(today)), day: today, pending: view.pending)
    session = s
    if !editing, let s { rows = s.rows }
    var cal = (view.json(RecordDocs.calendar(String(monday.prefix(7))))?["days"] as? [[String: Any]]) ?? []
    if String(RecordDay.add(monday, 6).prefix(7)) != String(monday.prefix(7)) {
      cal += (view.json(RecordDocs.calendar(String(RecordDay.add(monday, 6).prefix(7))))?["days"] as? [[String: Any]]) ?? []
    }
    week = TrainingWeek.build(view.json(RecordDocs.week(monday)), calendar: cal, monday: monday)
    calendar = TrainingMonth.build(view.json(RecordDocs.calendar(month)), month: month)
    muscle = muscleOn ? MuscleLoad.parse(view.json(RecordDocs.muscle)) : nil
    rules = ExerciseRule.parseList(view.list(RecordDocs.rules))
  }

  func refresh() async {
    let t = db.userToday()
    if t != today {
      today = t
      observeAll()
    }
    await send()
    var f = [DocFetch(RecordDocs.session(today), RecordAPI.session(today)), DocFetch(RecordDocs.week(monday), RecordAPI.week(today)),
             DocFetch(RecordDocs.calendar(month), RecordAPI.calendar(month)), DocFetch(RecordDocs.rules, RecordAPI.rules)]
    if String(monday.prefix(7)) != month {
      f.append(DocFetch(RecordDocs.calendar(String(monday.prefix(7))), RecordAPI.calendar(String(monday.prefix(7)))))
    }
    if muscleOn { f.append(DocFetch(RecordDocs.muscle, RecordAPI.muscle)) }
    await pull(f)
  }

  func showMonth(_ m: String) {
    guard m != month, m <= String(today.prefix(7)) else { return }
    month = m
    observeAll()
    changed()
    Task { await pull([DocFetch(RecordDocs.calendar(m), RecordAPI.calendar(m))]) }
  }

  /// A past day's detail (watch workouts + logged strength): the server's, else the cached copy (offline).
  func dayDetail(_ day: String) async -> [String: Any]? {
    let key = RecordDocs.trainingDay(day)
    _ = await RecordsPuller(api: api, db: db).pull([DocFetch(key, RecordAPI.day(day))], kinds: [])
    return (try? db.doc(key))?.json
  }

  // MARK: - set-by-set logging

  func edit(_ slug: String, _ change: (inout [SetRow]) -> Void) {
    editing = true
    var r = rows[slug] ?? []
    change(&r)
    rows[slug] = r
    saveState = tr("保存中…", "Saving…")
    saveTask?.cancel()
    saveTask = Task { [weak self] in
      try? await Task.sleep(nanoseconds: 700_000_000)
      guard !Task.isCancelled else { return }
      await self?.saveDraft()
    }
  }

  /// The day's draft, local first; a queued 「提交」 is put back after it so it still submits the newest sets.
  func saveDraft() async {
    guard var s = session else { return }
    s.rows = rows
    let hadSubmit = s.pendingSubmit != nil
    let out = await write(kind: RecordKind.session, date: today, group: "training:\(today)", method: .put,
                          path: RecordAPI.sessionPath(today), body: s.payload(), supersede: true)
    if hadSubmit {
      _ = await write(kind: RecordKind.submit, date: today, group: "training:\(today)", method: .post,
                      path: RecordAPI.sessionPath(today) + "/submit", body: ["allow_duplicate": false])
    }
    editing = false
    saveState = out.sent ? tr("已保存", "Saved") : out.queued ? tr("离线，已存在本机", "Offline, kept on this phone") : out.message
    if out.sent { await pull([DocFetch(RecordDocs.session(today), RecordAPI.session(today))]) }
  }

  /// 「提交」: the draft first, then the submit. Returns false when the server kept it as a draft (the same exercises
  /// were already logged today): the page offers 「仍然提交」.
  func submit(allowDuplicate: Bool = false) async -> (WriteOutcome, keptAsDraft: Bool) {
    saveTask?.cancel()
    await saveDraft()
    let out = await write(kind: RecordKind.submit, date: today, group: "training:\(today)", method: .post,
                          path: RecordAPI.sessionPath(today) + "/submit", body: ["allow_duplicate": allowDuplicate])
    guard out.sent else { return (out, false) }
    await pull([DocFetch(RecordDocs.session(today), RecordAPI.session(today)), DocFetch(RecordDocs.calendar(month), RecordAPI.calendar(month)),
                DocFetch(RecordDocs.week(monday), RecordAPI.week(today))])
    let log = (try? db.doc(RecordDocs.session(today)))?.json?["log"] as? [String: Any]
    return (out, (log?["status"] as? String) != "submitted")
  }
}
