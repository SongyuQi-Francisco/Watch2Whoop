import SwiftUI
import XCTest
@testable import Watch2Whoop

/// The two checks of the project rule "no false floor, every view has a visible door" (docs/design/2.0.1/drilldown/
/// README.md section 9, CONTRIBUTING.md). They read the Swift sources (like scripts/check_sources.py) and host the first
/// glances; they would have caught the 150 / 200 / 110 pt blank spacers of 2.1.0 and the views without any door.
///
/// 1. Entry inventory: every `View` under Features/ is built somewhere (outside `#if DEBUG`), every case of the
///    destination enums is constructed somewhere (outside `#if DEBUG` and outside `case` patterns), and every case has an
///    entry in `apps/ios/reachability.json` with a visible (tap) door at most two taps from a first screen.
/// 2. The fold: a tab root's scroll content never holds a blank spacer taller than `LevelParams.maxBlankGap`, and the
///    first glances of 今天 and 身体 have nothing below them and fit above the Jarvis key on this phone.
final class ReachabilityTests: XCTestCase {
  /// apps/ios
  private static let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()

  /// Every Swift file of the app, comments and `#if DEBUG` blocks taken out (path relative to apps/ios/Watch2Whoop).
  private static func sources() throws -> [(path: String, code: String)] {
    let app = root.appendingPathComponent("Watch2Whoop")
    guard let it = FileManager.default.enumerator(at: app, includingPropertiesForKeys: nil) else {
      XCTFail("cannot read \(app.path)")
      return []
    }
    var out: [(String, String)] = []
    for case let url as URL in it where url.pathExtension == "swift" {
      let text = try String(contentsOf: url, encoding: .utf8)
      out.append((String(url.path.dropFirst(app.path.count + 1)), Self.release(text)))
    }
    XCTAssertGreaterThan(out.count, 100, "the sources were not found next to this test (\(app.path))")
    return out
  }

  /// The code a release build compiles: `#if DEBUG` blocks (and their `#else` when the `#if` is not DEBUG) and `//`
  /// comments removed.
  static func release(_ text: String) -> String {
    var out: [String] = []
    var stack: [Bool] = []   // true = this level is dropped
    for line in text.components(separatedBy: "\n") {
      let t = line.trimmingCharacters(in: .whitespaces)
      if t.hasPrefix("#if") { stack.append(t.contains("DEBUG") && !t.contains("!DEBUG")); continue }
      if t.hasPrefix("#else"), let last = stack.popLast() {
        let parentDropped = stack.contains(true)
        stack.append(parentDropped || !last)
        continue
      }
      if t.hasPrefix("#endif") { _ = stack.popLast(); continue }
      if stack.contains(true) { continue }
      out.append(Self.stripComment(line))
    }
    return out.joined(separator: "\n")
  }

  private static func stripComment(_ line: String) -> String {
    var inString = false, prev: Character = " "
    var i = line.startIndex
    while i < line.endIndex {
      let c = line[i]
      if c == "\"" && prev != "\\" { inString.toggle() }
      if !inString, c == "/", line.index(after: i) < line.endIndex, line[line.index(after: i)] == "/" {
        return String(line[..<i])
      }
      prev = c
      i = line.index(after: i)
    }
    return line
  }

  private static func matches(_ pattern: String, _ text: String) -> [NSTextCheckingResult] {
    let re = try! NSRegularExpression(pattern: pattern, options: [.anchorsMatchLines])
    return re.matches(in: text, range: NSRange(text.startIndex..., in: text))
  }

  private static func group(_ m: NSTextCheckingResult, _ i: Int, _ text: String) -> String {
    guard let r = Range(m.range(at: i), in: text) else { return "" }
    return String(text[r])
  }

  // MARK: - check 1: entry inventory

  /// Views no code builds any more are dead weight that a later change might "restore" without a door.
  func testEveryViewIsBuiltSomewhere() throws {
    let src = try Self.sources()
    var views: [(name: String, path: String)] = []
    for (path, code) in src where path.hasPrefix("Features/") {
      for m in Self.matches(#"^\s*(?:private |fileprivate )?struct (\w+)(?:<[^>]*>)?\s*:\s*([^{]*)\{"#, code)
      where Self.group(m, 2, code).range(of: #"\bView\b"#, options: .regularExpression) != nil {
        views.append((Self.group(m, 1, code), path))
      }
    }
    XCTAssertGreaterThan(views.count, 200)
    var dead: [String] = []
    for v in views {
      let built = src.contains { _, code in
        Self.matches(#"(?<![\w.])"# + v.name + #"\s*(\(|\{|<[^>\n]*>\s*\()"#, code).contains { m in
          let line = Self.line(code, at: m.range.location)
          return line.range(of: #"\b(struct|extension|enum|class)\s+"# + v.name + #"\b"#, options: .regularExpression) == nil
        }
      }
      if !built { dead.append("\(v.name) (\(v.path))") }
    }
    XCTAssertEqual(dead, [], "views no code builds (delete them, or give them a door): \(dead)")
  }

  /// Every destination case is opened by some code a release build has (not only by a debug hook).
  func testEveryDestinationIsOpenedSomewhere() throws {
    let src = try Self.sources()
    var unused: [String] = []
    for e in try Self.inventory().enums {
      let cases = try Self.cases(of: e, in: src)
      XCTAssertFalse(cases.isEmpty, "enum \(e) not found")
      for c in cases {
        // `.case` as a value: after `=`, `(`, `[`, `{`, `,`, `:`, `??`, `return`, or `Enum.case`; never in a `case` pattern
        let pattern = #"(?:\b"# + e + #"|(?<![=!<>])=\s*|\(\s*|\[\s*|\{\s*|,\s*|:\s*|\?\?\s*|\breturn\s+)\."# + c + #"\b"#
        let opened = src.contains { _, code in !Self.matches(pattern, Self.withoutPatterns(code)).isEmpty }
        if !opened { unused.append("\(e).\(c)") }
      }
    }
    XCTAssertEqual(unused, [], "destinations nothing opens outside #if DEBUG: \(unused)")
  }

  /// Every destination has an entry with a visible door within two taps of a first screen; no entry is stale.
  func testEveryDestinationHasAVisibleDoorInTheInventory() throws {
    let src = try Self.sources()
    let inv = try Self.inventory()
    var all: Set<String> = []
    for e in inv.enums { for c in try Self.cases(of: e, in: src) { all.insert("\(e).\(c)") } }
    let missing = all.subtracting(inv.destinations.keys).sorted()
    XCTAssertEqual(missing, [], "add these to apps/ios/reachability.json: \(missing)")
    let stale = Set(inv.destinations.keys).subtracting(all).sorted()
    XCTAssertEqual(stale, [], "reachability.json names cases that no longer exist: \(stale)")
    for (dest, doors) in inv.destinations {
      XCTAssertTrue(doors.contains { $0.kind == "tap" && $0.taps <= 2 && $0.taps >= 1 },
                    "\(dest): needs a visible door (kind tap) at most 2 taps from a first screen")
      for d in doors {
        XCTAssertTrue(["tap", "gesture"].contains(d.kind), "\(dest): unknown door kind \(d.kind)")
        XCTAssertTrue(["today", "body", "record", "circles", "me", "member"].contains(d.root), "\(dest): unknown root \(d.root)")
        XCTAssertFalse(d.door.isEmpty, "\(dest): name the door")
      }
    }
  }

  private struct Door: Decodable { var root: String; var taps: Int; var kind: String; var door: String }
  private struct Inventory: Decodable { var enums: [String]; var destinations: [String: [Door]] }

  private static func inventory() throws -> Inventory {
    let url = root.appendingPathComponent("reachability.json")
    return try JSONDecoder().decode(Inventory.self, from: Data(contentsOf: url))
  }

  /// The case names of `enum name` (its own cases, not those of enums nested in it).
  private static func cases(of name: String, in src: [(path: String, code: String)]) throws -> [String] {
    for (_, code) in src {
      guard let m = matches(#"^\s*(?:indirect )?enum "# + name + #"\b[^{]*\{"#, code).first,
            let start = Range(m.range, in: code)?.upperBound else { continue }
      var depth = 1, i = start, body = ""
      while i < code.endIndex, depth > 0 {
        let c = code[i]
        if c == "{" { depth += 1 } else if c == "}" { depth -= 1 }
        body.append(depth == 1 || (depth == 2 && c == "{") ? c : (c == "\n" ? "\n" : " "))
        i = code.index(after: i)
      }
      var out: [String] = []
      for line in body.components(separatedBy: "\n") {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard t.hasPrefix("case ") else { continue }
        // `case a, b(X), c(d: Y)`: the names before each top-level comma
        var level = 0, part = ""
        for ch in t.dropFirst(5) {
          if ch == "(" { level += 1 } else if ch == ")" { level -= 1 }
          if ch == "," && level == 0 { out.append(part); part = "" } else { part.append(ch) }
        }
        out.append(part)
      }
      return out.compactMap { p in
        let n = p.trimmingCharacters(in: .whitespaces).prefix { $0.isLetter || $0.isNumber || $0 == "_" }
        return n.isEmpty ? nil : String(n)
      }
    }
    return []
  }

  /// The code with every `case` pattern blanked out: in a `switch` the part before its `:` (the body after it stays: a
  /// `case .sleepHelp: sheet = .detail(.sleepHelp)` opens the sheet), in `if / guard / for case` the part before its `=`.
  static func withoutPatterns(_ code: String) -> String {
    code.components(separatedBy: "\n").map { line -> String in
      let t = line.trimmingCharacters(in: .whitespaces)
      if t.hasPrefix("case ") || t.hasPrefix("default") {
        var level = 0
        for (i, ch) in line.enumerated() {
          if ch == "(" || ch == "[" { level += 1 } else if ch == ")" || ch == "]" { level -= 1 }
          if ch == ":" && level == 0 { return String(repeating: " ", count: i + 1) + String(line.dropFirst(i + 1)) }
        }
        return ""
      }
      if let r = line.range(of: #"\b(if|guard|for|while)\s+case\b[^=]*="#, options: .regularExpression) {
        return line.replacingCharacters(in: r, with: String(repeating: " ", count: line.distance(from: r.lowerBound, to: r.upperBound)))
      }
      return line
    }.joined(separator: "\n")
  }

  private static func line(_ text: String, at location: Int) -> String {
    let ns = text as NSString
    return ns.substring(with: ns.lineRange(for: NSRange(location: location, length: 0)))
  }

  // MARK: - check 2: the fold

  /// The files that draw a tab root's scroll content: 今天 and 身体 (levels, nothing below the glance), 记录 and 圈子 (the
  /// joint and the next block cut by the fold, `FoldFit`).
  static let tabRoots = [
    "Features/Today/TodayView.swift", "Features/Today/TodayGlanceView.swift",
    "Features/Trends/TrendsView.swift", "Features/Body/BodyGlanceView.swift",
    "Features/Record/RecordView.swift", "Features/Record/TrainingRoot.swift",
    "Features/Food/FoodView.swift", "Features/Food/FoodGlance.swift",
    "Features/Circles/CirclesTab.swift",
  ]

  func testNoTabRootHasABlankSpacerTallerThanTheRule() throws {
    let src = try Self.sources()
    var found: [String] = []
    for path in Self.tabRoots {
      guard let code = src.first(where: { $0.path == path })?.code else { XCTFail("\(path) not found"); continue }
      let spacers = [#"\.padding\(\.(?:top|bottom),\s*(\d+(?:\.\d+)?)\)"#,
                     #"(?:Color\.clear|Spacer\(\))\.frame\(height:\s*(\d+(?:\.\d+)?)\)"#,
                     #"Spacer\(minLength:\s*(\d+(?:\.\d+)?)\)"#]
      for p in spacers {
        for m in Self.matches(p, code) where (Double(Self.group(m, 1, code)) ?? 0) > Double(LevelParams.maxBlankGap) {
          found.append("\(path): \(Self.line(code, at: m.range.location).trimmingCharacters(in: .whitespacesAndNewlines))")
        }
      }
    }
    XCTAssertEqual(found, [], "a blank gap taller than \(LevelParams.maxBlankGap) pt reads as the end of the page")
  }

  func testCasePatternsAreNotOpenings() {
    let code = """
    switch a {
    case .recovery: sheet = .detail(.sleepHelp)
    case .day(let x) where x == nil: break
    }
    if case .metric(let id) = r { open(.night(nil)) }
    """
    let out = Self.withoutPatterns(code)
    XCTAssertFalse(out.contains(".recovery"))
    XCTAssertTrue(out.contains(".detail(.sleepHelp)"))
    XCTAssertFalse(out.contains(".day("))
    XCTAssertFalse(out.contains(".metric("))
    XCTAssertTrue(out.contains(".night(nil)"))
  }

  func testFoldRule() {
    let visible: CGFloat = 600
    XCTAssertEqual(FoldRule.verdict(blocks: [0...560], visible: visible), .nothingBelow)
    XCTAssertEqual(FoldRule.verdict(blocks: [0...500, 520...700], visible: visible), .cutByFold)   // 80 pt of it shows
    XCTAssertEqual(FoldRule.verdict(blocks: [0...500, 560...700], visible: visible), .falseFloor)  // 40 pt: too little
    XCTAssertEqual(FoldRule.verdict(blocks: [0...500, 650...900], visible: visible), .falseFloor)  // a 150 pt gap
    XCTAssertEqual(FoldRule.verdict(blocks: [0...500, 510...530, 540...640], visible: visible), .cutByFold)
    XCTAssertEqual(FoldRule.verdict(blocks: [0...500, 520...560, 570...640], visible: visible), .falseFloor)   // 30 pt
    XCTAssertEqual(FoldRule.verdict(blocks: [0...640], visible: visible), .cutByFold)   // the glance itself is cut
  }

  /// 记录 and 圈子 (form 2): the first glance gives up just enough height for the next block to peek above the key: its gaps
  /// first, its dial / ring only when the gaps are at their floor.
  func testFoldFitGivesUpJustEnough() {
    typealias Fit = FoldFit.Fit
    let none = Fit()
    // not measured yet: keep what is applied
    XCTAssertEqual(FoldFit.fit(bottom: 0, gaps: 100, applied: none, visible: 760), none)
    XCTAssertEqual(FoldFit.fit(bottom: 700, gaps: 100, applied: none, visible: 0), none)
    // a glance ending at 700 of 760 (fixed part 600): gaps × 0.8 leave the joint and 56 pt of the next block
    let f = FoldFit.fit(bottom: 700, gaps: 100, applied: none, visible: 760)
    XCTAssertEqual(f.scale, 0.8, accuracy: 1e-9)
    XCTAssertEqual(f.shrink, 0)
    // measured again with that fit applied, it stays (the fixed part does not depend on it)
    XCTAssertEqual(FoldFit.fit(bottom: 680, gaps: 100, applied: f, visible: 760), f)
    let next = 600 + 100 * f.scale + LevelParams.joint
    XCTAssertEqual(FoldRule.verdict(blocks: [0...(600 + 100 * f.scale), next...(next + 200)], visible: 760), .cutByFold)
    // room to spare: never above the approved rhythm
    XCTAssertEqual(FoldFit.fit(bottom: 500, gaps: 100, applied: none, visible: 760), none)
    // a short phone (gaps 35 pt in all): the gaps go to their floor, the hero gives up the rest
    let short = FoldFit.fit(bottom: 397, gaps: 35, applied: none, visible: 427)
    XCTAssertEqual(short.scale, LevelParams.minGapScale)
    XCTAssertEqual(short.shrink, (362 + 35 * LevelParams.minGapScale + 80 - 427).rounded(.up))
    XCTAssertEqual(FoldFit.fit(bottom: 397 - 35 * 0.7 - short.shrink, gaps: 35, applied: short, visible: 427), short)
    // and never more than its cap
    XCTAssertEqual(FoldFit.fit(bottom: 600, gaps: 35, applied: none, visible: 427).shrink, LevelParams.maxHeroShrink)
  }

  /// 今天 and 身体 have nothing below their first glance (form 1), and the glance fits above the key on this phone.
  @MainActor
  func testFirstGlancesFitAboveTheKey() throws {
    guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first else {
      throw XCTSkip("no window scene in this test host")
    }
    let screen = scene.screen.bounds
    let session = try Self.session()
    // the height a tab page has above the key band: a probe page in a real tab bar
    var visible: CGFloat = 0
    let probe = TabView {
      GeometryReader { g in Color.clear.onAppear { visible = g.size.height } }
        .statusBarCover().clearOfJarvisKey()
        .tabItem { Text(verbatim: "t") }
    }
    let window = UIWindow(windowScene: scene)
    window.frame = screen
    window.rootViewController = UIHostingController(rootView: probe)
    window.isHidden = false
    for _ in 0..<40 where visible == 0 { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
    window.isHidden = true
    XCTAssertGreaterThan(visible, 300)

    let today = UIHostingController(rootView: TodayGlancePage(model: Self.day(), isPast: false, circlesOn: false, day: nil,
                                                              session: session, run: Self.noop).padding(.horizontal, Metrics.gutter))
    let t = today.sizeThatFits(in: CGSize(width: screen.width, height: .greatestFiniteMagnitude)).height
    XCTAssertEqual(FoldRule.verdict(blocks: [0...t], visible: visible), .nothingBelow,
                   "今天's first glance (\(t) pt) is taller than the room above the key (\(visible) pt) on \(screen.size)")

    let page = TrendsPage(data: try XCTUnwrap(TrendsData(json: Self.body())), local: nil, vo2: nil, sleep: nil)
    let body = UIHostingController(rootView: BodyGlancePage(page: page, range: .month, end: nil, loading: false, session: session,
                                                            run: Self.bodyNoop).padding(.horizontal, Metrics.gutter))
    let b = body.sizeThatFits(in: CGSize(width: screen.width, height: .greatestFiniteMagnitude)).height
    XCTAssertEqual(FoldRule.verdict(blocks: [0...b], visible: visible), .nothingBelow,
                   "身体's first glance (\(b) pt) is taller than the room above the key (\(visible) pt) on \(screen.size)")
  }

  @MainActor private static func session() throws -> AccountSession {
    let cfg = AccountConfig(server: "https://body.example.com", userId: 7, displayName: "小林")
    return AccountSession(config: cfg, api: PathStub.client(), db: try AppDatabase.inMemory(), flags: [])
  }

  private static let noop = TodayGlanceActions(
    level: { _ in }, calendar: {}, backToToday: {}, step: { _ in }, syncTap: {}, syncCenter: {}, recoveryNext: { _ in },
    food: {}, circles: {})

  private static let bodyNoop = BodyGlanceActions(setRange: { _ in }, pickEnd: {}, backToToday: {}, level: { _ in }, all: {},
                                                  syncNow: {}, syncCenter: {})

  /// A synthetic day with a score, both readings and the 体征 face (the tallest first glance).
  private static func day() -> TodayModel {
    var m = TodayModel(TodayInputs(today: ["date": "2026-11-10"], userToday: "2026-11-10"))
    m.glance.recovery = TodayGlance.Dial(value: 82, tone: .good, usual: 58...74, week: [61, 77, 72, 64, 38, 52, 82], verdict: "可上强度")
    m.glance.sleep.value = 88
    m.glance.sleep.text = "88"
    m.glance.strain.value = 2.8
    m.glance.strain.text = "2.8"
    m.vitals = ["rhr", "hrv", "resp", "spo2", "temp"].map { k in
      TodayModel.Vital(id: k, name: k, short: k, value: 50, unit: nil, low: 40, high: 60, off: false, missing: nil, explainKey: nil)
    }
    return m
  }

  /// 30 days of recovery, HRV and training for 身体's hero and its 心肺 / 周报 faces.
  private static func body() -> [String: Any] {
    let end = "2026-11-10"
    let dates = (0..<30).map { LocalDay.add(end, $0 - 29) }
    func series(_ f: (Int) -> Double) -> [String: Any] {
      let v = (0..<30).map { f($0) }
      return ["values": v, "avg7": v, "n": 30, "mean": v.reduce(0, +) / 30, "prev_mean": 60]
    }
    return ["days": 30, "start": dates[0], "end": end, "dates": dates,
            "series": ["recovery": series { 55 + Double($0 % 9) * 3 }, "hrv": series { 55 + Double($0 % 5) },
                       "aerobic_min": series { $0 % 2 == 0 ? 40 : 0 }]]
  }
}
