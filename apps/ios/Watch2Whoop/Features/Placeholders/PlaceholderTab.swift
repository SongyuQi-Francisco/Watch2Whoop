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
