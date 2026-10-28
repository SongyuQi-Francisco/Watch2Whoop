import Foundation

/// Which AI Jarvis uses right now, as `/v1/web/me` and `/v1/users/me/ai-choice` → `ai` describe it (ai/api.py `_ai_mode`):
/// - `api` / `fallback`: the in-app chat (`POST /v1/assistant/chat`); the fallback needs its own consent first;
/// - `butler`: the deployer's body butler, opened by its link (`assistant_url`);
/// - `mcp`: a subscription AI, used in its own official app;
/// - `none`: nothing set up yet.
struct JarvisAI: Equatable {
  enum Mode: String { case api, mcp, fallback, butler, none }

  var mode: Mode = .none
  var displayName: String?
  /// Fallback only: the user agreed to the Jarvis terms.
  var consent = false
  var consentVersion: String?
  var available = true
  var reason: String?
  var message: String?
  var remainingPct: Int?
  /// false = this AI cannot read pictures (no photo / video buttons).
  var vision = true
  var assistantURL: URL?

  /// The chat runs inside the app (own API key, or the fallback AI).
  var inApp: Bool { mode == .api || mode == .fallback }
  /// Short name for the chat header's switcher (ai.js shortName).
  var shortName: String {
    switch mode {
    case .fallback: return "Jarvis"
    case .butler: return tr("身体管家", "Body butler")
    case .none: return tr("还没设置", "Not set up")
    default: return displayName ?? "AI"
    }
  }

  init() {}

  init(_ v: JSONValue, assistantURL: String? = nil) {
    mode = Mode(rawValue: v["mode"]?.string ?? "none") ?? .none
    displayName = v["display_name"]?.string
    consent = v["consent"]?.bool ?? false
    consentVersion = v["consent_version"]?.string
    available = v["available"]?.bool ?? true
    reason = v["reason"]?.string
    message = v["message"]?.string
    remainingPct = mode == .fallback ? ChatEvent.pct(v) : nil
    vision = v["vision"]?.bool ?? true
    let url = assistantURL ?? v["assistant_url"]?.string
    self.assistantURL = url.flatMap(URL.init(string:))
  }
}

/// One option of the AI switcher (`/v1/users/me/ai-choice` → `options`, ai/choice.py `options`).
struct JarvisChoice: Equatable, Identifiable {
  let key: String
  let label: String
  var kind: String?
  var available = true
  var note: String?
  var current = false
  /// Fallback only: already agreed (picking it otherwise asks first).
  var consent = true
  var id: String { key }

  init?(_ v: JSONValue) {
    guard let k = v["key"]?.string else { return nil }
    key = k
    label = v["label"]?.string ?? k
    kind = v["kind"]?.string
    available = v["available"]?.bool ?? true
    note = v["note"]?.string
    current = v["current"]?.bool ?? false
    consent = v["consent"]?.bool ?? true
  }
}

/// `GET /v1/users/me/ai-fallback`: the terms to agree to before the first fallback chat.
struct FallbackTerms: Equatable {
  var points: [String] = []
  var version = ""
  var translated = false

  init() {}

  init(_ v: JSONValue) {
    points = (v["points"]?.array ?? []).compactMap(\.string)
    version = v["consent_version"]?.string ?? ""
    translated = v["points_translated"]?.bool ?? false
  }
}

/// A row of the conversation list (`GET /v1/assistant/conversations`).
struct ConversationSummary: Equatable, Identifiable {
  let id: Int
  let title: String
  let updatedAt: String?

  init?(_ v: JSONValue) {
    guard let id = v["id"]?.int else { return nil }
    self.id = id
    title = v["title"]?.string ?? ""
    updatedAt = v["updated_at"]?.string
  }

  /// `2026-01-02T08:30:00+10:00` → `01-02 08:30` (the list shows when, not how long ago).
  var when: String {
    guard let s = updatedAt, s.count >= 16 else { return "" }
    let a = s.index(s.startIndex, offsetBy: 5), b = s.index(s.startIndex, offsetBy: 10)
    let c = s.index(s.startIndex, offsetBy: 11), d = s.index(s.startIndex, offsetBy: 16)
    return "\(s[a..<b]) \(s[c..<d])"
  }
}
