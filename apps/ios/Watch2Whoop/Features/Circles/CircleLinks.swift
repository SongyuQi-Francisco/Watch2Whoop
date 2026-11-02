import Foundation

/// Where a circle link points: a post of the feed (a like / comment notification) or an invite code to join with.
enum CircleLink: Equatable {
  case post(Int)
  case join(String)

  /// A notification's `url` (notify/core.py; feed.post_url = `/app/?tab=friends&feed=<id>`), the shared invite link
  /// `/app/join.html#<code>`, the older form with `?circle=<code>`, or the app's own `watch2whoop://circle?code=…` /
  /// `watch2whoop://feed?post=…`.
  static func parse(_ raw: String) -> CircleLink? {
    let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let c = URLComponents(string: s.hasPrefix("/") ? "https://x\(s)" : s) else { return nil }
    let q = Dictionary((c.queryItems ?? []).map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { a, _ in a })
    if c.scheme?.lowercased() == "watch2whoop" {
      switch c.host?.lowercased() {
      case "feed": return Int(q["post"] ?? "").map(CircleLink.post)
      case "circle", "join": return (q["code"]).flatMap(InviteCode.clean).map(CircleLink.join)
      default: return nil
      }
    }
    if c.path.hasSuffix("/join.html"), let code = c.fragment.flatMap(InviteCode.clean) { return .join(code) }
    if let id = Int(q["feed"] ?? ""), q["tab"] == nil || q["tab"] == "friends" { return .post(id) }
    if let code = q["circle"].flatMap(InviteCode.clean) { return .join(code) }
    return nil
  }
}

enum InviteCode {
  /// `ABCDE-FGHJK`: the shape of a circle invite (friends/store.py: 10 characters of login_codes.ALPHABET, a dash after 5).
  private static let shape = try! NSRegularExpression(pattern: "(?<![A-Z0-9])[A-HJ-NP-Z2-9]{5}-?[A-HJ-NP-Z2-9]{5}(?![A-Z0-9])", options: [.caseInsensitive])

  /// The code inside whatever was pasted: an invite link, the shared text 「…邀请码 ABCDE-FGHJK」, or the bare code (a
  /// circle activation code may have another shape: then the text itself, trimmed). nil when there is nothing usable.
  static func extract(_ text: String) -> String? {
    let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !t.isEmpty else { return nil }
    for word in t.split(whereSeparator: { $0.isWhitespace }) {
      if word.contains("://") || word.hasPrefix("/"), case .join(let code)? = CircleLink.parse(String(word)) { return code }
    }
    let ns = t as NSString
    if let m = shape.firstMatch(in: t, range: NSRange(location: 0, length: ns.length)) {
      return clean(ns.substring(with: m.range))
    }
    return clean(t)
  }

  /// Upper case, spaces out, at most 60 characters (the server's own limit is 80); nil when too short to be a code.
  static func clean(_ code: String) -> String? {
    let c = code.uppercased().components(separatedBy: .whitespacesAndNewlines).joined()
    guard c.replacingOccurrences(of: "-", with: "").count >= 4, c.count <= 60 else { return nil }
    if c.count == 10, !c.contains("-") { return "\(c.prefix(5))-\(c.suffix(5))" }
    return c
  }
}

/// The link an invite shares: the server's landing page (`web/static/join.js`), static, so the forced upgrade never stands
/// in front of it; it opens `watch2whoop://circle?code=<code>` here, or says how to install the app. The code is in the
/// fragment, which browsers do not send to the server. Universal Links would skip the page (they need the Apple team's setup).
enum InviteShare {
  static func webLink(server: String, code: String) -> URL? {
    var c = URLComponents(string: server)
    c?.path = "/app/join.html"
    c?.query = nil
    c?.fragment = code
    return c?.url
  }

  static func text(circle: String, code: String) -> String {
    tr("加入我在 Watch2Whoop 的圈子「\(circle)」：邀请码 \(code)", "Join my circle \u{201C}\(circle)\u{201D} on Watch2Whoop: invite code \(code)")
  }
}
