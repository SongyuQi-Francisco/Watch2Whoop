import Combine
import SwiftUI
import UIKit

/// Connects the cockpit's machine to what the app can already do (2.0.1):
/// - voice: the phone's recogniser (JarvisVoice) with the live words above the core, then the chat (JarvisChat.sendVoice,
///   the same path as the old hold-to-talk: the server's corrected words replace the phone's when they come in time);
/// - 记一餐 → a quick meal: logged at once from the phone (FoodStore.log, the offline queue), undo = FoodStore.undoLog;
/// - 记一餐 → 说: the AI logs it; the card shows the entries the day gained during the turn, undo deletes them;
/// - 记训练 → 说一组: the AI logs it; the card shows the set today's session gained, undo unticks it;
/// - the keys whose own screens are not designed yet open the app's existing entry (camera, chat, 快记, 记录 → 训练).
@MainActor
final class CockpitController: ObservableObject {
  /// Stepped by the view every frame. Not published: the frames redraw from the timeline, not from this object.
  var machine = CockpitMachine()
  /// Something moves: the view's timeline runs at the display's rate (otherwise a slow tick for the core's own turn).
  @Published private(set) var active = false
  /// The mode, published when it changes (hit area, accessibility).
  @Published private(set) var mode: CockpitMode = .idle
  @Published private(set) var level = 1
  @Published private(set) var reply: CockpitReply?
  /// The card's undo is running.
  @Published private(set) var undoing = false

  let voice: JarvisVoice
  private weak var router: JarvisRouter?
  private weak var session: AccountSession?
  private var food: FoodStore?
  private var training: TrainingStore?
  private var lastFrame: CFTimeInterval?
  private var undo: (() async -> Bool)?
  private var work: Task<Void, Never>?
  private var cancellables = Set<AnyCancellable>()

  init(voice: JarvisVoice) {
    self.voice = voice
    voice.$partial.sink { [weak self] t in
      guard let self, self.voice.running else { return }
      self.machine.heard(t)
    }
    .store(in: &cancellables)
  }

  func attach(router: JarvisRouter, session: AccountSession) {
    self.router = router
    self.session = session
  }

  /// The cockpit opens only where the chat runs in the app (own API key, or the fallback AI); for any other AI the key does
  /// what the Today page's key does (JarvisRouter.tapKey).
  var inApp: Bool { router?.ai.inApp ?? false }

  // MARK: - frames and input

  /// One frame of the timeline. Runs inside the view's update: published values and side effects wait for the next turn
  /// of the main queue.
  func frame(_ now: CFTimeInterval) {
    let dt = lastFrame.map { now - $0 } ?? 0
    lastFrame = now
    machine.voiceLevel = voice.running ? (voice.levels.last ?? 0) : 0
    let fx = machine.step(dt)
    let m = machine.mode, lv = machine.level, act = machine.isActive
    if !fx.isEmpty || m != mode || lv != level || act != active {
      DispatchQueue.main.async { [weak self] in
        guard let self else { return }
        self.publish(mode: m, level: lv, active: act)
        self.handle(fx)
      }
    }
  }

  /// A touch on the cockpit (the core's centre `c`).
  func tap(at p: CGPoint, center c: CGPoint) {
    if machine.mode == .idle, hypot(p.x - c.x, p.y - c.y) < CockpitGeometry.coreHit, !inApp {
      router?.tapKey()
      return
    }
    apply(machine.tap(at: p, center: c))
  }

  /// A long press on the closed core: talk at once (a question).
  func holdCore() {
    guard inApp, machine.mode == .idle else { return }
    JarvisHaptics.medium.impactOccurred()
    apply(machine.startListening(.qa))
  }

  /// VoiceOver: a panel by its key, the way back, the core.
  func activate(_ key: CockpitKey) { machine.activate(key); wake() }
  func back() { machine.back(); wake() }
  func tapCore() {
    if machine.mode == .idle && !inApp { router?.tapKey(); return }
    apply(machine.tapCore())
  }

  /// From outside: the tab changed, the app went to the background.
  func close() { apply(machine.close()) }

  private func apply(_ fx: [CockpitEffect]) {
    publish(mode: machine.mode, level: machine.level, active: true)
    handle(fx)
  }

  /// The timeline is slow while idle: anything that starts a movement switches it to full rate first.
  private func wake() {
    if !active { active = true }
  }

  private func publish(mode m: CockpitMode, level lv: Int, active act: Bool) {
    if m != mode {
      mode = m
      if m != .card { reply = nil; undo = nil }
      if m == .idle || m == .menu { work?.cancel() }
    }
    if lv != level { level = lv }
    if act != active { active = act }
  }

  // MARK: - effects

  private func handle(_ fx: [CockpitEffect]) {
    for e in fx {
      switch e {
      case .opened:
        JarvisHaptics.light.impactOccurred()
        JarvisPerf.shared.measure("cockpit-open", seconds: 0.6)
        prepare()
      case .closed:
        break
      case .listen(let flow):
        JarvisHaptics.light.impactOccurred()
        startVoice(flow)
      case .stopListening:
        voice.cancel()
      case .gaveUp:
        router?.show(tr("没听清，再说一次", "Didn't catch that; try again"))
      case .send(let flow):
        JarvisHaptics.selection.selectionChanged()
        send(flow)
      case .run(let key):
        run(key)
      }
    }
  }

  /// The stores the second rings read, and the two quick meals of 记一餐.
  private func prepare() {
    guard let session else { return }
    if food == nil { food = FoodStore(session: session, cardOnly: true) }
    if training == nil { training = TrainingStore(session: session) }
    machine.meals = topMeals().map { (name: $0.name, kcal: $0.totals.kcal) }
    Task { [weak self] in await self?.food?.refresh(); self?.refreshMeals() }
  }

  private func refreshMeals() {
    machine.meals = topMeals().map { (name: $0.name, kcal: $0.totals.kcal) }
  }

  /// The two most used quick meals of the last 30 days.
  private func topMeals() -> [MealTemplate] {
    Array(MealTemplate.byUse(food?.templates ?? []).prefix(2))
  }

  // MARK: - keys with their own action

  private func run(_ key: CockpitKey) {
    guard let router else { return }
    switch key {
    case .template(let i):
      logTemplate(i)
    case .photo, .mealPhoto:
      router.run(.photo)
    case .ask:
      router.run(.chat)
    case .mealMore:
      router.run(.quick)
    case .trainStart, .trainBackfill, .trainPlan:
      router.open(page: "record.training")
    default:
      break
    }
  }

  private func logTemplate(_ slot: Int) {
    let meals = topMeals()
    guard slot < meals.count, let food else { failed(nil); return }
    let t = meals[slot]
    work?.cancel()
    work = Task { [weak self] in
      let before = food.nutrition?.intake.kcal ?? 0
      let out = await food.log(t)
      guard let self, !Task.isCancelled else { return }
      if out.conflict {
        food.drop(out)
        self.failed(tr("今天已记过「\(t.name)」", "\(t.name) is already logged today"))
        return
      }
      guard out.sent || out.queued else { self.failed(out.message); return }
      JarvisHaptics.notice.notificationOccurred(.success)
      let day = food.nutrition
      self.undo = { [weak food] in
        guard let food else { return false }
        let cur = food.templates.first { $0.id == t.id } ?? t
        return await food.undoLog(cur)
      }
      self.show(.meal(Self.mealCard(slot: t.meal.title, name: t.name, added: t.totals, before: before, day: day)))
    }
  }

  // MARK: - voice

  private func startVoice(_ flow: CockpitFlow) {
    guard let router else { return }
    let ai = router.ai
    if ai.mode == .fallback && !ai.consent {
      machine.close()
      publish(mode: .idle, level: 1, active: true)
      router.run(.chat)   // the terms first (then the user tries again)
      return
    }
    guard !JarvisVoice.supportedLangs.isEmpty else {
      failed(tr("这台手机不能本机识别语音：用键盘上的麦克风听写", "This phone can't recognise speech on device: use the keyboard's dictation"))
      return
    }
    if flow == .set, training == nil, let session { training = TrainingStore(session: session) }
    if flow == .meal, food == nil, let session { food = FoodStore(session: session, cardOnly: true) }
    var lang = JarvisVoice.lang
    if !JarvisVoice.supportedLangs.contains(lang) { lang = JarvisVoice.supportedLangs[0] }
    #if DEBUG
    let keep = lang == "zh" && JarvisDemo.cockpitPhrase == nil   // the demo's words are the script's, not the fake server's
    #else
    let keep = lang == "zh"
    #endif
    work?.cancel()
    work = Task { [weak self] in
      do {
        try await self?.voice.start(lang: lang, keepAudio: keep)
        JarvisPerf.shared.measure("cockpit-listen", seconds: 0.5)
      } catch let f as JarvisVoice.Failure {
        self?.failed(f == .permission
                     ? tr("麦克风或语音识别没打开：在系统设置里允许 Watch2Whoop", "Microphone or speech recognition is off: allow Watch2Whoop in Settings")
                     : tr("语音识别暂时不可用，用键盘上的麦克风听写", "Speech recognition isn't available; use the keyboard's dictation"))
      } catch {
        self?.failed(nil)
      }
    }
  }

  private func send(_ flow: CockpitFlow) {
    guard let chat = router?.chat else { failed(nil); return }
    let question = machine.words
    work?.cancel()
    work = Task { [weak self] in
      guard let self else { return }
      guard var take = await self.voice.finish() else {
        self.failed(tr("没听清，再说一次", "Didn't catch that; try again"))
        return
      }
      var correction: Task<String?, Never>?
      if take.text.isEmpty {   // nothing on the phone: the server's words, as the old hold-to-talk did
        guard let t = await chat.first(chat.voiceCorrection(take), within: JarvisChat.voiceBoxWait), !t.isEmpty else {
          self.failed(tr("没听清，再说一次", "Didn't catch that; try again"))
          return
        }
        take = JarvisVoice.Take(text: t, seconds: take.seconds, wav: nil, final: Task { t })
        correction = Task { nil }
      }
      guard !Task.isCancelled, self.machine.mode == .think else { return }
      if chat.busy {
        self.failed(tr("上一条还在回答，等它说完再发", "Still answering the last message; wait for it to finish"))
        return
      }
      // what the turn may change, as it was before
      let foodBefore = Set(self.food?.nutrition?.entries.map(\.id) ?? [])
      let kcalBefore = self.food?.nutrition?.intake.kcal ?? 0
      let setsBefore = Self.doneSets(self.training?.session)
      let start = chat.items.count
      chat.page = self.router?.page ?? chat.page
      chat.sendVoice(take.text, seconds: take.seconds, correction: correction ?? chat.voiceCorrection(take))
      guard let items = await Self.turn(chat, from: start), !Task.isCancelled, self.machine.mode == .think else {
        if self.machine.mode == .think { self.failed(tr("Jarvis 没有回应，稍后再试", "Jarvis didn't answer; try again later")) }
        return
      }
      await self.finishTurn(flow, question: question, items: items, foodBefore: foodBefore, kcalBefore: kcalBefore,
                            setsBefore: setsBefore)
    }
  }

  /// Waits for the chat's turn to end (at most 90 s) and returns its items.
  private static func turn(_ chat: JarvisChat, from start: Int) async -> [JarvisChat.Item]? {
    let deadline = Date().addingTimeInterval(90)
    while chat.busy {
      if Date() > deadline || Task.isCancelled { return nil }
      try? await Task.sleep(nanoseconds: 100_000_000)
    }
    return Array(chat.items.dropFirst(min(start, chat.items.count)))
  }

  private func finishTurn(_ flow: CockpitFlow, question: String, items: [JarvisChat.Item], foodBefore: Set<String>,
                          kcalBefore: Double, setsBefore: [String: Int]) async {
    var text = "", cards: [JSONValue] = [], needsConfirm = false, notes: [String] = []
    for it in items {
      switch it.kind {
      case .assistant(let t): text = t
      case .card(let c): cards.append(c)
      case .confirm(_, approved: nil): needsConfirm = true
      case .note(let t, _, _): notes.append(t)
      default: break
      }
    }
    if needsConfirm {   // the AI wants a yes first (a delete or an undo): that happens in the conversation
      machine.close()
      publish(mode: .idle, level: 1, active: true)
      router?.present(.chat)
      return
    }
    if text.isEmpty && cards.isEmpty {
      failed(notes.last ?? tr("Jarvis 没有回应，稍后再试", "Jarvis didn't answer; try again later"))
      return
    }
    switch flow {
    case .meal:
      if let food {
        await food.refreshToday()
        let added = (food.nutrition?.entries ?? []).filter { !foodBefore.contains($0.id) && !$0.isLocal }
        if !added.isEmpty {
          JarvisHaptics.notice.notificationOccurred(.success)
          let total = added.reduce(Macros(kcal: 0, protein: 0, carbs: 0, fat: 0)) { $0 + $1.macros }
          let name = added.count == 1 ? added[0].food : tr("\(added[0].food) 等 \(added.count) 项", "\(added[0].food) + \(added.count - 1)")
          undo = { [weak food] in
            guard let food else { return false }
            var ok = true
            for e in added {
              let out = await food.delete(e)
              ok = ok && !out.failed && !out.conflict
            }
            return ok
          }
          show(.meal(Self.mealCard(slot: added[0].meal.title, name: name, added: total, before: kcalBefore, day: food.nutrition)))
          return
        }
      }
    case .set:
      if let training {
        await training.refresh()
        if let card = Self.setCard(training.session, before: setsBefore) {
          JarvisHaptics.notice.notificationOccurred(.success)
          undo = { [weak training] in
            guard let training else { return false }
            training.edit(card.slug) { rows in
              if let i = rows.lastIndex(where: \.done) { rows[i].done = false }
            }
            await training.flushDraft()
            return true
          }
          show(.set(card.log))
          return
        }
      }
    case .qa:
      break
    }
    show(.answer(answer(question: question, text: text, cards: cards)))
  }

  // MARK: - cards

  private func show(_ r: CockpitReply) {
    reply = r
    machine.showCard()
    publish(mode: machine.mode, level: machine.level, active: true)
  }

  /// The card's 「撤销」.
  func undoReply() {
    guard let undo, !undoing else { return }
    undoing = true
    Task { [weak self] in
      let ok = await undo()
      guard let self else { return }
      self.undoing = false
      JarvisHaptics.notice.notificationOccurred(ok ? .success : .warning)
      self.router?.show(ok ? tr("已撤销", "Undone") : tr("撤销要联网，稍后再试", "Undo needs a connection, try again later"))
      self.close()
    }
  }

  var canUndo: Bool { undo != nil }

  /// The words were tapped while listening: they go into the chat's box to be edited there (nothing is sent).
  func editWords() {
    let words = machine.words
    guard machine.mode == .listen || machine.mode == .confirm else { return }
    close()
    guard let router, let chat = router.chat else { return }
    if !words.isEmpty { chat.draft = chat.draft.isEmpty ? words : "\(chat.draft)\n\(words)" }
    router.present(.chat)
  }

  /// 「展开对话 ›」: the whole answer in the chat.
  func openChat() {
    close()
    router?.present(.chat)
  }

  /// The answer's primary action (a page of the app).
  func openPrimary(_ page: String) {
    close()
    router?.open(page: page)
  }

  private func failed(_ message: String?) {
    let fx = machine.close()
    publish(mode: .idle, level: 1, active: true)
    if fx.contains(.stopListening) { voice.cancel() }
    if let message {
      JarvisHaptics.notice.notificationOccurred(.warning)
      router?.show(message)
    }
  }

  /// The verdict card: the answer's first words; today's recovery, sleep, strain and HRV from the phone's own copy.
  private func answer(question: String, text: String, cards: [JSONValue]) -> CockpitReply.Answer {
    let (verdict, sub) = CockpitText.verdict(from: text)
    var a = CockpitReply.Answer(question: question, verdict: verdict, sub: sub)
    if let session {
      let today = TodayStore(session: session)
      let m = today.model
      today.stop()
      if let r = m.rings.first(where: { $0.key == "recovery" }), let v = r.value {
        a.gauge = CockpitReply.Gauge(value: v, fraction: r.fraction, tone: r.tone)
      }
      if let s = m.rings.first(where: { $0.key == "sleep" })?.value { a.readings.append(.init(value: s, label: tr("睡眠", "Sleep"))) }
      if let s = m.rings.first(where: { $0.key == "strain" })?.value { a.readings.append(.init(value: s, label: tr("负荷", "Strain"))) }
      if let h = m.detail.hrv { a.readings.append(.init(value: "\(Int(h.rounded())) ms", label: "HRV")) }
    }
    a.primary = Self.primary(cards)
    return a
  }

  /// The first 「open」 action among the turn's cards.
  static func primary(_ cards: [JSONValue]) -> CockpitReply.Primary? {
    for c in cards {
      for act in c["actions"]?.array ?? [] where act["kind"]?.string == "open" {
        guard let page = act["page"]?.string else { continue }
        let title = page == "record.training" ? tr("开始训练", "Start workout") : JarvisCards.openLabel(page)
        if let title { return CockpitReply.Primary(title: title, page: page) }
      }
    }
    return nil
  }

  static func mealCard(slot: String, name: String, added: Macros, before: Double, day: NutritionDay?) -> CockpitReply.Meal {
    let goal = day?.goal
    func frac(_ g: Double?, _ of: Double?) -> Double? {
      guard let g, let of, of > 0 else { return nil }
      return min(1, g / of)
    }
    return CockpitReply.Meal(
      slot: slot, name: name, kcal: added.kcal,
      macros: [CockpitReply.Macro(label: tr("蛋白", "Protein"), grams: added.protein, fraction: frac(added.protein, goal?.protein)),
               CockpitReply.Macro(label: tr("碳水", "Carbs"), grams: added.carbs, fraction: frac(added.carbs, goal?.carbs)),
               CockpitReply.Macro(label: tr("脂肪", "Fat"), grams: added.fat, fraction: frac(added.fat, goal?.fat))],
      dayBefore: before, dayAfter: before + (added.kcal ?? 0), goal: goal?.kcal)
  }

  /// Done sets per exercise.
  static func doneSets(_ s: TrainingSession?) -> [String: Int] {
    var out: [String: Int] = [:]
    for (slug, rows) in s?.rows ?? [:] { out[slug] = rows.filter { $0.done && $0.hasValue }.count }
    return out
  }

  /// The set the turn added: the exercise whose done sets went up.
  static func setCard(_ s: TrainingSession?, before: [String: Int]) -> (slug: String, log: CockpitReply.SetLog)? {
    guard let s else { return nil }
    let after = doneSets(s)
    guard let ex = s.loggable.first(where: { (after[$0.slug] ?? 0) > (before[$0.slug] ?? 0) }),
          let rows = s.rows[ex.slug], let row = rows.last(where: { $0.done && $0.hasValue }) else { return nil }
    let log = CockpitReply.SetLog(session: s.title, exercise: ex.name,
                                  set: CockpitText.set(weightKg: row.weightKg, reps: row.reps, seconds: row.durationS),
                                  index: after[ex.slug] ?? 1, planned: rows.count, done: s.doneSets, total: s.totalSets)
    return (ex.slug, log)
  }
}
