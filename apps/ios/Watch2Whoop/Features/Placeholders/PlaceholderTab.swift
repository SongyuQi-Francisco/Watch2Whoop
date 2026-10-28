import SwiftUI

/// Stand-in for a tab whose native page is still being built (owner = the #340 piece that replaces it).
struct PlaceholderTab: View {
  let tab: AppTab
  let owner: String

  var body: some View {
    PageScaffold(title: tab.title) {
      Card {
        VStack(alignment: .leading, spacing: 6) {
          Text(tr("原生页面开发中", "Native page in progress")).font(Typo.bodyStrong).foregroundColor(Palette.text)
          Text("#340 \(owner)").font(Typo.caption).foregroundColor(Palette.text2)
        }
      }
      .padding(.top, 8)
    }
  }
}

/// 我: for now the account and sign-out (N8 builds settings, AI settings, notifications, data status here).
struct MeTab: View {
  @EnvironmentObject private var model: AppModel
  @EnvironmentObject private var session: AccountSession
  @State private var confirmSignOut = false

  var body: some View {
    PageScaffold(title: tr("我", "Me")) {
      Card(padding: 4) {
        VStack(spacing: 0) {
          ListRow(icon: .user, title: session.displayName ?? tr("我的账号", "My account"),
                  subtitle: URL(string: session.config.server)?.host)
            .padding(.horizontal, 12)
          Divider().overlay(Palette.hair).padding(.leading, 44)
          ListRow(icon: .refreshCw, title: tr("版本", "Version"), value: APIClient.appVersion)
            .padding(.horizontal, 12)
        }
      }
      .padding(.top, 8)
      Button { confirmSignOut = true } label: {
        Text(tr("退出登录", "Sign out")).font(Typo.bodyStrong).foregroundColor(Palette.bad)
          .frame(maxWidth: .infinity, minHeight: 48)
          .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
      }
      .buttonStyle(PressScale())
      .padding(.top, 16)
      .confirmationDialog(tr("退出登录？", "Sign out?"), isPresented: $confirmSignOut, titleVisibility: .visible) {
        Button(tr("退出登录", "Sign out"), role: .destructive) { model.signOut() }
        Button(tr("取消", "Cancel"), role: .cancel) {}
      } message: {
        Text(tr("这台手机上的数据会删掉，服务器上的不受影响。", "Data on this iPhone is removed; your server keeps everything."))
      }
    }
  }
}
