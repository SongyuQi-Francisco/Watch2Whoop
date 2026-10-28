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
    var id: String { key }

    enum CodingKeys: String, CodingKey { case key, label, kind, available, note, current }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      key = try c.decode(String.self, forKey: .key)
      label = try c.decode(String.self, forKey: .label)
      kind = try c.decodeIfPresent(String.self, forKey: .kind)
      available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? true
      note = try c.decodeIfPresent(String.self, forKey: .note)
      current = try c.decodeIfPresent(Bool.self, forKey: .current) ?? false
    }
  }
}

/// GET /v1/users/me/ai-tokens (tokens for the user's own AI clients over remote MCP).
struct AITokens: Decodable, Equatable {
  var tokens: [Token] = []
  var mcpURL: String?

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
    /// "ai:claude-code" → "claude-code".
    var client: String { name.split(separator: ":").last.map(String.init) ?? name }
    var readOnly: Bool { !scopes.contains("write") }
  }

  enum CodingKeys: String, CodingKey { case tokens, mcp }
  private struct MCP: Decodable { let url: String? }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    tokens = try c.decodeIfPresent([Token].self, forKey: .tokens) ?? []
    mcpURL = try c.decodeIfPresent(MCP.self, forKey: .mcp)?.url
  }
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
