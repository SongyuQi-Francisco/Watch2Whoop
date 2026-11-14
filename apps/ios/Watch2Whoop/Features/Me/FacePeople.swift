import SwiftUI

/// A face on a 2.1.0 first-glance tile (PR #452): who can see my data (`/v1/web/me` → `audience`), whom my unread messages
/// are from (`/inbox` → `unread_from`) and whom the circle feed's unread likes and comments are from (`/feed/unread` →
/// `from`). A picture when the server gives one, else the initial; `system` = a message from the app itself (its icon).
struct FacePerson: Equatable, Identifiable {
  var id: String
  var name: String
  var initial: String
  var avatar: String?
  var system = false
  /// What they may see (`scopes`, audience only).
  var scopes: [String] = []

  /// One entry of the server's lists; nil for what cannot be drawn (no name, no initial, not a system message).
  static func parse(_ j: [String: Any], index: Int) -> FacePerson? {
    let type = j["type"] as? String
    if type == "system" { return FacePerson(id: "system.\(index)", name: tr("系统消息", "System message"), initial: "", system: true) }
    let name = (j["name"] as? String) ?? ""
    let initial = (j["initial"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? (name.isEmpty ? nil : AvatarView.initial(name))
    guard let initial else { return nil }
    let key = (j["id"] as? NSNumber).map { "\(type ?? "person").\($0)" } ?? "\(type ?? "person").\(index).\(name)"
    return FacePerson(id: key, name: name.isEmpty ? initial : name, initial: initial, avatar: j["avatar"] as? String,
                      scopes: (j["scopes"] as? [String]) ?? [])
  }

  static func list(_ v: Any?) -> [FacePerson]? {
    guard let a = v as? [[String: Any]] else { return nil }
    return a.enumerated().compactMap { parse($0.element, index: $0.offset) }
  }
}

/// `audience`: everyone who can see my data now; `people` holds at most a few faces (`more` = the rest).
struct Audience: Equatable {
  var count: Int
  var people: [FacePerson]

  static func parse(_ v: Any?) -> Audience? {
    guard let j = v as? [String: Any], let count = (j["count"] as? NSNumber)?.intValue else { return nil }
    return Audience(count: count, people: FacePerson.list(j["people"]) ?? [])
  }
}

/// Overlapping faces at the bottom of a first-glance tile (`07-me`, `06-circles`).
struct FaceRow: View {
  let people: [FacePerson]
  var max = 5
  var size: CGFloat = 24

  var body: some View {
    HStack(spacing: -size * 0.25) {
      ForEach(people.prefix(max)) { p in
        face(p).overlay(Circle().stroke(Palette.surface, lineWidth: 1.5))
      }
    }
    .accessibilityHidden(true)
  }

  @ViewBuilder private func face(_ p: FacePerson) -> some View {
    if p.system {
      Icon(.sparkles, size: size * 0.5).foregroundColor(Palette.text2)
        .frame(width: size, height: size)
        .background(Circle().fill(Palette.surface2))
    } else {
      AvatarView(url: p.avatar, name: p.initial, size: size)
    }
  }
}

/// 「谁能看我」: everyone who can see my data now and what (`GET /v1/users/me/audience`), pushed from the 共享 face of 我.
/// Shares are changed where they are made (the circles, the person's page); this list only shows them.
struct AudiencePage: View {
  @EnvironmentObject private var session: AccountSession
  @State private var audience: Audience?
  @State private var failed: String?

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 14) {
        if let a = audience {
          if a.people.isEmpty {
            EmptyNote(text: tr("现在没有人能看你的数据", "Nobody can see your data right now"))
          } else {
            RowGroup {
              ForEach(Array(a.people.enumerated()), id: \.element.id) { n, p in
                if n > 0 { RowDivider() }
                row(p)
              }
            }
          }
        } else if let failed {
          RetryNote(text: failed) { Task { await load() } }
        } else {
          EmptyNote(text: "…")
        }
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.top, 8)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(tr("谁能看我", "Who can see me"))
    .navigationBarTitleDisplayMode(.inline)
    .task { await load() }
  }

  private func row(_ p: FacePerson) -> some View {
    HStack(spacing: 12) {
      AvatarView(url: p.avatar, name: p.initial, size: 36)
      VStack(alignment: .leading, spacing: 3) {
        Text(p.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
        if !p.scopes.isEmpty {
          Text(CircleScope.list(p.scopes)).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(2)
        }
      }
      Spacer(minLength: 0)
    }
    .padding(.vertical, 10)
    .accessibilityElement(children: .combine)
  }

  private func load() async {
    do {
      let j = try await session.api.json(.get(Endpoint.audience))
      audience = Audience.parse(j)
      failed = audience == nil ? tr("暂无", "None") : nil
    } catch {
      failed = (error as? APIError)?.userMessage ?? tr("出错了", "Something went wrong")
    }
  }
}
