import XCTest
@testable import Watch2Whoop

/// N8: setup info parsing (QR code / link / paste), the server answers of the Me tab and the consent.
final class OnboardingAndMeTests: XCTestCase {
  func testSetupLinkFromQRCode() {
    let s = SetupInfo.parse("watch2whoop://setup?today=https%3A%2F%2Fbody.example.com%2Fapp%2F&login=abcd-efgh-jkmn")
    XCTAssertEqual(s.server, "https://body.example.com")
    XCTAssertEqual(s.loginCode, "ABCDEFGHJKMN")
    XCTAssertNil(s.activationCode)
  }

  func testSetupLinkWithOpenWearablesPartsStillSignsIn() {
    // links of 1.0.8 carry an OW host and invitation code: 1.0.9 ignores them (contract 7.1)
    let s = SetupInfo.parse("watch2whoop://setup?today=https://body.example.com&ow=https://sync.example.com&code=ABCD2345&login=ABCDEFGHJKMN")
    XCTAssertEqual(s.server, "https://body.example.com")
    XCTAssertEqual(s.loginCode, "ABCDEFGHJKMN")
  }

  func testPastedJSONAndLines() {
    XCTAssertEqual(SetupInfo.parse(#"{"today": "https://body.example.com/app/", "login": "ABCDEFGHJKMN"}"#).loginCode, "ABCDEFGHJKMN")
    let lines = SetupInfo.parse("today: https://body.example.com/app/\nlogin = ABCDEFGHJKMN")
    XCTAssertEqual(lines.server, "https://body.example.com")
    XCTAssertEqual(lines.loginCode, "ABCDEFGHJKMN")
  }

  func testBareCodes() {
    XCTAssertEqual(SetupInfo.parse("abcd efgh jkmn").loginCode, "ABCDEFGHJKMN")
    XCTAssertEqual(SetupInfo.parse("ABCD-EFGH-JKMN-PQRS").activationCode, "ABCDEFGHJKMNPQRS")
    // 0 / O / 1 / I are not in the alphabet
    XCTAssertTrue(SetupInfo.parse("ABCDEFGH0OIN").isEmpty)
    let act = SetupInfo.parse("https://body.example.com/app/activate.html#ABCDEFGHJKMNPQRS")
    XCTAssertEqual(act.activationCode, "ABCDEFGHJKMNPQRS")
    XCTAssertEqual(act.server, "https://body.example.com")
    XCTAssertTrue(SetupInfo.parse("hello").isEmpty)
  }

  func testProfileAndConsentStatus() throws {
    let j = #"""
    {"display_name": "Test", "birth_date": null, "sex": "female", "height_cm": 170.5, "timezone": "Europe/Berlin",
     "goal_weight_kg": null, "language": null, "missing": ["birth_date"],
     "consent": {"required": false, "version": "2026-10-22.1", "reconfirm": true, "one_tap": false}, "circle_pending": []}
    """#
    let p = try JSONDecoder().decode(Profile.self, from: Data(j.utf8))
    XCTAssertEqual(p.displayName, "Test")
    XCTAssertEqual(p.heightCm, 170.5)
    XCTAssertEqual(p.missing, ["birth_date"])
    XCTAssertTrue(p.consent.needed)
    XCTAssertTrue(p.consent.reconfirm)
  }

  func testConsentText() throws {
    let j = ##"{"version": "2026-10-22.1", "summary": ["a", "b"], "text": "# Title\n\n- **one**\n- two", "language": "en", "ai": null}"##
    let c = try JSONDecoder().decode(ConsentText.self, from: Data(j.utf8))
    XCTAssertEqual(c.version, "2026-10-22.1")
    XCTAssertEqual(c.summary.count, 2)
    XCTAssertEqual(ConsentView.paragraphs(c.text).map { String($0.characters) }, ["Title", "• one", "• two"])
  }

  func testAIChoiceAndTokens() throws {
    let choice = #"""
    {"options": [{"key": "fallback", "label": "Jarvis", "kind": "fallback", "available": true, "note": "80% left", "current": true},
                 {"key": "p:3", "label": "Claude", "kind": "api", "available": false, "status": "error", "current": false}],
     "ai": {"source": "fallback"}}
    """#
    let c = try JSONDecoder().decode(AIChoice.self, from: Data(choice.utf8))
    XCTAssertEqual(c.options.map(\.key), ["fallback", "p:3"])
    XCTAssertTrue(c.options[0].current)
    XCTAssertFalse(c.options[1].available)
    let tokens = #"""
    {"tokens": [{"id": 7, "name": "mcp:claude-code", "scopes": ["mcp", "read", "write"], "created_at": "2026-10-01T00:00:00+00:00",
                 "last_used_at": null, "revoked_at": null},
                {"id": 5, "name": "mcp:codex", "scopes": ["mcp", "read"], "revoked_at": "2026-10-02T00:00:00+00:00"}],
     "connections": [], "mcp": {"url": "https://body.example.com/mcp"}, "web": null}
    """#
    let t = try JSONDecoder().decode(AITokens.self, from: Data(tokens.utf8))
    XCTAssertEqual(t.tokens.filter(\.active).map(\.client), ["claude-code"])
    XCTAssertTrue(t.tokens[1].readOnly)
    XCTAssertEqual(t.mcpURL, "https://body.example.com/mcp")
  }

  func testNotificationSettingsAndWhatsNew() throws {
    let n = #"""
    {"categories": {"friends": true, "reminders": false}, "quiet": {"enabled": true, "start": "22:00", "end": "07:30"},
     "labels": {"friends": "Friends", "reminders": "Reminders"}, "push_configured": true, "devices": 1}
    """#
    let s = try JSONDecoder().decode(NotificationSettings.self, from: Data(n.utf8))
    XCTAssertEqual(s.ordered.map(\.key), ["friends", "reminders"])
    XCTAssertEqual(s.quiet.end, "07:30")
    let w = #"{"weeks": [{"week_start": "2026-10-26", "items": [{"date": "2026-10-28", "text": "Something new", "url": null}]}]}"#
    XCTAssertEqual(try JSONDecoder().decode(WhatsNew.self, from: Data(w.utf8)).weeks.first?.items.first?.text, "Something new")
  }

  func testProfilePatchSendsOnlyChanges() throws {
    let v = ProfileSetupView(first: false) {}
    // nothing loaded and nothing typed: only the time zone differs from the empty profile
    XCTAssertEqual(Set(v.patch().keys), ["timezone"])
  }
}
