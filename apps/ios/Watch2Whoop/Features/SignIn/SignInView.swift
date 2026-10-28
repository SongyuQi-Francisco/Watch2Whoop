import SwiftUI

/// Sign-in for the skeleton: server address + account token. N8 replaces it with the setup link / QR code, e-mail and
/// Sign in with Apple flows (the Flutter app's onboarding); the token form stays as the advanced option.
struct SignInView: View {
  @EnvironmentObject private var model: AppModel
  let message: String?
  @State private var server = ""
  @State private var token = ""
  @State private var busy = false
  @State private var error: String?

  var body: some View {
    PageScaffold(title: "Watch2Whoop") {
      VStack(alignment: .leading, spacing: 12) {
        if let m = error ?? message {
          Text(m).font(Typo.caption).foregroundColor(Palette.bad)
        }
        field(tr("服务器地址", "Server address"), text: $server, secure: false)
        field(tr("账号 token", "Account token"), text: $token, secure: true)
        PrimaryButton(title: tr("登录", "Sign in"), busy: busy) {
          busy = true
          Task {
            error = await model.signIn(server: server, token: token)
            busy = false
          }
        }
        .disabled(server.isEmpty || token.isEmpty)
        Text(tr("扫码设置、邮箱和 Apple 登录在后续版本加入。", "Setup QR code, e-mail and Apple sign-in come in a later build."))
          .font(Typo.label).foregroundColor(Palette.text3)
      }
      .padding(.top, 16)
    }
  }

  private func field(_ title: String, text: Binding<String>, secure: Bool) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      Text(title).font(Typo.label).foregroundColor(Palette.text2)
      Group {
        if secure { SecureField("", text: text) } else { TextField("", text: text).keyboardType(.URL) }
      }
      .textInputAutocapitalization(.never)
      .autocorrectionDisabled()
      .font(Typo.body)
      .padding(.horizontal, 14).frame(minHeight: 46)
      .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
    }
  }
}
