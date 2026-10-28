import Foundation

/// Sign-in calls against one server, before (or right after) there is a token (account/api.py, web/api.py). Every way in
/// ends with a device token for the Keychain:
/// - Sign in with Apple / e-mail code: `POST /v1/auth/apple`, `/v1/auth/email/verify` with `client: app`;
/// - a setup link / QR code: its one-time login code, `POST /v1/web/login-code` with `client: app`;
/// - an activation code (16 characters): `POST /v1/web/activate` → a setup link → its login code as above;
/// - a pending account (open sign-up closed): an invite code, `POST /v1/account/redeem-invite`.
struct AuthService {
  let server: String
  let api: APIClient

  init(server: String, token: String? = nil) {
    self.server = server
    // No token yet: an empty one passes APIClient's "signed in" check and sends no Authorization header. A 401 here is a
    // wrong code, not a sign-out, so it is not reported anywhere.
    self.api = APIClient(baseURL: URL(string: server) ?? URL(string: "https://invalid.invalid")!, token: { token ?? "" })
  }

  struct Methods: Decodable, Equatable {
    var email = false
    var apple = false
    var openSignup = false

    enum CodingKeys: String, CodingKey { case email, apple, openSignup = "open_signup" }

    init(email: Bool = false, apple: Bool = false, openSignup: Bool = false) {
      self.email = email
      self.apple = apple
      self.openSignup = openSignup
    }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      email = try c.decodeIfPresent(Bool.self, forKey: .email) ?? false
      apple = try c.decodeIfPresent(Bool.self, forKey: .apple) ?? false
      openSignup = try c.decodeIfPresent(Bool.self, forKey: .openSignup) ?? false
    }
  }

  /// What the server answers after a sign-in: the device token and the account's state.
  struct Session: Equatable {
    let token: String
    /// active / pending (needs an invite code) / disabled; nil when the answer did not say.
    let status: String?
    let userId: Int?
  }

  static var deviceLabel: String { "iPhone (Watch2Whoop)" }

  func methods() async throws -> Methods { try await api.send(.get(Endpoint.authMethods)) }

  func startEmail(_ email: String) async throws {
    _ = try await api.data(try .json(.post, Endpoint.authEmailStart, ["email": email, "lang": Lang.code], idempotent: false))
  }

  func verifyEmail(_ email: String, code: String) async throws -> Session {
    let j = try await api.json(try .json(.post, Endpoint.authEmailVerify,
                                         ["email": email, "code": code, "client": "app", "device": Self.deviceLabel], idempotent: false))
    return try Self.session(j)
  }

  func apple(identityToken: String, rawNonce: String, givenName: String?, familyName: String?) async throws -> Session {
    var body: [String: String] = ["identity_token": identityToken, "nonce": rawNonce, "client": "app", "device": Self.deviceLabel]
    if let givenName, !givenName.isEmpty { body["given_name"] = givenName }
    if let familyName, !familyName.isEmpty { body["family_name"] = familyName }
    let j = try await api.json(try .json(.post, Endpoint.authApple, body, idempotent: false))
    return try Self.session(j)
  }

  /// The setup link's one-time login code → a device token (single use).
  func redeemLoginCode(_ code: String) async throws -> Session {
    let j = try await api.json(try .json(.post, Endpoint.webLoginCode, ["code": code, "client": "app"], idempotent: false))
    guard let token = j["token"] as? String, !token.isEmpty else { throw APIError.decoding("no token") }
    return Session(token: token, status: j["status"] as? String, userId: (j["id"] as? NSNumber)?.intValue)
  }

  /// An activation code → a fresh setup link (with a login code). `web: true` mints no Open Wearables invitation.
  func activate(_ code: String) async throws -> SetupInfo {
    let j = try await api.json(try .json(.post, Endpoint.webActivate, ActivateBody(code: code), idempotent: false))
    guard let link = j["setup_link"] as? String else { throw APIError.decoding("no setup link") }
    return SetupInfo.parse(link)
  }

  private struct ActivateBody: Encodable {
    let code: String
    var web = true
  }

  /// GET /v1/account with a token: active / pending / disabled.
  func accountStatus() async throws -> String? { try await api.json(.get(Endpoint.account))["status"] as? String }

  /// An invite / circle / activation code for a pending account. A code of another (existing) account signs in there:
  /// the answer then carries that account's token.
  func redeemInvite(_ code: String) async throws -> (status: String?, token: String?) {
    let j = try await api.json(try .json(.post, Endpoint.redeemInvite, ["code": code], idempotent: false))
    return (j["status"] as? String, j["token"] as? String)
  }

  private static func session(_ j: [String: Any]) throws -> Session {
    guard let token = j["token"] as? String, !token.isEmpty else { throw APIError.decoding("no token") }
    let me = j["me"] as? [String: Any]
    return Session(token: token, status: (j["status"] as? String) ?? (me?["status"] as? String), userId: (me?["id"] as? NSNumber)?.intValue)
  }
}

extension APIError {
  /// Sign-in errors in plain words; nil = use the generic message.
  var signInMessage: String {
    switch self {
    case .unauthorized: return tr("码不对、已用过或已过期", "That code is wrong, used or expired")
    case .rateLimited: return tr("试得太频繁，稍后再试", "Too many tries; wait a moment")
    case .forbidden(let d) where d == "account_disabled": return tr("这个账号已停用，请联系管理员", "This account is disabled; contact the admin")
    case .http(503, _): return tr("这台服务器还没开通这种登录方式", "This server does not offer this sign-in yet")
    case .network: return tr("连不上服务器，检查网络或服务器地址", "Can't reach the server; check the network or the address")
    default: return userMessage
    }
  }
}
