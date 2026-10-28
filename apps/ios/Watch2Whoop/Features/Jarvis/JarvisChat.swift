import Foundation
import UIKit

/// A picture for the AI: JPEG, base64, as `POST /v1/assistant/chat` takes it (`images[]`).
struct ChatImage: Encodable, Equatable {
  let media_type: String   // swiftlint:disable:this identifier_name
  let data: String
}

/// Something attached in the composer: a photo, or a video's frames (≤ 8 in all, ai/chat.py MAX_IMAGES).
struct ChatAttachment: Identifiable, Equatable {
  enum Kind: Equatable { case photo, video(seconds: Int) }
  let id = UUID()
  var kind: Kind
  var images: [ChatImage]
  var thumb: UIImage?

  static func == (a: ChatAttachment, b: ChatAttachment) -> Bool { a.id == b.id }
}

/// The Jarvis conversation: sends a message, reads the answer as server-sent events and keeps what the chat view shows.
/// Online only (docs/ios-native-app.md 5.5): nothing of a conversation is stored on the phone.
///
/// Protocol (body_engine ai/chat.py, ai/api.py, ai/runs.py):
/// - `POST /v1/assistant/chat {message, images, conversation_id, page}` → `conversation`, `text`…, `tool`, `card`,
///   `usage`, `notice`, then `done` / `confirm` (a delete or undo waits for the user) / `error`.
/// - `POST /v1/assistant/chat/confirm {call_id, approve}` → the same stream, the turn goes on.
/// - The turn runs to its end on the server even when the connection drops; `GET /chat/{id}/events?after=<seq>` picks
///   it up again, or answers `resync` when the turn is gone (then the conversation is read back).
@MainActor
final class JarvisChat: ObservableObject {
  struct Item: Identifiable, Equatable {
    enum Kind: Equatable {
      case user(String, images: Int, voiceSeconds: Double?)
      case assistant(String)
      case card(JSONValue)
      /// approved: nil = waiting for the user.
      case confirm(PendingCall, approved: Bool?)
      case note(String, warn: Bool, retry: Bool)
    }
    let id: Int
    var kind: Kind
  }

  @Published private(set) var items: [Item] = []
  @Published private(set) var busy = false
  /// The one-line state under the last message while a turn runs ("…", "查看 get_sleep…").
  @Published private(set) var status: String?
  @Published var draft = ""
  @Published var attachments: [ChatAttachment] = []
  @Published private(set) var conversationId: Int?
  /// Fallback AI: what is left of this month's allowance, in percent.
  @Published private(set) var quotaPct: Int?
  @Published var ai = JarvisAI()
  @Published private(set) var choices: [JarvisChoice] = []
  @Published private(set) var conversations: [ConversationSummary] = []
  /// Set when the fallback AI's terms must be agreed to first; the chat view shows them.
  @Published var consent: FallbackTerms?

  /// The page Jarvis was opened from (ai/params.PAGE_GROUPS: extra tool groups for that page).
  var page: String?
  /// After a turn that wrote something: the account's data is pulled again.
  var onWrote: (() -> Void)?
  /// After the AI choice changed (the key's tap goes the new way).
  var onAIChange: ((JarvisAI) -> Void)?

  let api: APIClient
  private var nextId = 0
  private var afterConsent: (() -> Void)?
  private var pendingText = ""
  private var flushScheduled = false
  private var streaming: Int?
  private var lost: (conv: Int, seq: Int, tries: Int)?
  /// Waits before re-sending a message the server could not take because it was restarting (502–504).
  var restartWaits: [TimeInterval] = [3, 6]
  var sleep: (TimeInterval) async -> Void = { try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) }

  init(api: APIClient) {
    self.api = api
  }

  var started: Bool { items.contains { if case .user = $0.kind { return true } else { return false } } }
  var imageCount: Int { attachments.reduce(0) { $0 + $1.images.count } }

  // MARK: - sending

  /// Sends the draft and the attachments (the composer's send button).
  func sendDraft() {
    let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !busy, !text.isEmpty || !attachments.isEmpty else { return }
    let att = attachments
    draft = ""
    attachments = []
    send(text, attachments: att)
  }

  /// Sends one message. Video frames are announced to the model ("［视频 8 帧，约 12 秒］"), as the page does.
  /// voiceSeconds: a recognised voice message (shown with a voice tag, marked ［语音］ for the model).
  func send(_ text: String, attachments att: [ChatAttachment] = [], voiceSeconds: Double? = nil) {
    guard !busy else {
      if !text.isEmpty { draft = draft.isEmpty ? text : "\(draft)\n\(text)" }
      attachments += att
      return
    }
    let photos = att.filter { $0.kind == .photo }.count
    let tags = att.compactMap { a -> String? in
      if case .video(let s) = a.kind { return tr("［视频 \(a.images.count) 帧，约 \(s) 秒］", "[video, \(a.images.count) frames, about \(s) s]") }
      return nil
    }.joined()
    var message = voiceSeconds != nil ? tr("［语音］", "[voice] ") + text : text
    if !tags.isEmpty { message = tags + (message.isEmpty ? "" : "\n" + message) }
    let shown = [photos > 0 ? tr("［图片 \(photos) 张］", "[\(photos) photo(s)]") : "", tags].filter { !$0.isEmpty }.joined()
    append(.user(text.isEmpty ? shown : (shown.isEmpty ? text : "\(text)\n\(shown)"), images: att.reduce(0) { $0 + $1.images.count },
                 voiceSeconds: voiceSeconds))
    let body = ChatBody(message: message, images: att.flatMap(\.images), conversation_id: conversationId, page: page)
    Task { await run(try? .json(.post, Endpoint.assistantChat, body, idempotent: false), fresh: true) }
  }

  /// The user's answer to a pending write (delete / undo).
  func confirm(_ call: PendingCall, approve: Bool) {
    guard !busy, let i = items.firstIndex(where: { if case .confirm(let c, nil) = $0.kind { return c == call } else { return false } }) else { return }
    items[i].kind = .confirm(call, approved: approve)
    struct Body: Encodable { let call_id: Int; let approve: Bool }   // swiftlint:disable:this identifier_name
    Task { await run(try? .json(.post, Endpoint.assistantConfirm, Body(call_id: call.callId, approve: approve), idempotent: false), fresh: false, wrote: approve) }
  }

  func newConversation() {
    guard !busy else { return }
    conversationId = nil
    items = []
    lost = nil
  }

  /// Back in the foreground: a turn whose connection dropped is picked up from its last event.
  func resume() async {
    guard let l = lost, !busy else { return }
    guard l.conv == conversationId else { lost = nil; return }
    lost = nil
    if l.tries > 0 { await sleep(1.5) }
    if l.tries >= 3 {
      note(tr("连不上服务器，稍后再试", "Can't reach the server, try again later"), warn: true)
      return
    }
    var r = APIRequest.get(Endpoint.assistantEvents(l.conv), ["after": String(l.seq)])
    r.timeout = 120
    await run(r, fresh: false, resumeFrom: l)
  }

  private struct ChatBody: Encodable {
    let message: String
    let images: [ChatImage]
    let conversation_id: Int?   // swiftlint:disable:this identifier_name
    let page: String?
  }

  // MARK: - the stream

  private func run(_ request: APIRequest?, fresh: Bool, wrote: Bool = false, resumeFrom: (conv: Int, seq: Int, tries: Int)? = nil) async {
    guard var req = request else { return }
    req.timeout = max(req.timeout, 120)   // the idle limit between events; the server sends a keep-alive meanwhile
    busy = true
    status = resumeFrom == nil ? "…" : tr("接着回答…", "Carrying on…")
    streaming = nil
    var seq = resumeFrom?.seq ?? 0, ended = false, didWrite = wrote
    defer {
      flush()
      busy = false
      if status != nil, lost == nil { status = nil }
      if didWrite { onWrote?() }
    }
    do {
      var (code, bytes) = try await api.eventStream(req)
      var attempt = 0
      while fresh, [502, 503, 504].contains(code), attempt < restartWaits.count {   // a deploy restarts the server
        status = tr("服务器正在更新，几秒后自动重试…", "The server is updating; retrying in a few seconds…")
        await sleep(restartWaits[attempt])
        attempt += 1
        (code, bytes) = try await api.eventStream(req)
      }
      guard (200..<300).contains(code) else {
        status = nil
        await startFailed(code, bytes, request: req, fresh: fresh)
        return
      }
      var parser = SSEParser()
      for try await b in bytes {
        guard let f = parser.feed(b) else { continue }
        if let id = f.id { seq = id }
        let e = ChatEvent(f)
        // a tool ran or a card came (write tools get one): pull the account's changes afterwards (cheap when none)
        if case .tool(_, "done", _) = e { didWrite = true }
        if case .card = e { didWrite = true }
        await apply(e)
        if e.isTerminal { ended = true; break }
      }
      if !ended { throw APIError.network(-1) }   // cut without its last event: a dropped connection, not the end
    } catch {
      flush()
      status = nil
      if seq > 0, let conv = conversationId {
        lost = (conv, seq, seq > (resumeFrom?.seq ?? 0) ? 0 : (resumeFrom?.tries ?? 0) + 1)
        status = tr("接着回答…", "Carrying on…")
        if UIApplication.shared.applicationState == .active {
          Task { [weak self] in
            await Task.yield()
            await self?.resume()
          }
        }
      } else if resumeFrom == nil {
        note(tr("连不上服务器，稍后再试", "Can't reach the server, try again later"), warn: true, retry: fresh ? req : nil)
      }
    }
  }

  private var retryRequests: [Int: APIRequest] = [:]

  /// The retry button on a failed message: the same request again.
  func retry(_ item: Item) {
    guard !busy, let r = retryRequests.removeValue(forKey: item.id) else { return }
    items.removeAll { $0.id == item.id }
    Task { await run(r, fresh: true) }
  }

  private func startFailed(_ code: Int, _ bytes: URLSession.AsyncBytes, request: APIRequest, fresh: Bool) async {
    var data = Data()
    do {
      for try await b in bytes {
        data.append(b)
        if data.count > 65_536 { break }
      }
    } catch {}
    let detail = JSONValue.parse(data)?["detail"]
    let msg = detail?.string ?? detail?["message"]?.string ?? tr("请求失败（\(code)）", "Request failed (\(code))")
    let errCode = detail?["code"]?.string
    if errCode == "fallback_consent" {
      // The terms first; agreeing sends the same message again
      if let i = items.lastIndex(where: { if case .user = $0.kind { return true } else { return false } }) { items.remove(at: i) }
      afterConsent = { [weak self] in Task { await self?.run(request, fresh: true) } }
      await askConsent()
      return
    }
    if let p = ChatEvent.pct(detail) { quotaPct = p }
    if let url = detail?["assistant_url"]?.string, let u = URL(string: url) { ai.assistantURL = u }
    note(msg, warn: true, retry: [502, 503, 504].contains(code) && fresh ? request : nil)
  }

  private func apply(_ e: ChatEvent) async {
    switch e {
    case .conversation(let id, _, let pct):
      conversationId = id
      if let pct { quotaPct = pct }
    case .text(let d):
      if streaming == nil {
        status = nil
        streaming = append(.assistant(""))
      }
      pendingText += d
      scheduleFlush()
    case .tool(let name, let st, let message):
      flush()
      streaming = nil
      status = [
        "running": tr("查看 \(name)…", "Checking \(name)…"),
        "done": "✓ \(name)",
        "error": "✗ \(name)",
        "rejected": tr("已取消 \(name)", "Cancelled \(name)"),
      ][st] ?? name
      if st == "error", let message { note(tr("\(name) 校验失败：\(message)", "\(name) was rejected: \(message)"), warn: true) }
    case .card(let c):
      flush()
      streaming = nil
      status = nil
      if c["type"]?.string != nil { append(.card(c)) }
    case .confirm(let calls):
      flush()
      streaming = nil
      status = nil
      for c in calls where !items.contains(where: { if case .confirm(let x, _) = $0.kind { return x == c } else { return false } }) {
        append(.confirm(c, approved: nil))
      }
    case .usage(let pct):
      if let pct { quotaPct = pct }
    case .notice(let m):
      if !m.isEmpty { note(m, warn: true) }
    case .error(_, let m):
      flush()
      status = nil
      note(m, warn: true)
    case .done:
      flush()
      status = nil
    case .resync(let id):
      flush()
      status = nil
      await reloadTail(id)
    case .unknown:
      break
    }
  }

  /// Text deltas are painted at most once a frame: re-parsing the Markdown for every token would cost frames.
  private func scheduleFlush() {
    guard !flushScheduled else { return }
    flushScheduled = true
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.0 / 30) { [weak self] in self?.flush() }
  }

  private func flush() {
    flushScheduled = false
    guard !pendingText.isEmpty, let id = streaming, let i = items.firstIndex(where: { $0.id == id }),
          case .assistant(let t) = items[i].kind else { pendingText = ""; return }
    items[i].kind = .assistant(t + pendingText)
    pendingText = ""
  }

  @discardableResult
  private func append(_ k: Item.Kind) -> Int {
    nextId += 1
    items.append(Item(id: nextId, kind: k))
    return nextId
  }

  private func note(_ text: String, warn: Bool, retry: APIRequest? = nil) {
    let id = append(.note(text, warn: warn, retry: retry != nil))
    if let retry { retryRequests[id] = retry }
  }

  // MARK: - conversations

  /// The answer the stream missed (resync): the messages after the last user message, as the server stored them.
  private func reloadTail(_ id: Int) async {
    guard let v = try? await api.json(.get(Endpoint.conversation(id))) else { return }
    let view = JSONValue(any: v)
    let msgs = view["messages"]?.array ?? []
    let from = (msgs.lastIndex { $0["role"]?.string == "user" } ?? -1) + 1
    for m in msgs[from...] where m["role"]?.string == "assistant" {
      if let t = m["text"]?.string, !t.isEmpty { append(.assistant(t)) }
      for c in m["cards"]?.array ?? [] { append(.card(c)) }
    }
    for p in (view["pending"]?.array ?? []).compactMap(PendingCall.init) { append(.confirm(p, approved: nil)) }
  }

  func loadConversations() async {
    guard let d = try? await api.data(.get(Endpoint.conversations)), let v = JSONValue.parse(d) else { return }
    conversations = (v.array ?? []).compactMap(ConversationSummary.init)
  }

  /// Reopens a conversation: its messages and cards, and a write still waiting for the user.
  func open(_ id: Int) async {
    guard !busy, let d = try? await api.data(.get(Endpoint.conversation(id))), let v = JSONValue.parse(d) else { return }
    items = []
    lost = nil
    conversationId = id
    for m in v["messages"]?.array ?? [] {
      let text = m["text"]?.string ?? ""
      if m["role"]?.string == "user" {
        append(.user(text, images: m["images"]?.int ?? 0, voiceSeconds: nil))
      } else {
        if !text.isEmpty { append(.assistant(text)) }
        for c in m["cards"]?.array ?? [] { append(.card(c)) }
      }
    }
    for p in (v["pending"]?.array ?? []).compactMap(PendingCall.init) { append(.confirm(p, approved: nil)) }
  }

  func delete(_ id: Int) async {
    _ = try? await api.data(APIRequest(method: .delete, path: Endpoint.conversation(id)))
    conversations.removeAll { $0.id == id }
    if conversationId == id { newConversation() }
  }

  // MARK: - which AI, and the fallback's terms

  /// `GET /v1/users/me/ai-choice`: the options of the switcher and the AI in use.
  func loadChoices() async {
    guard let d = try? await api.data(.get(Endpoint.aiChoice)), let v = JSONValue.parse(d) else { return }
    applyChoice(v)
  }

  /// Picks an AI (takes effect at once). The fallback AI asks for its terms first when they were never agreed to.
  func choose(_ c: JarvisChoice) async -> String? {
    if c.key == "fallback" && !c.consent {
      afterConsent = { [weak self] in Task { _ = await self?.choose(JarvisChoice(JSONValue.object(["key": .string("fallback"), "consent": .bool(true)]))!) } }
      await askConsent()
      return nil
    }
    do {
      let d = try await api.data(try .json(.put, Endpoint.aiChoice, ["key": c.key]))
      if let v = JSONValue.parse(d) { applyChoice(v) }
      if ai.inApp { newConversation() }
      return nil
    } catch let e as APIError {
      return e.userMessage
    } catch {
      return String(describing: error)
    }
  }

  private func applyChoice(_ v: JSONValue) {
    choices = (v["options"]?.array ?? []).compactMap(JarvisChoice.init)
    if let a = v["ai"] {
      ai = JarvisAI(a, assistantURL: v["assistant_url"]?.string)
      if ai.mode == .fallback { quotaPct = ai.remainingPct }
      onAIChange?(ai)
    }
  }

  /// Shows the fallback AI's terms (`GET /ai-fallback`); `then` runs after agreeing.
  func askConsent(then: (() -> Void)? = nil) async {
    if let then { afterConsent = then }
    guard let d = try? await api.data(.get(Endpoint.aiFallback)), let v = JSONValue.parse(d) else {
      note(tr("Jarvis 暂时不可用", "Jarvis is not available right now"), warn: true)
      return
    }
    consent = FallbackTerms(v)
  }

  /// Records the agreement (`POST /ai-fallback/consent`), then carries on with what asked for it.
  func acceptConsent() async -> String? {
    guard let terms = consent else { return nil }
    struct Body: Encodable { let version: String; let agree: Bool }
    do {
      _ = try await api.data(try .json(.post, Endpoint.aiFallbackConsent, Body(version: terms.version, agree: true), idempotent: false))
    } catch let e as APIError {
      return e.userMessage
    } catch {
      return String(describing: error)
    }
    consent = nil
    await loadChoices()
    let next = afterConsent
    afterConsent = nil
    next?()
    return nil
  }

  func declineConsent() {
    consent = nil
    afterConsent = nil
  }

  // MARK: - voice

  /// Chinese: the recording through the server's recogniser with the user's own terms (`/transcribe?fix=1`).
  func transcribe(wav: Data) async -> String? {
    var r = APIRequest(method: .post, path: Endpoint.assistantTranscribe, query: [URLQueryItem(name: "fix", value: "1")],
                       body: wav, idempotent: false, timeout: 60)
    r.contentType = "audio/wav"
    guard let d = try? await api.data(r), let v = JSONValue.parse(d) else { return nil }
    let t = v["text"]?.string?.trimmingCharacters(in: .whitespacesAndNewlines)
    return t?.isEmpty == false ? t : nil
  }

  /// Text recognised on the phone, corrected with the user's own terms (`/voice-fix`); the phone's text when that fails.
  func voiceFix(_ text: String) async -> String {
    guard let d = try? await api.data(try .json(.post, Endpoint.assistantVoiceFix, ["text": text], idempotent: false)),
          let t = JSONValue.parse(d)?["text"]?.string, !t.isEmpty else { return text }
    return t
  }
}
