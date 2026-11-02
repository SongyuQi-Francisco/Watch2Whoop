import Foundation

// The user's own AI sources (ai/api.py, ai/providers.py, ai/catalog.py). Online only (docs/ios-native-app.md 5.5): nothing
// here reaches the local database, the offline queue, UserDefaults or the Keychain. The server keeps the key, encrypted,
// and only ever answers its last 4 characters (`secret_last4`).

/// GET /v1/users/me/ai-providers.
struct AIProviderList: Decodable, Equatable {
  var providers: [AIProvider] = []
  var monthCostUSD = 0.0
  var kinds: [AIKind] = []
  var fallback: AIFallback?

  enum CodingKeys: String, CodingKey { case providers, kinds, fallback, monthCostUSD = "month_cost_usd" }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    providers = try c.decodeIfPresent([AIProvider].self, forKey: .providers) ?? []
    monthCostUSD = try c.decodeIfPresent(Double.self, forKey: .monthCostUSD) ?? 0
    kinds = try c.decodeIfPresent([AIKind].self, forKey: .kinds) ?? []
    fallback = try c.decodeIfPresent(AIFallback.self, forKey: .fallback)
  }

  func kind(_ name: String) -> AIKind? { kinds.first { $0.kind == name } }
  var hasAPIProvider: Bool { providers.contains { $0.isAPI } }
}

/// One row of `ai_providers` (providers._public): never the key itself.
struct AIProvider: Decodable, Equatable, Identifiable {
  let id: Int
  let kind: String
  var displayName: String
  var category = "api"
  var model: String?
  var baseURL: String?
  var secretLast4: String?
  var monthlyBudgetUSD: Double?
  var webTools = false
  var priceInPerMtok: Double?
  var priceOutPerMtok: Double?
  var status = "active"
  var retired = false
  var monthCostUSD = 0.0

  enum CodingKeys: String, CodingKey {
    case id, kind, category, model, status, retired
    case displayName = "display_name", baseURL = "base_url", secretLast4 = "secret_last4"
    case monthlyBudgetUSD = "monthly_budget_usd", webTools = "web_tools", priceInPerMtok = "price_in_per_mtok"
    case priceOutPerMtok = "price_out_per_mtok", monthCostUSD = "month_cost_usd"
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(Int.self, forKey: .id)
    kind = try c.decode(String.self, forKey: .kind)
    displayName = try c.decodeIfPresent(String.self, forKey: .displayName) ?? kind
    category = try c.decodeIfPresent(String.self, forKey: .category) ?? "api"
    model = try c.decodeIfPresent(String.self, forKey: .model)
    baseURL = try c.decodeIfPresent(String.self, forKey: .baseURL)
    secretLast4 = try c.decodeIfPresent(String.self, forKey: .secretLast4)
    monthlyBudgetUSD = try c.decodeIfPresent(Double.self, forKey: .monthlyBudgetUSD)
    webTools = try c.decodeIfPresent(Bool.self, forKey: .webTools) ?? false
    priceInPerMtok = try c.decodeIfPresent(Double.self, forKey: .priceInPerMtok)
    priceOutPerMtok = try c.decodeIfPresent(Double.self, forKey: .priceOutPerMtok)
    status = try c.decodeIfPresent(String.self, forKey: .status) ?? "active"
    retired = try c.decodeIfPresent(Bool.self, forKey: .retired) ?? false
    monthCostUSD = try c.decodeIfPresent(Double.self, forKey: .monthCostUSD) ?? 0
  }

  var isAPI: Bool { category == "api" }
  var isActive: Bool { status == "active" }
  /// The $0 unit price of 「免费额度」 (a custom price set some other way is not this).
  var isFree: Bool { priceInPerMtok == 0 && priceOutPerMtok == 0 }

  /// The line under the name: model, spend and the key's last 4, the only part of a key the phone ever sees.
  var subtitle: String {
    if retired { return tr("已下线，只能删除", "Retired; it can only be deleted") }
    guard isAPI else { return tr("订阅", "Subscription") }
    var parts: [String] = []
    if let model, !model.isEmpty { parts.append(model) }
    if isFree {
      parts.append(tr("免费额度", "Free tier"))
    } else {
      let spent = Self.money(monthCostUSD), budget = Self.money(monthlyBudgetUSD)
      parts.append(tr("本月 \(spent) / \(budget)", "This month \(spent) / \(budget)"))
    }
    if let secretLast4, !secretLast4.isEmpty { parts.append("key …\u{2060}\(secretLast4)") }   // a word joiner: never broken after the dots
    return parts.joined(separator: " · ")
  }

  static func money(_ v: Double?) -> String {
    guard let v else { return "—" }
    return String(format: v < 1 ? "$%.3f" : "$%.2f", v)
  }
}

/// One entry of the server's catalog (catalog.public_catalog): the labels and hints come in the request's language.
struct AIKind: Decodable, Equatable, Identifiable {
  let kind: String
  var label: String
  var category = "api"
  var defaultModel: String?
  var models: [String] = []
  var keyHint: String?
  var note: String?
  var client: String?
  var needsBaseURL = false
  var baseURLExamples: [String] = []
  var id: String { kind }

  enum CodingKeys: String, CodingKey {
    case kind, label, category, models, note, client
    case defaultModel = "default_model", keyHint = "key_hint", needsBaseURL = "needs_base_url", baseURLExamples = "base_url_examples"
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    kind = try c.decode(String.self, forKey: .kind)
    label = try c.decodeIfPresent(String.self, forKey: .label) ?? kind
    category = try c.decodeIfPresent(String.self, forKey: .category) ?? "api"
    defaultModel = try c.decodeIfPresent(String.self, forKey: .defaultModel)
    models = try c.decodeIfPresent([String].self, forKey: .models) ?? []
    keyHint = try c.decodeIfPresent(String.self, forKey: .keyHint)
    note = try c.decodeIfPresent(String.self, forKey: .note)
    client = try c.decodeIfPresent(String.self, forKey: .client)
    needsBaseURL = try c.decodeIfPresent(Bool.self, forKey: .needsBaseURL) ?? false
    baseURLExamples = try c.decodeIfPresent([String].self, forKey: .baseURLExamples) ?? []
  }

  var isAPI: Bool { category == "api" }
  /// ai/catalog.WEB_TOOL_KINDS: the vendor's own web search, off by default, billed on the user's own key.
  var offersWebTools: Bool { ["anthropic_api", "openai_api", "gemini_api"].contains(kind) }
}

/// The fallback AI in AI settings (api._fallback_view): what the user sees is a percentage, never amounts or the model.
struct AIFallback: Decodable, Equatable {
  var displayName = "Jarvis"
  var remainingPct: Int?
  var resetOn: String?
  var consent = false
  var consentedAt: String?
  var consentVersion = ""
  var available = false
  var reason: String?
  var message: String?
  var used = false
  var points: [String] = []
  var pointsTranslated = false

  enum CodingKeys: String, CodingKey {
    case consent, available, reason, message, used, points
    case displayName = "display_name", remainingPct = "remaining_pct", resetOn = "reset_on", consentedAt = "consented_at"
    case consentVersion = "consent_version", pointsTranslated = "points_translated"
  }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    displayName = try c.decodeIfPresent(String.self, forKey: .displayName) ?? "Jarvis"
    remainingPct = try c.decodeIfPresent(Int.self, forKey: .remainingPct)
    resetOn = try c.decodeIfPresent(String.self, forKey: .resetOn)
    consent = try c.decodeIfPresent(Bool.self, forKey: .consent) ?? false
    consentedAt = try c.decodeIfPresent(String.self, forKey: .consentedAt)
    consentVersion = try c.decodeIfPresent(String.self, forKey: .consentVersion) ?? ""
    available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
    reason = try c.decodeIfPresent(String.self, forKey: .reason)
    message = try c.decodeIfPresent(String.self, forKey: .message)
    used = try c.decodeIfPresent(Bool.self, forKey: .used) ?? false
    points = try c.decodeIfPresent([String].self, forKey: .points) ?? []
    pointsTranslated = try c.decodeIfPresent(Bool.self, forKey: .pointsTranslated) ?? false
  }

  /// The tag next to the name (`fbBadge` in ai.js).
  var badge: (text: String, tone: Tone)? {
    switch reason {
    case "quota": return (tr("已用完", "Used up"), .mid)
    case "fallback_consent": return (tr("未同意", "Not agreed"), .text)
    case "fallback_paused": return (tr("已暂停", "Paused"), .mid)
    case "fallback_off": return (tr("已关闭", "Off"), .text)
    default: return nil
    }
  }

  var quotaLine: String {
    let pct = remainingPct.map(String.init) ?? "—", day = AIFallback.monthDay(resetOn)
    return tr("本月剩 \(pct)% · \(day) 重置", "\(pct)% left this month · resets \(day)")
  }

  static func monthDay(_ iso: String?) -> String {
    guard let iso, iso.count >= 10 else { return "—" }
    return String(iso.dropFirst(5).prefix(5))
  }
}

/// PATCH / POST body of a provider. The API key can sit in here for the moment between the form and the request, so this
/// type never prints it: `description`, `debugDescription`, `dump` and `Mirror` all leave it out, and the app has no
/// logger, crash reporter or queue that takes request bodies.
struct AIProviderBody: Encodable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
  enum Price { case keep, free, clear }

  var kind: String?
  var model: String?
  var baseURL: String?
  var apiKey: String?
  var monthlyBudgetUSD: Double?
  var price = Price.keep
  var webTools: Bool?
  var isDefault: Bool?

  enum CodingKeys: String, CodingKey {
    case kind, model
    case baseURL = "base_url", apiKey = "api_key", monthlyBudgetUSD = "monthly_budget_usd", webTools = "web_tools"
    case isDefault = "is_default", priceIn = "price_in_per_mtok", priceOut = "price_out_per_mtok"
  }

  func encode(to e: Encoder) throws {
    var c = e.container(keyedBy: CodingKeys.self)
    try c.encodeIfPresent(kind, forKey: .kind)
    try c.encodeIfPresent(model, forKey: .model)
    try c.encodeIfPresent(baseURL, forKey: .baseURL)
    try c.encodeIfPresent(apiKey, forKey: .apiKey)
    try c.encodeIfPresent(monthlyBudgetUSD, forKey: .monthlyBudgetUSD)
    try c.encodeIfPresent(webTools, forKey: .webTools)
    try c.encodeIfPresent(isDefault, forKey: .isDefault)
    switch price {
    case .keep: break
    case .free:
      try c.encode(0, forKey: .priceIn)
      try c.encode(0, forKey: .priceOut)
    case .clear:   // unticking removes only the $0 this box set
      try c.encodeNil(forKey: .priceIn)
      try c.encodeNil(forKey: .priceOut)
    }
  }

  var description: String { "AIProviderBody(kind: \(kind ?? "-"), key: \(apiKey == nil ? "none" : "<redacted>"))" }
  var debugDescription: String { description }
  var customMirror: Mirror { Mirror(self, children: ["summary": description]) }
}

/// What the provider form holds. The key stays in this struct, in view state, until it is sent; the form empties it before
/// the request leaves and when it goes away. It is never stored anywhere.
struct AIProviderDraft: Equatable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
  var model = ""
  var baseURL = ""
  var apiKey = ""
  var budget = "5"
  var free = false
  var webTools = false
  var makeDefault = true
  private(set) var wasFree = false

  // The same limits the server checks (providers.py, api.ProviderIn), asked first so that a typo does not cost the key.
  static let keyLengths = 8...512
  static let maxBudget = 10_000.0
  static let localHosts: Set<String> = ["localhost", "127.0.0.1", "host.docker.internal"]

  init(kind: AIKind) {
    model = kind.defaultModel ?? ""
  }

  init(provider p: AIProvider) {
    model = p.model ?? ""
    baseURL = p.baseURL ?? ""
    budget = p.monthlyBudgetUSD.map(Self.number) ?? "5"
    free = p.isFree
    wasFree = p.isFree
    webTools = p.webTools
    makeDefault = false
  }

  var key: String { apiKey.trimmingCharacters(in: .whitespacesAndNewlines) }

  /// What is wrong before anything is sent; nil = send it. The server checks everything again.
  func problem(kind: AIKind, editing: Bool) -> String? {
    guard kind.isAPI else { return nil }
    let m = model.trimmingCharacters(in: .whitespaces)
    if m.isEmpty || m.count > 100 || m.range(of: "^[A-Za-z0-9._:/@+-]+$", options: .regularExpression) == nil {
      return tr("要填模型名", "Enter a model name")
    }
    if kind.needsBaseURL && baseURL.trimmingCharacters(in: .whitespaces).isEmpty {
      return tr("要填接口地址", "Enter the API address")
    }
    if !key.isEmpty, !Self.keyLengths.contains(key.count) || key.contains(where: \.isWhitespace) {
      return tr("API key 格式不对（8–512 个字符，不能有空格）", "The API key looks wrong (8–512 characters, no spaces)")
    }
    if key.isEmpty && !editing && !isLocalService { return tr("要填 API key", "Enter an API key") }
    guard let b = budgetValue, b >= 0, b <= Self.maxBudget else {
      return tr("每月预算要在 0–10000 之间", "The monthly budget must be between 0 and 10000")
    }
    return nil
  }

  /// A local service (Ollama…) needs no key (providers._normalize).
  private var isLocalService: Bool {
    guard let host = URL(string: baseURL.trimmingCharacters(in: .whitespaces))?.host else { return false }
    return Self.localHosts.contains(host)
  }

  private var budgetValue: Double? {
    let s = budget.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ".")
    return s.isEmpty ? 0 : Double(s)
  }

  /// The body of POST (`editing` false) or PATCH: same fields as `readApiFields` in ai.js.
  func body(kind: AIKind, editing: Bool) -> AIProviderBody {
    var b = AIProviderBody()
    if !editing {
      b.kind = kind.kind
      b.isDefault = makeDefault
    }
    guard kind.isAPI else { return b }
    b.model = model.trimmingCharacters(in: .whitespaces)
    if kind.needsBaseURL { b.baseURL = baseURL.trimmingCharacters(in: .whitespaces) }
    b.monthlyBudgetUSD = budgetValue ?? 0
    if kind.offersWebTools { b.webTools = webTools }
    b.price = free ? .free : (editing && wasFree ? .clear : .keep)
    if !key.isEmpty { b.apiKey = key }
    return b
  }

  static func number(_ v: Double) -> String { v == v.rounded() ? String(Int(v)) : String(format: "%.2f", v) }

  var description: String { "AIProviderDraft(model: \(model), key: \(apiKey.isEmpty ? "none" : "<redacted>"))" }
  var debugDescription: String { description }
  var customMirror: Mirror { Mirror(self, children: ["summary": description]) }
}

/// The clients of 「连接客户端（MCP）」 (`CLIENTS` in ai.js); the server's snippets are keyed by these names.
enum AIClient: String, CaseIterable, Identifiable {
  case claudeCode = "claude-code", codex, antigravity, geminiCLI = "gemini-cli", other
  var id: String { rawValue }

  var label: String {
    switch self {
    case .claudeCode: return tr("Claude Code / Claude 桌面版", "Claude Code / Claude Desktop")
    case .codex: return "Codex CLI"
    case .antigravity: return "Antigravity"
    case .geminiCLI: return tr("Gemini CLI（API key）", "Gemini CLI (API key)")
    case .other: return tr("其他 MCP 客户端", "Other MCP client")
    }
  }

  init(catalog name: String?) { self = name.flatMap(AIClient.init(rawValue:)) ?? .claudeCode }
}
