import Foundation

/// Errors every caller sees. Pages show them with `APIError.userMessage`; the sync layer decides retry / keep / drop by
/// `isRetryable` (docs/ios-native-app.md section 5.4).
enum APIError: Error, Equatable {
  /// 401: token unknown or revoked. The client has already told `onUnauthorized` (sign-in needed); never retried.
  case unauthorized
  /// 403 with the server's `detail` (no share, `account_disabled`, a flag that is off …).
  case forbidden(String?)
  /// 404 (also: an endpoint behind a feature flag that is off for this user).
  case notFound
  /// 409 with `detail` (`consent_required`, a duplicate meal, a stale version …): the caller decides.
  case conflict(String?)
  /// 429: `Retry-After` seconds when the server sent it.
  case rateLimited(TimeInterval?)
  /// Any other status, with `detail`.
  case http(Int, String?)
  /// No connection, timeout, TLS … (URLError code).
  case network(Int)
  /// The answer was not the JSON we expected.
  case decoding(String)
  /// No server configured / not signed in.
  case notConfigured

  var isRetryable: Bool {
    switch self {
    case .network, .rateLimited: return true
    case .http(let s, _): return s >= 500 || s == 408
    default: return false
    }
  }

  var userMessage: String {
    switch self {
    case .unauthorized: return tr("登录已失效，请重新登录", "Signed out, please sign in again")
    case .network: return tr("连不上服务器，稍后再试", "Can't reach the server, try again later")
    case .rateLimited: return tr("请求太频繁，稍后再试", "Too many requests, try again shortly")
    case .forbidden(let d), .conflict(let d), .http(_, let d): return d ?? tr("出错了", "Something went wrong")
    case .notFound: return tr("没有找到", "Not found")
    case .decoding: return tr("服务器的回答看不懂", "Unexpected answer from the server")
    case .notConfigured: return tr("还没有登录", "Not signed in")
    }
  }
}

/// One request. `idempotent` requests are retried on network errors / 5xx / 429; others are sent once (the offline
/// queue retries writes itself, with their `client_id`).
struct APIRequest {
  enum Method: String { case get = "GET", post = "POST", put = "PUT", patch = "PATCH", delete = "DELETE" }

  var method: Method = .get
  /// Path from the server root, e.g. `/v1/users/me/today`.
  var path: String
  var query: [URLQueryItem] = []
  var body: Data?
  var idempotent: Bool? = nil
  var timeout: TimeInterval = 30
  /// Body type when it is not JSON (an avatar upload sends the picture itself, `image/jpeg`; Jarvis voice `audio/wav`).
  var contentType: String? = nil

  var isIdempotent: Bool { idempotent ?? (method == .get || method == .put || method == .delete) }

  static func get(_ path: String, _ query: [String: String] = [:]) -> APIRequest {
    APIRequest(method: .get, path: path, query: query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) })
  }

  static func json<T: Encodable>(_ method: Method, _ path: String, _ body: T, idempotent: Bool? = nil) throws -> APIRequest {
    APIRequest(method: method, path: path, body: try JSONEncoder.api.encode(body), idempotent: idempotent)
  }
}

/// The app's only HTTP client: Bearer token from the Keychain, language and app version headers, retry with backoff
/// for idempotent requests, and one place that turns 401 into "sign in again".
final class APIClient {
  let baseURL: URL
  private let token: () -> String?
  private let session: URLSession
  private let onUnauthorized: () -> Void
  private let sleep: (TimeInterval) async -> Void
  /// Attempts for idempotent requests (first try included).
  var maxAttempts = 3

  init(baseURL: URL, token: @escaping () -> String?, session: URLSession = APIClient.defaultSession,
       onUnauthorized: @escaping () -> Void = {},
       sleep: @escaping (TimeInterval) async -> Void = { try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) }) {
    self.baseURL = baseURL
    self.token = token
    self.session = session
    self.onUnauthorized = onUnauthorized
    self.sleep = sleep
  }

  static let defaultSession: URLSession = {
    let c = URLSessionConfiguration.default
    c.timeoutIntervalForRequest = 15        // contract: connect 10 s, request 30 s (per request below)
    c.waitsForConnectivity = false
    c.requestCachePolicy = .reloadIgnoringLocalCacheData
    c.httpCookieStorage = nil               // Bearer only; the web session cookie is not used by the native app
    c.httpShouldSetCookies = false
    return URLSession(configuration: c)
  }()

  static var appVersion: String {
    Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
  }

  func urlRequest(_ r: APIRequest) throws -> URLRequest {
    guard var c = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { throw APIError.notConfigured }
    c.path = (c.path.hasSuffix("/") ? String(c.path.dropLast()) : c.path) + r.path
    if !r.query.isEmpty { c.queryItems = r.query }
    guard let url = c.url else { throw APIError.notConfigured }
    var u = URLRequest(url: url, timeoutInterval: r.timeout)
    u.httpMethod = r.method.rawValue
    u.setValue("application/json", forHTTPHeaderField: "Accept")
    u.setValue(Lang.code, forHTTPHeaderField: "X-Body-Lang")
    u.setValue(Self.appVersion, forHTTPHeaderField: "X-App-Version")
    u.setValue("ios-native", forHTTPHeaderField: "X-App-Client")
    if let t = token(), !t.isEmpty { u.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }
    if let b = r.body {
      u.httpBody = b
      u.setValue(r.contentType ?? "application/json", forHTTPHeaderField: "Content-Type")
    }
    return u
  }

  /// Raw answer body (2xx). Throws `APIError`.
  func data(_ r: APIRequest) async throws -> Data {
    guard token() != nil else { throw APIError.notConfigured }
    let req = try urlRequest(r)
    var attempt = 0
    while true {
      attempt += 1
      do {
        return try await once(req)
      } catch let e as APIError {
        guard r.isIdempotent, e.isRetryable, attempt < maxAttempts else { throw e }
        await sleep(Self.backoff(attempt: attempt, error: e))
      }
    }
  }

  /// Decoded answer.
  func send<T: Decodable>(_ r: APIRequest, as: T.Type = T.self) async throws -> T {
    let d = try await data(r)
    do {
      return try JSONDecoder.api.decode(T.self, from: d)
    } catch {
      throw APIError.decoding(String(describing: error).prefix(300).description)
    }
  }

  /// Answer as a JSON object (for documents stored as they come, e.g. the today document).
  func json(_ r: APIRequest) async throws -> [String: Any] {
    let d = try await data(r)
    guard let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { throw APIError.decoding("not an object") }
    return o
  }

  /// A streamed answer (server-sent events: the Jarvis chat). Sent once, never retried here: a turn that started runs on
  /// on the server and its caller picks it up again from the events endpoint. 401 goes to `onUnauthorized` and throws; any
  /// other status comes back with the stream, so the caller can read the server's `{code, message}` detail.
  func eventStream(_ r: APIRequest) async throws -> (status: Int, bytes: URLSession.AsyncBytes) {
    guard token() != nil else { throw APIError.notConfigured }
    var req = try urlRequest(r)
    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
    let bytes: URLSession.AsyncBytes, resp: URLResponse
    do {
      (bytes, resp) = try await session.bytes(for: req)
    } catch let e as URLError {
      throw APIError.network(e.errorCode)
    } catch {
      throw APIError.network(-1)
    }
    guard let http = resp as? HTTPURLResponse else { throw APIError.network(-1) }
    if http.statusCode == 401 {
      onUnauthorized()
      throw APIError.unauthorized
    }
    return (http.statusCode, bytes)
  }

  /// 0.5 s, 1 s, 2 s … (max 30 s) with ±20 % jitter; 429 waits `Retry-After` when given (max 30 s here; longer waits
  /// are left to the offline queue).
  static func backoff(attempt: Int, error: APIError) -> TimeInterval {
    if case .rateLimited(let after?) = error { return min(after, 30) }
    let base = min(30, 0.5 * pow(2, Double(attempt - 1)))
    return base * Double.random(in: 0.8...1.2)
  }

  private func once(_ req: URLRequest) async throws -> Data {
    let data: Data, resp: URLResponse
    do {
      (data, resp) = try await session.data(for: req)
    } catch let e as URLError {
      throw APIError.network(e.errorCode)
    } catch {
      throw APIError.network(-1)
    }
    guard let http = resp as? HTTPURLResponse else { throw APIError.network(-1) }
    let status = http.statusCode
    if (200..<300).contains(status) { return data }
    let detail = Self.detail(data)
    if status == 426 {   // R2: this app version must be upgraded (UpgradeGate shows the full-screen page)
      Task { @MainActor in UpgradeGate.shared.receivedUpgradeRequired(data) }
    }
    switch status {
    case 401:
      onUnauthorized()
      throw APIError.unauthorized
    case 403: throw APIError.forbidden(detail)
    case 404: throw APIError.notFound
    case 409: throw APIError.conflict(detail)
    case 429: throw APIError.rateLimited(http.value(forHTTPHeaderField: "Retry-After").flatMap(TimeInterval.init))
    default: throw APIError.http(status, detail)
    }
  }

  /// FastAPI's `{"detail": "…"}` (a string, a `{code, message}` object, or a list of validation errors).
  static func detail(_ data: Data) -> String? {
    guard let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
    if let s = o["detail"] as? String { return s }
    if let d = o["detail"] as? [String: Any], let m = d["message"] as? String { return m }   // {code, message, …}
    if let l = o["detail"] as? [[String: Any]], let m = l.first?["msg"] as? String { return m }
    return nil
  }
}

extension JSONDecoder {
  /// Server JSON: snake_case keys are mapped by explicit CodingKeys (no automatic conversion), dates stay strings.
  static let api = JSONDecoder()
}

extension JSONEncoder {
  static let api: JSONEncoder = {
    let e = JSONEncoder()
    e.outputFormatting = [.sortedKeys]
    return e
  }()
}
