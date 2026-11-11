import SwiftUI
import UIKit

/// The one way into Jarvis from anywhere in the app (#340 N6).
///
/// Other pages call `JarvisRouter.ask(prompt:)` (an explain sheet's 「问 AI」, a detail page): the question goes to the
/// AI the user picked, the same way as on the Today page (app.js askAI):
/// - in-app AI (own API key, or the fallback Jarvis): the chat opens with the question in the box, for the user to send;
/// - the body butler (a link): the question is copied and the link opens (the Claude app);
/// - a subscription AI used in its own app, or none yet: the question is copied, a short note says where to paste it.
///
/// MainTabView hands N3's environment hook `\.askAI` (explain sheets) to it.
@MainActor
final class JarvisRouter: ObservableObject {
  /// The router of the signed-in account's tab view (set when the Jarvis layer appears).
  static weak var current: JarvisRouter?

  /// Ask the user's AI about something (see the type's comment). Does nothing before the tabs exist.
  static func ask(prompt: String) {
    current?.ask(prompt: prompt)
  }

  /// Posted after Jarvis changed the user's records: pages showing server views refresh.
  static let wroteNotification = Notification.Name("w2w.jarvis.wrote")

  enum Sheet: Identifiable, Equatable {
    case chat, quick, camera
    /// The cockpit's screens (2.0.1): 记一餐 → 拍照 / 更多, 记训练 → 计划.
    case mealPhoto, templates, plan
    var id: Self { self }
  }

  @Published var sheet: Sheet?
  /// The chat opens with the keyboard up (the cockpit's 提问).
  var focusComposer = false
  /// A page a card opened over the chat (closing it comes back to the conversation).
  @Published var detail: JarvisDetail?
  /// A short message over the tab bar (copied, needs a connection, …); clears itself.
  @Published private(set) var toast: String?
  /// The page Jarvis is opened from: picks the quick questions and the server's extra tool groups.
  @Published var page = "today"

  private(set) var chat: JarvisChat?
  private(set) var session: AccountSession?
  /// Switches the tab bar (a card's 「看趋势」…); set by the layer.
  var selectTab: ((AppTab) -> Void)?
  private var toastTask: Task<Void, Never>?
  /// The question a camera photo goes into the chat with, for the user to send (一键小票); nil = the 拍照 key's way.
  private var photoPrompt: String?

  /// The AI in use (from the chat model, which keeps it fresh).
  var ai: JarvisAI { chat?.ai ?? JarvisAI() }

  func attach(_ s: AccountSession) {
    Self.current = self
    guard session !== s else { return }
    session = s
    let c = JarvisChat(api: s.api)
    c.onWrote = { [weak s] in
      NotificationCenter.default.post(name: Self.wroteNotification, object: nil)
      Task { await s?.sync.refresh(.changes) }
    }
    c.onAIChange = { [weak self] _ in self?.objectWillChange.send() }
    chat = c
    Task { await c.loadChoices() }
  }

  /// Flag `jarvis_voice_direct`: a recognised voice message goes out at once; otherwise it is put in the box first.
  var voiceDirect: Bool { session?.flags.contains("jarvis_voice_direct") ?? false }

  // MARK: - entry points

  func ask(prompt: String) {
    let ai = self.ai
    if ai.inApp, let chat {
      if ai.mode == .fallback && !ai.consent {
        Task { await chat.askConsent { [weak self] in self?.ask(prompt: prompt) } ; presentOverOthers(.chat) }
        return
      }
      chat.draft = prompt
      presentOverOthers(.chat)
      return
    }
    UIPasteboard.general.string = prompt
    if let url = ai.assistantURL {
      UIApplication.shared.open(url)
      show(tr("问题已复制", "Question copied"))
    } else {
      show(tr("问题已复制 · 去你的 AI app 粘贴", "Question copied · paste it in your AI app"))
    }
  }

  /// A question the user tapped or typed on the AI-first Today (flag `ui_ai_first`): sent at once in an in-app AI, as on the web
  /// page (ui-ai-first.js ask); any other AI goes the way `ask` goes.
  func send(prompt: String) {
    let ai = self.ai
    guard ai.inApp, let chat else { ask(prompt: prompt); return }
    if ai.mode == .fallback && !ai.consent {
      Task { await chat.askConsent { [weak self] in self?.send(prompt: prompt) } ; presentOverOthers(.chat) }
      return
    }
    presentOverOthers(.chat)
    chat.send(prompt)
  }

  /// How 一键小票 goes for the AI in use (#292).
  enum ScanRoute: Equatable {
    /// The camera, then the chat with the picture and the question.
    case camera
    /// The fallback AI's terms first, then the camera.
    case consent
    /// This AI cannot read pictures.
    case noVision
    /// The fallback AI is paused or its allowance is used up: the server's message.
    case unavailable(String?)
    /// A subscription AI in its own app, or the butler's link: the question is copied, the picture stays with the user.
    case elsewhere
    /// No AI yet.
    case none
  }

  static func scanRoute(_ ai: JarvisAI) -> ScanRoute {
    switch ai.mode {
    case .none: return .none
    case .butler, .mcp: return .elsewhere
    case .api, .fallback:
      // The server's order (ai/fallback.py summary): paused, main consent, allowance, then the fallback's own terms.
      if ai.mode == .fallback, ai.reason == "fallback_consent" || (ai.reason == nil && !ai.consent) { return .consent }
      if !ai.available { return .unavailable(ai.message) }
      return ai.vision ? .camera : .noVision
    }
  }

  /// 一键小票 (#292): one tap to the camera; the photo goes to the chat with `Self.receiptPrompt` in the box, and the AI the user
  /// picked records the prices (`record_store_prices`). Its usage counts against that AI, nothing else leaves the phone.
  func scanPrices() {
    page = "food.shopping"
    switch Self.scanRoute(ai) {
    case .camera:
      photoPrompt = Self.receiptPrompt
      present(.camera)
    case .consent:
      guard let chat else { return }
      Task {
        await chat.askConsent { [weak self] in
          self?.photoPrompt = Self.receiptPrompt
          self?.present(.camera)
        }
        present(.chat)
      }
    case .noVision:
      show(tr("这个 AI 看不了图片", "This AI can't read pictures"))
    case .unavailable(let m):
      show(m ?? tr("Jarvis 暂时不可用", "Jarvis is not available right now"))
    case .elsewhere:
      ask(prompt: Self.receiptPrompt)
    case .none:
      show(tr("还没设置 AI：「我」→ AI 设置", "No AI yet: Me → AI settings"))
    }
  }

  /// What goes with the picture. Plain words: the tool is the AI's business (its description says the same).
  static var receiptPrompt: String {
    tr("记下这张小票 / 价签的价格：逐行读商品、规格、价格、特价和原价，超市和日期也记上，一次记完。小票上的算我买了；读不准的行问我，别猜。", "Record the prices on this receipt / price tag: each line's product, size, price, special and original price, plus store and date, in one go. Receipt items count as bought; ask me about lines you can't read, don't guess.")
  }

  /// A tap on the Jarvis key where the chat does not run in the app: what the Today page's key does (open the butler link,
  /// or say where this AI is used). In the app's own AI the key is the cockpit (CockpitController).
  func tapKey() {
    switch ai.mode {
    case .api, .fallback:
      break
    case .butler:
      if let u = ai.assistantURL { UIApplication.shared.open(u) }
    case .mcp:
      let name = ai.displayName ?? "AI"
      show(tr("\(name) · 在它自己的 app 里问", "\(name) · ask it in its own app"))
    case .none:
      show(tr("还没设置 AI：「我」→ AI 设置", "No AI yet: Me → AI settings"))
    }
  }

  /// A cockpit key that opens one of the existing entries (the chat, 快记, the camera).
  func run(_ key: JarvisMenuKey) {
    guard let chat else { return }
    let go: () -> Void = { [weak self] in
      guard let self else { return }
      switch key {
      case .chat: self.present(.chat)
      case .quick: self.present(.quick)
      case .photo:
        if !self.ai.vision { self.show(tr("这个 AI 看不了图片", "This AI can't read pictures")); return }
        self.photoPrompt = nil
        self.present(.camera)
      }
    }
    if ai.mode == .fallback && !ai.consent {
      Task { await chat.askConsent(then: go); present(.chat) }
      return
    }
    go()
  }

  /// Asked from inside another sheet (an explain sheet): that one goes first, then the chat comes up.
  private func presentOverOthers(_ s: Sheet) {
    let root = UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.keyWindow }.first?.rootViewController
    if sheet == nil, let root, root.presentedViewController != nil {
      root.dismiss(animated: true) { [weak self] in self?.present(s) }
    } else {
      present(s)
    }
  }

  func present(_ s: Sheet) {
    chat?.page = page
    sheet = s
  }

  /// A photo from the 拍照 key goes to Jarvis with the photo quick task's text; one from 一键小票 waits in the box with its
  /// question, for the user to add to or send.
  func sendPhoto(_ image: UIImage) {
    guard let chat, let a = JarvisMedia.photo(image) else { return }
    present(.chat)
    if let prompt = photoPrompt {
      photoPrompt = nil
      if chat.imageCount + a.images.count <= JarvisMedia.maxImages { chat.attachments.append(a) }
      chat.draft = chat.draft.isEmpty ? prompt : "\(chat.draft)\n\(prompt)"
      return
    }
    chat.send(tr("拍照记一餐", "Log this meal from the photo"), attachments: [a])
  }

  /// The camera was closed without a photo.
  func cancelPhoto() {
    photoPrompt = nil
  }

  #if DEBUG
  /// Unit tests: the state `scanPrices` and `attach` leave, without a camera or a session.
  func prepareForTesting(chat: JarvisChat, photoPrompt: String?) {
    self.chat = chat
    self.photoPrompt = photoPrompt
  }

  /// Simulator screenshots (`W2W_DEV_FOOD=scan`): what the chat looks like after the camera, with a blank picture.
  func devScanShot() {
    photoPrompt = Self.receiptPrompt
    sendPhoto(UIGraphicsImageRenderer(size: CGSize(width: 600, height: 900)).image { ctx in
      UIColor.lightGray.setFill()
      ctx.fill(CGRect(x: 0, y: 0, width: 600, height: 900))
    })
  }
  #endif

  /// A recognised voice message: sent, or put in the box for checking (flag `jarvis_voice_direct`).
  /// The words of a hold on the key, at once: sent (flag jarvis_voice_direct) or put in the box, each with its correction
  /// (`correction` nil = the usual one; the key passes an empty one when the words already are the server's).
  func deliverVoice(_ take: JarvisVoice.Take, correction given: Task<String?, Never>? = nil) {
    guard let chat else { return }
    present(.chat)
    let correction = given ?? chat.voiceCorrection(take)
    if voiceDirect { chat.sendVoice(take.text, seconds: take.seconds, correction: correction) }
    else { chat.insertVoice(take.text, correction: correction) }
  }

  /// Where a card's button goes: the sheets over the chat, or a tab and what that tab should show.
  enum Route: Equatable {
    case detail(JarvisDetail)
    case tab(AppTab, JarvisPageLink.Request?)
  }

  /// `card`: the card or the row the button is on (a lab's key, a night's date, a recipe's id). The page keys are
  /// cards.OPEN_PAGES plus `explain.<metric>` from a card's own title.
  static func route(page p: String, card: JSONValue?, circles: Bool) -> Route {
    let foodTab: AppTab = circles ? .record : .food
    if p.hasPrefix("explain.") {
      return .detail(.explain(key: String(p.dropFirst(8)), title: card?["title"]?.string ?? ""))
    }
    switch p {
    case "lab": return .detail(.lab(key: card?["test_key"]?.string, name: card?["test_name"]?.string))
    case "supplements": return .detail(.supplements)
    case "data_status": return .detail(.dataStatus)
    case "profile": return .detail(.profile)
    case "sleep": return .detail(.sleep(date: card?["date"]?.string))
    case "healthspan": return .detail(.bodyAge)
    case "today": return .tab(.today, nil)
    case "trends": return .tab(.trends, .trends(.metrics))
    case "trends.report": return .tab(.trends, .trends(.report))
    case "record.training": return .tab(.record, .training)
    case "food": return .tab(foodTab, .food(.diet))
    case "food.shopping": return .tab(foodTab, .food(.shopping))
    case "food.recipes": return .tab(foodTab, .food(.recipes))
    case "recipe": return .tab(foodTab, .food(.recipe(id: card?["id"]?.string ?? "")))
    default: return .tab(.record, nil)
    }
  }

  /// A card's 「open」 button.
  func open(page p: String, card: JSONValue? = nil) {
    switch Self.route(page: p, card: card, circles: session?.circlesTab ?? false) {
    case .detail(let d):
      detail = d
    case .tab(let tab, let request):
      sheet = nil
      if let request { JarvisPageLink.shared.ask(request, circles: session?.circlesTab ?? false) }
      selectTab?(tab)
    }
  }

  func show(_ text: String) {
    toastTask?.cancel()
    withAnimation(.easeOut(duration: 0.2)) { toast = text }
    toastTask = Task { [weak self] in
      try? await Task.sleep(nanoseconds: 2_600_000_000)
      guard !Task.isCancelled else { return }
      withAnimation(.easeIn(duration: 0.2)) { self?.toast = nil }
    }
  }

  // MARK: - quick questions

  /// Quick questions for the page Jarvis was opened from (ai.js quickList, #101). photo: picks a photo and sends the text
  /// with it.
  struct Quick: Equatable, Hashable {
    let text: String
    var photo = false
  }

  static func quick(for page: String, vision: Bool = true) -> [Quick] {
    let lists: [String: [Quick]] = [
      "today": [Quick(text: tr("今天适合练吗", "Should I train today")), Quick(text: tr("解释一下今天的恢复分", "Explain today's recovery score")),
                Quick(text: tr("昨晚睡得怎么样", "How did I sleep last night"))],
      "trends": [Quick(text: tr("复盘这一周", "Review this week")), Quick(text: tr("最近负荷是不是太高", "Is my strain too high lately")),
                 Quick(text: tr("最近睡得够吗", "Am I sleeping enough lately"))],
      "record": [Quick(text: tr("今天练什么", "What should I train today")), Quick(text: tr("记一下今天的状态", "Log how I feel today")),
                 Quick(text: tr("这周按恢复调整一下", "Adjust this week to my recovery"))],
      "record.training": [Quick(text: tr("今天练什么", "What should I train today")), Quick(text: tr("这周按恢复调整一下", "Adjust this week to my recovery"))],
      "record.journal": [Quick(text: tr("最近哪些习惯影响恢复", "Which habits affect my recovery")), Quick(text: tr("记一下今天的状态", "Log how I feel today"))],
      "food": [Quick(text: tr("拍照记一餐", "Log a meal from a photo"), photo: true), Quick(text: tr("今天还差多少蛋白", "How much protein is left today")),
               Quick(text: tr("下一餐吃什么合适", "What should my next meal be"))],
      "food.shopping": [Quick(text: tr("按下周菜单列清单并查价", "List next week's shopping with prices")),
                        Quick(text: tr("这周有什么半价适合我", "Which half-price items suit me this week"))],
      "circles": [Quick(text: tr("这周我和朋友比怎么样", "How do I compare with my friends this week")), Quick(text: tr("今天适合练吗", "Should I train today"))],
      "me": [Quick(text: tr("我的数据都共享给了谁", "Who can see my data")), Quick(text: tr("你都记得我哪些事", "What do you remember about me"))],
    ]
    let tab = page.split(separator: ".").first.map(String.init) ?? page
    return (lists[page] ?? lists[tab] ?? []).filter { vision || !$0.photo }
  }
}

/// The existing entries the cockpit's keys open (until their own screens are designed).
enum JarvisMenuKey: String, CaseIterable, Identifiable {
  case chat, photo, quick
  var id: String { rawValue }
}
