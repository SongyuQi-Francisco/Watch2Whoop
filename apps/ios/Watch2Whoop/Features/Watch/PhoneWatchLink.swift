import Combine
import Foundation
import WatchConnectivity

// The iPhone's half of the watch link (docs/watch-app-plan.md 4.4 and 4.6). The phone stays the only writer: it pushes
// today's plan, today's numbers (its own Today: recovery, sleep, strain and its sync capsule; the watch computes nothing)
// and the quick-log tiles to the watch; it merges the watch's set events into the day's draft (`TrainingStore.saveDraft` /
// `submit`) and logs the watch's quick meals and drinks with the same calls as its own quick-meal strip and water card
// (`FoodStore.log` / `addWater`, the watch's event id as the write key), so the existing local-first write and offline
// queue send them. No new endpoint, no backend change.

/// Today's plan as the watch needs it.
enum WatchPlanBuilder {
  static func plan(from s: TrainingSession?) -> WatchPlan? {
    guard let s, !s.restDay, s.canLog else { return nil }
    let exercises = s.loggable.map { e -> WatchExercise in
      let rows = s.rows[e.slug] ?? []
      return WatchExercise(slug: e.slug, name: e.name, sets: max(rows.count, e.targetSets, 1),
                           done: rows.prefix { $0.done }.count,
                           reps: e.timed ? nil : e.target.reps, weightKg: e.weighted ? e.target.weightKg : nil,
                           durationS: e.timed ? e.target.durationS : nil, weighted: e.weighted,
                           restSec: e.restSec ?? WatchParams.defaultRestSec, weightStepKg: WatchParams.defaultWeightStepKg,
                           last: e.lastSets)
    }
    guard !exercises.isEmpty else { return nil }
    return WatchPlan(day: s.day, title: s.title, exercises: exercises)
  }
}

/// Today's numbers for the watch (plan 2.4, phase 2a): what the phone's Today shows, never recomputed.
enum WatchTodayBuilder {
  static func today(_ m: TodayModel, day: String?, capsule: SyncCapsuleState, offline: Bool, now: Date) -> WatchToday? {
    guard let day else { return nil }
    let g = m.glance
    let rec = g.recovery.value
    return WatchToday(day: day, recovery: rec, tone: rec.map(tone), usualLo: g.recovery.usual?.lowerBound,
                      usualHi: g.recovery.usual?.upperBound, verdict: rec.map(verdict), reason: rec == nil ? g.recovery.reason : nil,
                      sleep: g.sleep.value, strain: g.strain.value, computedAt: now,
                      dataAt: m.sync?.minutes.map { now.addingTimeInterval(-Double($0) * 60) }, phone: phone(capsule, offline: offline))
  }

  static func tone(_ v: Double) -> WatchTone {
    switch TodayModel.zone(v) {
    case .good: return .good
    case .mid: return .mid
    default: return .bad
    }
  }

  /// The watch's word under the number: at most 4 characters / 2 English words (plan 3), by the phone's zone; the
  /// mockups' words (D1 「可上强度」, D2 「正常练」).
  static func verdict(_ v: Double) -> String {
    switch tone(v) {
    case .good: return tr("可上强度", "Push hard")
    case .mid: return tr("正常练", "Train normally")
    case .bad: return tr("先恢复", "Recover first")
    }
  }

  /// The phone's capsule as the watch's mark shows it.
  static func phone(_ c: SyncCapsuleState, offline: Bool) -> WatchPhoneSync {
    switch c {
    case .ok: return WatchPhoneSync(.ok)
    case .busy: return WatchPhoneSync(.busy)
    case .stale(let l): return offline ? WatchPhoneSync(.offline, l) : WatchPhoneSync(.stale, l)
    case .behind(let l): return WatchPhoneSync(.behind, l)
    case .off: return WatchPhoneSync(.off, tr("同步已关", "Sync off"))
    case .failed: return WatchPhoneSync(.failed)
    }
  }
}

/// The quick-log tiles for the watch (plan 2.2): the phone's most used quick meals (the ones that log in one tap: a
/// template whose grams still need confirming opens a sheet on the phone, so it is not offered), water when it is on.
enum WatchQuickBuilder {
  static func quick(templates: [MealTemplate], nutrition: NutritionDay?, day: String, water: Bool,
                    results: [String: Bool]) -> WatchQuick {
    let meals = MealTemplate.byUse(templates).filter { !$0.needsConfirmation }.prefix(WatchParams.quickMealsMax).map {
      WatchQuickMeal(id: $0.id, name: $0.name, slot: $0.meal.title, kcal: $0.totals.kcal, proteinG: $0.totals.protein)
    }
    let n = nutrition?.date == day ? nutrition : nil
    return WatchQuick(day: day, meals: Array(meals), waterMl: water ? WatchParams.waterQuickMl : nil, kcal: n?.intake.kcal,
                      kcalGoal: n?.goal.kcal, results: results)
  }
}

/// Merging the watch's events into the day's rows (pure; `PhoneWatchBridge` feeds it, the tests call it directly).
enum WatchEventMerge {
  struct Result: Equatable {
    var rows: [String: [SetRow]]
    /// Events used (set or end), in the order applied.
    var applied: [String]
    /// An `end` event was among them: the phone submits the day.
    var ended: Bool
    /// Events for another day or an exercise not in today's plan: kept for later, not lost.
    var held: [WatchSetEvent]
  }

  /// `seen`: ids already applied (a transfer can arrive twice). Events are applied in the order they happened on the
  /// watch; a later event for the same exercise and set index replaces the earlier one.
  static func apply(_ events: [WatchSetEvent], to rows: [String: [SetRow]], day: String, exercises: [PlannedExercise],
                    seen: Set<String>) -> Result {
    var out = rows
    var applied: [String] = []
    var held: [WatchSetEvent] = []
    var ended = false
    var done = seen
    for e in events.sorted(by: { $0.at < $1.at }) where !done.contains(e.id) {
      guard e.day == day else { held.append(e); continue }
      switch e.kind {
      case .end:
        ended = true
      case .set:
        guard let slug = e.slug, let i = e.setIndex, i >= 0, let ex = exercises.first(where: { $0.slug == slug }), !ex.banned else {
          held.append(e)
          continue
        }
        var list = out[slug] ?? []
        while list.count <= i { list.append(SetRow(reps: ex.target.reps, weightKg: ex.target.weightKg, durationS: ex.target.durationS, done: false)) }
        list[i] = SetRow(reps: e.reps, weightKg: e.weightKg, durationS: e.durationS, done: true)
        out[slug] = list
      case .meal, .water, .undo, .voiceUndo:
        continue   // quick logs are not training: the bridge's quick-log path handles them
      }
      applied.append(e.id)
      done.insert(e.id)
    }
    return Result(rows: out, applied: applied, ended: ended, held: held)
  }
}

/// Ids already merged, events held for another day, and the watch's recent quick logs with whether each is counted in the
/// day now (its undo needs the original; the watch's receipt needs the count), kept across launches (UserDefaults; a
/// few KB at most).
struct PhoneWatchInbox: Codable, Equatable {
  struct Quick: Codable, Equatable {
    var event: WatchSetEvent
    /// In the day's intake now: logged (sent or queued); false = undone, or the phone could not log it.
    var counted: Bool
  }

  var seen: [String] = []
  var held: [WatchSetEvent] = []
  var quick: [Quick] = []
  /// Undo events that could not reach the server yet: tried again with the next event or when the watch link wakes.
  var undoLater: [WatchSetEvent] = []

  init() {}

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    seen = try c.decodeIfPresent([String].self, forKey: .seen) ?? []
    held = try c.decodeIfPresent([WatchSetEvent].self, forKey: .held) ?? []
    quick = try c.decodeIfPresent([Quick].self, forKey: .quick) ?? []
    undoLater = try c.decodeIfPresent([WatchSetEvent].self, forKey: .undoLater) ?? []
  }

  mutating func remember(_ ids: [String]) {
    seen.append(contentsOf: ids)
    if seen.count > WatchParams.phoneSeenIdsKept { seen.removeFirst(seen.count - WatchParams.phoneSeenIdsKept) }
  }

  mutating func logged(_ e: WatchSetEvent, counted: Bool) {
    quick.removeAll { $0.event.id == e.id }
    quick.append(Quick(event: e, counted: counted))
    if quick.count > WatchParams.phoneResultsKept { quick.removeFirst(quick.count - WatchParams.phoneResultsKept) }
  }

  mutating func uncount(_ id: String) {
    for i in quick.indices where quick[i].event.id == id { quick[i].counted = false }
  }

  func original(_ id: String) -> Quick? { quick.first { $0.event.id == id } }

  /// What the watch's receipt reads: id → counted.
  var results: [String: Bool] { Dictionary(quick.map { ($0.event.id, $0.counted) }, uniquingKeysWith: { $1 }) }

  static let key = "w2w.native.watchInbox.v1"
  static func load(_ d: UserDefaults = .standard) -> PhoneWatchInbox { WatchCodec.decode(PhoneWatchInbox.self, d.data(forKey: key)) ?? PhoneWatchInbox() }
  func save(_ d: UserDefaults = .standard) { d.set(WatchCodec.encode(self), forKey: Self.key) }
}

/// When the phone does anything for the watch: only with a paired watch that has the app installed. Without one the
/// bridge stays silent: no training store, no network pull, no timer, no error (activating WCSession itself is a cheap
/// local call the system answers once). Pure, so the rule is unit-tested.
enum WatchLinkGate {
  static func linked(supported: Bool, activated: Bool, paired: Bool, appInstalled: Bool) -> Bool {
    supported && activated && paired && appInstalled
  }
}

/// WatchConnectivity on the phone. Attached to the signed-in account; it only starts its work (today's training, numbers
/// and quick meals, the pushes) once the system reports a paired watch with the app installed (`WatchLinkGate`), and stops
/// when that ends.
@MainActor
final class PhoneWatchBridge: NSObject, ObservableObject {
  static let shared = PhoneWatchBridge()

  private var account: AccountSession?
  private var store: TrainingStore?
  private var today: TodayStore?
  private var food: FoodStore?
  private var cancellable: AnyCancellable?
  private var inbox = PhoneWatchInbox.load()
  private var queued: [WatchSetEvent] = []
  private var quickQueued: [WatchSetEvent] = []
  private var working = false
  private var quickWorking = false
  private var lastPushed: WatchContext?
  private var lastTodayRefresh: Date?
  /// The account's AI as `/v1/users/me/ai-choice` → `ai` says (mode, fallback consent): decides the watch's voice key.
  private var aiMode: String?
  private var aiConsent = false
  /// The last watch code asked for (the watch asks at most once a minute; the phone too).
  private var lastCodeAt: Date?

  private var wc: WCSession? { WCSession.isSupported() ? WCSession.default : nil }

  /// Whether a watch is there to talk to right now.
  private var linked: Bool {
    guard let wc else { return false }
    return WatchLinkGate.linked(supported: true, activated: wc.activationState == .activated, paired: wc.isPaired,
                                appInstalled: wc.isWatchAppInstalled)
  }

  /// Called when an account opens. Only activates WatchConnectivity; the rest waits for a watch (`update`).
  func attach(_ session: AccountSession) {
    account = session
    guard let wc else { return }
    if wc.delegate == nil {
      wc.delegate = self
      wc.activate()   // answered once by activationDidComplete; nothing runs until then
    } else {
      update()
    }
  }

  /// Sign-out: the watch (if any) shows 「在手机上登录」 and keeps its own queue until someone signs in again.
  func detach() {
    let wasLinked = linked
    account = nil
    stopStores()
    lastPushed = nil
    aiMode = nil
    lastCodeAt = nil
    if wasLinked { send(WatchContext(signedIn: false, plan: nil, lang: Lang.code, sentAt: Date())) }
  }

  /// A watch appeared or went away (activation, pairing, app installed / removed): start or stop the work.
  fileprivate func update() {
    guard linked, let account else { stopStores(); return }
    if store == nil {
      let s = TrainingStore(session: account)
      let t = TodayStore(session: account)   // reads the local copy (first value at once); the Today tab refreshes it
      let f = FoodStore(session: account, cardOnly: true)
      store = s
      today = t
      food = f
      // anything the watch shows changed: one push after things settle (updateApplicationContext keeps the newest only)
      let changes: [AnyPublisher<Void, Never>] = [
        s.$session.map { _ in () }.eraseToAnyPublisher(), t.$model.map { _ in () }.eraseToAnyPublisher(),
        f.$templates.map { _ in () }.eraseToAnyPublisher(), f.$nutrition.map { _ in () }.eraseToAnyPublisher(),
        account.sync.$status.map { _ in () }.eraseToAnyPublisher(),
        HealthSyncService.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
        account.$flags.map { _ in () }.eraseToAnyPublisher(),
      ]
      cancellable = Publishers.MergeMany(changes).debounce(for: .milliseconds(600), scheduler: RunLoop.main)
        .sink { [weak self] in self?.push() }
      Task {
        await refreshAI()
        await s.refresh()
        await f.refresh()
        await drain()
        await drainQuick()
        push()
      }
    } else {
      push()
      Task { await drainQuick() }
    }
  }

  private func stopStores() {
    cancellable = nil
    store = nil
    today?.stop()
    today = nil
    food?.stop()
    food = nil
  }

  /// The watch's whole context from the phone's stores, sent when it differs from the last one in more than its times.
  private func push(force: Bool = false) {
    guard linked, let account, let store else { return }
    let now = Date()
    let userToday = today?.userToday ?? account.db.userToday()
    let d = HealthSyncService.shared.diagnosis
    let capsule = SyncCapsuleState.of(overall: d.overall, page: account.sync.status, card: today?.model.sync,
                                      background: d.background)
    let t = today.flatMap { WatchTodayBuilder.today($0.model, day: $0.userToday, capsule: capsule, offline: account.sync.status == .offline, now: now) }
    let q = food.map { f in
      WatchQuickBuilder.quick(templates: f.templates, nutrition: f.nutrition, day: userToday, water: account.flags.contains("water"),
                              results: inbox.results)
    }
    let c = WatchContext(signedIn: true, plan: WatchPlanBuilder.plan(from: store.session), lang: Lang.code, sentAt: now,
                         planFor: store.session?.day, today: t, quick: q, voice: voiceOffer(account))
    guard force || !Self.sameExceptTimes(c, lastPushed) else { return }
    lastPushed = c
    send(c)
  }

  /// Voice on the watch (plan 2.3, Q6): an in-app AI and the direct-voice flag; nil until the AI is known.
  private func voiceOffer(_ a: AccountSession) -> WatchVoiceOffer? {
    guard aiMode != nil else { return nil }
    return WatchVoiceOffer.of(mode: aiMode, consent: aiConsent, direct: a.flags.contains("jarvis_voice_direct"),
                              account: Self.accountKey(a))
  }

  /// Who is signed in, for the watch to tell a token of another account (opaque to the watch).
  static func accountKey(_ a: AccountSession) -> String { "\(a.config.userId ?? 0)@\(a.config.server)" }

  /// `GET /v1/users/me/ai-choice` → `ai.mode` / `ai.consent` (an unanswered request keeps what was known).
  private func refreshAI() async {
    guard let account, let j = try? await account.api.json(.get(Endpoint.aiChoice)), let ai = j["ai"] as? [String: Any] else { return }
    aiMode = ai["mode"] as? String ?? "none"
    aiConsent = ai["consent"] as? Bool ?? false
  }

  /// The watch has no token of its own (first time, or a 401): ask the server for a one-time watch code with this
  /// phone's token (`POST /v1/account/watch-code`, #458 A) and hand it over outside the application context: at once
  /// when the watch is reachable, else queued (the watch drops a code that expired on the way and asks again).
  fileprivate func pairWatch() async {
    guard linked, let account, let wc, voiceOffer(account)?.on == true else { return }
    if let t = lastCodeAt, Date().timeIntervalSince(t) < WatchParams.pairAskEverySec / 2 { return }
    lastCodeAt = Date()
    guard let j = try? await account.api.json(try .json(.post, Endpoint.watchCode, ["device": "Apple Watch"], idempotent: false)),
          let code = j["code"] as? String else {
      lastCodeAt = nil
      return
    }
    let c = WatchPairCode(server: account.config.server, code: code, account: Self.accountKey(account), at: Date(),
                          expiresIn: (j["expires_in"] as? NSNumber)?.intValue ?? 600)
    let msg = WatchCodec.message(c)
    if wc.isReachable {
      wc.sendMessage(msg, replyHandler: nil) { _ in wc.transferUserInfo(msg) }
    } else {
      wc.transferUserInfo(msg)
    }
  }

  /// J4 「在手机上继续」: the watch's conversation opens in Jarvis (now if the app is open, else at the next start).
  fileprivate func continueOnPhone(_ conversation: Int) {
    JarvisRouter.continueFromWatch(conversation)
  }

  /// Two contexts that differ only in when they were made (a push of the same numbers would only wake the watch).
  static func sameExceptTimes(_ a: WatchContext, _ b: WatchContext?) -> Bool {
    guard var b else { return false }
    var a = a
    let t0 = Date(timeIntervalSince1970: 0)
    a.sentAt = t0
    b.sentAt = t0
    a.today?.computedAt = t0
    b.today?.computedAt = t0
    // the data's age moves with `now`: equal within a minute
    if let x = a.today?.dataAt, let y = b.today?.dataAt, abs(x.timeIntervalSince(y)) < 60 { b.today?.dataAt = x }
    return a == b
  }

  private func send(_ c: WatchContext) {
    guard linked, let wc else { return }
    try? wc.updateApplicationContext(WatchCodec.message(c))
  }

  fileprivate func received(_ e: WatchSetEvent) {
    if e.kind == .voiceUndo {
      quickQueued.append(e)
      if food == nil { update() }
      Task { await drainQuick() }
      return
    }
    if e.isTraining {
      queued.append(e)
      if store == nil { update() }
      Task { await drain() }
    } else {
      quickQueued.append(e)
      if food == nil { update() }
      Task { await drainQuick() }
    }
  }

  /// 「Send me the plan and today's numbers again」 (the watch's 「计划没同步」 key, its sync mark).
  fileprivate func requested() async {
    guard account != nil else {
      send(WatchContext(signedIn: false, plan: nil, lang: Lang.code, sentAt: Date()))
      return
    }
    update()
    await refreshAI()
    await store?.refresh()
    // the Today numbers come from the server's documents and the phone's own scoring: at most once a minute from here
    if let t = today, Date().timeIntervalSince(lastTodayRefresh ?? .distantPast) > 60 {
      lastTodayRefresh = Date()
      await t.refresh()
    }
    await food?.refresh()
    push(force: true)
  }

  /// Merges what arrived (and what was held) into today's draft, saves it the local-first way, submits on 「end」.
  private func drain() async {
    guard !working, let store, let session = store.session else { return }
    let events = inbox.held + queued
    guard !events.isEmpty else { return }
    working = true
    defer { working = false }
    queued.removeAll()
    let r = WatchEventMerge.apply(events, to: store.rows, day: session.day, exercises: session.exercises, seen: Set(inbox.seen))
    inbox.held = r.held
    inbox.remember(r.applied)
    inbox.save()
    if r.rows != store.rows {
      store.rows = r.rows
      await store.saveDraft()
    }
    if r.ended { _ = await store.submit() }
    if !queued.isEmpty { await drain() }
  }

  /// The watch's quick meals, drinks and undos, in the order they happened, each once (its id is the write's key, so a
  /// transfer that arrives twice is stored once here and on the server).
  private func drainQuick() async {
    guard !quickWorking, let food else { return }
    let events = (inbox.undoLater + quickQueued).sorted { $0.at < $1.at }
    guard !events.isEmpty else { return }
    quickWorking = true
    defer { quickWorking = false }
    quickQueued.removeAll()
    inbox.undoLater.removeAll()
    for e in events {
      let retry = e.kind == .undo && inbox.seen.contains(e.id)
      guard retry || !inbox.seen.contains(e.id) else { continue }
      switch e.kind {
      case .meal, .water:
        inbox.logged(e, counted: await logQuick(e, food: food))
      case .voiceUndo:
        if !(await undoVoice(e, food: food)) { inbox.undoLater.append(e) }   // no connection: again later
      case .undo:
        guard let target = e.target, let orig = inbox.original(target), orig.counted else { break }
        if await undoQuick(orig.event, food: food) {
          inbox.uncount(target)
        } else {
          inbox.undoLater.append(e)   // no connection: again later
        }
      case .set, .end:
        break
      }
      if !retry { inbox.remember([e.id]) }
      inbox.save()
    }
    push()
    if !quickQueued.isEmpty { await drainQuick() }
  }

  /// Logs one tile the way the phone's own strip / water card does. Counted = it is in the day now (sent or queued).
  private func logQuick(_ e: WatchSetEvent, food: FoodStore) async -> Bool {
    let out: WriteOutcome
    switch e.kind {
    case .meal:
      // the watch's tap is the decision (the receipt has its undo): another logging of a meal already logged today is kept
      guard let id = e.templateId, let t = food.templates.first(where: { $0.id == id }) else { return false }
      out = await food.log(t, again: true, day: e.day, clientId: e.id)
    case .water:
      out = await food.addWater(e.ml ?? WatchParams.waterQuickMl, day: e.day, at: e.at, clientId: e.id)
    default:
      return false
    }
    if !out.saved { return LocalRecords.outboxState(food.db, clientId: e.id) != nil }   // stored before a relaunch
    return out.sent || out.queued
  }

  /// A meal Jarvis logged from the watch's voice (#458 D): the whole logging by its id, else each entry it wrote. One
  /// already gone (404) counts as undone. false = no connection (tried again later).
  private func undoVoice(_ e: WatchSetEvent, food: FoodStore) async -> Bool {
    for w in e.writes ?? [] {
      if let id = w.logId {
        guard await food.undoMealLog(id: id, clientId: nil) else { return false }
        continue
      }
      for id in w.entryIds {
        do {
          guard let api = account?.api else { return false }
          _ = try await api.data(APIRequest(method: .delete, path: FoodAPI.foodEntry(id),
                                            query: [URLQueryItem(name: "date", value: w.date)]))
        } catch APIError.notFound {
        } catch {
          return false
        }
      }
    }
    await food.refresh()
    return true
  }

  private func undoQuick(_ e: WatchSetEvent, food: FoodStore) async -> Bool {
    switch e.kind {
    case .meal:
      guard let id = e.templateId else { return true }
      return await food.undoWrite(clientId: e.id, path: FoodAPI.logTemplate(id), body: ["date": e.day, "allow_duplicates": true]) { j in
        (j["log_id"] as? NSNumber).map { FoodAPI.mealLog($0.intValue) }
      }
    case .water:
      let at = ISO8601DateFormatter.localWithOffset.string(from: e.at)
      return await food.undoWrite(clientId: e.id, path: FoodAPI.waterPath,
                                  body: ["ml": e.ml ?? WatchParams.waterQuickMl, "source": "button", "at": at]) { j in
        ((j["entry"] as? [String: Any])?["id"] as? NSNumber).map { FoodAPI.waterEntry(String($0.intValue)) }
      }
    default:
      return true
    }
  }
}

extension PhoneWatchBridge: WCSessionDelegate {
  nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
    Task { @MainActor in self.update() }
  }

  nonisolated func sessionWatchStateDidChange(_ session: WCSession) {
    Task { @MainActor in self.update() }
  }

  nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}

  nonisolated func sessionDidDeactivate(_ session: WCSession) {
    session.activate()   // switched to another watch
  }

  nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
    if userInfo[WatchCodec.pairRequestKey] != nil {
      Task { @MainActor in await self.pairWatch() }
      return
    }
    if let c = WatchCodec.continued(userInfo) {
      Task { @MainActor in self.continueOnPhone(c) }
      return
    }
    guard let e = WatchCodec.event(userInfo) else { return }
    Task { @MainActor in self.received(e) }
  }

  nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
    if message[WatchCodec.pairRequestKey] != nil {
      Task { @MainActor in await self.pairWatch() }
      return
    }
    guard message[WatchCodec.requestKey] != nil else { return }
    Task { @MainActor in await self.requested() }
  }
}
