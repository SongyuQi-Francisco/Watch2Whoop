import Foundation

/// "Setup info" from a QR code, a `watch2whoop://setup?today=…&login=…` link, pasted JSON or `key: value` lines
/// (deploy/open-wearables/invite.py, web/activation.py; the Flutter app's config.dart parseSetupText). 1.0.9 has no Open
/// Wearables: `ow` and `code` (its invitation code) are read but not used (contract section 7.1).
struct SetupInfo: Equatable {
  var server: String?
  var loginCode: String?
  /// A reusable activation code (16 characters) typed or pasted instead of a link.
  var activationCode: String?

  var isEmpty: Bool { server == nil && loginCode == nil && activationCode == nil }

  static let scheme = "watch2whoop"
  /// The login code's alphabet (web/login_codes.py): no 0 O 1 I.
  static let alphabet = Set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
  static let loginLength = 12
  static let activationLength = 16

  /// Upper case, without spaces and dashes; nil when it is not `length` characters of the alphabet.
  static func normalizeCode(_ s: String, length: Int) -> String? {
    let c = s.uppercased().filter { !" -\t\r\n".contains($0) }
    return c.count == length && c.allSatisfy { alphabet.contains($0) } ? c : nil
  }

  static func parse(_ text: String) -> SetupInfo {
    let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
    var values: [String: String] = [:]
    if let u = URL(string: t), ["http", "https"].contains(u.scheme?.lowercased() ?? "") {
      // the activation page's link: https://host/app/activate.html#CODE
      var info = SetupInfo()
      info.activationCode = u.fragment.flatMap { normalizeCode($0, length: activationLength) }
      if info.activationCode != nil { info.server = FlutterMigration.serverOrigin(t) }
      return info
    }
    if t.lowercased().hasPrefix("\(scheme)://"), let c = URLComponents(string: t) {
      for q in c.queryItems ?? [] { if let v = q.value { values[q.name.lowercased()] = v.trimmingCharacters(in: .whitespaces) } }
    } else if t.hasPrefix("{"), let d = t.data(using: .utf8), let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
      for (k, v) in o { if let s = v as? String { values[k.lowercased()] = s.trimmingCharacters(in: .whitespaces) } }
    }
    if values.isEmpty {
      for line in t.components(separatedBy: .newlines) {
        guard let r = line.range(of: #"^\s*([A-Za-z_]+)\s*[=:]\s*(.+?)\s*$"#, options: .regularExpression) else { continue }
        let l = String(line[r])
        guard let sep = l.firstIndex(where: { $0 == "=" || $0 == ":" }) else { continue }
        // "today: https://…" — split at the first separator that follows the key, not inside the URL
        let key = l[..<sep].trimmingCharacters(in: .whitespaces).lowercased()
        let value = l[l.index(after: sep)...].trimmingCharacters(in: .whitespaces)
        if !key.isEmpty, !value.isEmpty { values[key] = value }
      }
    }
    func pick(_ keys: [String]) -> String? { keys.lazy.compactMap { values[$0] }.first { !$0.isEmpty } }

    var info = SetupInfo()
    info.server = pick(["today", "today_url", "body", "body_engine", "server"]).flatMap(FlutterMigration.serverOrigin)
    info.loginCode = pick(["login", "login_code"]).flatMap { normalizeCode($0, length: loginLength) }
    info.activationCode = pick(["activation", "activate"]).flatMap { normalizeCode($0, length: activationLength) }
    if values.isEmpty {
      // a bare code was pasted
      info.loginCode = normalizeCode(t, length: loginLength)
      info.activationCode = normalizeCode(t, length: activationLength)
    }
    return info
  }
}
