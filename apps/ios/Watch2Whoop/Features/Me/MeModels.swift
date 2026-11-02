import Foundation

// Server answers the Me tab, the consent and the profile step read (profile/api.py, ai/api.py, notify/api.py,
// whatsnew/api.py, sync/data_status.py, appearance/api.py). Online-only settings (docs/ios-native-app.md 5.5): not kept
// in the local database.

/// GET / PATCH /v1/users/me/profile.
struct Profile: Decodable, Equatable {
  var displayName: String?
  var birthDate: String?
  var sex: String?
  var heightCm: Double?
  var timezone: String?
  var goalWeightKg: Double?
  var language: String?
  var missing: [String] = []
  var consent: ConsentStatus = ConsentStatus()

  enum CodingKeys: String, CodingKey {
    case displayName = "display_name", birthDate = "birth_date", sex, heightCm = "height_cm", timezone
    case goalWeightKg = "goal_weight_kg", language, missing, consent
  }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    displayName = try c.decodeIfPresent(String.self, forKey: .displayName)
    birthDate = try c.decodeIfPresent(String.self, forKey: .birthDate)
    sex = try c.decodeIfPresent(String.self, forKey: .sex)
    heightCm = try c.decodeIfPresent(Double.self, forKey: .heightCm)
    timezone = try c.decodeIfPresent(String.self, forKey: .timezone)
    goalWeightKg = try c.decodeIfPresent(Double.self, forKey: .goalWeightKg)
    language = try c.decodeIfPresent(String.self, forKey: .language)
    missing = try c.decodeIfPresent([String].self, forKey: .missing) ?? []
    consent = try c.decodeIfPresent(ConsentStatus.self, forKey: .consent) ?? ConsentStatus()
  }
}

struct ConsentStatus: Decodable, Equatable {
  var required = false
  var reconfirm = false
  var version: String?
  var oneTap = false

  enum CodingKeys: String, CodingKey { case required, reconfirm, version, oneTap = "one_tap" }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    required = try c.decodeIfPresent(Bool.self, forKey: .required) ?? false
    reconfirm = try c.decodeIfPresent(Bool.self, forKey: .reconfirm) ?? false
    version = try c.decodeIfPresent(String.self, forKey: .version)
    oneTap = try c.decodeIfPresent(Bool.self, forKey: .oneTap) ?? false
  }

  var needed: Bool { required || reconfirm }
}

/// GET /v1/consent: the text and its version come from the server, in the request's language.
struct ConsentText: Decodable, Equatable {
  let version: String
  var summary: [String] = []
  var text: String = ""
  var language: String?
  var ai: AITerms?

  struct AITerms: Decodable, Equatable {
    let version: String
    var points: [String] = []
  }
}

/// GET / PUT /v1/users/me/ai-choice.
struct AIChoice: Decodable, Equatable {
  var options: [Option] = []

  struct Option: Decodable, Equatable, Identifiable {
    let key: String
    let label: String
    var kind: String?
    var available = true
    var note: String?
    var current = false
    /// active / disabled for the user's own providers.
    var status: String?
    /// The fallback AI's terms were agreed to.
    var consent = false
    var id: String { key }

    enum CodingKeys: String, CodingKey { case key, label, kind, available, note, current, status, consent }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      key = try c.decode(String.self, forKey: .key)
      label = try c.decode(String.self, forKey: .label)
      kind = try c.decodeIfPresent(String.self, forKey: .kind)
      available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? true
      note = try c.decodeIfPresent(String.self, forKey: .note)
      current = try c.decodeIfPresent(Bool.self, forKey: .current) ?? false
      status = try c.decodeIfPresent(String.self, forKey: .status)
      consent = try c.decodeIfPresent(Bool.self, forKey: .consent) ?? false
    }
  }
}

/// GET /v1/users/me/ai-tokens: tokens for the user's own AI clients over remote MCP, the apps connected with OAuth, and
/// how to set a client up (`api.mcp_setup`; a token is shown only once, in the answer of POST).
struct AITokens: Decodable, Equatable {
  var tokens: [Token] = []
  var connections: [Connection] = []
  var mcpURL: String?
  var oauthNote: String?
  var keychain: String?
  var prompt: String?
  var clients: [String: String] = [:]
  /// The server's web tools for external AIs: nil = off, else whether search is among them.
  var webSearch: Bool?

  /// An app connected with OAuth (DELETE /v1/users/me/ai-connections/{id} disconnects it).
  struct Connection: Decodable, Equatable, Identifiable {
    let id: Int
    var clientName: String?
    var redirectHost: String?
    var scopes: [String] = []
    var lastUsedAt: String?

    enum CodingKeys: String, CodingKey {
      case id, scopes, clientName = "client_name", redirectHost = "redirect_host", lastUsedAt = "last_used_at"
    }

    var readOnly: Bool { !scopes.contains("write") }
  }

  struct Token: Decodable, Equatable, Identifiable {
    let id: Int
    let name: String
    var createdAt: String?
    var lastUsedAt: String?
    var revokedAt: String?
    var scopes: [String] = []

    enum CodingKeys: String, CodingKey {
      case id, name, scopes, createdAt = "created_at", lastUsedAt = "last_used_at", revokedAt = "revoked_at"
    }

    var active: Bool { revokedAt == nil }
    /// "mcp:claude-code" → "claude-code".
    var client: String { name.split(separator: ":").last.map(String.init) ?? name }
    var readOnly: Bool { !scopes.contains("write") }
  }

  enum CodingKeys: String, CodingKey { case tokens, connections, mcp, web }
  private struct MCP: Decodable {
    let url: String?
    var oauth: String?
    var keychain: String?
    var prompt: String?
    var clients: [String: String]?
  }
  private struct Web: Decodable { var search: Bool? }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    tokens = try c.decodeIfPresent([Token].self, forKey: .tokens) ?? []
    connections = try c.decodeIfPresent([Connection].self, forKey: .connections) ?? []
    let m = try c.decodeIfPresent(MCP.self, forKey: .mcp)
    mcpURL = m?.url
    oauthNote = m?.oauth
    keychain = m?.keychain
    prompt = m?.prompt
    clients = m?.clients ?? [:]
    if let w = try c.decodeIfPresent(Web.self, forKey: .web) { webSearch = w.search ?? false }
  }

  var liveTokens: [Token] { tokens.filter(\.active) }
  /// The snippet for a client (the same fallbacks as ai.js: "other", then "claude-code").
  func snippet(_ client: AIClient) -> String? { clients[client.rawValue] ?? clients["other"] ?? clients["claude-code"] }
}

/// GET / PUT /v1/users/me/notification-settings.
struct NotificationSettings: Decodable, Equatable {
  var categories: [String: Bool] = [:]
  var labels: [String: String] = [:]
  var quiet = Quiet()
  var pushConfigured = false
  var devices = 0

  struct Quiet: Codable, Equatable {
    var enabled = false
    var start = "22:00"
    var end = "08:00"
  }

  enum CodingKeys: String, CodingKey { case categories, labels, quiet, pushConfigured = "push_configured", devices }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    categories = try c.decodeIfPresent([String: Bool].self, forKey: .categories) ?? [:]
    labels = try c.decodeIfPresent([String: String].self, forKey: .labels) ?? [:]
    quiet = try c.decodeIfPresent(Quiet.self, forKey: .quiet) ?? Quiet()
    pushConfigured = try c.decodeIfPresent(Bool.self, forKey: .pushConfigured) ?? false
    devices = try c.decodeIfPresent(Int.self, forKey: .devices) ?? 0
  }

  /// Categories in a stable order (by label).
  var ordered: [(key: String, label: String)] {
    categories.keys.map { ($0, labels[$0] ?? $0) }.sorted { $0.1 < $1.1 }
  }
}

/// GET /v1/whats-new.
struct WhatsNew: Decodable, Equatable {
  var weeks: [Week] = []

  struct Week: Decodable, Equatable, Identifiable {
    let weekStart: String
    var items: [Item] = []
    var id: String { weekStart }
    enum CodingKeys: String, CodingKey { case weekStart = "week_start", items }
  }

  struct Item: Decodable, Equatable, Hashable {
    let date: String
    let text: String
  }
}

/// GET /v1/users/me/data-status.
struct DataStatus: Decodable, Equatable {
  var types: [Kind] = []
  var summary: [String: Int] = [:]
  /// When each way in last delivered (sync/data_status._channels).
  var channels: Channels?
  /// Strength-training records of the last weeks by source (#210): the same records body age counts.
  var strength: Strength?

  struct Channels: Decodable, Equatable {
    var owLastSampleAt: String?
    var appUploadAt: String?
    var shortcutUploadAt: String?

    enum CodingKeys: String, CodingKey {
      case owLastSampleAt = "ow_last_sample_at", appUploadAt = "app_upload_at", shortcutUploadAt = "shortcut_upload_at"
    }
  }

  struct Strength: Decodable, Equatable {
    struct Sessions: Decodable, Equatable {
      var watch = 0
      var sparky: Int?
      var hevy = 0

      enum CodingKeys: String, CodingKey { case watch, sparky, hevy }

      init(watch: Int = 0, sparky: Int? = nil, hevy: Int = 0) {
        self.watch = watch
        self.sparky = sparky
        self.hevy = hevy
      }

      init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        watch = try c.decodeIfPresent(Int.self, forKey: .watch) ?? 0
        sparky = try c.decodeIfPresent(Int.self, forKey: .sparky)
        hevy = try c.decodeIfPresent(Int.self, forKey: .hevy) ?? 0
      }
    }

    var weeks = 0
    var days = 0
    var sessions = Sessions()
    /// connected / not_linked / unreadable.
    var sparky: String?

    enum CodingKeys: String, CodingKey { case weeks, days, sessions, sparky }

    init(sessions: Sessions, sparky: String?) {
      self.sessions = sessions
      self.sparky = sparky
    }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      weeks = try c.decodeIfPresent(Int.self, forKey: .weeks) ?? 0
      days = try c.decodeIfPresent(Int.self, forKey: .days) ?? 0
      sessions = try c.decodeIfPresent(Sessions.self, forKey: .sessions) ?? Sessions()
      sparky = try c.decodeIfPresent(String.self, forKey: .sparky)
    }

    /// `strengthText` in data-status.js; the flag ai_parity keeps the back end's name out of the app.
    func sourcesLine(hideBackEnd: Bool) -> String {
      let n: String
      switch sparky {
      case "connected": n = String(sessions.sparky ?? 0)
      case "not_linked": n = tr("未连接", "Not connected")
      default: n = tr("读不到", "Can't read")
      }
      let db = hideBackEnd ? tr("底层数据库", "Database") : "SparkyFitness"
      let watch = tr("手表", "Watch")
      return "\(watch) \(sessions.watch) · \(db) \(n) · Hevy \(sessions.hevy)"
    }
  }

  enum CodingKeys: String, CodingKey { case types, summary, channels, strength }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    types = try c.decodeIfPresent([Kind].self, forKey: .types) ?? []
    summary = try c.decodeIfPresent([String: Int].self, forKey: .summary) ?? [:]
    channels = try c.decodeIfPresent(Channels.self, forKey: .channels)
    strength = try c.decodeIfPresent(Strength.self, forKey: .strength)
  }

  /// "11月3日 09:30" in the phone's time zone; "—" for nothing. The server's times are ISO 8601, with or without fractions.
  static func when(_ iso: String?, zone: TimeZone = .current) -> String {
    guard let iso, let date = FeedFormat.parseDate(iso) else { return "—" }
    let f = DateFormatter()
    f.locale = Locale(identifier: Lang.isZh ? "zh_Hans" : "en_US_POSIX")
    f.timeZone = zone
    f.dateFormat = tr("M月d日 HH:mm", "MMM d, HH:mm")
    return f.string(from: date)
  }

  struct Kind: Decodable, Equatable, Identifiable {
    let key: String
    let label: String
    let status: String              // ok / stale / never
    var lastDate: String?
    var daysSince: Int?
    var id: String { key }

    enum CodingKeys: String, CodingKey { case key, label, status, lastDate = "last_date", daysSince = "days_since" }
  }
}

/// GET / PUT /v1/users/me/appearance (flag slim_mode).
struct AppearanceChoice: Codable, Equatable {
  var theme = "default"
  var quotes = true
}
