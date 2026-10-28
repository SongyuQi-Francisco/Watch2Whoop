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

  enum Sheet: Identifiable, Equatable {
    case chat, quick, camera
    var id: Self { self }
  }

  @Published var menuOpen = false
  @Published var sheet: Sheet?
  /// A short message over the tab bar (copied, needs a connection, …); clears itself.
  @Published private(set) var toast: String?
  /// The page Jarvis is opened from: picks the quick questions and the server's extra tool groups.
  @Published var page = "today"

  private(set) var chat: JarvisChat?
  private(set) var session: AccountSession?
  /// Switches the tab bar (a card's 「看趋势」…); set by the layer.
  var selectTab: ((AppTab) -> Void)?
  private var toastTask: Task<Void, Never>?

  /// The AI in use (from the chat model, which keeps it fresh).
  var ai: JarvisAI { chat?.ai ?? JarvisAI() }

  func attach(_ s: AccountSession) {
    Self.current = self
    guard session !== s else { return }
    session = s
    let c = JarvisChat(api: s.api)
    c.onWrote = { [weak s] in Task { await s?.sync.refresh(.changes) } }
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

  /// A tap on the Jarvis key: the menu where the chat runs in the app, otherwise what the Today page's key does
  /// (open the butler link, or say where this AI is used).
  func tapKey() {
    if menuOpen { menuOpen = false; return }
    switch ai.mode {
    case .api, .fallback:
      menuOpen = true
    case .butler:
      if let u = ai.assistantURL { UIApplication.shared.open(u) }
    case .mcp:
      let name = ai.displayName ?? "AI"
      show(tr("\(name) · 在它自己的 app 里问", "\(name) · ask it in its own app"))
    case .none:
      show(tr("还没设置 AI：「我」→ AI 设置", "No AI yet: Me → AI settings"))
    }
  }

  /// One of the four menu keys.
  func run(_ key: JarvisMenuKey) {
    menuOpen = false
    guard let chat else { return }
    let go: () -> Void = { [weak self] in
      guard let self else { return }
      switch key {
      case .chat: self.present(.chat)
      case .quick: self.present(.quick)
      case .photo:
        if !self.ai.vision { self.show(tr("这个 AI 看不了图片", "This AI can't read pictures")); return }
        self.present(.camera)
      case .voice: self.voiceRequested = true
      }
    }
    if ai.mode == .fallback && !ai.consent {
      Task { await chat.askConsent(then: go); present(.chat) }
      return
    }
    go()
  }

  /// Set by the 语音 key: the layer starts a locked recording (no finger on the key).
  @Published var voiceRequested = false

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

  /// A photo from the 拍照 key goes to Jarvis with the photo quick task's text.
  func sendPhoto(_ image: UIImage) {
    guard let chat, let a = JarvisMedia.photo(image) else { return }
    present(.chat)
    chat.send(tr("拍照记一餐", "Log this meal from the photo"), attachments: [a])
  }

  /// A recognised voice message: sent, or put in the box for checking (flag `jarvis_voice_direct`).
  func deliverVoice(_ text: String, seconds: Double) {
    guard let chat else { return }
    present(.chat)
    if voiceDirect { chat.send(text, voiceSeconds: seconds) } else { chat.draft = text }
  }

  /// A card's 「open」 button: the tab that has that page.
  func open(page p: String) {
    sheet = nil
    let circles = session?.circlesTab ?? false
    let tab: AppTab
    switch p.split(separator: ".").first.map(String.init) ?? p {
    case "today", "sleep", "healthspan": tab = .today
    case "trends", "lab", "data_status": tab = .trends
    case "food", "recipe": tab = circles ? .record : .food
    case "profile", "supplements": tab = .me
    default: tab = .record
    }
    selectTab?(tab)
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

/// The four keys of the Jarvis menu, left to right on the arc.
enum JarvisMenuKey: String, CaseIterable, Identifiable {
  case chat, voice, photo, quick
  var id: String { rawValue }

  var title: String {
    switch self {
    case .chat: return tr("对话", "Chat")
    case .voice: return tr("语音", "Voice")
    case .photo: return tr("拍照", "Photo")
    case .quick: return tr("快记", "Quick log")
    }
  }

  var icon: Icon.Name {
    switch self {
    case .chat: return .botMessageSquare
    case .voice: return .audioWaveform
    case .photo: return .scanEye
    case .quick: return .zap
    }
  }

  /// Angle on the arc, 0 = 12 o'clock, clockwise (jarvis-entry.js KEYS thd).
  var degrees: Double {
    switch self {
    case .chat: return -72
    case .voice: return -36
    case .photo: return 36
    case .quick: return 72
    }
  }
}
