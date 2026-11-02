import SwiftUI
import UIKit

// Inviting someone I already know into another circle of mine (#359 section 3; server: friends/direct.py, flag `inbox`). Only
// from a member card, so only people who already share a circle with me; the server answers 404 for anyone else.

extension Endpoint.Circles {
  static func inviteOptions(_ person: Int) -> String { "/v1/friends/people/\(person)/invite-options" }
  static func invitePerson(_ person: Int) -> String { "/v1/friends/people/\(person)/invites" }
  static let inviteMutes = "/v1/friends/invite-mutes"
  static func inviteMute(_ person: Int) -> String { "/v1/friends/invite-mutes/\(person)" }
}

/// `GET /v1/friends/people/{id}/invite-options`: the circles I own and where this person stands in each.
struct InviteOptions: Decodable, Equatable {
  var circles: [Option] = []
  var perDay = 0
  var leftToday = 0

  struct Option: Decodable, Equatable, Identifiable {
    let id: Int
    var name = ""
    var avatar: String?
    var members = 0
    /// open / pending / accepted / member / full. A declined invite stays `pending` until it expires: nobody sees a no.
    var status = "open"

    enum CodingKeys: String, CodingKey { case id, name, avatar, members, status }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      id = try c.decode(Int.self, forKey: .id)
      name = c.opt(.name) ?? ""
      avatar = c.opt(.avatar)
      members = c.opt(.members) ?? 0
      status = c.opt(.status) ?? "open"
    }
  }

  enum CodingKeys: String, CodingKey { case circles, perDay = "per_day", leftToday = "left_today" }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    circles = c.opt(.circles) ?? []
    perDay = c.opt(.perDay) ?? 0
    leftToday = c.opt(.leftToday) ?? 0
  }
}

/// Someone whose invites I turned off.
struct InviteMute: Decodable, Equatable, Identifiable {
  let id: Int
  var displayName = ""

  enum CodingKeys: String, CodingKey { case id, displayName = "display_name" }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(Int.self, forKey: .id)
    displayName = c.opt(.displayName) ?? ""
  }
}

extension CirclesStore {
  var inboxOn: Bool { session.flags.contains(InboxStore.flag) }

  func inviteOptions(_ person: Int) async -> InviteOptions? {
    do {
      return try await session.api.send(.get(Endpoint.Circles.inviteOptions(person)), as: InviteOptions.self)
    } catch let e as APIError {
      toast = e.userMessage
    } catch {}
    return nil
  }

  /// The invite; the status the person now has in that circle. nil = not sent, the toast says why.
  func invite(_ person: Int, to circle: Int) async -> String? {
    struct Answer: Decodable { var status: String }
    do {
      let a = try await session.api.send(try .json(.post, Endpoint.Circles.invitePerson(person), ["circle_id": circle], idempotent: true), as: Answer.self)
      return a.status
    } catch APIError.rateLimited {
      toast = tr("今天的邀请发完了，明天再试", "No invites left today; try again tomorrow")
    } catch let e as APIError {
      toast = e.userMessage
    } catch {}
    return nil
  }

  func inviteMutes() async -> [InviteMute] {
    struct Answer: Decodable { var people: [InviteMute] }
    return (try? await session.api.send(.get(Endpoint.Circles.inviteMutes), as: Answer.self).people) ?? []
  }

  func unmuteInvites(_ person: Int) async -> Bool {
    do {
      _ = try await session.api.data(APIRequest(method: .delete, path: Endpoint.Circles.inviteMute(person)))
      return true
    } catch let e as APIError {
      toast = e.userMessage
    } catch {}
    return false
  }
}

/// 邀请进我的另一个圈子: my circles, with one button per circle. 待处理 / 已接受 say where it stands.
struct DirectInviteSheet: View {
  @ObservedObject var store: CirclesStore
  let person: Int
  let name: String
  @Environment(\.dismiss) private var dismiss
  @State private var options: InviteOptions?
  @State private var failed = false
  @State private var sending: Int?

  var body: some View {
    SheetContainer(title: tr("邀请 \(name)", "Invite \(name)"), close: { dismiss() }) {
      VStack(alignment: .leading, spacing: 12) {
        if let o = options {
          if o.circles.isEmpty {
            Text(tr("你还没有自己的圈子", "You don't have a circle of your own yet"))
              .font(Typo.body).foregroundColor(Palette.text2).frame(maxWidth: .infinity).padding(.vertical, 24)
          } else {
            RowGroup {
              ForEach(Array(o.circles.enumerated()), id: \.element.id) { i, c in
                if i > 0 { RowDivider(inset: 0) }
                row(c, canSend: o.leftToday > 0)
              }
            }
            Text(tr("今天还能邀请 \(o.leftToday) 次", "\(o.leftToday) invites left today"))
              .font(Typo.caption).foregroundColor(Palette.text2)
          }
        } else if failed {
          Banner(text: tr("打不开，稍后再试", "Couldn't open this; try again later"), tone: .bad)
        } else {
          ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.vertical, 30)
        }
      }
    }
    .presentationDetents([.medium, .large])
    .presentationDragIndicator(.visible)
    .overlay(alignment: .top) { ToastOverlay(store: store) }
    .task {
      options = await store.inviteOptions(person)
      failed = options == nil
    }
  }

  private func row(_ c: InviteOptions.Option, canSend: Bool) -> some View {
    HStack(spacing: 10) {
      AvatarView(url: c.avatar, name: c.name, size: 34)
      VStack(alignment: .leading, spacing: 1) {
        Text(c.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
        Text(tr("\(c.members) 人", "\(c.members) people")).font(Typo.label).foregroundColor(Palette.text2)
      }
      Spacer(minLength: 6)
      if c.status == "open" {
        Button {
          Task { await send(c) }
        } label: {
          Group {
            if sending == c.id { ProgressView().tint(Palette.bg) } else { Text(tr("邀请", "Invite")).font(Typo.bodyStrong) }
          }
          .foregroundColor(Palette.bg)
          .frame(minWidth: 64, minHeight: 34)
          .background(Capsule().fill(canSend ? Palette.text : Palette.text3))
        }
        .buttonStyle(PressScale())
        .disabled(!canSend || sending != nil)
      } else {
        Text(Self.label(c.status)).font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
    .padding(.vertical, 10)
  }

  private func send(_ c: InviteOptions.Option) async {
    sending = c.id
    defer { sending = nil }
    guard await store.invite(person, to: c.id) != nil else { return }
    UINotificationFeedbackGenerator().notificationOccurred(.success)
    options = await store.inviteOptions(person) ?? options
  }

  static func label(_ status: String) -> String {
    switch status {
    case "pending": return tr("待处理", "Pending")
    case "accepted": return tr("已接受", "Accepted")
    case "member": return tr("已在圈子里", "Already in")
    case "full": return tr("圈子已满", "Full")
    default: return status
    }
  }
}

/// 已屏蔽 → people whose invites I turned off, one tap to take them back. Hidden while the list is empty.
struct InviteMutesSection: View {
  @ObservedObject var store: CirclesStore
  @State private var people: [InviteMute] = []

  var body: some View {
    Group {
      if !people.isEmpty {
        RowGroup(title: tr("不接收邀请", "No invites from")) {
          ForEach(Array(people.enumerated()), id: \.element.id) { i, p in
            if i > 0 { RowDivider(inset: 0) }
            HStack(spacing: 10) {
              AvatarView(url: nil, name: p.displayName, size: 34)
              Text(p.displayName).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
              Spacer()
              Button(tr("恢复", "Restore")) {
                Task {
                  if await store.unmuteInvites(p.id) { people.removeAll { $0.id == p.id } }
                }
              }
              .font(Typo.bodyStrong).foregroundColor(Palette.text)
            }
            .padding(.vertical, 10)
          }
        }
      }
    }
    .task { if store.inboxOn { people = await store.inviteMutes() } }
  }
}
