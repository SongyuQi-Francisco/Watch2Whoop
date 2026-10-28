import PhotosUI
import SwiftUI

/// The Jarvis chat (the page's ai.js panel, native): the answer streams in and is drawn as Markdown, cards are native
/// views, a delete / undo waits for 确认, conversations can be reopened, the AI in use can be switched at the top, and
/// the fallback AI asks for its terms the first time. Online only; one ink, as the key.
struct JarvisChatView: View {
  @ObservedObject var chat: JarvisChat
  @ObservedObject var router: JarvisRouter
  @EnvironmentObject private var session: AccountSession
  @Environment(\.dismiss) private var dismiss
  @StateObject private var voice = JarvisVoice()
  @State private var history = false
  @State private var camera = false
  @State private var library = false
  @State private var picked: [PhotosPickerItem] = []
  @State private var recording = false
  @State private var preparing = false
  @FocusState private var focused: Bool

  private var quick: [JarvisRouter.Quick] { JarvisRouter.quick(for: router.page, vision: chat.ai.vision) }

  var body: some View {
    VStack(spacing: 0) {
      header
      if let p = chat.quotaPct, chat.ai.mode == .fallback {
        Text(tr("剩 \(p)%", "\(p)% left")).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text2)
          .frame(maxWidth: .infinity).padding(.bottom, 4)
      }
      log
      if chat.started && !quick.isEmpty { quickRow }
      if !chat.attachments.isEmpty { thumbs }
      if recording { recorder } else { composer }
    }
    .background(Palette.sheet.ignoresSafeArea())
    .sheet(isPresented: $history) { HistorySheet(chat: chat) }
    .sheet(isPresented: consentShown) {
      if let t = chat.consent { ConsentSheet(chat: chat, terms: t) }
    }
    .sheet(isPresented: $camera) {
      CameraPicker { img in
        camera = false
        if let img, let a = JarvisMedia.photo(img) { chat.attachments.append(a) }
      }
      .ignoresSafeArea()
    }
    .photosPicker(isPresented: $library, selection: $picked, maxSelectionCount: JarvisMedia.maxImages, matching: .any(of: [.images, .videos]))
    .onChange(of: picked) { items in
      guard !items.isEmpty else { return }
      picked = []
      Task { await addPicked(items) }
    }
    .presentationDragIndicator(.visible)
    .task { if chat.choices.isEmpty { await chat.loadChoices() } }
  }

  private var consentShown: Binding<Bool> {
    Binding(get: { chat.consent != nil }, set: { if !$0 { chat.declineConsent() } })
  }

  // MARK: - header

  private var header: some View {
    HStack(spacing: 6) {
      roundButton(.chevronDown, tr("收起", "Close")) { dismiss() }
      Spacer()
      Menu {
        ForEach(chat.choices) { o in
          Button {
            Task { await choose(o) }
          } label: {
            if o.current { Label(o.label + (o.note.map { " · \($0)" } ?? ""), image: Icon.Name.check.assetName) }
            else { Text(o.label + (o.note.map { " · \($0)" } ?? "")) }
          }
          .disabled(!o.available || o.current)
        }
      } label: {
        HStack(spacing: 4) {
          Text("Jarvis").font(.system(size: 17, weight: .semibold)).foregroundColor(Palette.text)
          Text(chat.ai.shortName == "Jarvis" ? "" : "· \(chat.ai.shortName)").font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1)
          Icon(.chevronDown, size: 14).foregroundColor(Palette.text2)
        }
      }
      .disabled(chat.busy || chat.choices.count < 2)
      .accessibilityLabel(tr("切换 AI，现在用 \(chat.ai.shortName)", "Switch AI, now \(chat.ai.shortName)"))
      Spacer()
      roundButton(.messagesSquare, tr("历史对话", "Conversations")) { history = true }
      roundButton(.plus, tr("新对话", "New conversation")) { chat.newConversation() }.disabled(chat.busy)
    }
    .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 6)
  }

  private func roundButton(_ icon: Icon.Name, _ label: String, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Icon(icon, size: 18).foregroundColor(Palette.text)
        .frame(width: 36, height: 36)
        .background(Circle().fill(Palette.inSheet))
    }
    .buttonStyle(PressScale())
    .accessibilityLabel(label)
  }

  private func choose(_ o: JarvisChoice) async {
    if let err = await chat.choose(o) { router.show(err); return }
    if !chat.ai.inApp {   // a link / subscription AI is used outside the app: the key now goes there
      dismiss()
      router.show(tr("Jarvis 改用 \(chat.ai.shortName)", "Jarvis now uses \(chat.ai.shortName)"))
    }
  }

  // MARK: - messages

  private var log: some View {
    ScrollViewReader { proxy in
      ScrollView(.vertical, showsIndicators: false) {
        LazyVStack(alignment: .leading, spacing: 14) {
          if !chat.started && chat.items.isEmpty { welcome }
          ForEach(chat.items) { it in row(it).id(it.id) }
          if let s = chat.status {
            Text(s).font(Typo.caption).foregroundColor(Palette.text3).id(-1)
          }
          Color.clear.frame(height: 4).id(-2)
        }
        .padding(.horizontal, Metrics.gutter).padding(.vertical, 10)
      }
      .scrollDismissesKeyboard(.interactively)
      .onChange(of: chat.items) { _ in withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(-2, anchor: .bottom) } }
      .onChange(of: chat.status) { _ in proxy.scrollTo(-2, anchor: .bottom) }
    }
  }

  private var welcome: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text(tr("问问题，或者直接说要记什么", "Ask anything, or just say what to log"))
        .font(.system(size: 20, weight: .semibold)).foregroundColor(Palette.text)
        .padding(.top, 24)
      if !quick.isEmpty {
        FlowRow(spacing: 8) { ForEach(quick, id: \.self) { q in quickChip(q) } }
      }
    }
  }

  private var quickRow: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) { ForEach(quick, id: \.self) { q in quickChip(q) } }.padding(.horizontal, Metrics.gutter)
    }
    .padding(.vertical, 6)
  }

  private func quickChip(_ q: JarvisRouter.Quick) -> some View {
    Button {
      if q.photo { library = true; pendingQuick = q.text } else { chat.send(q.text) }
    } label: {
      Text(q.text).font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text)
        .padding(.vertical, 8).padding(.horizontal, 12)
        .overlay(Capsule().strokeBorder(Palette.text.opacity(0.3), lineWidth: 1))
    }
    .buttonStyle(PressScale())
    .disabled(chat.busy)
  }

  @State private var pendingQuick: String?

  @ViewBuilder private func row(_ it: JarvisChat.Item) -> some View {
    switch it.kind {
    case .user(let text, let images, let voiceSeconds):
      HStack {
        Spacer(minLength: 48)
        VStack(alignment: .trailing, spacing: 4) {
          if let s = voiceSeconds {
            HStack(spacing: 4) {
              Icon(.audioLines, size: 13)
              Text(JarvisLayer.mmss(max(1, s))).font(Typo.label.monospacedDigit())
            }
            .foregroundColor(Palette.text2)
          }
          if !text.isEmpty { Text(text).font(Typo.body).foregroundColor(Palette.text).textSelection(.enabled) }
          if images > 0 && text.isEmpty { Text(tr("［图片 \(images) 张］", "[\(images) picture(s)]")).font(Typo.caption).foregroundColor(Palette.text2) }
        }
        .padding(.vertical, 10).padding(.horizontal, 14)
        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Palette.inSheet))
      }
    case .assistant(let text):
      MarkdownText(text: text.isEmpty ? "…" : text)
    case .card(let c):
      JarvisCardView(card: c, busy: chat.busy, send: { chat.send($0) }, open: { router.open(page: $0) })
    case .confirm(let call, let approved):
      ConfirmCard(call: call, approved: approved, busy: chat.busy) { chat.confirm(call, approve: $0) }
    case .note(let text, let warn, let retry):
      HStack(alignment: .firstTextBaseline, spacing: 8) {
        if warn { Icon(.circleAlert, size: 15).foregroundColor(Palette.text2) }
        Text(text).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
        Spacer(minLength: 4)
        if retry {
          Button(tr("重试", "Retry")) { chat.retry(it) }
            .font(.system(size: 13, weight: .semibold)).foregroundColor(Palette.text)
            .padding(.vertical, 6).padding(.horizontal, 12)
            .overlay(Capsule().strokeBorder(Palette.text.opacity(0.4), lineWidth: 1))
        }
      }
    }
  }

  // MARK: - composer

  private var thumbs: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        ForEach(chat.attachments) { a in
          ZStack(alignment: .topTrailing) {
            Group {
              if let t = a.thumb { Image(uiImage: t).resizable().scaledToFill() } else { Palette.surface2 }
            }
            .frame(width: 56, height: 56).clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(alignment: .bottomLeading) {
              if case .video(let s) = a.kind {
                Text("\(a.images.count)·\(s)s").font(.system(size: 10, weight: .semibold).monospacedDigit())
                  .foregroundColor(.white).padding(3).background(Color.black.opacity(0.5)).cornerRadius(4).padding(3)
              }
            }
            Button { chat.attachments.removeAll { $0.id == a.id } } label: {
              Icon(.x, size: 11).foregroundColor(Palette.bg).frame(width: 18, height: 18).background(Circle().fill(Palette.text))
            }
            .offset(x: 5, y: -5)
            .accessibilityLabel(tr("移除", "Remove"))
          }
        }
        if preparing { ProgressView().tint(Palette.text2).frame(width: 56, height: 56) }
      }
      .padding(.horizontal, Metrics.gutter).padding(.top, 8)
    }
  }

  private var composer: some View {
    HStack(alignment: .bottom, spacing: 8) {
      if chat.ai.vision {
        Menu {
          Button { camera = true } label: { Label(tr("拍照", "Camera"), image: Icon.Name.camera.assetName) }
          Button { library = true } label: { Label(tr("照片或视频", "Photo or video"), image: Icon.Name.image.assetName) }
        } label: {
          Icon(.camera, size: 20).foregroundColor(Palette.text).frame(width: 40, height: 40)
            .background(Circle().fill(Palette.inSheet))
        }
        .disabled(chat.imageCount >= JarvisMedia.maxImages)
        .accessibilityLabel(tr("照片", "Photo"))
      }
      TextField(tr("问问题或记点什么", "Ask or log something"), text: $chat.draft, axis: .vertical)
        .lineLimit(1...6)
        .font(Typo.body)
        .focused($focused)
        .padding(.vertical, 10).padding(.horizontal, 14)
        .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Palette.inSheet))
      if chat.draft.isEmpty && chat.attachments.isEmpty && !JarvisVoice.supportedLangs.isEmpty {
        Button { startRecording() } label: {
          Icon(.mic, size: 20).foregroundColor(Palette.text).frame(width: 40, height: 40).background(Circle().fill(Palette.inSheet))
        }
        .buttonStyle(PressScale())
        .disabled(chat.busy)
        .accessibilityLabel(tr("说话", "Talk"))
      } else {
        Button { chat.sendDraft() } label: {
          Icon(.arrowUp, size: 20).foregroundColor(Palette.bg).frame(width: 40, height: 40).background(Circle().fill(Palette.text))
        }
        .buttonStyle(PressScale())
        .disabled(chat.busy)
        .opacity(chat.busy ? 0.4 : 1)
        .accessibilityLabel(tr("发送", "Send"))
      }
    }
    .padding(.horizontal, 12).padding(.top, 8).padding(.bottom, 10)
  }

  /// The chat's own microphone: tap to talk, 完成 puts the recognised text in the box.
  private var recorder: some View {
    VStack(spacing: 10) {
      Waveform(levels: voice.levels).frame(height: 26)
      Text(voice.partial.isEmpty ? tr("在听…", "Listening…") : voice.partial).font(Typo.body)
        .foregroundColor(voice.partial.isEmpty ? Palette.text3 : Palette.text).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
      HStack(spacing: 10) {
        Button { voice.cancel(); recording = false } label: {
          Text(tr("取消", "Cancel")).font(Typo.bodyStrong).foregroundColor(Palette.text).frame(maxWidth: .infinity, minHeight: 42)
            .overlay(Capsule().strokeBorder(Palette.text.opacity(0.4), lineWidth: 1))
        }
        Button { Task { await stopRecording() } } label: {
          Text(tr("完成", "Done")).font(Typo.bodyStrong).foregroundColor(Palette.bg).frame(maxWidth: .infinity, minHeight: 42)
            .background(Capsule().fill(Palette.text))
        }
      }
      .buttonStyle(PressScale())
    }
    .padding(14)
  }

  private func startRecording() {
    focused = false
    recording = true
    let lang = JarvisVoice.supportedLangs.contains(JarvisVoice.lang) ? JarvisVoice.lang : JarvisVoice.supportedLangs[0]
    Task {
      do { try await voice.start(lang: lang, keepAudio: lang == "zh") } catch {
        recording = false
        router.show(tr("语音识别暂时不可用，用键盘上的麦克风听写", "Speech recognition isn't available; use the keyboard's dictation"))
      }
    }
  }

  private func stopRecording() async {
    let out = await voice.stop()
    recording = false
    var text = out?.text ?? ""
    if let wav = out?.wav, let t = await chat.transcribe(wav: wav) { text = t } else if !text.isEmpty { text = await chat.voiceFix(text) }
    guard !text.isEmpty else { return }
    chat.draft = chat.draft.isEmpty ? text : "\(chat.draft)\n\(text)"
  }

  private func addPicked(_ items: [PhotosPickerItem]) async {
    let room = JarvisMedia.maxImages - chat.imageCount
    if items.count > room { router.show(tr("一次最多 \(JarvisMedia.maxImages) 张", "At most \(JarvisMedia.maxImages) pictures at a time")) }
    preparing = true
    let r = await JarvisMedia.load(items, room: room)
    preparing = false
    if r.failed { router.show(tr("有的照片或视频读不了", "Some photos or videos couldn't be read")) }
    if let q = pendingQuick {   // the photo quick task: its text goes out with the photos just picked
      pendingQuick = nil
      if !r.added.isEmpty { chat.send(q, attachments: r.added) }
      return
    }
    chat.attachments += r.added
  }
}

/// A write that needs the user's yes (only deleting / undoing asks; ai/params.ALWAYS_CONFIRM).
struct ConfirmCard: View {
  let call: PendingCall
  let approved: Bool?
  let busy: Bool
  let decide: (Bool) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      Text(tr("要写入（确认后才执行）", "About to change your data (runs only after you confirm)"))
        .font(Typo.label).foregroundColor(Palette.text2)
      Text(call.summary).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
      if let approved {
        Text(approved ? tr("已确认", "Confirmed") : tr("已取消", "Cancelled")).font(Typo.caption).foregroundColor(Palette.text2)
      } else {
        HStack(spacing: 10) {
          Button { decide(false) } label: {
            Text(tr("不要", "No")).font(Typo.bodyStrong).foregroundColor(Palette.text).frame(maxWidth: .infinity, minHeight: 42)
              .overlay(Capsule().strokeBorder(Palette.text.opacity(0.4), lineWidth: 1))
          }
          Button { decide(true) } label: {
            Text(tr("确认", "Confirm")).font(Typo.bodyStrong).foregroundColor(Palette.bg).frame(maxWidth: .infinity, minHeight: 42)
              .background(Capsule().fill(Palette.text))
          }
        }
        .buttonStyle(PressScale())
        .disabled(busy)
      }
    }
    .padding(14)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).strokeBorder(Palette.text.opacity(0.25), lineWidth: 1))
  }
}

/// Earlier conversations: tap to reopen, swipe to delete.
private struct HistorySheet: View {
  @ObservedObject var chat: JarvisChat
  @Environment(\.dismiss) private var dismiss
  @State private var loaded = false

  var body: some View {
    SheetContainer(title: tr("历史对话", "Conversations"), close: { dismiss() }) {
      VStack(spacing: 0) {
        if loaded && chat.conversations.isEmpty {
          Text(tr("还没有对话", "No conversations yet")).font(Typo.caption).foregroundColor(Palette.text2).padding(.vertical, 24)
        }
        ForEach(chat.conversations) { c in
          HStack(spacing: 10) {
            Button {
              Task { await chat.open(c.id); dismiss() }
            } label: {
              VStack(alignment: .leading, spacing: 2) {
                Text(c.title.isEmpty ? tr("（无标题）", "(untitled)") : c.title).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
                Text(c.when).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3)
              }
              .frame(maxWidth: .infinity, alignment: .leading)
              .contentShape(Rectangle())
            }
            .buttonStyle(PressScale())
            if c.id == chat.conversationId { Icon(.check, size: 16).foregroundColor(Palette.text) }
            Button { Task { await chat.delete(c.id) } } label: { Icon(.trash, size: 16).foregroundColor(Palette.text3) }
              .accessibilityLabel(tr("删除", "Delete"))
          }
          .padding(.vertical, 12)
          Divider().overlay(Palette.hair)
        }
      }
    }
    .presentationDetents([.medium, .large])
    .task { await chat.loadConversations(); loaded = true }
  }
}

/// The fallback AI's terms, before its first use (the points come from the server, in the request's language).
private struct ConsentSheet: View {
  @ObservedObject var chat: JarvisChat
  let terms: FallbackTerms
  @State private var agreed = false
  @State private var busy = false
  @State private var error: String?

  var body: some View {
    SheetContainer(title: tr("Jarvis · 使用前确认", "Jarvis · before you start"), close: { chat.declineConsent() }) {
      VStack(alignment: .leading, spacing: 14) {
        ForEach(Array(terms.points.enumerated()), id: \.offset) { _, p in
          HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle().fill(Palette.text2).frame(width: 4, height: 4)
            Text(p).font(Typo.caption).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
          }
        }
        if terms.translated {
          Text(tr("以中文版为准", "The Chinese text is the one agreed to")).font(Typo.label).foregroundColor(Palette.text3)
        }
        Button { agreed.toggle() } label: {
          HStack(spacing: 10) {
            Group {
              if agreed { Icon(.check, size: 14).foregroundColor(Palette.bg).frame(width: 20, height: 20).background(RoundedRectangle(cornerRadius: 5).fill(Palette.text)) }
              else { RoundedRectangle(cornerRadius: 5).strokeBorder(Palette.text.opacity(0.5), lineWidth: 1).frame(width: 20, height: 20) }
            }
            Text(tr("我已阅读并同意", "I have read and agree")).font(Typo.body).foregroundColor(Palette.text)
          }
        }
        .accessibilityAddTraits(agreed ? .isSelected : [])
        if let error { Text(error).font(Typo.caption).foregroundColor(Palette.text2) }
        PrimaryButton(title: tr("同意并开始", "Agree and start"), busy: busy) {
          Task { busy = true; error = await chat.acceptConsent(); busy = false }
        }
        .disabled(!agreed)
        .opacity(agreed ? 1 : 0.4)
      }
    }
    .presentationDetents([.medium, .large])
  }
}

/// 快记: the most used quick meals, water, "training done as planned" and weight; a tap sends one sentence to Jarvis
/// (who logs it and answers with a card), weight only fills the box (jarvis-entry.js openQuick).
struct QuickLogSheet: View {
  @ObservedObject var chat: JarvisChat
  @ObservedObject var router: JarvisRouter
  @EnvironmentObject private var session: AccountSession
  @State private var meals: [(id: Int, name: String)] = []
  @State private var text = ""
  @FocusState private var focused: Bool

  private struct Chip: Hashable { let label: String; let send: String?; let prefill: String? }

  private var chips: [Chip] {
    var out = meals.map { m in
      Chip(label: m.name, send: tr("按快捷模板「\(m.name)」记一次（模板 id \(m.id)）", "Log the quick meal \"\(m.name)\" once (template id \(m.id))"), prefill: nil)
    }
    if session.flags.contains("water") {
      out.append(Chip(label: tr("喝水 250 ml", "Water 250 ml"), send: tr("喝了 250 毫升水", "I drank 250 ml of water"), prefill: nil))
    }
    out.append(Chip(label: tr("训练按计划完成", "Training done as planned"), send: tr("今天的训练按计划完成", "Today's training is done as planned"), prefill: nil))
    out.append(Chip(label: tr("体重", "Weight"), send: nil, prefill: tr("今天体重 ", "Weight today ")))
    return out
  }

  var body: some View {
    SheetContainer(title: tr("快记", "Quick log"), close: { router.sheet = nil }) {
      VStack(alignment: .leading, spacing: 14) {
        FlowRow(spacing: 8) {
          ForEach(chips, id: \.self) { c in
            Button {
              if let s = c.send { send(s) } else if let p = c.prefill { text = p; focused = true }
            } label: {
              Text(c.label).font(.system(size: 14, weight: .medium)).foregroundColor(Palette.text)
                .padding(.vertical, 9).padding(.horizontal, 14)
                .overlay(Capsule().strokeBorder(Palette.text.opacity(0.3), lineWidth: 1))
            }
            .buttonStyle(PressScale())
          }
        }
        HStack(spacing: 8) {
          TextField(tr("比如：午饭一碗牛肉面", "e.g. a bowl of beef noodles for lunch"), text: $text)
            .font(Typo.body).focused($focused)
            .padding(.vertical, 11).padding(.horizontal, 14)
            .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Palette.inSheet))
            .onSubmit { send(text) }
          Button { send(text) } label: {
            Icon(.arrowUp, size: 20).foregroundColor(Palette.bg).frame(width: 40, height: 40).background(Circle().fill(Palette.text))
          }
          .buttonStyle(PressScale())
          .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
        }
      }
    }
    .task { await loadMeals() }
  }

  private func send(_ s: String) {
    let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !t.isEmpty else { return }
    router.sheet = nil
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
      router.present(.chat)
      chat.send(t)
    }
  }

  /// The three most used quick meals of the last 30 days.
  private func loadMeals() async {
    guard let d = try? await chat.api.data(.get(Endpoint.mealTemplates)), let v = JSONValue.parse(d) else { return }
    let rows = (v.array ?? []).enumerated().compactMap { i, t -> (Int, String, Double, Int)? in
      guard let id = t["id"]?.int, let n = t["name"]?.string else { return nil }
      return (id, n, t["uses_30d"]?.double ?? 0, i)
    }
    meals = rows.sorted { $0.2 != $1.2 ? $0.2 > $1.2 : $0.3 < $1.3 }.prefix(3).map { (id: $0.0, name: $0.1) }
  }
}
