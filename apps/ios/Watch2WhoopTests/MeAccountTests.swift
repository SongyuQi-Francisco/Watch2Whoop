import XCTest
@testable import Watch2Whoop

/// AI settings (own API key), sign-in methods, memory, feedback: the server's answers and what is sent. The key in these
/// tests is an obvious fake.
final class MeAccountTests: XCTestCase {
  private let fakeKey = "fake-key-for-tests-0000"

  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  private func kind(_ json: String) throws -> AIKind { try JSONDecoder().decode(AIKind.self, from: Data(json.utf8)) }
  private var anthropic: AIKind {
    get throws { try kind(#"{"kind": "anthropic_api", "label": "Anthropic API", "category": "api", "default_model": "model-a", "models": ["model-a", "model-b"], "key_hint": "sk-…"}"#) }
  }
  private var compatible: AIKind {
    get throws { try kind(#"{"kind": "openai_compatible", "label": "Compatible", "category": "api", "default_model": null, "models": [], "needs_base_url": true, "base_url_examples": ["https://api.example.com/v1"]}"#) }
  }
  private var subscription: AIKind {
    get throws { try kind(#"{"kind": "claude_plan_mcp", "label": "Plan", "category": "mcp", "client": "claude-code", "note": "Use its own app"}"#) }
  }

  private func object(_ b: AIProviderBody) throws -> [String: Any] {
    try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.api.encode(b)) as? [String: Any])
  }

  func testProviderListKeepsOnlyTheLastFour() throws {
    let j = #"""
    {"providers": [{"id": 3, "kind": "anthropic_api", "display_name": "My Claude", "model": "model-a", "base_url": null, "secret_last4": "abcd",
                    "monthly_budget_usd": 5.0, "web_tools": false, "price_in_per_mtok": null, "price_out_per_mtok": null, "status": "active",
                    "is_default": true, "has_secret": true, "category": "api", "retired": false, "label": "Anthropic API", "month_cost_usd": 0.1234},
                   {"id": 4, "kind": "claude_plan_mcp", "display_name": "Plan", "category": "mcp", "status": "disabled", "retired": false, "month_cost_usd": 0},
                   {"id": 5, "kind": "gemini_cli", "display_name": "Old", "category": "api", "status": "disabled", "retired": true}],
     "month_cost_usd": 0.1234, "mcp": {"url": "https://body.example.com/mcp"},
     "kinds": [{"kind": "anthropic_api", "label": "Anthropic API", "category": "api", "default_model": "model-a", "models": ["model-a"], "vision": true, "prices": {"model-a": [4.0, 20.0]}}],
     "fallback": {"mode": "fallback", "display_name": "Jarvis", "remaining_pct": 80, "reset_on": "2026-12-01", "consent": false, "consent_version": "v1",
                  "available": false, "reason": "fallback_consent", "message": "Agree first", "used": true, "points": ["One"], "points_translated": true}}
    """#
    let l = try JSONDecoder().decode(AIProviderList.self, from: Data(j.utf8))
    XCTAssertEqual(l.providers.map(\.id), [3, 4, 5])
    XCTAssertEqual(l.providers[0].subtitle, "model-a · 本月 $0.123 / $5.00 · key …abcd")
    XCTAssertTrue(l.hasAPIProvider)
    XCTAssertEqual(l.kind("anthropic_api")?.defaultModel, "model-a")
    XCTAssertEqual(l.fallback?.badge?.text, "未同意")
    XCTAssertEqual(l.fallback?.quotaLine, "本月剩 80% · 12-01 重置")
    XCTAssertEqual(l.providers[1].subtitle, "订阅")
    XCTAssertFalse(l.providers[1].isActive)
    XCTAssertTrue(l.providers[2].retired)
  }

  func testAddBodySendsTheTrimmedKeyAndItsFields() throws {
    var d = AIProviderDraft(kind: try anthropic)
    XCTAssertEqual(d.model, "model-a")
    d.apiKey = "  \(fakeKey)\n"
    d.budget = "7,5"
    d.webTools = true
    XCTAssertNil(d.problem(kind: try anthropic, editing: false))
    let o = try object(d.body(kind: try anthropic, editing: false))
    XCTAssertEqual(o["api_key"] as? String, fakeKey)
    XCTAssertEqual(o["kind"] as? String, "anthropic_api")
    XCTAssertEqual(o["model"] as? String, "model-a")
    XCTAssertEqual(o["monthly_budget_usd"] as? Double, 7.5)
    XCTAssertEqual(o["web_tools"] as? Bool, true)
    XCTAssertEqual(o["is_default"] as? Bool, true)
    XCTAssertNil(o["price_in_per_mtok"])
  }

  func testAProblemIsFoundBeforeTheKeyIsSpent() throws {
    let k = try anthropic
    var d = AIProviderDraft(kind: k)
    XCTAssertNotNil(d.problem(kind: k, editing: false), "a new API AI needs a key")
    XCTAssertNil(d.problem(kind: k, editing: true), "editing keeps the saved key")
    d.apiKey = "short"
    XCTAssertNotNil(d.problem(kind: k, editing: false))
    d.apiKey = "has a space in it"
    XCTAssertNotNil(d.problem(kind: k, editing: false))
    d.apiKey = fakeKey
    d.budget = "99999"
    XCTAssertNotNil(d.problem(kind: k, editing: false))
    d.budget = "abc"
    XCTAssertNotNil(d.problem(kind: k, editing: false))
    d.budget = ""
    d.model = "bad model"
    XCTAssertNotNil(d.problem(kind: k, editing: false))
  }

  func testACompatibleServiceNeedsAnAddressAndALocalOneNoKey() throws {
    let k = try compatible
    var d = AIProviderDraft(kind: k)
    d.model = "local-model"
    XCTAssertNotNil(d.problem(kind: k, editing: false))
    d.baseURL = "https://api.example.com/v1"
    XCTAssertNotNil(d.problem(kind: k, editing: false), "a remote service needs a key")
    d.baseURL = "http://localhost:11434/v1"
    XCTAssertNil(d.problem(kind: k, editing: false))
    let o = try object(d.body(kind: k, editing: false))
    XCTAssertEqual(o["base_url"] as? String, "http://localhost:11434/v1")
    XCTAssertNil(o["api_key"])
    XCTAssertNil(o["web_tools"], "only the three vendors have web tools")
  }

  func testASubscriptionSendsNoModelOrKey() throws {
    let k = try subscription
    XCTAssertNil(AIProviderDraft(kind: k).problem(kind: k, editing: false))
    let o = try object(AIProviderDraft(kind: k).body(kind: k, editing: false))
    XCTAssertEqual(o.keys.sorted(), ["is_default", "kind"])
    XCTAssertEqual(AIClient(catalog: k.client), .claudeCode)
  }

  func testEditingLeavesAnEmptyKeyOutAndClearsOnlyItsOwnFreePrice() throws {
    let k = try anthropic
    let free = try JSONDecoder().decode(AIProvider.self, from: Data(
      #"{"id": 1, "kind": "anthropic_api", "display_name": "A", "model": "model-a", "monthly_budget_usd": 5, "price_in_per_mtok": 0, "price_out_per_mtok": 0}"#.utf8))
    var d = AIProviderDraft(provider: free)
    XCTAssertTrue(d.free)
    var o = try object(d.body(kind: k, editing: true))
    XCTAssertNil(o["api_key"], "empty = keep the saved key")
    XCTAssertNil(o["kind"])
    XCTAssertEqual(o["price_in_per_mtok"] as? Int, 0)
    d.free = false
    o = try object(d.body(kind: k, editing: true))
    XCTAssertTrue(o["price_in_per_mtok"] is NSNull, "unticking sends null")
    XCTAssertTrue(o["price_out_per_mtok"] is NSNull)
    let paid = try JSONDecoder().decode(AIProvider.self, from: Data(
      #"{"id": 2, "kind": "anthropic_api", "display_name": "B", "model": "model-a", "price_in_per_mtok": 1.5, "price_out_per_mtok": 2}"#.utf8))
    XCTAssertNil(try object(AIProviderDraft(provider: paid).body(kind: k, editing: true))["price_in_per_mtok"], "a custom price is left alone")
  }

  func testTheKeyNeverAppearsInPrintedOrReflectedForms() throws {
    let k = try anthropic
    var d = AIProviderDraft(kind: k)
    d.apiKey = fakeKey
    let b = d.body(kind: k, editing: false)
    var dumped = ""
    dump(b, to: &dumped)
    var dumpedDraft = ""
    dump(d, to: &dumpedDraft)
    for text in [String(describing: b), String(reflecting: b), "\(b)", dumped, "\(Mirror(reflecting: b).children.map(\.value))",
                 String(describing: d), String(reflecting: d), dumpedDraft] {
      XCTAssertFalse(text.contains(fakeKey), text)
    }
    XCTAssertEqual(try object(b)["api_key"] as? String, fakeKey, "only the encoded request body carries it")
  }

  func testAKeyWriteIsSentOnceAndNeverQueued() async throws {
    var d = AIProviderDraft(kind: try anthropic)
    d.apiKey = fakeKey
    let r = try APIRequest.json(.post, Endpoint.aiProviders, d.body(kind: try anthropic, editing: false), idempotent: false)
    XCTAssertFalse(r.isIdempotent)
    StubProtocol.reset([.init(status: 500)])
    let api = APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" }, session: StubProtocol.session(), sleep: { _ in })
    do {
      _ = try await api.data(r)
      XCTFail("500")
    } catch let e as APIError {
      XCTAssertTrue(e.isRetryable)     // a queue would retry this one
    }
    XCTAssertEqual(StubProtocol.requests.count, 1)
  }

  func testAWrongCodeDoesNotSignOutWithKeepingSignIn() async throws {
    var signedOut = 0
    let api = APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" }, session: StubProtocol.session(),
                        onUnauthorized: { signedOut += 1 }, sleep: { _ in })
    StubProtocol.reset([.init(status: 401, body: #"{"detail": "code_invalid"}"#)])
    do {
      _ = try await api.keepingSignIn().data(try .json(.post, Endpoint.bindEmail, ["email": "a@example.com", "code": "000000"], idempotent: false))
      XCTFail("401")
    } catch let e as APIError {
      XCTAssertEqual(e, .unauthorized)
    }
    XCTAssertEqual(signedOut, 0)
    let sent = try XCTUnwrap(StubProtocol.requests.first)
    XCTAssertEqual(sent.value(forHTTPHeaderField: "Authorization"), "Bearer example-token")
  }

  func testTokensConnectionsAndSnippets() throws {
    let j = #"""
    {"tokens": [{"id": 7, "name": "mcp:codex", "scopes": ["mcp", "read"], "last_used_at": "2026-10-20T10:00:00+00:00", "revoked_at": null}],
     "connections": [{"id": 9, "client_name": "Some app", "redirect_host": "app.example.com", "scopes": ["mcp", "read", "write"], "created_at": "2026-10-01T00:00:00+00:00", "last_used_at": null}],
     "mcp": {"url": "https://body.example.com/mcp", "keychain": "security add …", "prompt": "Read the prompt", "oauth": "Add this address",
             "clients": {"codex": "codex mcp add …", "other": "{}"}},
     "web": {"search": true}}
    """#
    let t = try JSONDecoder().decode(AITokens.self, from: Data(j.utf8))
    XCTAssertEqual(t.liveTokens.map(\.client), ["codex"])
    XCTAssertEqual(t.connections.first?.clientName, "Some app")
    XCTAssertFalse(try XCTUnwrap(t.connections.first).readOnly)
    XCTAssertEqual(t.snippet(.codex), "codex mcp add …")
    XCTAssertEqual(t.snippet(.antigravity), "{}", "falls back to the generic one")
    XCTAssertEqual(t.webSearch, true)
    XCTAssertEqual(t.oauthNote, "Add this address")
  }

  func testChoiceOptionCarriesStatusAndConsent() throws {
    let j = #"{"options": [{"key": "fallback", "label": "Jarvis", "consent": false}, {"key": "p:3", "label": "Mine", "status": "disabled", "available": false}]}"#
    let c = try JSONDecoder().decode(AIChoice.self, from: Data(j.utf8))
    XCTAssertFalse(c.options[0].consent)
    XCTAssertEqual(c.options[1].status, "disabled")
  }

  func testAccountInfoAndReferralCode() throws {
    let a = try JSONDecoder().decode(AccountInfo.self, from: Data(
      #"{"id": 1, "status": "active", "identities": [{"provider": "email", "email": "a@example.com"}, {"provider": "apple", "email": null}], "apple_link": true, "invite_optional": true}"#.utf8))
    XCTAssertTrue(a.hasApple)
    XCTAssertTrue(a.inviteOptional)
    let r = try JSONDecoder().decode(ReferralCode.self, from: Data(
      #"{"code": "ABCDE12345", "link": "/app/?invite=ABCDE12345", "joined": 2, "remaining": 3, "daily_limit": 5}"#.utf8))
    XCTAssertEqual(r.shareLink(server: "https://body.example.com/"), "https://body.example.com/app/?invite=ABCDE12345")
    XCTAssertEqual(r.joined, 2)
    let m = try JSONDecoder().decode(AuthService.Methods.self, from: Data(#"{"email": true, "referrals": true}"#.utf8))
    XCTAssertTrue(m.referrals)
  }

  func testMemoryEntriesGroupedWithTheirPaths() throws {
    let j = #"""
    {"status": [], "expired_recent": 0,
     "profile_card": [{"key": "goals", "label": "目标", "value": "Run more", "topic": "goals", "confirmed_days": 3, "stale": false},
                      {"key": "pets", "label": "Pets", "value": "A cat", "topic": null, "confirmed_days": 0, "stale": true}],
     "observations": [{"id": 12, "text": "Sleeps late", "kind": "fact", "topic": "schedule", "confirmed_days": 40, "stale": true},
                      {"id": 13, "text": "Tired this week", "kind": "recent", "topic": "schedule"}],
     "archived": {"profile_card": [], "observations": [{"id": 8, "text": "Old note", "kind": "fact", "archived_reason": "Merged"}]}}
    """#
    let d = try JSONDecoder().decode(MemoryDoc.self, from: Data(j.utf8))
    XCTAssertEqual(d.entries.map(\.id), ["p:goals", "p:pets", "o:12"], "recent notes are not long-term memory")
    XCTAssertEqual(d.groups.map(\.entries.count), [1, 1, 1])
    XCTAssertEqual(d.groups.last?.title, "其他")
    XCTAssertEqual(d.entries[0].path, "profile/goals")
    XCTAssertEqual(d.entries[2].path, "observations/12")
    XCTAssertNil(d.entries[0].title, "a standard item is named by its group")
    XCTAssertEqual(d.entries[1].title, "Pets")
    XCTAssertEqual(d.entries[2].age, "40 天前确认")
    XCTAssertEqual(d.archived.first?.archivedReason, "Merged")
  }

  func testFeedbackReports() throws {
    let j = #"""
    {"mode": "ask", "chosen_mode": "auto", "available": true, "statuses": {},
     "reports": [{"id": "FB-3", "kind": "bug", "title": "A bug", "status": "fixed", "status_label": "Fixed", "votes": 2, "created_at": "2026-10-20T10:00:00+00:00",
                  "public_url": "https://example.com/issues/1", "withdrawable": false},
                 {"id": "FB-4", "kind": "feature", "title": "An idea", "status": "new", "voted": true, "public_url": "http://example.com/plain", "withdrawable": true}]}
    """#
    let f = try JSONDecoder().decode(FeedbackList.self, from: Data(j.utf8))
    XCTAssertEqual(f.chosenMode, "auto")
    XCTAssertTrue(f.reports[0].done)
    XCTAssertEqual(f.reports[0].day, "10-20")
    XCTAssertNotNil(f.reports[0].link)
    XCTAssertNil(f.reports[1].link, "https only")
    XCTAssertTrue(f.reports[1].withdrawable)
  }

  func testGzipIsWhatTheServerReads() throws {
    XCTAssertEqual(Gzip.crc32(Data("123456789".utf8)), 0xCBF4_3926)   // the standard check value of CRC-32
    let csv = Data(String(repeating: "date,exercise,reps\n", count: 500).utf8)
    let gz = try Gzip.compress(csv)
    XCTAssertEqual(Array(gz.prefix(3)), [0x1F, 0x8B, 0x08])
    XCTAssertLessThan(gz.count, csv.count / 4)
    let deflated = gz.dropFirst(10).dropLast(8)
    XCTAssertEqual(try (Data(deflated) as NSData).decompressed(using: .zlib) as Data, csv)
    let size = gz.suffix(4).reversed().reduce(UInt32(0)) { $0 << 8 | UInt32($1) }
    XCTAssertEqual(Int(size), csv.count)
  }

  func testHevyStatus() throws {
    let j = #"""
    {"last": {"workouts": 12, "new_workouts": 12, "sets": 80, "exercises": 9, "unmatched": [{"name": "Odd Lift", "sets": 3}], "skipped_rows": 2,
              "start": "2026-01-02", "end": "2026-02-03", "muscle_load": {}, "imported_at": "2026-02-04T00:00:00+00:00"},
     "unmatched": [{"name": "Odd Lift", "sets": 3}], "limits": {"upload_bytes": 2000000, "csv_bytes": 30000000}}
    """#
    let s = try JSONDecoder().decode(HevyStatus.self, from: Data(j.utf8))
    XCTAssertEqual(s.last?.days, "2026-01-02 – 2026-02-03")
    XCTAssertEqual(s.unmatched.first?.sets, 3)
    XCTAssertEqual(s.uploadBytes, 2_000_000)
    XCTAssertNil(try JSONDecoder().decode(HevyStatus.self, from: Data(#"{"last": null, "unmatched": []}"#.utf8)).csvBytes)
  }

  func testTrainingGoals() throws {
    let g = try JSONDecoder().decode(TrainingGoals.self, from: Data(
      #"{"focus_muscles": ["chest"], "physique_goal": null, "updated_at": null, "muscles": [{"key": "chest", "name": "胸"}], "max_focus": 3}"#.utf8))
    XCTAssertEqual(g.maxFocus, 3)
    XCTAssertEqual(g.muscles.first?.key, "chest")
  }

  func testDataStatusChannelsAndStrength() throws {
    let j = #"""
    {"today": "2026-11-03", "window_days": 30, "summary": {"ok": 1, "stale": 0, "never": 0}, "extended": true, "mode": "app",
     "types": [{"key": "steps", "label": "Steps", "status": "ok", "last_date": "2026-11-03", "days_since": 0}],
     "channels": {"ow_last_sample_at": null, "app_upload_at": "2026-11-03T01:30:00+00:00", "shortcut_upload_at": null},
     "strength": {"weeks": 8, "days": 11, "sessions": {"watch": 9, "sparky": null, "hevy": 3}, "sparky": "not_linked"}}
    """#
    let s = try JSONDecoder().decode(DataStatus.self, from: Data(j.utf8))
    XCTAssertEqual(s.channels?.appUploadAt, "2026-11-03T01:30:00+00:00")
    XCTAssertNil(s.channels?.shortcutUploadAt)
    XCTAssertEqual(s.strength?.days, 11)
    XCTAssertEqual(s.strength?.sourcesLine(hideBackEnd: false), "手表 9 · SparkyFitness 未连接 · Hevy 3")
    XCTAssertEqual(s.strength?.sourcesLine(hideBackEnd: true), "手表 9 · 底层数据库 未连接 · Hevy 3")
    XCTAssertEqual(DataStatus.Strength(sessions: .init(watch: 1, sparky: 2, hevy: 0), sparky: "connected").sourcesLine(hideBackEnd: false),
                   "手表 1 · SparkyFitness 2 · Hevy 0")
    XCTAssertEqual(DataStatus.Strength(sessions: .init(), sparky: "unreadable").sourcesLine(hideBackEnd: false),
                   "手表 0 · SparkyFitness 读不到 · Hevy 0")
    XCTAssertEqual(DataStatus.when(nil), "—")
    XCTAssertEqual(DataStatus.when("not a date"), "—")
    let utc = TimeZone(identifier: "UTC")!
    XCTAssertEqual(DataStatus.when("2026-11-03T01:30:00+00:00", zone: utc), "11月3日 01:30")
    XCTAssertEqual(DataStatus.when("2026-11-03T01:30:00.123456+00:00", zone: utc), "11月3日 01:30")
    XCTAssertEqual(DataStatus.when("2026-11-03T12:30:00+11:00", zone: utc), "11月3日 01:30")
    // an older server's answer still decodes
    XCTAssertNil(try JSONDecoder().decode(DataStatus.self, from: Data(#"{"types": [], "summary": {}}"#.utf8)).strength)
  }
}
