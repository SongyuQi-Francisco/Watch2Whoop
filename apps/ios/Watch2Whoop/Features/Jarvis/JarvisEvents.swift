import Foundation

/// One server-sent event of the Jarvis chat stream (body_engine ai/runs.py `_frame`): `id: <seq>`, `event: <name>`,
/// `data: <json>`, then a blank line. Comment lines (`: keep-alive`) are skipped.
struct SSEFrame: Equatable {
  var id: Int?
  var event: String
  var data: String
}

/// Byte-level SSE parser. `URLSession.AsyncBytes.lines` drops blank lines, and blank lines are what end an event, so the
/// stream is split here instead. Handles `\n`, `\r\n` and multi-line `data:` fields.
struct SSEParser {
  private var line: [UInt8] = []
  private var id: Int?
  private var event: String?
  private var data: [String] = []

  /// Feeds one byte; returns a frame when this byte ended one.
  mutating func feed(_ byte: UInt8) -> SSEFrame? {
    if byte != 0x0A {
      line.append(byte)
      return nil
    }
    if line.last == 0x0D { line.removeLast() }
    defer { line.removeAll(keepingCapacity: true) }
    if line.isEmpty { return flush() }
    let text = String(decoding: line, as: UTF8.self)
    if text.hasPrefix(":") { return nil }
    let (field, value) = Self.split(text)
    switch field {
    case "id": id = Int(value)
    case "event": event = value
    case "data": data.append(value)
    default: break
    }
    return nil
  }

  /// Feeds a whole chunk (tests, and the end of a stream without a final blank line).
  mutating func feed(_ bytes: Data) -> [SSEFrame] {
    var out: [SSEFrame] = []
    for b in bytes { if let f = feed(b) { out.append(f) } }
    return out
  }

  private mutating func flush() -> SSEFrame? {
    defer { event = nil; data = []; id = nil }
    guard !data.isEmpty || event != nil else { return nil }
    return SSEFrame(id: id, event: event ?? "message", data: data.joined(separator: "\n"))
  }

  private static func split(_ s: String) -> (String, String) {
    guard let i = s.firstIndex(of: ":") else { return (s, "") }
    var v = s[s.index(after: i)...]
    if v.first == " " { v = v.dropFirst() }
    return (String(s[..<i]), String(v))
  }
}

/// A write the AI wants to do that needs the user's yes first (ai/chat.py `_pending_payload`).
struct PendingCall: Equatable, Identifiable {
  let callId: Int
  let tool: String
  let summary: String
  var id: Int { callId }

  init?(_ v: JSONValue) {
    guard let id = v["call_id"]?.int else { return nil }
    callId = id
    tool = v["tool"]?.string ?? ""
    summary = v["summary"]?.string ?? tool
  }
}

/// The chat stream's events (ai/chat.py docstring): conversation, text, tool, card, confirm, usage, notice, error, done,
/// plus resync from the resume endpoint.
enum ChatEvent: Equatable {
  case conversation(id: Int, name: String?, quotaPct: Int?)
  case text(String)
  case tool(name: String, status: String, message: String?)
  case card(JSONValue)
  case confirm([PendingCall])
  case usage(remainingPct: Int?)
  case notice(String)
  case error(code: String?, message: String)
  case done
  case resync(conversationId: Int)
  case unknown(String)

  init(_ f: SSEFrame) {
    let d = JSONValue.parse(Data(f.data.utf8)) ?? .null
    switch f.event {
    case "conversation":
      self = .conversation(id: d["id"]?.int ?? 0, name: d["display_name"]?.string, quotaPct: Self.pct(d["quota"]))
    case "text": self = .text(d["delta"]?.string ?? "")
    case "tool": self = .tool(name: d["name"]?.string ?? "", status: d["status"]?.string ?? "", message: d["message"]?.string)
    case "card": self = .card(d["card"] ?? .null)
    case "confirm": self = .confirm((d["calls"]?.array ?? []).compactMap(PendingCall.init))
    case "usage": self = .usage(remainingPct: Self.pct(d))
    case "notice": self = .notice(d["message"]?.string ?? "")
    case "error": self = .error(code: d["code"]?.string, message: d["message"]?.string ?? tr("出错了", "Something went wrong"))
    case "done": self = .done
    case "resync": self = .resync(conversationId: d["conversation_id"]?.int ?? 0)
    default: self = .unknown(f.event)
    }
  }

  /// Ends the turn: nothing more comes on this stream.
  var isTerminal: Bool {
    switch self {
    case .done, .confirm, .error, .resync: return true
    default: return false
    }
  }

  /// The fallback AI's allowance as users see it: a remaining percentage only (owner, 2026-10-22).
  static func pct(_ v: JSONValue?) -> Int? {
    guard let v, !v.isNull else { return nil }
    if let p = v["remaining_pct"]?.double { return Int(p.rounded()) }
    guard let q = v["quota_cny"]?.double, q > 0 else { return nil }
    let left = v["remaining_cny"]?.double ?? (q - (v["spent_cny"]?.double ?? v["month_cost_cny"]?.double ?? 0))
    return max(0, min(100, Int((100 * left / q).rounded())))
  }
}
