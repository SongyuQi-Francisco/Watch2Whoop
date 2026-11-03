import SwiftUI
import UIKit
import XCTest
@testable import Watch2Whoop

/// Slim mode (flag `slim_mode`, #126): the colours (the hex values against slim.js are compared by
/// scripts/check_sources.py), the local copy for the first frame, and the daily quote's choice of the day.
@MainActor
final class SlimThemeTests: XCTestCase {
  override func tearDown() async throws {
    Theme.apply(nil)
    DailyQuoteStore.forget()
  }

  private func hex(_ c: Color, dark: Bool) -> String {
    let style: UIUserInterfaceStyle = dark ? .dark : .light
    let resolved = UIColor(c).resolvedColor(with: UITraitCollection(userInterfaceStyle: style))
    var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
    resolved.getRed(&r, green: &g, blue: &b, alpha: &a)
    return String(format: "%02X%02X%02X", Int(round(r * 255)), Int(round(g * 255)), Int(round(b * 255)))
  }

  func testDefaultLookIsTheColoursItAlwaysWas() {
    Theme.apply(nil)
    XCTAssertFalse(Theme.isSlim)
    XCTAssertEqual(hex(Palette.bg, dark: false), "FFFFFF")
    XCTAssertEqual(hex(Palette.bg, dark: true), "000000")
    XCTAssertEqual(hex(Palette.text, dark: false), "111113")
    XCTAssertEqual(hex(Palette.good, dark: true), "72BF8E")
    // Buttons and selected chips: the accent is --text on --bg, so nothing moves in the normal look
    XCTAssertEqual(hex(Palette.accent, dark: false), hex(Palette.text, dark: false))
    XCTAssertEqual(hex(Palette.accent, dark: true), hex(Palette.text, dark: true))
    XCTAssertEqual(hex(Palette.onAccent, dark: false), hex(Palette.bg, dark: false))
    XCTAssertEqual(hex(Palette.onAccent, dark: true), hex(Palette.bg, dark: true))
  }

  func testSlimChangesTheSameColoursAtOnce() {
    Theme.apply(AppearanceChoice(theme: "slim", quotes: true))
    XCTAssertTrue(Theme.isSlim)
    XCTAssertEqual(hex(Palette.bg, dark: false), "FBFAF7")
    XCTAssertEqual(hex(Palette.bg, dark: true), "0A0A0B")
    XCTAssertEqual(hex(Palette.good, dark: true), "B8F23D")
    XCTAssertEqual(hex(Palette.accent, dark: false), "D93F0B")
    XCTAssertEqual(hex(Palette.onAccent, dark: true), "0A0A0B")
    XCTAssertEqual(hex(Tone.good.color, dark: false), "2F8A2F")
    Theme.apply(AppearanceChoice(theme: "default", quotes: true))
    XCTAssertEqual(hex(Palette.bg, dark: false), "FFFFFF")
  }

  func testLocalCopyIsKeptForTheNextStartAndClearedWithTheFlag() {
    Theme.apply(AppearanceChoice(theme: "slim", quotes: false))
    XCTAssertEqual(AppearanceChoice.saved, AppearanceChoice(theme: "slim", quotes: false))
    Theme.apply(nil)
    XCTAssertNil(AppearanceChoice.saved)
    XCTAssertFalse(Theme.isSlim)
  }

  func testChoiceFromTheServer() {
    XCTAssertEqual(AppearanceChoice(json: ["theme": "slim", "quotes": false]), AppearanceChoice(theme: "slim", quotes: false))
    XCTAssertNil(AppearanceChoice(json: nil))
    XCTAssertNil(AppearanceChoice(json: ["quotes": true]))
    XCTAssertTrue(AppearanceChoice(theme: "slim", quotes: true).showsQuote)
    XCTAssertFalse(AppearanceChoice(theme: "slim", quotes: false).showsQuote)
    XCTAssertFalse(AppearanceChoice(theme: "default", quotes: true).showsQuote)   // the quote belongs to the slim look
  }

  func testPatchSendsOnlyWhatChanged() throws {
    let d = try JSONEncoder.api.encode(AppearancePatch(quotes: false))
    XCTAssertEqual(try JSONSerialization.jsonObject(with: d) as? [String: Bool], ["quotes": false])
  }

  // MARK: - the quote

  private let list: [DailyQuote] = (0..<4).map {
    DailyQuote(id: "q\($0)", person: "p", text: "t\($0)", lang: "zh", sourceURL: "https://x.com/a/status/\($0)")
  }

  func testDayNumberIsTheSameAsThePage() {
    XCTAssertEqual(DailyQuote.dayNumber("1970-01-01"), 0)
    XCTAssertEqual(DailyQuote.dayNumber("1970-01-02"), 1)
    XCTAssertEqual(DailyQuote.dayNumber("2026-10-27"), 20753)
    XCTAssertEqual(DailyQuote.dayNumber("not a day"), 0)
  }

  func testOneQuotePerDayAndATapIsTheNextOne() {
    let a = DailyQuote.at(list, day: "2026-10-27")
    XCTAssertEqual(a, DailyQuote.at(list, day: "2026-10-27"))
    XCTAssertEqual(DailyQuote.at(list, day: "2026-10-27", taps: 1), DailyQuote.at(list, day: "2026-10-28"))
    XCTAssertNotEqual(a, DailyQuote.at(list, day: "2026-10-28"))
    XCTAssertEqual(DailyQuote.at(list, day: "2026-10-27", taps: 4), a)   // wraps around
    XCTAssertNil(DailyQuote.at([], day: "2026-10-27"))
  }

  func testTapsCountForTheDayAndSurviveARestart() throws {
    let defaults = try XCTUnwrap(UserDefaults(suiteName: "SlimThemeTests"))
    defaults.removePersistentDomain(forName: "SlimThemeTests")
    let s = DailyQuoteStore(defaults: defaults)
    XCTAssertNil(s.quote(on: "2026-10-27"))
    s.set(list)
    let first = s.quote(on: "2026-10-27")
    s.next(on: "2026-10-27")
    XCTAssertNotEqual(s.quote(on: "2026-10-27"), first)
    let again = DailyQuoteStore(defaults: defaults)    // the next start: the list and the taps are still there
    XCTAssertEqual(again.quote(on: "2026-10-27"), s.quote(on: "2026-10-27"))
    XCTAssertEqual(again.quote(on: "2026-10-28"), DailyQuote.at(list, day: "2026-10-28"))   // a new day starts over
    DailyQuoteStore.forget(defaults: defaults)
    XCTAssertTrue(DailyQuoteStore(defaults: defaults).items.isEmpty)
  }

  func testServerAnswerDecodesWordForWord() throws {
    let json = #"{"version":1,"items":[{"id":"a-1","person":"甲","text":"第一行\n\n第二行 😎","lang":"zh","source_url":"https://x.com/a/status/1"}]}"#
    let list = try JSONDecoder.api.decode(DailyQuoteList.self, from: Data(json.utf8))
    XCTAssertEqual(list.items.first?.text, "第一行\n\n第二行 😎")
    XCTAssertEqual(list.items.first?.url?.absoluteString, "https://x.com/a/status/1")
    let notWeb = DailyQuote(id: "b", person: "p", text: "t", lang: "en", sourceURL: "javascript:alert(1)")
    XCTAssertNil(notWeb.url)
  }
}
