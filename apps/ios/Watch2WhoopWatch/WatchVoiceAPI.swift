import Foundation

/// The four requests the watch's token may make (#458 B, C) and the redeeming of a code (#458 A). The watch talks to
/// the server itself (plan 4.4: AI requests go direct; the system routes them through the phone, Wi-Fi or cellular).
protocol WatchVoiceBackend: AnyObject {
  /// `POST /v1/web/login-code {code, client: "watch"}` → the watch's token.
  func redeem(_ code: WatchPairCode) async throws -> String
  /// `POST /v1/assistant/transcribe?fix=1` → the words.
  func transcribe(_ wav: Data) async throws -> String
  /// `POST /v1/assistant/chat` with `page: "watch"`.
  func chat(_ text: String, conversation: Int?) -> AsyncThrowingStream<WatchSSEEvent, Error>
  /// `POST /v1/assistant/chat/confirm`.
  func approve(callId: String, approve: Bool) -> AsyncThrowingStream<WatchSSEEvent, Error>
  /// `GET /v1/assistant/chat/{id}/events?after=`.
  func events(conversation: Int, after: Int) -> AsyncThrowingStream<WatchSSEEvent, Error>
}

/// Why a request did not get an answer.
enum WatchVoiceError: Error, Equatable {
  /// The server answered with an error (`detail.code` when it gave one).
  case http(Int, String?)
  /// No network, or it dropped (`URLError`).
  case network
  /// Not configured (no server / no token).
  case notPaired

  /// What the user is shown.
  var failure: WatchVoiceFailure {
    switch self {
    case .http(let s, let c): return WatchVoiceFailure.http(status: s, code: c)
    case .network: return .offline
    case .notPaired: return .signedOut
    }
  }

  static func of(_ e: Error) -> WatchVoiceError {
    if let v = e as? WatchVoiceError { return v }
    return .network
  }
}

final class WatchVoiceAPI: WatchVoiceBackend {
  private let server: () -> String?
  private let token: () -> String?
  private let session: URLSession

  init(server: @escaping () -> String?, token: @escaping () -> String? = { WatchKeychain.token() }) {
    self.server = server
    self.token = token
    let c = URLSessionConfiguration.ephemeral   // nothing cached on the watch: no words, no answers
    c.timeoutIntervalForRequest = WatchParams.chatTimeoutSec
    c.waitsForConnectivity = false
    session = URLSession(configuration: c)
  }

  private static let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"

  private func request(_ path: String, method: String = "POST", query: [URLQueryItem] = [], body: Data? = nil,
                       type: String = "application/json", auth: Bool = true, server explicit: String? = nil,
                       timeout: TimeInterval = WatchParams.chatTimeoutSec) throws -> URLRequest {
    guard let base = explicit ?? server(), var u = URLComponents(string: base) else { throw WatchVoiceError.notPaired }
    u.path = (u.path.hasSuffix("/") ? String(u.path.dropLast()) : u.path) + path
    if !query.isEmpty { u.queryItems = query }
    guard let url = u.url else { throw WatchVoiceError.notPaired }
    var r = URLRequest(url: url, timeoutInterval: timeout)
    r.httpMethod = method
    r.httpBody = body
    if body != nil { r.setValue(type, forHTTPHeaderField: "Content-Type") }
    // the watch says what it is: never `ios-native` (the phone's forced-upgrade rule would stop the watch too)
    r.setValue("watchos", forHTTPHeaderField: "X-App-Client")
    r.setValue(Self.version, forHTTPHeaderField: "X-App-Version")
    r.setValue(Lang.code, forHTTPHeaderField: "X-Body-Lang")
    if auth {
      guard let t = token() else { throw WatchVoiceError.notPaired }
      r.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization")
    }
    return r
  }

  /// `{"detail": {"code": …}}` (voice and chat), `{"detail": "code"}` (account endpoints).
  static func code(_ d: Data?) -> String? {
    guard let d, let j = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { return nil }
    if let s = j["detail"] as? String { return s }
    return (j["detail"] as? [String: Any])?["code"] as? String
  }

  private func send(_ r: URLRequest) async throws -> [String: Any] {
    let (d, resp): (Data, URLResponse)
    do { (d, resp) = try await session.data(for: r) } catch { throw WatchVoiceError.network }
    let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
    guard (200..<300).contains(status) else { throw WatchVoiceError.http(status, Self.code(d)) }
    return (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] ?? [:]
  }

  func redeem(_ code: WatchPairCode) async throws -> String {
    let body = try JSONSerialization.data(withJSONObject: ["code": code.code, "client": "watch"])
    let j = try await send(try request("/v1/web/login-code", body: body, auth: false, server: code.server, timeout: 20))
    guard let t = j["token"] as? String, !t.isEmpty else { throw WatchVoiceError.http(500, nil) }
    return t
  }

  func transcribe(_ wav: Data) async throws -> String {
    let r = try request("/v1/assistant/transcribe", query: [URLQueryItem(name: "fix", value: "1")], body: wav, type: "audio/wav",
                        timeout: WatchParams.transcribeTimeoutSec)
    return try await send(r)["text"] as? String ?? ""
  }

  func chat(_ text: String, conversation: Int?) -> AsyncThrowingStream<WatchSSEEvent, Error> {
    var body: [String: Any] = ["message": text, "page": "watch"]
    if let conversation { body["conversation_id"] = conversation }
    return stream { try self.request("/v1/assistant/chat", body: try JSONSerialization.data(withJSONObject: body)) }
  }

  func approve(callId: String, approve: Bool) -> AsyncThrowingStream<WatchSSEEvent, Error> {
    stream {
      try self.request("/v1/assistant/chat/confirm",
                       body: try JSONSerialization.data(withJSONObject: ["call_id": callId, "approve": approve]))
    }
  }

  func events(conversation: Int, after: Int) -> AsyncThrowingStream<WatchSSEEvent, Error> {
    stream {
      try self.request("/v1/assistant/chat/\(conversation)/events", method: "GET",
                       query: [URLQueryItem(name: "after", value: String(after))])
    }
  }

  /// The SSE stream: an HTTP error before it starts throws `.http`; a connection that drops throws `.network`.
  private func stream(_ make: @escaping () throws -> URLRequest) -> AsyncThrowingStream<WatchSSEEvent, Error> {
    AsyncThrowingStream { cont in
      let task = Task {
        do {
          var r = try make()
          r.setValue("text/event-stream", forHTTPHeaderField: "Accept")
          let (bytes, resp): (URLSession.AsyncBytes, URLResponse)
          do { (bytes, resp) = try await session.bytes(for: r) } catch { throw WatchVoiceError.network }
          let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
          guard (200..<300).contains(status) else {
            var d = Data()
            for try await b in bytes {
              d.append(b)
              if d.count > 4096 { break }
            }
            throw WatchVoiceError.http(status, Self.code(d))
          }
          var p = WatchSSEParser()
          do {
            for try await line in bytes.lines {
              if let e = p.feed(line) { cont.yield(e) }
            }
          } catch {
            throw WatchVoiceError.network
          }
          cont.finish()
        } catch {
          cont.finish(throwing: error)
        }
      }
      cont.onTermination = { _ in task.cancel() }
    }
  }
}
