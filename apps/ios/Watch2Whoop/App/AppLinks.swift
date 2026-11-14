import Foundation
import SwiftUI

/// Where a tapped notification's `url` (notify/core.py) or a link inside the app points: the same links the Today page
/// understands (notify.js `parseLink` / `go`, whatsnew.js `go`). Pure (unit-tested).
/// - `/app/?tab=friends&feed=12`, `/app/?circle=<code>`, `/app/join.html#<code>`, `watch2whoop://circle?code=…`: circles (CircleLink);
/// - `/app/?whatsnew=1`: the 「本周更新」 sheet (the weekly message, whatsnew/params.py);
/// - `/app/?tab=me`, `/app/#tab=friends`, `/app/?tab=me&ai_cards=1`: that tab. `/app/` and `#notifications`: nil, opening
///   the app is the whole answer.
enum AppLink: Equatable {
  case post(Int)
  case join(String)
  case friends
  case whatsNew
  case tab(AppTab)

  static func parse(_ raw: String) -> AppLink? {
    switch CircleLink.parse(raw) {
    case .post(let id)?: return .post(id)
    case .join(let code)?: return .join(code)
    case nil: break
    }
    let p = params(raw)
    if p["whatsnew"] != nil { return .whatsNew }
    switch p["tab"] {
    case "friends":
      if let id = p["post"].flatMap({ Int($0) }) { return .post(id) }
      return .friends
    case "today": return .tab(.today)
    case "trends": return .tab(.trends)
    case "food": return .tab(.food)
    case "record": return .tab(.record)
    case "me": return .tab(.me)
    default: return nil
    }
  }

  /// Query and fragment parameters together (notify.js parseLink): "/app/?tab=me" and "/app/#tab=me" say the same.
  static func params(_ raw: String) -> [String: String] {
    let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let c = URLComponents(string: s.hasPrefix("/") ? "https://x\(s)" : s) else { return [:] }
    var out = Dictionary((c.queryItems ?? []).map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { a, _ in a })
    for part in (c.percentEncodedFragment ?? "").split(separator: "&") {
      let kv = part.split(separator: "=", maxSplits: 1).map(String.init)
      guard let k = kv.first?.removingPercentEncoding else { continue }
      out[k] = kv.count > 1 ? (kv[1].removingPercentEncoding ?? kv[1]) : ""
    }
    return out
  }
}

extension AppTab {
  /// The tab a link lands on, given what the tab bar shows: 饮食 is inside 记录 (2.0.1), 圈子 only with the flag (`visible`).
  func landing(circles: Bool) -> AppTab {
    switch self {
    case .circles: return circles ? .circles : .today
    case .food: return .record
    default: return self
    }
  }
}

private struct FlowsSettledKey: EnvironmentKey {
  static let defaultValue = true
}

extension EnvironmentValues {
  /// Nothing that follows a sign-in is still to come (consent, profile, Health access; `SignedInFlow.settled`): links
  /// and first-visit questions wait for it, a sheet cannot present over those covers and must not stand in their way.
  var flowsSettled: Bool {
    get { self[FlowsSettledKey.self] }
    set { self[FlowsSettledKey.self] = newValue }
  }
}
