import AuthenticationServices
import CryptoKit
import SwiftUI
import UIKit

// The account's sign-in methods, inviting friends and entering a code (account/api.py; `identities.js` and `account.js` on
// the page). A wrong code answers 401 on these calls: they go through `api.keepingSignIn()`, so that it stays an error
// message and does not sign the person out.

/// GET /v1/account: what these screens read of it.
struct AccountInfo: Decodable, Equatable {
  var identities: [Identity] = []
  var appleLink = false
  var inviteOptional = false

  struct Identity: Decodable, Equatable, Hashable {
    let provider: String
    var email: String?
  }

  enum CodingKeys: String, CodingKey { case identities, appleLink = "apple_link", inviteOptional = "invite_optional" }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    identities = try c.decodeIfPresent([Identity].self, forKey: .identities) ?? []
    appleLink = try c.decodeIfPresent(Bool.self, forKey: .appleLink) ?? false
    inviteOptional = try c.decodeIfPresent(Bool.self, forKey: .inviteOptional) ?? false
  }

  var hasApple: Bool { identities.contains { $0.provider == "apple" } }
}

/// GET /v1/users/me/invite-code.
struct ReferralCode: Decodable, Equatable {
  let code: String
  var link: String?
  var joined = 0
  var remaining = 0

  enum CodingKeys: String, CodingKey { case code, link, joined, remaining }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    code = try c.decode(String.self, forKey: .code)
    link = try c.decodeIfPresent(String.self, forKey: .link)
    joined = try c.decodeIfPresent(Int.self, forKey: .joined) ?? 0
    remaining = try c.decodeIfPresent(Int.self, forKey: .remaining) ?? 0
  }

  /// `/app/?invite=…` on the signed-in server.
  func shareLink(server: String) -> String? {
    guard let link else { return nil }
    if link.contains("://") { return link }
    return server.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + link
  }
}

private extension APIError {
  /// The same words as `errText` in identities.js.
  var bindMessage: String {
    switch self {
    case .conflict: return tr("这个邮箱或 Apple ID 已经属于另一个账号", "This e-mail or Apple ID already belongs to another account")
    case .unauthorized: return tr("验证码不对或已过期", "That code is wrong or expired")
    case .rateLimited: return tr("发送太频繁，过一会儿再试", "Too many tries; wait a moment")
    case .http(400, _): return tr("邮箱格式不对", "That e-mail address looks wrong")
    case .http(503, _): return tr("这台服务器还没开通这种登录方式", "This server does not offer this sign-in yet")
    default: return userMessage
    }
  }

  var inviteMessage: String {
    switch self {
    case .unauthorized: return tr("激活码无效、已用完或已过期", "That code is invalid, used up or expired")
    case .rateLimited: return tr("尝试太多次，过一会儿再试", "Too many tries; wait a moment")
    default: return userMessage
    }
  }
}

/// 登录方式: the bound e-mails and Apple ID; bind another. A method that already belongs to another account is refused
/// (409 identity_taken): accounts are never merged here.
struct SignInMethodsView: View {
  @EnvironmentObject private var session: AccountSession
  @Environment(\.colorScheme) private var scheme
  @State private var info: AccountInfo?
  @State private var showEmail = false
  @State private var email = ""
  @State private var code = ""
  @State private var codeSent = false
  @State private var busy = false
  @State private var message: String?
  @State private var note: String?
  @State private var rawNonce = ""

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      if let info {
        if info.identities.isEmpty {
          Text(tr("还没有绑定登录方式", "No sign-in method yet")).font(Typo.body).foregroundColor(Palette.text2)
        } else {
          RowGroup {
            ForEach(Array(info.identities.enumerated()), id: \.element) { i, id in
              if i > 0 { RowDivider(inset: 0) }
              ListRow(title: id.provider == "apple" ? "Apple" : tr("邮箱", "E-mail"), value: id.email ?? (id.provider == "apple" ? "Apple ID" : nil))
            }
          }
        }
        if showEmail {
          emailForm
        } else {
          SecondaryButton(icon: .mail, title: tr("绑定邮箱", "Add an e-mail")) { showEmail = true; message = nil }
        }
        if info.appleLink && !info.hasApple {
          SignInWithAppleButton(.continue, onRequest: prepareApple) { result in Task { await finishApple(result) } }
            .signInWithAppleButtonStyle(scheme == .dark ? .white : .black)
            .frame(height: 48)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .disabled(busy)
        }
      } else if message == nil {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity)
      }
      if let note { Banner(text: note, tone: .good) }
      if let message { Banner(text: message, tone: .bad) }
    }
    .task { await load() }
  }

  private var emailForm: some View {
    VStack(alignment: .leading, spacing: 12) {
      FormField(label: tr("邮箱", "E-mail"), text: $email, keyboard: .emailAddress, contentType: .emailAddress)
      SecondaryButton(title: tr("发送验证码", "Send the code")) { Task { await send() } }.disabled(busy)
      if codeSent {
        FormField(label: tr("6 位验证码", "6-digit code"), text: $code, keyboard: .numberPad, contentType: .oneTimeCode)
        PrimaryButton(title: tr("绑定", "Add"), busy: busy) { Task { await bind() } }
      }
    }
  }

  private func load() async {
    do {
      info = try await session.api.send(.get(Endpoint.account), as: AccountInfo.self)
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func send() async {
    let address = email.trimmingCharacters(in: .whitespaces)
    guard address.range(of: #"^[^\s@]+@[^\s@]+\.[^\s@]+$"#, options: .regularExpression) != nil else {
      message = tr("邮箱格式不对", "That e-mail address looks wrong")
      return
    }
    await run {
      _ = try await session.api.keepingSignIn().data(try .json(.post, Endpoint.authEmailStart, ["email": address, "lang": Lang.code], idempotent: false))
      codeSent = true
      note = tr("验证码已发送，10 分钟内有效", "Code sent; valid for 10 minutes")
    }
  }

  private func bind() async {
    let address = email.trimmingCharacters(in: .whitespaces), c = code.trimmingCharacters(in: .whitespaces)
    await run {
      _ = try await session.api.keepingSignIn().data(try .json(.post, Endpoint.bindEmail, ["email": address, "code": c], idempotent: false))
      bound()
    }
  }

  /// The SHA-256 of a fresh nonce goes into Apple's request; the raw one to the server, which checks it.
  private func prepareApple(_ request: ASAuthorizationAppleIDRequest) {
    rawNonce = OnboardingModel.randomNonce()
    request.requestedScopes = [.email]
    request.nonce = SHA256.hash(data: Data(rawNonce.utf8)).map { String(format: "%02x", $0) }.joined()
  }

  private func finishApple(_ result: Result<ASAuthorization, Error>) async {
    guard case .success(let auth) = result,   // closing Apple's sheet is not an error
          let cred = auth.credential as? ASAuthorizationAppleIDCredential, let data = cred.identityToken,
          let token = String(data: data, encoding: .utf8) else { return }
    var body = ["identity_token": token, "nonce": rawNonce]
    if let c = cred.authorizationCode.flatMap({ String(data: $0, encoding: .utf8) }), !c.isEmpty { body["authorization_code"] = c }
    await run {
      _ = try await session.api.keepingSignIn().data(try .json(.post, Endpoint.bindApple, body, idempotent: false))
      bound()
    }
  }

  private func bound() {
    showEmail = false
    codeSent = false
    code = ""
    email = ""
    note = tr("已绑定", "Added")
  }

  private func run(_ work: () async throws -> Void) async {
    busy = true
    message = nil
    note = nil
    defer { busy = false }
    do {
      try await work()
      await load()
    } catch let e as APIError {
      message = e.bindMessage
    } catch {
      message = tr("出错了", "Something went wrong")
    }
  }
}

/// 邀请好友: the user's own code, how many joined, copy, share (only when the server turned referrals on).
struct InviteFriendsView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var r: ReferralCode?
  @State private var message: String?
  @State private var copied = false

  var body: some View {
    VStack(spacing: 14) {
      if let r {
        Text(r.code).font(.system(size: 28, weight: .semibold, design: .monospaced)).foregroundColor(Palette.text)
          .textSelection(.enabled).padding(.top, 6)
        HStack(spacing: 8) {
          Chip(label: tr("已加入 \(r.joined)", "Joined \(r.joined)"), tone: .good)
          Chip(label: tr("还能邀请 \(r.remaining)", "\(r.remaining) left"))
        }
        SecondaryButton(icon: .copy, title: copied ? tr("已复制", "Copied") : tr("复制邀请码", "Copy the code")) {
          UIPasteboard.general.string = r.code
          copied = true
        }
        ShareLink(item: shareText(r)) {
          HStack(spacing: 8) {
            Icon(.share, size: 18)
            Text(tr("分享", "Share")).font(Typo.bodyStrong)
          }
          .foregroundColor(Palette.bg)
          .frame(maxWidth: .infinity, minHeight: 48)
          .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.text))
        }
      } else if let message {
        Banner(text: message, tone: .bad)
      } else {
        ProgressView().tint(Palette.text2)
      }
    }
    .frame(maxWidth: .infinity)
    .task { await load() }
  }

  private func shareText(_ r: ReferralCode) -> String {
    let line = tr("用我的邀请码 \(r.code) 注册 Watch2Whoop", "Sign up for Watch2Whoop with my invite code \(r.code)")
    guard let link = r.shareLink(server: session.config.server) else { return line }
    return "\(line) \(link)"
  }

  private func load() async {
    do {
      r = try await session.api.send(.get(Endpoint.inviteCode), as: ReferralCode.self)
    } catch APIError.forbidden(let d) where d == "referral_revoked" {
      message = tr("你的邀请码已被管理员停用", "Your invite code was turned off by the admin")
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }
}

/// 填写邀请码: while `GET /v1/account` says invite_optional (open sign-up, the first days), one friend's code or a circle code.
struct LateInviteView: View {
  @EnvironmentObject private var session: AccountSession
  let done: () -> Void
  @State private var code = ""
  @State private var busy = false
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      FormField(label: tr("邀请码", "Invite code"), text: $code, placeholder: "XXXXX-XXXXX")
      if let message { Banner(text: message, tone: .bad) }
      PrimaryButton(title: tr("提交邀请码", "Submit the code"), busy: busy) { Task { await submit() } }
    }
  }

  private func submit() async {
    let v = code.trimmingCharacters(in: .whitespaces)
    guard v.filter({ !$0.isWhitespace && $0 != "-" }).count >= 4 else {
      message = tr("请输入激活码", "Enter the code")
      return
    }
    busy = true
    message = nil
    defer { busy = false }
    do {
      _ = try await session.api.keepingSignIn().data(try .json(.post, Endpoint.redeemInvite, ["code": v], idempotent: false))
      done()
    } catch let e as APIError {
      message = e.inviteMessage
    } catch {
      message = tr("出错了", "Something went wrong")
    }
  }
}
