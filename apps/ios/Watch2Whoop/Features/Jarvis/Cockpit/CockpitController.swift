import Combine
import SwiftUI
import UIKit

/// Connects the cockpit's machine to what the app can already do (2.0.1):
/// - voice: the phone's recogniser (JarvisVoice) with the live words above the core (60 s at most, 中文 / English chip),
///   then the chat. Flag `jarvis_voice_direct` on: a pause sends by itself after the one-second ring, the server's corrected
///   words replace the phone's (JarvisChat.sendVoice), as the old hold-to-talk did. Off: the words wait above the core, are
///   corrected there, and go out only when the user taps 发送 (or the core); tapping them edits them in the chat's box.
/// - the reply: the AI's answer as a verdict card (TurnReading). What the AI logged has no id the app could undo by, so the
///   card's 撤销 asks the AI to undo it (it asks for a yes in the conversation); nothing is guessed from before / after.
/// - 记一餐 → a quick meal: logged from the phone (FoodStore.log), 撤销 = that logging's own id (FoodStore.undoLog).
/// - the keys whose screens are not part of this change open the app's existing entry (camera, chat, 快记, 记录 → 训练).
@MainActor
final class CockpitController: ObservableObject {
  /// Stepped by the view every frame. Not published: the frames redraw from the timeline, not from this object.
  var machine = CockpitMachine()
  /// Something moves: the view's timeline runs. When everything has settled it stops (nothing is redrawn while idle).
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
  private var lastFrame: CFTimeInterval?
  private var undo: (() async -> Bool)?
  /// The recording (listen) and the turn (send → reply) run separately: closing the cockpit stops a recording, never a
  /// write that already went out.
  private var listening: Task<Void, Never>?
  private var turn: Task<Void, Never>?
  /// Flag off: the words taken at the pause, corrected while they wait for 发送.
  private var waiting: JarvisVoice.Take?
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
  /// of the main queue, and read the machine as it is then (a tap in between is never overwritten by this frame's state).
  func frame(_ now: CFTimeInterval) {
    let dt = lastFrame.map { now - $0 } ?? 0
    lastFrame = now
    machine.voiceLevel = voice.running ? (voice.levels.last ?? 0) : 0
    let fx = machine.step(dt)
    if !fx.isEmpty || machine.mode != mode || machine.level != level || machine.isActive != active {
      DispatchQueue.main.async { [weak self] in
        guard let self else { return }
        self.sync()
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

  /// A long press on the closed core: talk at once (a question); for an AI outside the app, what a tap does.
  func holdCore() {
    guard inApp else { router?.tapKey(); return }
    guard machine.mode == .idle else { return }
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

  /// 发送 in the confirm state (flag off).
  func sendNow() { apply(machine.sendNow()) }

  /// From outside: the tab changed, the app went to the background.
  func close() { apply(machine.close()) }

  private func apply(_ fx: [CockpitEffect]) {
    wake()
    sync()
    handle(fx)
  }

  /// Anything that starts a movement restarts the timeline (it stops while idle); the first frame after a pause moves by 0.
  private func wake() {
    if !active {
      lastFrame = nil
      active = true
    }
  }

  /// The published values from the machine as it is now.
  private func sync() {
    let m = machine.mode
    if m != mode {
      let was = mode
      mode = m
      if m != .card { reply = nil; undo = nil }
      if m == .confirm && !machine.autoSend { prepareTake() }
      if (was == .listen || was == .confirm) && m != .think { waiting = nil }
    }
    if machine.level != level { level = machine.level }
    if machine.isActive != active { active = machine.isActive || mode != .idle }
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
      case .switchLanguage:
        switchLanguage()
      case .stopListening:
        listening?.cancel()
        voice.cancel()
        waiting = nil
      case .gaveUp:
        voice.cancel()
        router?.show(tr("没听清，再说一次", "Didn't catch that; try again"))
      case .send(let flow):
        JarvisHaptics.selection.selectionChanged()
        send(flow)
      case .run(let key):
        run(key)
      }
    }
  }

  /// The two quick meals of 记一餐, from the phone's copy now and again once the server's list is in.
  private func prepare() {
    guard let session else { return }
    if food == nil { food = FoodStore(session: session, cardOnly: true) }
    machine.meals = Self.meals(food?.templates ?? [])
    Task { [weak self] in
      await self?.food?.refresh()   // sends what is queued, then pulls the quick meals from the server
      guard let self else { return }
      self.machine.meals = Self.meals(self.food?.templates ?? [])
    }
  }

  /// The two most used quick meals of the last 30 days.
  static func meals(_ templates: [MealTemplate]) -> [CockpitMeal] {
    MealTemplate.byUse(templates).prefix(2).map { CockpitMeal(id: $0.id, name: $0.name, kcal: $0.totals.kcal) }
  }

  // MARK: - keys with their own action

  private func run(_ key: CockpitKey) {
    guard let router else { return }
    switch key {
    case .template(let id):
      logTemplate(id)
    case .photo:
      router.run(.photo)
    case .mealPhoto:   // 16: the meal camera (the photo goes to the user's own AI)
      if !router.ai.vision { router.show(tr("这个 AI 看不了图片", "This AI can't read pictures")); return }
      router.present(.mealPhoto)
    case .ask:   // 17: the chat, keyboard up
      router.focusComposer = true
      router.run(.chat)
    case .mealMore:   // 20: every quick meal
      router.present(.templates)
    case .trainPlan:   // 19: this week's plan
      router.present(.plan)
    case .trainBackfill:   // 18: opens at once and loads the server's types inside (no endpoint yet → 记录 → 训练)
      router.present(.backfill)
    case .trainStart:
      router.open(page: "record.training")
    default:
      break
    }
  }

  /// A quick meal, by its id. Once the log is sent its result is shown even if the cockpit was closed meanwhile.
  private func logTemplate(_ id: Int) {
    guard let food, let t = food.templates.first(where: { $0.id == id }) else {
      failed(tr("这份快捷餐找不到了", "That quick meal is gone"))
      return
    }
    turn?.cancel()
    turn = Task { [weak self] in
      let before = food.nutrition?.intake.kcal ?? 0
      let out = await food.log(t)
      guard let self else { return }
      if out.conflict {
        food.drop(out)
        self.failed(tr("今天已记过「\(t.name)」", "\(t.name) is already logged today"))
        return
      }
      guard out.sent || out.queued else { self.failed(out.message); return }
      JarvisHaptics.notice.notificationOccurred(.success)
      self.undo = { [weak food] in
        // that logging's own id (or its queued copy): never anything else of the day
        guard let food, let cur = food.templates.first(where: { $0.id == t.id }), cur.isLogged else { return false }
        return await food.undoLog(cur)
      }
      self.show(.meal(Self.mealCard(slot: t.meal.title, name: t.name, added: t.totals, before: before, day: food.nutrition)))
    }
  }

  // MARK: - voice

  private func startVoice(_ flow: CockpitFlow) {
    guard let router else { return }
    let ai = router.ai
    if ai.mode == .fallback && !ai.consent {
      machine.close()
      sync()
      router.run(.chat)   // the terms first (then the user tries again)
      return
    }
    guard !JarvisVoice.supportedLangs.isEmpty else {
      failed(tr("这台手机不能本机识别语音：用键盘上的麦克风听写", "This phone can't recognise speech on device: use the keyboard's dictation"))
      return
    }
    if flow == .meal, food == nil, let session { food = FoodStore(session: session, cardOnly: true) }
    machine.autoSend = router.voiceDirect
    machine.canSwitchLanguage = JarvisVoice.supportedLangs.count > 1
    waiting = nil
    listen()
  }

  /// Starts (or restarts, after the language chip) the recogniser; at most JarvisVoice.maxSeconds, then it stops as if the
  /// core had been tapped.
  private func listen() {
    var lang = JarvisVoice.lang
    if !JarvisVoice.supportedLangs.contains(lang) { lang = JarvisVoice.supportedLangs[0] }
    #if DEBUG
    let keep = lang == "zh" && JarvisDemo.cockpitPhrase == nil   // the demo's words are the script's, not the fake server's
    #else
    let keep = lang == "zh"
    #endif
    listening?.cancel()
    listening = Task { [weak self] in
      do {
        try await self?.voice.start(lang: lang, keepAudio: keep)
        JarvisPerf.shared.measure("cockpit-listen", seconds: 0.5)
        try await Task.sleep(nanoseconds: UInt64(JarvisVoice.maxSeconds * 1_000_000_000))
        guard let self, self.machine.mode == .listen else { return }
        self.apply(self.machine.timeUp())
      } catch is CancellationError {
        return
      } catch let f as JarvisVoice.Failure {
        guard let self, self.machine.mode == .listen else { return }   // closed meanwhile: nothing to say
        self.failed(f == .permission
                    ? tr("麦克风或语音识别没打开：在系统设置里允许 Watch2Whoop", "Microphone or speech recognition is off: allow Watch2Whoop in Settings")
                    : tr("语音识别暂时不可用，用键盘上的麦克风听写", "Speech recognition isn't available; use the keyboard's dictation"))
      } catch {
        guard let self, self.machine.mode == .listen else { return }
        self.failed(nil)
      }
    }
  }

  private func switchLanguage() {
    let next = JarvisVoice.lang == "zh" ? "en" : "zh"
    JarvisVoice.lang = next
    JarvisHaptics.selection.selectionChanged()
    voice.cancel()
    listen()
  }

  /// The short name of the language being recognised (the chip).
  var languageLabel: String { JarvisVoice.lang == "en" ? "EN" : tr("中", "ZH") }

  /// Flag off, at the pause: the words are taken now and corrected while they wait for 发送 (the user sees what goes out).
  private func prepareTake() {
    guard let chat = router?.chat else { return }
    listening?.cancel()
    Task { [weak self] in
      guard let self, let take = await self.voice.finish() else { return }
      self.waiting = take
      guard let t = await chat.first(chat.voiceCorrection(take), within: JarvisChat.voiceBoxWait), !t.isEmpty,
            self.machine.mode == .confirm, self.waiting != nil else { return }
      self.machine.correct(t)
    }
  }

  private func send(_ flow: CockpitFlow) {
    guard let router, let chat = router.chat else { failed(nil); return }
    let question = machine.words
    let direct = machine.autoSend
    let waited = waiting
    waiting = nil
    listening?.cancel()
    turn?.cancel()
    turn = Task { [weak self] in
      guard let self else { return }
      if chat.busy {
        self.failed(tr("上一条还在回答，等它说完再发", "Still answering the last message; wait for it to finish"))
        return
      }
      var start = chat.items.count
      chat.page = router.page
      if direct {
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
        start = chat.items.count
        chat.sendVoice(take.text, seconds: take.seconds, correction: correction ?? chat.voiceCorrection(take))
      } else {
        // flag off: exactly the words the user saw and sent (corrected while they waited)
        let words = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty else { self.failed(tr("没听清，再说一次", "Didn't catch that; try again")); return }
        chat.send(words, voiceSeconds: waited?.seconds ?? 1)
      }
      guard let items = await Self.turn(chat, from: start) else {
        self.failed(tr("Jarvis 没有回应，稍后再试", "Jarvis didn't answer; try again later"))
        return
      }
      switch TurnReading.read(items, question: question, flow: flow, today: self.session.map(TodayStore.snapshot)) {
      case .needsConfirm:   // the AI wants a yes first (a delete or an undo): that happens in the conversation
        self.machine.close()
        self.sync()
        router.present(.chat)
      case .failed(let m):
        self.failed(m)
      case .answer(let a):
        if a.undoText != nil { JarvisHaptics.notice.notificationOccurred(.success) }
        self.show(.answer(a))
      }
    }
  }

  /// Waits for the chat's turn to end (at most 90 s) and returns its items.
  static func turn(_ chat: JarvisChat, from start: Int) async -> [JarvisChat.Item]? {
    let deadline = Date().addingTimeInterval(90)
    while chat.busy {
      if Date() > deadline || Task.isCancelled { return nil }
      try? await Task.sleep(nanoseconds: 100_000_000)
    }
    return Array(chat.items.dropFirst(min(start, chat.items.count)))
  }

  // MARK: - cards

  /// The result of a write is shown even when the cockpit was closed while it was on its way (the card comes back).
  private func show(_ r: CockpitReply) {
    reply = r
    machine.showCard()
    wake()
    sync()
  }

  /// The card's 「撤销」: the quick meal's own logging, or (for what the AI logged) the question to the AI, answered in the chat.
  func undoReply() {
    if case .answer(let a)? = reply, let text = a.undoText {
      close()
      guard let router, let chat = router.chat else { return }
      router.present(.chat)
      chat.send(text)
      return
    }
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

  var canUndo: Bool {
    if case .answer(let a)? = reply { return a.undoText != nil }
    return undo != nil
  }

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
    sync()
    if fx.contains(.stopListening) { voice.cancel() }
    if let message {
      JarvisHaptics.notice.notificationOccurred(.warning)
      router?.show(message)
    }
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
}

/// What a chat turn says, for the cockpit's card (pure: tested in JarvisCockpitTests).
enum TurnReading: Equatable {
  /// The AI asked for a yes (a delete or an undo): the conversation shows it.
  case needsConfirm
  case failed(String)
  case answer(CockpitReply.Answer)

  static func read(_ items: [JarvisChat.Item], question: String, flow: CockpitFlow, today: TodayModel?) -> TurnReading {
    var text = "", cards: [JSONValue] = [], notes: [String] = []
    for it in items {
      switch it.kind {
      case .assistant(let t): text = t
      case .card(let c): cards.append(c)
      case .confirm(_, approved: nil): return .needsConfirm
      case .note(let t, _, _): notes.append(t)
      default: break
      }
    }
    if text.isEmpty && cards.isEmpty {
      return .failed(notes.last ?? tr("Jarvis 没有回应，稍后再试", "Jarvis didn't answer; try again later"))
    }
    let (verdict, sub) = CockpitText.verdict(from: text)
    var a = CockpitReply.Answer(question: question, verdict: verdict, sub: sub)
    let meal = cards.last { $0["type"]?.string == "meal" }
    if let meal, let day = Self.day(meal) {
      a.readings = [day]   // the day's food as the server counted it after this turn
    } else if flow == .qa, let m = today {
      if let r = m.rings.first(where: { $0.key == "recovery" }), let v = r.value {
        a.gauge = CockpitReply.Gauge(value: v, fraction: r.fraction, tone: r.tone)
      }
      if let s = m.rings.first(where: { $0.key == "sleep" })?.value { a.readings.append(.init(value: s, label: tr("睡眠", "Sleep"))) }
      if let s = m.rings.first(where: { $0.key == "strain" })?.value { a.readings.append(.init(value: s, label: tr("负荷", "Strain"))) }
      if let h = m.detail.hrv { a.readings.append(.init(value: "\(Int(h.rounded())) ms", label: "HRV")) }
    }
    a.primary = primary(cards)
    // the AI logged a meal (its card offers undo_meal): 撤销 goes back to the AI, who knows what it wrote
    if cards.contains(where: { c in (c["actions"]?.array ?? []).contains { $0["key"]?.string == "undo_meal" } }) {
      a.undoText = JarvisCards.send("undo_meal", .object([:]))?.1
    }
    return .answer(a)
  }

  /// `990 / 2,300 kcal 今天` from a meal card.
  static func day(_ card: JSONValue) -> CockpitReply.Reading? {
    guard let eaten = card["totals"]?["calories"]?.double else { return nil }
    let goal = card["goal"]?["calories"]?.double
    let v = goal.map { "\(CockpitText.kcal(eaten)) / \(CockpitText.kcal($0))" } ?? CockpitText.kcal(eaten)
    return CockpitReply.Reading(value: v + " kcal", label: tr("今天", "Today"))
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
}

extension TodayStore {
  /// Today's model as the phone has it now (one synchronous read, no observation left running).
  @MainActor
  static func snapshot(_ session: AccountSession) -> TodayModel {
    let t = TodayStore(session: session)
    let m = t.model
    t.stop()
    return m
  }
}
