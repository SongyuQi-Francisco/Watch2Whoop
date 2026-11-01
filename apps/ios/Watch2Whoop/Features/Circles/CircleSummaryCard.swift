import SwiftUI

/// The circles in one card, for other pages (N3 puts it on Today; anyone may reuse it):
///
///     CircleSummaryCard()                         // tap → the Friends tab
///     CircleSummaryCard { selectedTab = .circles } // or your own action
///
/// Shows who is in my circles (avatars), how many of them trained today, the newest training post and a red dot for new
/// likes / comments on mine. Without circles it invites to make one. It reads the same cached store as the Friends tab
/// (`CirclesStore.shared(for:)`), so it draws at once, offline too, and refreshes in the background (at most every
/// 20 seconds). Draw it only when the `friends` flag is on (`session.circlesTab`); it hides itself otherwise.
struct CircleSummaryCard: View {
  @EnvironmentObject private var session: AccountSession
  var onOpen: (() -> Void)?

  var body: some View {
    if session.circlesTab {
      SummaryContent(store: CirclesStore.shared(for: session), onOpen: onOpen)
    }
  }
}

private struct SummaryContent: View {
  @EnvironmentObject private var app: AppModel
  @ObservedObject var store: CirclesStore
  let onOpen: (() -> Void)?

  var body: some View {
    Button(action: open) {
      VStack(alignment: .leading, spacing: 12) {
        HStack(spacing: 6) {
          Icon(.users, size: 16).foregroundColor(Palette.text2)
          Text(tr("朋友", "Friends")).font(Typo.section).foregroundColor(Palette.text2)
          if store.unread > 0 {
            Text(store.unread > 99 ? "99+" : "\(store.unread)")
              .font(.system(size: 11, weight: .bold).monospacedDigit()).foregroundColor(.white)
              .padding(.horizontal, 6).padding(.vertical, 1)
              .background(Capsule().fill(Palette.bad))
              .accessibilityLabel(tr("\(store.unread) 条新的点赞或评论", "\(store.unread) new likes or comments"))
          }
          Spacer()
          Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
        }
        if store.hasCircles || store.list == nil {
          content
        } else {
          HStack(spacing: 10) {
            Icon(.userPlus, size: 20).foregroundColor(Palette.text)
            Text(tr("建个圈子，和朋友一起练", "Make a circle and train with friends"))
              .font(Typo.body).foregroundColor(Palette.text)
          }
        }
      }
      .padding(16)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    }
    .buttonStyle(PressScale())
    .task { await store.refresh() }
  }

  @ViewBuilder private var content: some View {
    let others = store.overview?.members ?? []
    let trained = others.filter { $0.trained == true }.count
    HStack(spacing: 12) {
      AvatarStack(people: others.map { ($0.avatar, $0.displayName) }, size: 30)
      VStack(alignment: .leading, spacing: 2) {
        Text(others.isEmpty ? tr("还没有朋友加入", "No friends yet") : tr("\(others.count) 位朋友", "\(others.count) friends"))
          .font(Typo.bodyStrong).foregroundColor(Palette.text)
        if !others.isEmpty {
          Text(trained > 0 ? tr("今天 \(trained) 人练了", "\(trained) trained today") : tr("今天还没人练", "Nobody trained yet today"))
            .font(Typo.caption).foregroundColor(trained > 0 ? Palette.train : Palette.text2)
        }
      }
      Spacer(minLength: 0)
    }
    if let p = store.posts.first(where: { !$0.user.isSelf }) ?? store.posts.first {
      HStack(spacing: 10) {
        AvatarView(url: p.user.avatar, name: p.user.name, size: 26)
        (Text(p.user.isSelf ? tr("我", "Me") : p.user.shownName).fontWeight(.semibold) + Text("  \(p.title)"))
          .font(.system(size: 14)).foregroundColor(Palette.text).lineLimit(1)
        Spacer(minLength: 4)
        Text(FeedFormat.ago(p)).font(.system(size: 12)).foregroundColor(Palette.text3)
        if p.likes.count > 0 {
          HStack(spacing: 3) {
            Icon(p.likes.mine ? .heartFill : .heart, size: 14).foregroundColor(p.likes.mine ? PostCard.likeRed : Palette.text2)
            Text("\(p.likes.count)").font(.system(size: 12).monospacedDigit()).foregroundColor(Palette.text2)
          }
        }
      }
      .padding(10)
      .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.bg))
    }
  }

  private func open() {
    if let onOpen { onOpen() } else { app.route = .circles(post: nil) }
  }
}
