import Foundation

/// Any JSON value, for payloads whose shape the server owns and grows (Jarvis cards, chat events): read with optional
/// accessors instead of one Decodable per card type, so a field the app does not know yet never breaks decoding.
enum JSONValue: Equatable, Codable {
  case null
  case bool(Bool)
  case number(Double)
  case string(String)
  case array([JSONValue])
  case object([String: JSONValue])

  init(from decoder: Decoder) throws {
    let c = try decoder.singleValueContainer()
    if c.decodeNil() { self = .null }
    else if let b = try? c.decode(Bool.self) { self = .bool(b) }
    else if let n = try? c.decode(Double.self) { self = .number(n) }
    else if let s = try? c.decode(String.self) { self = .string(s) }
    else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
    else { self = .object(try c.decode([String: JSONValue].self)) }
  }

  func encode(to encoder: Encoder) throws {
    var c = encoder.singleValueContainer()
    switch self {
    case .null: try c.encodeNil()
    case .bool(let b): try c.encode(b)
    case .number(let n): try c.encode(n)
    case .string(let s): try c.encode(s)
    case .array(let a): try c.encode(a)
    case .object(let o): try c.encode(o)
    }
  }

  /// From JSONSerialization output (NSNumber booleans stay booleans).
  init(any: Any?) {
    switch any {
    case nil, is NSNull: self = .null
    case let n as NSNumber:
      self = CFGetTypeID(n) == CFBooleanGetTypeID() ? .bool(n.boolValue) : .number(n.doubleValue)
    case let s as String: self = .string(s)
    case let a as [Any]: self = .array(a.map { JSONValue(any: $0) })
    case let o as [String: Any]: self = .object(o.mapValues { JSONValue(any: $0) })
    default: self = .null
    }
  }

  static func parse(_ data: Data) -> JSONValue? {
    guard let o = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) else { return nil }
    return JSONValue(any: o)
  }

  subscript(key: String) -> JSONValue? {
    if case .object(let o) = self { return o[key] }
    return nil
  }

  subscript(index: Int) -> JSONValue? {
    if case .array(let a) = self, a.indices.contains(index) { return a[index] }
    return nil
  }

  var string: String? {
    switch self {
    case .string(let s): return s
    case .number(let n): return n.rounded() == n && abs(n) < 1e15 ? String(Int(n)) : String(n)
    default: return nil
    }
  }

  var double: Double? {
    switch self {
    case .number(let n): return n
    case .string(let s): return Double(s)
    default: return nil
    }
  }

  var int: Int? { double.map { Int($0) } }

  var bool: Bool? {
    if case .bool(let b) = self { return b }
    return nil
  }

  var array: [JSONValue]? {
    if case .array(let a) = self { return a }
    return nil
  }

  var object: [String: JSONValue]? {
    if case .object(let o) = self { return o }
    return nil
  }

  var isNull: Bool { self == .null }
}
