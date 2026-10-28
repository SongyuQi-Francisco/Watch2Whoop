import AuthenticationServices
import SwiftUI

/// Sign-in (replaces N0's token form). Zero tutorial: the familiar ways in, each one tap — Sign in with Apple, an e-mail
/// code, scanning the setup QR code (or opening its link), pasting setup info / an activation code. A link opened from the
/// camera (`watch2whoop://setup?…`) signs in straight away.
struct OnboardingView: View {
  @EnvironmentObject private var app: AppModel
  @Environment(\.colorScheme) private var scheme
  let message: String?
  @StateObject private var model = OnboardingModel(app: nil)
  @State private var scanning = false

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 14) {
        HStack {
          if model.step != .welcome {
            Button(tr("返回", "Back")) { model.back() }.font(Typo.body).foregroundColor(Palette.text2)
          }
          Spacer()
          LanguageChip()
        }
        .padding(.top, 6)
        content(model)
        if model.busy { ProgressView().tint(Palette.text2).frame(maxWidth: .infinity) }
        if let i = model.info { Banner(text: i, tone: .good) }
        if let e = model.error ?? (model.step == .welcome ? message : nil) { Banner(text: e, tone: .bad) }
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .scrollDismissesKeyboardIfAvailable()
    .background(Palette.bg.ignoresSafeArea())
    .task {
      model.attach(app)
      await model.loadMethods()
    }
    .onReceive(app.$pendingSetup) { info in
      guard let info else { return }
      model.attach(app)
      app.pendingSetup = nil
      Task { await model.apply(info) }
    }
    .sheet(isPresented: $scanning) {
      QRScannerSheet { text in
        scanning = false
        Task { await model.applySetup(text) }
      }
    }
  }

  @ViewBuilder private func content(_ model: OnboardingModel) -> some View {
    switch model.step {
    case .welcome: welcome(model)
    case .email: emailStep(model)
    case .code: codeStep(model)
    case .invite: inviteStep(model)
    case .setup: setupStep(model)
    case .token: tokenStep(model)
    }
  }

  private func title(_ t: String, _ sub: String? = nil) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(t).font(.system(size: 30, weight: .bold)).titleTracking().foregroundColor(Palette.text)
      if let sub { Text(sub).font(Typo.body).foregroundColor(Palette.text2) }
    }
    .padding(.top, 24).padding(.bottom, 14)
  }

  @ViewBuilder private func serverRow(_ model: OnboardingModel) -> some View {
    if model.editServer {
      FormField(label: tr("服务器地址", "Server address"), text: Binding(get: { model.server }, set: { model.server = $0 }),
                placeholder: "https://body.example.com", keyboard: .URL, contentType: .URL)
        .onSubmit { Task { await model.loadMethods() } }
    } else {
      HStack {
        Text(tr("服务器", "Server")).font(Typo.caption).foregroundColor(Palette.text2)
        Text(URL(string: model.server)?.host ?? model.server).font(Typo.caption).foregroundColor(Palette.text)
        Spacer()
        Button(tr("更改", "Change")) { model.editServer = true }.font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
  }

  @ViewBuilder private func welcome(_ model: OnboardingModel) -> some View {
    title("Watch2Whoop", tr("登录后开始同步 Apple 健康数据。", "Sign in to start syncing your Apple Health data."))
    serverRow(model)
    if model.methods?.apple ?? true {
      SignInWithAppleButton(.signIn, onRequest: model.prepareApple) { result in Task { await model.finishApple(result) } }
        .signInWithAppleButtonStyle(scheme == .dark ? .white : .black)
        .frame(height: 50)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .disabled(model.busy)
    }
    if model.methods?.email ?? true {
      SecondaryButton(icon: .mail, title: tr("用邮箱登录", "Sign in with e-mail")) { model.error = nil; model.step = .email }
    }
    if QRScannerSheet.isAvailable {
      SecondaryButton(icon: .qrCode, title: tr("扫设置二维码", "Scan the setup QR code")) { scanning = true }
    }
    SecondaryButton(icon: .keyRound, title: tr("我有设置链接或激活码", "I have a setup link or activation code")) {
      model.error = nil
      model.step = .setup
    }
    Button(tr("用 token 登录（高级）", "Sign in with a token (advanced)")) { model.error = nil; model.step = .token }
      .font(Typo.label).foregroundColor(Palette.text3).frame(maxWidth: .infinity).padding(.top, 8)
  }

  @ViewBuilder private func emailStep(_ model: OnboardingModel) -> some View {
    title(tr("用邮箱登录", "Sign in with e-mail"), tr("我们会发一个 6 位验证码。", "We'll send you a 6-digit code."))
    FormField(label: tr("邮箱", "E-mail"), text: Binding(get: { model.email }, set: { model.email = $0 }),
              placeholder: "name@example.com", keyboard: .emailAddress, contentType: .emailAddress)
      .onSubmit { Task { await model.sendCode() } }
    PrimaryButton(title: tr("发送验证码", "Send code"), busy: model.busy) { Task { await model.sendCode() } }
      .disabled(model.email.isEmpty)
  }

  @ViewBuilder private func codeStep(_ model: OnboardingModel) -> some View {
    title(tr("输入验证码", "Enter the code"), model.email)
    FormField(label: tr("6 位验证码", "6-digit code"), text: Binding(get: { model.code }, set: { model.code = $0 }),
              placeholder: "123456", keyboard: .numberPad, contentType: .oneTimeCode)
    PrimaryButton(title: tr("登录", "Sign in"), busy: model.busy) { Task { await model.verifyCode() } }
      .disabled(model.code.filter(\.isNumber).count != 6)
    Button(model.cooldown > 0 ? tr("重新发送（\(model.cooldown) 秒）", "Resend (\(model.cooldown) s)") : tr("重新发送", "Resend")) {
      Task { await model.sendCode() }
    }
    .disabled(model.cooldown > 0 || model.busy)
    .font(Typo.caption).foregroundColor(Palette.text2).frame(maxWidth: .infinity)
  }

  @ViewBuilder private func inviteStep(_ model: OnboardingModel) -> some View {
    title(tr("输入激活码", "Enter your invite code"), tr("这个账号还没激活：填管理员或朋友给你的激活码。", "This account is not active yet: enter the code from the admin or a friend."))
    FormField(label: tr("激活码", "Invite code"), text: Binding(get: { model.invite }, set: { model.invite = $0 }),
              placeholder: "ABCDEFGH23")
    PrimaryButton(title: tr("激活", "Activate"), busy: model.busy) { Task { await model.redeemInvite() } }
      .disabled(model.invite.trimmingCharacters(in: .whitespaces).count < 4)
  }

  @ViewBuilder private func setupStep(_ model: OnboardingModel) -> some View {
    title(tr("设置链接或激活码", "Setup link or activation code"), tr("粘贴管理员发给你的设置信息、登录码或激活码。", "Paste the setup info, login code or activation code you were given."))
    serverRow(model)
    PasteButton(payloadType: String.self) { strings in
      guard let s = strings.first else { return }
      Task { @MainActor in
        model.setupText = s
        await model.applySetup(s)
      }
    }
    .tint(Palette.text)
    .frame(maxWidth: .infinity)
    FormField(label: tr("或者输入", "Or type it"), text: Binding(get: { model.setupText }, set: { model.setupText = $0 }),
              placeholder: "watch2whoop://setup?…")
    PrimaryButton(title: tr("登录", "Sign in"), busy: model.busy) { Task { await model.applySetup(model.setupText) } }
      .disabled(model.setupText.trimmingCharacters(in: .whitespaces).isEmpty)
    if QRScannerSheet.isAvailable {
      SecondaryButton(icon: .qrCode, title: tr("扫设置二维码", "Scan the setup QR code")) { scanning = true }
    }
  }

  @ViewBuilder private func tokenStep(_ model: OnboardingModel) -> some View {
    title(tr("用 token 登录", "Sign in with a token"), tr("给自己部署服务器的人用。", "For people who run their own server."))
    FormField(label: tr("服务器地址", "Server address"), text: Binding(get: { model.server }, set: { model.server = $0 }),
              placeholder: "https://body.example.com", keyboard: .URL, contentType: .URL)
    FormField(label: tr("账号 token", "Account token"), text: Binding(get: { model.tokenText }, set: { model.tokenText = $0 }), secure: true)
    PrimaryButton(title: tr("登录", "Sign in"), busy: model.busy) { Task { await model.signInWithToken() } }
      .disabled(model.server.isEmpty || model.tokenText.isEmpty)
  }
}

/// 中文 / English, top right on every sign-in step.
struct LanguageChip: View {
  @EnvironmentObject private var app: AppModel

  var body: some View {
    // each language named in itself (escaped: no Chinese literal outside tr(), scripts/check_sources.py)
    Button(Lang.isZh ? "English" : "\u{4E2D}\u{6587}") { app.setLanguage(Lang.isZh ? "en" : "zh") }
      .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
      .padding(.vertical, 6).padding(.horizontal, 12)
      .background(Capsule().fill(Palette.surface))
      .accessibilityLabel(tr("切换语言", "Switch language"))
  }
}

extension View {
  /// Drag down to dismiss the keyboard (iOS 16).
  func scrollDismissesKeyboardIfAvailable() -> some View { scrollDismissesKeyboard(.interactively) }
}
