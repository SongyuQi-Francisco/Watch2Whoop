import XCTest
@testable import Watch2Whoop

/// A URLProtocol that answers from a script and records requests.
final class StubProtocol: URLProtocol {
  struct Reply {
    var status: Int
    var body: String = "{}"
    var headers: [String: String] = [:]
    var error: URLError.Code?
  }

  static var replies: [Reply] = []
  static var requests: [URLRequest] = []

  static func reset(_ r: [Reply]) {
    replies = r
    requests = []
  }

  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

  override func startLoading() {
    Self.requests.append(request)
    let r = Self.replies.isEmpty ? Reply(status: 500) : Self.replies.removeFirst()
    if let code = r.error {
      client?.urlProtocol(self, didFailWithError: URLError(code))
      return
    }
    let resp = HTTPURLResponse(url: request.url!, statusCode: r.status, httpVersion: "HTTP/1.1", headerFields: r.headers)!
    client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: Data(r.body.utf8))
    client?.urlProtocolDidFinishLoading(self)
  }

  override func stopLoading() {}

  static func session() -> URLSession {
    let c = URLSessionConfiguration.ephemeral
    c.protocolClasses = [StubProtocol.self]
    return URLSession(configuration: c)
  }
}

final class APIClientTests: XCTestCase {
  private var unauthorized = 0

  private func client(token: String? = "example-token") -> APIClient {
    unauthorized = 0
    return APIClient(baseURL: URL(string: "https://body.example.com")!, token: { token }, session: StubProtocol.session(),
                     onUnauthorized: { [weak self] in self?.unauthorized += 1 }, sleep: { _ in })
  }

  func testBearerLanguageAndPath() async throws {
    StubProtocol.reset([.init(status: 200, body: #"{"ok":true}"#)])
    let out = try await client().json(.get("/v1/users/me/today", ["date": "2026-01-02"]))
    XCTAssertEqual(out["ok"] as? Bool, true)
    let r = try XCTUnwrap(StubProtocol.requests.first)
    XCTAssertEqual(r.url?.absoluteString, "https://body.example.com/v1/users/me/today?date=2026-01-02")
    XCTAssertEqual(r.value(forHTTPHeaderField: "Authorization"), "Bearer example-token")
    XCTAssertNotNil(r.value(forHTTPHeaderField: "X-Body-Lang"))
    XCTAssertNotNil(r.value(forHTTPHeaderField: "X-App-Version"))
  }

  func test401TellsTheAppAndIsNotRetried() async {
    StubProtocol.reset([.init(status: 401, body: #"{"detail":"token revoked"}"#), .init(status: 200)])
    let c = client()
    do {
      _ = try await c.data(.get("/v1/web/me"))
      XCTFail("expected 401")
    } catch {
      XCTAssertEqual(error as? APIError, .unauthorized)
    }
    XCTAssertEqual(unauthorized, 1)
    XCTAssertEqual(StubProtocol.requests.count, 1)
  }

  func testGetIsRetriedAfter5xxAndNetworkErrors() async throws {
    StubProtocol.reset([.init(status: 503), .init(status: 0, error: .timedOut), .init(status: 200, body: #"{"n":1}"#)])
    let out = try await client().json(.get("/v1/users/me/today"))
    XCTAssertEqual(out["n"] as? Int, 1)
    XCTAssertEqual(StubProtocol.requests.count, 3)
  }

  func testPostIsSentOnce() async {
    StubProtocol.reset([.init(status: 503), .init(status: 200)])
    do {
      _ = try await client().data(APIRequest(method: .post, path: "/v1/users/me/water", body: Data("{}".utf8)))
      XCTFail("expected 503")
    } catch {
      XCTAssertEqual(error as? APIError, .http(503, nil))
    }
    XCTAssertEqual(StubProtocol.requests.count, 1)
  }

  func testErrorMapping() async {
    StubProtocol.reset([.init(status: 409, body: #"{"detail":"consent_required"}"#)])
    do {
      _ = try await client().data(.get("/v1/users/me/today"))
    } catch {
      XCTAssertEqual(error as? APIError, .conflict("consent_required"))
    }
    XCTAssertEqual(APIClient.backoff(attempt: 1, error: .rateLimited(7)), 7)
  }

  func testNoTokenNoRequest() async {
    StubProtocol.reset([.init(status: 200)])
    do {
      _ = try await client(token: nil).data(.get("/v1/web/me"))
    } catch {
      XCTAssertEqual(error as? APIError, .notConfigured)
    }
    XCTAssertTrue(StubProtocol.requests.isEmpty)
  }
}
