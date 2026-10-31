import SwiftUI

// Circle moderation in the app (App Store guideline 1.2; server: friends/moderation.py): report a post, a comment or a
// member's profile with a reason, block someone (both of you stop seeing each other in circles; undo under 我的圈子 → 已屏蔽),
// and the community rules shown once per version before creating / joining a circle or commenting (flag circle_rules).
// Everything here is online-only; the server decides what is hidden.

/// The menu items for one item someone else wrote: 「举报 ▸ reason」 and 「屏蔽」. Goes inside a `Menu` or a `.contextMenu`.
struct ModerationItems: View {
  @ObservedObject var store: CirclesStore
  let kind: ReportKind
  let target: Int
  let user: Int
  var post: Int?

  var body: some View {
    Menu {
      ForEach(ReportReason.all, id: \.self) { r in
        Button(ReportReason.label(r)) { Task { await store.report(kind, target, reason: r, post: post) } }
      }
    } label: {
      Label { Text(reportTitle) } icon: { Image(Icon.Name.flag.assetName).renderingMode(.template) }
    }
    Button(role: .destructive) { Task { await store.block(user) } } label: {
      Label { Text(tr("屏蔽此人", "Block this person")) } icon: { Image(Icon.Name.ban.assetName).renderingMode(.template) }
    }
  }

  private var reportTitle: String {
    switch kind {
    case .post: return tr("举报动态", "Report post")
    case .comment: return tr("举报评论", "Report comment")
    case .profile: return tr("举报资料", "Report profile")
    }
  }
}

/// The ⋯ button with `ModerationItems` (post headers, member rows).
struct ModerationMenu<Extra: View>: View {
  @ObservedObject var store: CirclesStore
  let kind: ReportKind
  let target: Int
  let user: Int
  @ViewBuilder var extra: () -> Extra

  var body: some View {
    Menu {
      extra()
      ModerationItems(store: store, kind: kind, target: target, user: user)
    } label: {
      Icon(.ellipsis, size: 18).foregroundColor(Palette.text2).frame(width: 32, height: 32).contentShape(Rectangle())
    }
    .accessibilityLabel(tr("更多", "More"))
  }
}

extension ModerationMenu where Extra == EmptyView {
  init(store: CirclesStore, kind: ReportKind, target: Int, user: Int) {
    self.init(store: store, kind: kind, target: target, user: user, extra: { EmptyView() })
  }
}

/// 我的圈子 → 已屏蔽: who I blocked; unblocking shows them again (and their sharing resumes).
struct BlockedListView: View {
  @ObservedObject var store: CirclesStore

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      if let list = store.blocked {
        if list.isEmpty {
          Text(tr("没有屏蔽任何人", "You haven't blocked anyone"))
            .font(Typo.body).foregroundColor(Palette.text2).frame(maxWidth: .infinity).padding(.vertical, 30)
        } else {
          RowGroup {
            ForEach(Array(list.enumerated()), id: \.element.id) { i, p in
              if i > 0 { RowDivider(inset: 0) }
              HStack(spacing: 10) {
                AvatarView(url: nil, name: p.displayName, size: 34)
                Text(p.displayName).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
                Spacer()
                Button(tr("取消屏蔽", "Unblock")) { Task { await store.unblock(p.id) } }
                  .font(Typo.bodyStrong).foregroundColor(Palette.text)
              }
              .padding(.vertical, 10)
            }
          }
        }
        Text(tr("互相看不到，圈子共享暂停", "Hidden both ways; circle sharing paused"))
          .font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
      } else {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.vertical, 30)
      }
    }
    .padding(.horizontal, Metrics.gutter)
    .task { await store.loadBlocked() }
  }
}

/// Presents the community rules when `store.requireRules(host:)` was called with this host (each screen that can create,
/// join or comment carries its own, so the sheet comes from whatever is on top).
struct RulesGate: ViewModifier {
  @ObservedObject var store: CirclesStore
  let host: String

  func body(content: Content) -> some View {
    content.sheet(isPresented: Binding(get: { store.showRules && store.rulesHost == host },
                                       set: { if !$0 && store.showRules { store.declineRules() } })) {
      CommunityRulesSheet(store: store)
    }
  }
}

extension View {
  func rulesGate(_ store: CirclesStore, host: String) -> some View { modifier(RulesGate(store: store, host: host)) }
}

/// The community rules (GET /v1/friends/rules, Markdown in the app's language) with 「同意」 / 「取消」.
struct CommunityRulesSheet: View {
  @ObservedObject var store: CirclesStore
  @State private var rules: CircleRules?
  @State private var failed = false
  @State private var busy = false

  var body: some View {
    VStack(spacing: 0) {
      ScrollView(.vertical, showsIndicators: true) {
        VStack(alignment: .leading, spacing: 10) {
          if let rules {
            MarkdownBlocks(markdown: rules.markdown)
          } else if failed {
            Text(tr("规则没加载出来，检查网络后再试", "Couldn't load the rules; check the connection"))
              .font(Typo.body).foregroundColor(Palette.text2).padding(.vertical, 40)
          } else {
            ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.vertical, 40)
          }
        }
        .padding(.horizontal, Metrics.gutter).padding(.top, 24).padding(.bottom, 16)
      }
      VStack(spacing: 10) {
        PrimaryButton(title: tr("我同意", "I agree"), busy: busy) {
          guard let v = rules?.version else { return }
          Task {
            busy = true
            _ = await store.acceptRules(v)
            busy = false
          }
        }
        .disabled(rules == nil)
        Button(tr("暂不", "Not now")) { store.declineRules() }
          .font(Typo.bodyStrong).foregroundColor(Palette.text2).frame(minHeight: 40)
      }
      .padding(.horizontal, Metrics.gutter).padding(.vertical, 12)
      .background(Palette.sheet)
    }
    .background(Palette.sheet.ignoresSafeArea())
    .presentationDragIndicator(.visible)
    .interactiveDismissDisabled(busy)
    .overlay(alignment: .top) { ToastOverlay(store: store) }
    .task {
      rules = await store.loadRules()
      failed = rules == nil
    }
  }
}

/// A small Markdown renderer for the rules text: `#` / `##` headings, `- ` bullets, paragraphs; inline **bold** via
/// AttributedString. Enough for docs/legal/community-rules*.md.
struct MarkdownBlocks: View {
  let markdown: String

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      ForEach(Array(Self.blocks(markdown).enumerated()), id: \.offset) { _, b in
        switch b.kind {
        case .h1: Text(Self.inline(b.text)).font(.system(size: 24, weight: .bold)).foregroundColor(Palette.text).padding(.bottom, 4)
        case .h2: Text(Self.inline(b.text)).font(Typo.bodyStrong).foregroundColor(Palette.text).padding(.top, 8)
        case .bullet:
          HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\u{2022}").foregroundColor(Palette.text2)
            Text(Self.inline(b.text)).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
          }
        case .para: Text(Self.inline(b.text)).font(Typo.body).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
        }
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  enum Kind { case h1, h2, bullet, para }
  struct Block: Equatable { let kind: Kind; var text: String }

  /// Lines → blocks; a line that continues a bullet or paragraph (no blank line between) is joined to it.
  static func blocks(_ md: String) -> [Block] {
    var out: [Block] = []
    var open = false
    for raw in md.components(separatedBy: "\n") {
      let line = raw.trimmingCharacters(in: .whitespaces)
      if line.isEmpty { open = false; continue }
      if line.hasPrefix("## ") { out.append(Block(kind: .h2, text: String(line.dropFirst(3)))); open = false }
      else if line.hasPrefix("# ") { out.append(Block(kind: .h1, text: String(line.dropFirst(2)))); open = false }
      else if line.hasPrefix("- ") { out.append(Block(kind: .bullet, text: String(line.dropFirst(2)))); open = true }
      else if open, let last = out.last, last.kind == .bullet || last.kind == .para {
        out[out.count - 1].text += (line.first?.isASCII == true ? " " : "") + line
      } else { out.append(Block(kind: .para, text: line)); open = true }
    }
    return out
  }

  static func inline(_ s: String) -> AttributedString {
    (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(s)
  }
}
