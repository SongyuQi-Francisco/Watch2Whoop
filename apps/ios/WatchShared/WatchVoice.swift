import Foundation

// Hold to talk on the watch (docs/watch-app-plan.md 2.2, 2.3, 4.3–4.5; mockups M1–M4, J1–J4, S7; backend PR #458).
// Plain Foundation, compiled into both apps, so every rule here is unit-tested on the phone:
//
//   pairing     the phone asks `POST /v1/account/watch-code` and hands `{server, code}` to the watch outside the
//               application context (sendMessage / transferUserInfo); the watch redeems it at `POST /v1/web/login-code`
//               (`client: "watch"`) for a token of its own (watch Keychain). Any 401 = the token is gone: ask again.
//   recording   16 kHz mono 16-bit PCM WAV, `WatchParams.voiceMinSec`…`voiceMaxSec` (`WatchWAV` checks the bytes)
//   transcript  shown for `WatchParams.voiceSendAfterSec`, cancellable, then sent (`WatchSendTimer`)
//   chat        `POST /v1/assistant/chat` with `page: "watch"`, SSE (`WatchSSEParser` → `WatchChatTurn`), resumed with
//               `/chat/{id}/events?after=` when the connection drops
//   the flow    `WatchVoiceFlow`: the screens' state machine; it returns effects the watch app carries out

// MARK: - phone → watch: is voice offered for this account (a block of the application context)

/// The phone's answer about voice on the watch (plan 2.3): only for an AI that runs inside the app (own API key or the
/// fallback AI, already agreed to) and only with the phone's direct-voice flag `jarvis_voice_direct` on (plan Q6: the
/// watch cannot edit the words, it can only cancel them).
struct WatchVoiceOffer: Codable, Equatable {
  enum Why: String, Codable, Equatable {
    /// The AI runs elsewhere (subscription via MCP, the butler link) or none is set up.
    case mode
    /// The fallback AI's terms were never agreed to: only the phone can do that.
    case consent
    /// `jarvis_voice_direct` is off for this account.
    case direct
  }

  var on: Bool
  var why: Why?
  /// Who is signed in on the phone (opaque): a token redeemed for another account is dropped.
  var account: String

  /// The phone's rule (`PhoneWatchBridge`), pure so it is tested: `mode` = `ai.mode` of `/v1/users/me/ai-choice`.
  static func of(mode: String?, consent: Bool, direct: Bool, account: String) -> WatchVoiceOffer {
    let inApp = mode == "api" || mode == "fallback"
    if !inApp { return WatchVoiceOffer(on: false, why: .mode, account: account) }
    if mode == "fallback" && !consent { return WatchVoiceOffer(on: false, why: .consent, account: account) }
    if !direct { return WatchVoiceOffer(on: false, why: .direct, account: account) }
    return WatchVoiceOffer(on: true, why: nil, account: account)
  }
}

// MARK: - pairing

/// A one-time code for the watch's own token (phone → watch, never in the application context).
struct WatchPairCode: Codable, Equatable {
  /// The server's origin (`https://…`), as the phone uses it.
  var server: String
  var code: String
  /// The account it was asked for (`WatchVoiceOffer.account`).
  var account: String
  /// When the phone got it, and for how long the server keeps it (`expires_in`, 600 s).
  var at: Date
  var expiresIn: Int

  /// Still worth redeeming (a queued transfer may arrive late; an expired code only earns a 401).
  func fresh(now: Date) -> Bool { now.timeIntervalSince(at) < Double(expiresIn) - WatchParams.pairCodeMarginSec }
}

/// What the watch knows about its own credential (the token itself is in the watch's Keychain, never here).
struct WatchPairing: Codable, Equatable {
  /// The server the token belongs to.
  var server: String?
  /// The account the token belongs to.
  var account: String?
  /// A token is stored.
  var paired = false
  /// The last time the watch asked the phone for a code (asks are spaced by `WatchParams.pairAskEverySec`).
  var askedAt: Date?

  /// Ask the phone for a code now? Only while voice is offered, without a token, with the phone reachable (a message
  /// that is not answered is not repeated before `pairAskEverySec`).
  func shouldAsk(offer: WatchVoiceOffer?, reachable: Bool, now: Date) -> Bool {
    guard let offer, offer.on, reachable else { return false }
    if paired && account == offer.account { return false }
    guard let a = askedAt else { return true }
    return now.timeIntervalSince(a) >= WatchParams.pairAskEverySec
  }

  mutating func asked(at now: Date) { askedAt = now }

  /// The code was redeemed; the token is in the Keychain.
  mutating func redeemed(_ c: WatchPairCode) {
    server = c.server
    account = c.account
    paired = true
    askedAt = nil
  }

  /// A 401 from the server (the phone signed out, its token was revoked, a new watch was paired): the token is gone,
  /// the core goes dark and the phone is asked again at once when reachable.
  mutating func lost() {
    paired = false
    askedAt = nil
  }

  /// The phone says who is signed in now: another account (or nobody) drops the token. True = the caller must delete
  /// the token from the Keychain.
  mutating func phoneSays(signedIn: Bool, offer: WatchVoiceOffer?) -> Bool {
    guard paired else { return false }
    if !signedIn || (offer != nil && offer?.account != account) {
      self = WatchPairing()
      return true
    }
    return false
  }

  /// Usable for requests: paired for the account the phone has now.
  func usable(for offer: WatchVoiceOffer?) -> Bool { paired && server != nil && (offer == nil || offer?.account == account) }
}

// MARK: - recording limits and the WAV the server takes

/// The audio the server's transcriber takes (#458 B: RIFF/WAVE, 16-bit integer PCM, mono, 8–48 kHz, 0.3–60 s), read
/// from the bytes before they are sent, so a recorder setting that drifts fails here and not as `bad_audio`.
struct WatchWAV: Equatable {
  var format: Int
  var channels: Int
  var sampleRate: Int
  var bits: Int
  var dataBytes: Int

  var seconds: Double { sampleRate > 0 && channels > 0 ? Double(dataBytes) / Double(sampleRate * channels * bits / 8) : 0 }

  /// PCM (1) or WAVE_FORMAT_EXTENSIBLE (0xFFFE, which AVAudioRecorder may write), 16 bit, mono, 8–48 kHz.
  var accepted: Bool {
    (format == 1 || format == 0xFFFE) && bits == 16 && channels == 1 && (8000...48000).contains(sampleRate)
  }

  /// Parses the RIFF chunks (`fmt `, `data`; others such as FLLR / LIST are skipped). nil = not a WAV file.
  static func inspect(_ d: Data) -> WatchWAV? {
    let b = [UInt8](d)
    func u32(_ i: Int) -> Int { i + 4 <= b.count ? Int(b[i]) | Int(b[i + 1]) << 8 | Int(b[i + 2]) << 16 | Int(b[i + 3]) << 24 : 0 }
    func u16(_ i: Int) -> Int { i + 2 <= b.count ? Int(b[i]) | Int(b[i + 1]) << 8 : 0 }
    func tag(_ i: Int) -> String { i + 4 <= b.count ? String(bytes: b[i..<i + 4], encoding: .ascii) ?? "" : "" }
    guard b.count >= 12, tag(0) == "RIFF", tag(8) == "WAVE" else { return nil }
    var i = 12
    var fmt: (Int, Int, Int, Int)?
    var data: Int?
    while i + 8 <= b.count {
      let id = tag(i), size = u32(i + 4), body = i + 8
      if id == "fmt " { fmt = (u16(body), u16(body + 2), u32(body + 4), u16(body + 14)) }
      if id == "data" { data = min(size, b.count - body) }
      i = body + size + (size & 1)
    }
    guard let f = fmt, let n = data else { return nil }
    return WatchWAV(format: f.0, channels: f.1, sampleRate: f.2, bits: f.3, dataBytes: n)
  }

  /// A 16-bit mono PCM WAV of these samples (the simulator's canned recording; the tests).
  static func make(samples: [Int16], rate: Int = WatchParams.voiceSampleRate) -> Data {
    var d = Data()
    func put32(_ v: Int) { var x = UInt32(v).littleEndian; d.append(Data(bytes: &x, count: 4)) }
    func put16(_ v: Int) { var x = UInt16(v).littleEndian; d.append(Data(bytes: &x, count: 2)) }
    let n = samples.count * 2
    d.append(contentsOf: Array("RIFF".utf8)); put32(36 + n); d.append(contentsOf: Array("WAVE".utf8))
    d.append(contentsOf: Array("fmt ".utf8)); put32(16); put16(1); put16(1); put32(rate); put32(rate * 2); put16(2); put16(16)
    d.append(contentsOf: Array("data".utf8)); put32(n)
    for s in samples { put16(Int(UInt16(bitPattern: s))) }
    return d
  }
}

/// How long a hold lasted, against the transcriber's limits.
enum WatchRecordingCheck: Equatable {
  /// Shorter than `voiceMinSec`: a slip, not speech (nothing is sent, a failure tap).
  case tooShort
  case ok
  /// The recorder stops itself at `voiceMaxSec` and sends what it has (the server would refuse more than 60 s).
  case clipped

  static func of(seconds: Double) -> WatchRecordingCheck {
    if seconds < WatchParams.voiceMinSec { return .tooShort }
    if seconds >= WatchParams.voiceMaxSec { return .clipped }
    return .ok
  }
}

// MARK: - transcript: shown, cancellable, then sent

/// M2: the words stay for `voiceSendAfterSec` while a ring fills; then they are sent (tap the core = at once, ✕ = cancel).
struct WatchSendTimer: Equatable {
  var start: Date
  var duration: TimeInterval = WatchParams.voiceSendAfterSec

  func progress(_ now: Date) -> Double { duration <= 0 ? 1 : min(1, max(0, now.timeIntervalSince(start) / duration)) }
  func due(_ now: Date) -> Bool { now.timeIntervalSince(start) >= duration }
}

// MARK: - the chat stream

/// One server-sent event (`id: n` / `event: name` / `data: {json}`).
struct WatchSSEEvent: Equatable {
  var id: Int?
  var event: String
  var data: Data
}

/// Reads the chat stream line by line. The server writes every event as `id:` (except `resync`), `event:`, one `data:`
/// line and a blank line (ai/runs.py); `URLSession`'s line reader drops blank lines, so an event is complete at its
/// `data:` line.
struct WatchSSEParser {
  private var id: Int?
  private var event: String?

  mutating func feed(_ raw: String) -> WatchSSEEvent? {
    let line = raw.hasSuffix("\r") ? String(raw.dropLast()) : raw
    if line.isEmpty || line.hasPrefix(":") { return nil }
    let (field, value): (String, String) = {
      guard let c = line.firstIndex(of: ":") else { return (line, "") }
      var v = line[line.index(after: c)...]
      if v.first == " " { v = v.dropFirst() }
      return (String(line[..<c]), String(v))
    }()
    switch field {
    case "id": id = Int(value)
    case "event": event = value
    case "data":
      let e = WatchSSEEvent(id: id, event: event ?? "message", data: Data(value.utf8))
      id = nil
      event = nil
      return e
    default: break
    }
    return nil
  }
}

/// What one food write wrote (#458: a `meal` card's `written[]`): the receipt (M3) and its undo through the phone.
struct WatchVoiceWrite: Codable, Equatable {
  var tool: String?
  var date: String
  /// Quick meal / recipe: the whole logging is undone with `DELETE /meal-logs/{logId}`.
  var logId: Int?
  /// Otherwise each entry: `DELETE /nutrition/entries/{id}?date=`.
  var entryIds: [String]
  /// `breakfast` / `lunch` / `dinner` / `snacks`, nil when the write spans meals.
  var meal: String?
  var foods: [String]
  var calories: Double?
  var proteinG: Double?

  init?(_ j: [String: Any]) {
    guard let date = j["date"] as? String else { return nil }
    let ids = (j["entry_ids"] as? [Any] ?? []).compactMap { v -> String? in
      if let s = v as? String { return s }
      return (v as? NSNumber).map { $0.stringValue }
    }
    let log = (j["log_id"] as? NSNumber)?.intValue
    guard log != nil || !ids.isEmpty else { return nil }
    self.tool = j["tool"] as? String
    self.date = date
    self.logId = log
    self.entryIds = ids
    self.meal = j["meal"] as? String
    self.foods = (j["foods"] as? [Any] ?? []).compactMap { $0 as? String }
    self.calories = (j["calories"] as? NSNumber)?.doubleValue
    self.proteinG = (j["protein_g"] as? NSNumber)?.doubleValue
  }

  init(tool: String?, date: String, logId: Int?, entryIds: [String], meal: String?, foods: [String], calories: Double?,
       proteinG: Double?) {
    self.tool = tool
    self.date = date
    self.logId = logId
    self.entryIds = entryIds
    self.meal = meal
    self.foods = foods
    self.calories = calories
    self.proteinG = proteinG
  }

  /// The meal's name in the UI language (the server's slot keys).
  static func slotName(_ m: String?) -> String? {
    switch m {
    case "breakfast": return tr("早餐", "Breakfast")
    case "lunch": return tr("午餐", "Lunch")
    case "dinner": return tr("晚餐", "Dinner")
    case "snacks", "snack": return tr("加餐", "Snacks")
    default: return nil
    }
  }
}

/// A meal Jarvis logged in this turn, with the day's totals the receipt bar needs (all from the server's card).
struct WatchVoiceMeal: Equatable {
  var writes: [WatchVoiceWrite]
  /// The day's calories after this write (`totals.calories`) and the goal (`goal.calories`, nil = no goal).
  var dayKcal: Double?
  var goalKcal: Double?

  var date: String { writes.first?.date ?? "" }
  var calories: Double? {
    let v = writes.compactMap(\.calories)
    return v.isEmpty ? nil : v.reduce(0, +)
  }
  var proteinG: Double? {
    let v = writes.compactMap(\.proteinG)
    return v.isEmpty ? nil : v.reduce(0, +)
  }
  var foods: [String] { writes.flatMap(\.foods) }
  var meal: String? {
    let m = Set(writes.map { $0.meal ?? "" })
    return m.count == 1 ? writes.first?.meal : nil
  }
}

/// A delete / undo the server asks to be confirmed (`confirm` event, mockup M4).
struct WatchConfirmCall: Equatable {
  var callId: String
  var tool: String
  var summary: String
}

/// Everything one turn of the chat stream said, accumulated event by event (also across a resume: the server numbers
/// the events, `lastId` is where to pick up).
struct WatchChatTurn: Equatable {
  var conversationId: Int?
  var text = ""
  var lastId = 0
  var meal: WatchVoiceMeal?
  var confirm: WatchConfirmCall?
  var failure: WatchVoiceFailure?
  var done = false
  /// The turn is no longer in the server's memory (`resync`): the watch cannot replay it, the phone can.
  var resync = false
  /// A tool is running (the J2 ring keeps turning).
  var working = false

  /// The stream said its last word; an end of the connection before this is an interruption.
  var finished: Bool { done || confirm != nil || failure != nil || resync }

  mutating func apply(_ e: WatchSSEEvent) {
    if let id = e.id {
      guard id > lastId else { return }   // a resume repeats nothing, but never count an event twice
      lastId = id
    }
    let j = (try? JSONSerialization.jsonObject(with: e.data)) as? [String: Any] ?? [:]
    func num(_ v: Any?) -> Double? { (v as? NSNumber)?.doubleValue }
    switch e.event {
    case "conversation":
      if let id = (j["id"] as? NSNumber)?.intValue { conversationId = id }
    case "text":
      text += j["delta"] as? String ?? ""
    case "tool":
      working = (j["status"] as? String) == "running"
    case "card":
      guard let card = j["card"] as? [String: Any], (card["type"] as? String) == "meal" else { return }
      let writes = (card["written"] as? [[String: Any]] ?? []).compactMap(WatchVoiceWrite.init)
      guard !writes.isEmpty else { return }
      let totals = card["totals"] as? [String: Any]
      let goal = card["goal"] as? [String: Any]
      var all = (meal?.writes ?? []).filter { w in !writes.contains(w) }
      all.append(contentsOf: writes)
      meal = WatchVoiceMeal(writes: all, dayKcal: num(totals?["calories"]), goalKcal: num(goal?["calories"]))
    case "confirm":
      if let c = (j["conversation_id"] as? NSNumber)?.intValue { conversationId = c }
      if let call = (j["calls"] as? [[String: Any]])?.first {
        let id = (call["call_id"] as? String) ?? (call["call_id"] as? NSNumber)?.stringValue ?? ""
        confirm = WatchConfirmCall(callId: id, tool: call["tool"] as? String ?? "", summary: call["summary"] as? String ?? "")
      }
    case "done":
      if let c = (j["conversation_id"] as? NSNumber)?.intValue { conversationId = c }
      done = true
      working = false
    case "error":
      failure = WatchVoiceFailure.stream(code: j["code"] as? String)
      working = false
    case "resync":
      if let c = (j["conversation_id"] as? NSNumber)?.intValue { conversationId = c }
      resync = true
    default:
      break   // usage, notice: nothing on the watch
    }
  }
}

// MARK: - the answer as the watch shows it (J3 / J4)

/// A short answer: the first line is the conclusion (the server's watch rule, ai/watch.py), the rest is at most a
/// sentence; anything longer, or the server's own 「详细的在手机上看」, gets 「在手机上继续」.
struct WatchAnswer: Equatable {
  var headline: String
  var body: String?
  var more: Bool

  static func of(_ text: String, resync: Bool = false) -> WatchAnswer {
    let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
    let hinted = WatchParams.phoneHints.contains { t.lowercased().contains($0) }
    // the server's hint becomes the 「在手机上继续」 key, not words on the screen
    var clean = t
    for h in WatchParams.phoneHints {
      while let r = clean.range(of: h, options: .caseInsensitive) { clean.removeSubrange(r) }
    }
    var lines = clean.split(whereSeparator: \.isNewline)
      .map { $0.trimmingCharacters(in: CharacterSet.whitespaces.union(CharacterSet(charactersIn: "，,"))) }
      .filter { !$0.isEmpty }
    // one long line: its first sentence is the headline
    if lines.count == 1, lines[0].count > WatchParams.answerHeadlineMax, let cut = sentenceEnd(lines[0]),
       lines[0].distance(from: lines[0].startIndex, to: cut) < WatchParams.answerHeadlineMax {
      let l = lines[0]
      let rest = String(l[l.index(after: cut)...]).trimmingCharacters(in: .whitespaces)
      lines = [String(l[...cut])] + (rest.isEmpty ? [] : [rest])
    }
    let body = lines.dropFirst().joined(separator: " ")
    return WatchAnswer(headline: lines.first ?? "", body: body.isEmpty ? nil : body,
                       more: hinted || resync || t.count > WatchParams.answerMaxChars)
  }

  /// The end of the first sentence: 。！？；, or ! ? ; . followed by a space (not the point of 2.8).
  private static func sentenceEnd(_ s: String) -> String.Index? {
    var i = s.startIndex
    while i < s.endIndex {
      let c = s[i]
      let next = s.index(after: i)
      if "。！？；".contains(c) { return i }
      if "!?;.".contains(c) && (next == s.endIndex || s[next] == " ") { return i }
      i = next
    }
    return nil
  }
}

// MARK: - what went wrong, and what the watch offers then

/// Every way a voice request can end without an answer (#458 B / C error codes), mapped to one short label, an icon and
/// one way on. Never a dead end: each has a key (again, dictation, or back home) and the ones only the phone can fix say
/// so.
enum WatchVoiceFailure: Equatable {
  /// The watch has no network (Wi-Fi, cellular, or through the phone).
  case offline
  /// Today's voice messages are used up (429 `rate_limit`, shared with the phone): dictation still works.
  case dailyLimit
  /// The account's AI does not run in the app (409 `subscription` / `no_provider` / `disabled`).
  case noAI
  /// The fallback AI's terms must be agreed to on the phone (403 `fallback_consent` / `needs_consent`).
  case consent
  /// This month's allowance (fallback, 402 `quota`) or budget (own key, 402 `budget`) is used up.
  case allowance
  /// A read-only token cannot write or approve (403 `read_only`).
  case readOnly
  /// Nothing was understood (422 `bad_audio` / `too_short` / `too_long`, an empty transcript).
  case unheard
  /// The server or its transcriber is down or busy (5xx, `asr_down`, `asr_off`, `busy`, `asr_failed`).
  case server
  /// The watch's token was refused (401): it is dropped and the phone asked for a new code.
  case signedOut
  /// The stream broke and could not be picked up again.
  case interrupted
  /// The microphone permission was not given (only the user can, in the system prompt or Settings).
  case microphone

  /// A refusal before the stream (`detail.code` of an HTTP error, or the status alone).
  static func http(status: Int, code: String?) -> WatchVoiceFailure {
    switch (status, code) {
    case (401, _): return .signedOut
    case (429, _): return .dailyLimit
    case (402, _): return .allowance
    case (403, "fallback_consent"), (403, "needs_consent"): return .consent
    case (403, "read_only"): return .readOnly
    case (403, _): return .signedOut
    case (409, "subscription"), (409, "no_provider"), (409, "disabled"): return .noAI
    case (409, _): return .server   // `busy`: the conversation is still answering
    case (413, _), (422, _): return .unheard
    case (404, _): return .server   // `not_found`: a conversation that is not this watch's (a new one is started)
    default: return .server
    }
  }

  /// An `error` event inside the stream.
  static func stream(code: String?) -> WatchVoiceFailure {
    switch code {
    case "quota", "budget": return .allowance
    case "rate_limit": return .dailyLimit
    case "subscription", "no_provider", "disabled": return .noAI
    case "fallback_consent", "needs_consent": return .consent
    case "read_only": return .readOnly
    default: return .server
    }
  }

  /// ≤ 4 characters / 2 English words (plan 3), except the hints that send the user to the phone.
  var label: String {
    switch self {
    case .offline: return tr("离线", "Offline")
    case .dailyLimit: return tr("今天用完", "Used up")
    case .noAI: return tr("在手机上设置 AI", "Set up AI on iPhone")
    case .consent: return tr("在手机上打开 Jarvis", "Open Jarvis on iPhone")
    case .allowance: return tr("额度用完", "Allowance used")
    case .readOnly: return tr("只读", "Read-only")
    case .unheard: return tr("没听清", "Didn't catch")
    case .server: return tr("服务器忙", "Server busy")
    case .signedOut: return tr("在手机上登录", "Sign in on iPhone")
    case .interrupted: return tr("连接断了", "Cut off")
    case .microphone: return tr("麦克风", "Microphone")
    }
  }

  /// SF Symbol (the watch app's icon set, as in phase 1).
  var icon: String {
    switch self {
    case .offline: return "wifi.slash"
    case .dailyLimit: return "mic.badge.xmark"
    case .noAI, .consent, .signedOut: return "iphone"
    case .allowance: return "gauge.with.dots.needle.100percent"
    case .readOnly: return "lock"
    case .unheard: return "ear"
    case .server, .interrupted: return "exclamationmark.circle"
    case .microphone: return "mic.slash"
    }
  }

  /// The same request again can work (the network came back, the server recovered). Not heard = hold and say it again.
  var retryable: Bool {
    switch self {
    case .offline, .server, .interrupted: return true
    default: return false
    }
  }

  /// The system's dictation can stand in for our transcriber (plan 4.3 C): the words still go to Jarvis.
  var dictation: Bool {
    switch self {
    case .dailyLimit, .server, .unheard: return true
    default: return false
    }
  }
}

// MARK: - the flow (the screens' state machine)

/// The voice screens and their transitions, pure: the watch app feeds it the user's inputs and the server's answers and
/// carries out the effects it returns (record, transcribe, send, haptics). Unit-tested on the phone.
struct WatchVoiceFlow: Equatable {
  enum Stage: Equatable {
    case idle
    /// M1 / J1: the finger is down, the microphone is on.
    case listening(since: Date)
    /// After release, while the server transcribes (the J2 ring, no words yet).
    case transcribing
    /// M2: the words, cancellable until the timer is due.
    case confirming(text: String, timer: WatchSendTimer)
    /// J2: sent, the answer is coming.
    case thinking(question: String)
    /// J3 / J4.
    case answer(question: String, answer: WatchAnswer, conversation: Int?)
    /// M4 for a delete / undo the server asks about.
    case confirmCall(question: String, call: WatchConfirmCall)
    case failed(WatchVoiceFailure)
  }

  enum Effect: Equatable {
    case startRecording
    /// Stop the microphone; `keep` = the recording is sent, else it is thrown away.
    case stopRecording(keep: Bool)
    case transcribe
    case send(String)
    case approve(callId: String, approve: Bool)
    /// Pick the turn up again after `lastId`.
    case resume(conversation: Int, after: Int)
    /// A meal was logged: the receipt (M3) takes over.
    case receipt(WatchVoiceMeal)
    case haptic(Haptic)
    /// The watch's token was refused: forget it, ask the phone for a new code.
    case dropToken
  }

  enum Haptic: Equatable { case start, stop, failure, arrived, success }

  /// What a retry repeats.
  enum Again: Equatable {
    case transcribe
    case send(String)
    case resume(conversation: Int, after: Int, question: String)
  }

  private(set) var stage: Stage = .idle
  /// The conversation the next question continues (within `WatchParams.voiceFollowUpSec` of the last answer).
  private(set) var conversation: Int?
  private(set) var conversationAt: Date?
  private(set) var again: Again?
  private(set) var resumes = 0
  /// The turn being received.
  private(set) var turn = WatchChatTurn()

  var isIdle: Bool { stage == .idle }
  var isListening: Bool { if case .listening = stage { return true } else { return false } }

  /// The conversation to continue, if the last answer was recent enough.
  func followUp(now: Date) -> Int? {
    guard let c = conversation, let at = conversationAt, now.timeIntervalSince(at) <= WatchParams.voiceFollowUpSec else { return nil }
    return c
  }

  /// The finger stayed on the core: listen (M1).
  mutating func hold(now: Date) -> [Effect] {
    guard isIdle || isTerminal else { return [] }
    stage = .listening(since: now)
    again = nil
    return [.startRecording, .haptic(.start)]
  }

  /// The finger left the core (or the recorder hit `voiceMaxSec`): too short = nothing; else transcribe.
  mutating func release(seconds: Double) -> [Effect] {
    guard isListening else { return [] }
    if WatchRecordingCheck.of(seconds: seconds) == .tooShort {
      stage = .idle
      return [.stopRecording(keep: false), .haptic(.failure)]
    }
    stage = .transcribing
    again = .transcribe
    return [.stopRecording(keep: true), .haptic(.stop), .transcribe]
  }

  /// Slide up while listening (M1 「上滑取消」), ✕ on M2, a tap on the core while thinking (J2), back from any voice screen.
  mutating func cancel() -> [Effect] {
    let wasListening = isListening
    stage = .idle
    again = nil
    turn = WatchChatTurn()
    return wasListening ? [.stopRecording(keep: false)] : []
  }

  mutating func transcribed(_ text: String, now: Date) -> [Effect] {
    guard stage == .transcribing else { return [] }
    let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !t.isEmpty else { return fail(.unheard) }
    stage = .confirming(text: t, timer: WatchSendTimer(start: now))
    return []
  }

  /// Words from the system dictation (the fallback when our transcriber cannot): they were typed by the user's own
  /// choice in the system sheet, so they go out at once.
  mutating func dictated(_ text: String) -> [Effect] {
    let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !t.isEmpty else { return [] }
    return send(t)
  }

  /// The clock: M2's ring is full → send.
  mutating func tick(now: Date) -> [Effect] {
    if case .confirming(let t, let timer) = stage, timer.due(now) { return send(t) }
    return []
  }

  /// A tap on the core in M2 (or Double Tap): send now.
  mutating func sendNow() -> [Effect] {
    if case .confirming(let t, _) = stage { return send(t) }
    return []
  }

  private mutating func send(_ text: String) -> [Effect] {
    stage = .thinking(question: text)
    again = .send(text)
    resumes = 0
    turn = WatchChatTurn()
    return [.send(text)]
  }

  /// M4: 「撤销」 / 「保留」 on a call the server asked about.
  mutating func answerCall(approve: Bool) -> [Effect] {
    guard case .confirmCall(let q, let call) = stage else { return [] }
    stage = .thinking(question: q)
    again = nil
    resumes = 0
    turn = WatchChatTurn(conversationId: turn.conversationId)
    return [.approve(callId: call.callId, approve: approve)]
  }

  /// One event of the stream.
  mutating func received(_ e: WatchSSEEvent) {
    turn.apply(e)
  }

  /// The stream ended (cleanly or not). `broken` = the connection dropped (not an HTTP refusal).
  mutating func streamEnded(broken: Bool, now: Date) -> [Effect] {
    guard case .thinking(let q) = stage else { return [] }
    if let c = turn.conversationId {
      conversation = c
      conversationAt = now
    }
    if let f = turn.failure { return fail(f) }
    if let call = turn.confirm {
      stage = .confirmCall(question: q, call: call)
      return [.haptic(.arrived)]
    }
    if let meal = turn.meal, turn.done || !broken {
      stage = .idle
      again = nil
      return [.receipt(meal), .haptic(.success)]
    }
    if turn.done || turn.resync {
      stage = .answer(question: q, answer: WatchAnswer.of(turn.text, resync: turn.resync), conversation: turn.conversationId)
      again = nil
      return [.haptic(.arrived)]
    }
    // the connection dropped before the end: pick the turn up where it was, a few times
    if let c = turn.conversationId, resumes < WatchParams.voiceResumeTries {
      resumes += 1
      again = .resume(conversation: c, after: turn.lastId, question: q)
      return [.resume(conversation: c, after: turn.lastId)]
    }
    if let c = turn.conversationId { again = .resume(conversation: c, after: turn.lastId, question: q) }
    return fail(broken ? .interrupted : .server)
  }

  /// The microphone could not start (permission refused, or busy).
  mutating func microphoneRefused() -> [Effect] {
    guard isListening else { return [] }
    return fail(.microphone)
  }

  /// A request was refused or could not go out.
  mutating func failed(_ f: WatchVoiceFailure) -> [Effect] {
    guard !isIdle else { return [] }
    return fail(f)
  }

  private mutating func fail(_ f: WatchVoiceFailure) -> [Effect] {
    stage = .failed(f)
    var fx: [Effect] = [.haptic(.failure)]
    if f == .signedOut { fx.append(.dropToken) }
    return fx
  }

  /// 「重试」 on a failure screen.
  mutating func retry() -> [Effect] {
    guard case .failed(let f) = stage, f.retryable, let a = again else { return [] }
    switch a {
    case .transcribe:
      stage = .transcribing
      return [.transcribe]
    case .send(let t):
      return send(t)
    case .resume(let c, let after, let q):
      stage = .thinking(question: q)
      resumes = 0
      return [.resume(conversation: c, after: after)]
    }
  }

  /// Leaving a finished screen (answer, failure) or the receipt: home.
  mutating func close() {
    stage = .idle
    again = nil
  }

  /// The flow ended with an answer or a failure (a new hold may start over).
  var isTerminal: Bool {
    switch stage {
    case .answer, .failed: return true
    default: return false
    }
  }

  #if DEBUG
  /// Frozen screenshots only: put the flow on one screen.
  mutating func demo(_ s: Stage) { stage = s }
  #endif
}
