import AuthenticationServices
import CryptoKit
import Foundation

/// Sign-in, one step at a time (the Flutter app's onboarding_page.dart + setup_link_page.dart, without Open Wearables):
/// welcome (Apple / e-mail / setup QR or link / activation code) → e-mail code → invite code (pending accounts only) →
/// signed in. Consent, profile and Health access follow once signed in (SignedInFlows).
@MainActor
final class OnboardingModel: ObservableObject {
  enum Step: Equatable { case welcome, email, code, invite, setup, token }

  @Published var step: Step = .welcome
  @Published var server: String
  @Published var editServer: Bool
  @Published var email = ""
  @Published var code = ""
  @Published var invite = ""
  @Published var setupText = ""
  @Published var tokenText = ""
  @Published private(set) var methods: AuthService.Methods?
  @Published private(set) var busy = false
  @Published var error: String?
  @Published var info: String?
  @Published private(set) var cooldown = 0

  /// A pending account's token, kept until its invite code went through.
  private var pendingToken: String?
  private var rawNonce = ""
  private var timer: Timer?
  private weak var app: AppModel?

  func attach(_ app: AppModel) { self.app = app }

  init(app: AppModel?, defaults: UserDefaults = .standard) {
    self.app = app
    let s = OnboardingModel.defaultServer(defaults)
    server = s ?? ""
    editServer = s == nil
  }

  /// The server to offer: the last one signed out of, the Flutter app's, or the build's default (Info.plist
  /// `W2WDefaultHost`, from the git-ignored Watch2Whoop.local.xcconfig).
  static func defaultServer(_ defaults: UserDefaults) -> String? {
    if let s = defaults.string(forKey: AppModel.lastServerKey) { return s }
    if let s = FlutterMigration.flutterConfig(defaults).flatMap({ FlutterMigration.serverOrigin($0["todayUrl"] as? String) }) { return s }
    if let h = Bundle.main.object(forInfoDictionaryKey: "W2WDefaultHost") as? String, !h.isEmpty, !h.hasPrefix("$(") {
      return FlutterMigration.serverOrigin(h.contains("://") ? h : "https://\(h)")
    }
    return nil
  }

  var serverOrigin: String? { FlutterMigration.serverOrigin(server.contains("://") ? server : "https://\(server)") }

  private func auth(token: String? = nil) -> AuthService? {
    guard let s = serverOrigin else {
      error = tr("服务器地址不对", "That server address is not valid")
      editServer = true
      return nil
    }
    server = s
    return AuthService(server: s, token: token)
  }

  func loadMethods() async {
    guard let a = auth() else { error = nil; return }
    methods = try? await a.methods()
  }

  private func run(_ work: () async throws -> Void) async {
    busy = true
    error = nil
    info = nil
    defer { busy = false }
    do {
      try await work()
    } catch let e as APIError {
      error = e.signInMessage
    } catch let e as ASAuthorizationError where e.code == .canceled {
      // the user closed the Apple sheet
    } catch let e {
      let why = e.localizedDescription
      self.error = tr("没有成功：\(why)", "That did not work: \(why)")
    }
  }

  // MARK: - Apple

  /// The SHA-256 of a fresh random nonce goes into Apple's request; the raw one to the server, which checks it.
  func prepareApple(_ request: ASAuthorizationAppleIDRequest) {
    rawNonce = Self.randomNonce()
    request.requestedScopes = [.email, .fullName]
    request.nonce = SHA256.hash(data: Data(rawNonce.utf8)).map { String(format: "%02x", $0) }.joined()
  }

  func finishApple(_ result: Result<ASAuthorization, Error>) async {
    await run {
      let auth = try result.get()
      guard let cred = auth.credential as? ASAuthorizationAppleIDCredential, let data = cred.identityToken,
            let idToken = String(data: data, encoding: .utf8), let a = self.auth() else { throw APIError.decoding("no Apple token") }
      let s = try await a.apple(identityToken: idToken, rawNonce: rawNonce, givenName: cred.fullName?.givenName,
                                familyName: cred.fullName?.familyName)
      await signedIn(s)
    }
  }

  static func randomNonce(length: Int = 32) -> String {
    // digits, upper and lower case letters and -._ (the nonce alphabet of Apple's sample code)
    let chars: [Character] = [48...57, 65...90, 97...122].flatMap { r in r.map { Character(UnicodeScalar(UInt8($0))) } } + ["-", ".", "_"]
    var out = ""
    var g = SystemRandomNumberGenerator()
    for _ in 0..<length { out.append(chars[Int.random(in: 0..<chars.count, using: &g)]) }
    return out
  }

  // MARK: - e-mail

  func sendCode() async {
    let e = email.trimmingCharacters(in: .whitespaces)
    guard e.contains("@"), e.contains(".") else { error = tr("邮箱格式不对", "Invalid e-mail address"); return }
    await run {
      guard let a = auth() else { return }
      try await a.startEmail(e)
      code = ""
      step = .code
      info = tr("验证码已发到 \(e)，10 分钟内有效", "Code sent to \(e) (valid for 10 minutes)")
      startCooldown(60)
    }
  }

  func verifyCode() async {
    let c = code.filter(\.isNumber)
    guard c.count == 6 else { error = tr("验证码是 6 位数字", "The code has 6 digits"); return }
    await run {
      guard let a = auth() else { return }
      let s = try await a.verifyEmail(email.trimmingCharacters(in: .whitespaces), code: c)
      await signedIn(s)
    }
  }

  private func startCooldown(_ seconds: Int) {
    cooldown = seconds
    timer?.invalidate()
    timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] t in
      Task { @MainActor in
        guard let self else { t.invalidate(); return }
        self.cooldown -= 1
        if self.cooldown <= 0 { t.invalidate() }
      }
    }
  }

  // MARK: - setup link / QR code / activation code

  /// A scanned or pasted setup link, a login code or an activation code: everything in one step.
  func applySetup(_ text: String) async {
    let parsed = SetupInfo.parse(text)
    guard !parsed.isEmpty else {
      error = tr("没认出来：要设置链接、设置二维码、登录码（12 位）或激活码（16 位）", "Not recognised: use a setup link or QR code, a login code (12 characters) or an activation code (16)")
      return
    }
    await apply(parsed)
  }

  func apply(_ parsed: SetupInfo) async {
    await run {
      if let s = parsed.server { server = s; editServer = false }
      var info = parsed
      if info.loginCode == nil, let act = info.activationCode {
        guard let a = auth() else { return }
        let link = try await a.activate(act)
        if let s = link.server { server = s }
        info.loginCode = link.loginCode
      }
      guard let login = info.loginCode, let a = auth() else {
        error = tr("这条设置信息里没有登录码，请向管理员要新的", "This setup info has no login code; ask the admin for a new one")
        return
      }
      let s = try await a.redeemLoginCode(login)
      await signedIn(s)
    }
  }

  // MARK: - invite code (pending accounts)

  func redeemInvite() async {
    let c = invite.trimmingCharacters(in: .whitespacesAndNewlines)
    guard c.count >= 4, let token = pendingToken else { error = tr("请输入激活码", "Enter the invite code"); return }
    await run {
      guard let a = auth(token: token) else { return }
      let r = try await a.redeemInvite(c)
      let t = r.token ?? token   // an existing account's code signs in there with its own token
      if r.status == "pending" { error = tr("账号还没激活，请换一个激活码", "The account is still not active; try another code"); return }
      pendingToken = nil
      await finish(token: t)
    }
  }

  // MARK: - advanced: an account token

  func signInWithToken() async {
    let t = tokenText.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !t.isEmpty else { return }
    await run { await finish(token: t) }
  }

  // MARK: - after a sign-in

  private func signedIn(_ s: AuthService.Session) async {
    switch s.status {
    case "pending":
      pendingToken = s.token
      invite = ""
      step = .invite
    case "disabled":
      error = tr("这个账号已停用，请联系管理员", "This account is disabled; contact the admin")
    default:
      await finish(token: s.token)
    }
  }

  private func finish(token: String) async {
    guard let app, let s = serverOrigin else { return }
    if let m = await app.signIn(server: s, token: token) { error = m }
  }

  func back() {
    error = nil
    info = nil
    switch step {
    case .code: step = .email
    case .invite: pendingToken = nil; step = .welcome
    default: step = .welcome
    }
  }
}
