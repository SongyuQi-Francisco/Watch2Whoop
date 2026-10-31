import SwiftUI
import UIKit

/// Invite friends to a circle I own: pick how long the code works (1 / 3 / 7 / 30 days or never), get the code, then
/// copy it, share the link, or share a picture card (WeChat / Messages / AirDrop through the system share sheet). The live
/// codes are listed with 「作废」 (a code already used keeps its members).
struct InviteView: View {
  @ObservedObject var store: CirclesStore
  let circleId: Int
  /// A code just made (a new circle comes with one).
  @State var minted: MintedInvite?
  @State private var days: Int = 3
  @State private var busy = false
  @State private var share: [Any]?
  @State private var copied = false
  @State private var confirmRevoke: FriendCircle.LiveInvites.Code?

  private var circle: FriendCircle? { store.list?.circles.first { $0.id == circleId } }

  var body: some View {
    let name = circle?.name ?? ""
    VStack(alignment: .leading, spacing: 18) {
      if let minted {
        codeCard(minted, name: name)
      }
      VStack(alignment: .leading, spacing: 10) {
        SectionHeader(title: tr("有效期", "Valid for"))
        HStack(spacing: 6) {
          ForEach((store.list?.limits.inviteDays ?? [1, 3, 7, 30]) + [0], id: \.self) { d in
            PillButton(title: d == 0 ? tr("永久", "Forever") : (d == 1 ? tr("1 天", "1 day") : tr("\(d) 天", "\(d) days")), on: days == d) { days = d }
          }
        }
        PrimaryButton(title: minted == nil ? tr("生成邀请", "Create invite") : tr("再生成一个", "Create another"), busy: busy) {
          Task {
            busy = true
            if let m = await store.mintInvite(circleId, days: days == 0 ? nil : days) {
              withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) { minted = m }
              UINotificationFeedbackGenerator().notificationOccurred(.success)
            }
            busy = false
          }
        }
        .padding(.top, 4)
      }
      if let codes = circle?.invites?.codes, !codes.isEmpty {
        RowGroup(title: tr("\(codes.count) 个邀请有效", "\(codes.count) live invites")) {
          ForEach(Array(codes.enumerated()), id: \.element.id) { i, c in
            if i > 0 { RowDivider(inset: 0) }
            HStack {
              VStack(alignment: .leading, spacing: 2) {
                Text(FeedFormat.timeLeft(c.expiresAt)).font(Typo.body).foregroundColor(Palette.text)
                if c.usedCount > 0 {
                  Text(tr("已用 \(c.usedCount) 次", "Used \(c.usedCount) times")).font(Typo.caption).foregroundColor(Palette.text2)
                }
              }
              Spacer()
              Button(tr("作废", "Revoke")) { confirmRevoke = c }
                .font(.system(size: 14, weight: .medium)).foregroundColor(Palette.bad)
            }
            .padding(.vertical, 10)
          }
        }
      }
    }
    .onAppear { days = store.list?.limits.inviteDefaultDays ?? 3 }
    .sheet(isPresented: Binding(get: { share != nil }, set: { if !$0 { share = nil } })) {
      ActivityView(items: share ?? []).presentationDetents([.medium, .large])
    }
    .confirmationDialog(tr("作废这个邀请？", "Revoke this invite?"), isPresented: Binding(get: { confirmRevoke != nil }, set: { if !$0 { confirmRevoke = nil } }), titleVisibility: .visible) {
      Button(tr("作废", "Revoke"), role: .destructive) {
        if let c = confirmRevoke { Task { await store.revokeInvite(circleId, c.id) } }
      }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      Text(tr("已发出的链接立即失效，已经加入的人不受影响。", "Links already sent stop working; people who joined stay."))
    }
  }

  private func codeCard(_ m: MintedInvite, name: String) -> some View {
    VStack(spacing: 14) {
      InviteCard(circle: name, code: m.code, expires: m.expiresAt, inviter: store.me.name)
      HStack(spacing: 10) {
        SecondaryButton(icon: .copy, title: copied ? tr("已复制", "Copied") : tr("复制邀请码", "Copy code")) {
          UIPasteboard.general.string = m.code
          UINotificationFeedbackGenerator().notificationOccurred(.success)
          withAnimation { copied = true }
          DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) { withAnimation { copied = false } }
        }
        SecondaryButton(icon: .share, title: tr("分享", "Share")) { share = shareItems(m, name: name) }
      }
    }
    .transition(.scale(scale: 0.95).combined(with: .opacity))
  }

  /// The card as a picture, the text with the code, and the link (the Today page's join link; the app opens it too).
  private func shareItems(_ m: MintedInvite, name: String) -> [Any] {
    var items: [Any] = []
    let r = ImageRenderer(content: InviteCard(circle: name, code: m.code, expires: m.expiresAt, inviter: store.me.name)
      .frame(width: 340).padding(20).background(Palette.bg))
    r.scale = 3
    if let img = r.uiImage { items.append(img) }
    var text = InviteShare.text(circle: name, code: m.code)
    if let link = InviteShare.webLink(server: store.session.config.server, code: m.code) { text += " \(link.absoluteString)" }
    items.append(text)
    return items
  }
}

/// The invite as a card (also the picture that is shared): the circle, who invites, the code, how long it works.
struct InviteCard: View {
  let circle: String
  let code: String
  let expires: String?
  let inviter: String

  var body: some View {
    VStack(spacing: 10) {
      HStack(spacing: 6) {
        Icon(.users, size: 16)
        Text("Watch2Whoop").font(.system(size: 13, weight: .semibold))
      }
      .foregroundColor(Palette.text2)
      Text(tr("\(inviter) 邀请你加入", "\(inviter) invites you to"))
        .font(Typo.caption).foregroundColor(Palette.text2)
      Text(circle).font(.system(size: 22, weight: .bold)).foregroundColor(Palette.text).multilineTextAlignment(.center)
      Text(code)
        .font(.system(size: 28, weight: .semibold, design: .monospaced)).tracking(1.5)
        .foregroundColor(Palette.text)
        .padding(.vertical, 10).padding(.horizontal, 18)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.bg))
        .textSelection(.enabled)
      Text(expires == nil ? tr("永久有效", "Never expires") : FeedFormat.timeLeft(expires))
        .font(.system(size: 12)).foregroundColor(Palette.text3)
      Text(tr("打开 Watch2Whoop → 朋友 → 用邀请码加入", "Open Watch2Whoop → Friends → Join with a code"))
        .font(.system(size: 12)).foregroundColor(Palette.text2).multilineTextAlignment(.center)
    }
    .frame(maxWidth: .infinity)
    .padding(.vertical, 22).padding(.horizontal, 16)
    .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Palette.surface))
  }
}

/// The system share sheet.
struct ActivityView: UIViewControllerRepresentable {
  let items: [Any]
  func makeUIViewController(context: Context) -> UIActivityViewController {
    UIActivityViewController(activityItems: items, applicationActivities: nil)
  }
  func updateUIViewController(_ c: UIActivityViewController, context: Context) {}
}

/// Join a circle: a code (typed, pasted, or from a link), then what to share with its members.
struct JoinView: View {
  @ObservedObject var store: CirclesStore
  @State var code: String
  /// An invite addressed to me (no code needed).
  var invite: InboxInvite?
  let done: () -> Void
  @State private var preview: InvitePreview?
  @State private var scopes: Set<String> = Set(CircleScope.defaults)
  @State private var busy = false
  @State private var error: String?
  @State private var gateId = "join:\(UUID().uuidString)"

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      if store.list?.consentOk == false {
        Banner(text: tr("同意书更新了，先在「我」里确认", "The consent was updated: confirm it under Me first"), tone: .mid)
      }
      if let p = preview {
        HStack(spacing: 12) {
          AvatarView(url: nil, name: p.name, size: 48)
          VStack(alignment: .leading, spacing: 2) {
            Text(p.name).font(.system(size: 18, weight: .bold)).foregroundColor(Palette.text)
            if let o = p.ownerName {
              Text(tr("\(o) 的圈子 · \(p.members) 人", "\(o)'s circle · \(p.members) people")).font(Typo.caption).foregroundColor(Palette.text2)
            }
          }
        }
        Text(tr("给圈子里的人看", "What its members see")).font(Typo.bodyStrong).foregroundColor(Palette.text)
        ScopePicker(all: store.list?.scopes ?? CircleScope.all, defaults: store.list?.defaultScopes ?? CircleScope.defaults, chosen: $scopes)
        PrimaryButton(title: tr("加入", "Join"), busy: busy) {
          store.requireRules(host: gateId) {   // the community rules first, once (flag circle_rules)
            Task {
              busy = true
              let ok = await store.join(code: invite == nil ? code : nil, inviteId: invite?.id, scopes: Array(scopes))
              busy = false
              if ok {
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                store.toast = tr("已加入", "Joined")
                done()
              }
            }
          }
        }
        .rulesGate(store, host: gateId)
      } else {
        HStack(spacing: 8) {
          TextField("XXXXX-XXXXX", text: $code)
            .textInputAutocapitalization(.characters).autocorrectionDisabled()
            .font(.system(size: 20, weight: .semibold, design: .monospaced))
            .padding(.horizontal, 14).frame(minHeight: 52)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.inSheet))
            .submitLabel(.next)
            .onSubmit { Task { await look() } }
          PasteButton(payloadType: String.self) { strings in
            if let s = strings.first, let c = InviteCode.extract(s) {
              code = c
              Task { await look() }
            }
          }
          .labelStyle(.iconOnly)
          .buttonBorderShape(.capsule)
          .tint(Palette.text)
        }
        Text(tr("可以粘贴朋友发来的整段话或链接", "Paste the whole message or link your friend sent"))
          .font(Typo.caption).foregroundColor(Palette.text2)
        if let error { Banner(text: error, tone: .bad) }
        PrimaryButton(title: tr("下一步", "Next"), busy: busy) { Task { await look() } }
      }
    }
    .task {
      if let invite {
        var p = InvitePreview(name: invite.name)
        p.ownerName = invite.fromName
        p.members = invite.members
        p.defaultScopes = invite.defaultScopes
        scopes = Set(invite.defaultScopes)
        preview = p
      } else if InviteCode.extract(code) != nil {
        await look()
      }
    }
  }

  private func look() async {
    guard let c = InviteCode.extract(code) else {
      error = tr("请输入邀请码", "Enter an invite code")
      return
    }
    code = c
    busy = true
    defer { busy = false }
    switch await store.preview(code: c) {
    case .success(let p):
      error = nil
      if let d = p.defaultScopes { scopes = Set(d) }
      withAnimation(.easeOut(duration: 0.2)) { preview = p }
    case .failure(let e):
      if case .notFound = e { error = tr("邀请码无效或已过期", "That invite code is not valid or has expired") } else { error = e.userMessage }
    }
  }
}

/// What to share with a circle: the recommended four first, the rest under 「可选（适合家人）」; ECGs never.
struct ScopePicker: View {
  let all: [String]
  let defaults: [String]
  @Binding var chosen: Set<String>

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      group(tr("推荐", "Recommended"), all.filter { defaults.contains($0) })
      group(tr("可选（适合家人）", "Optional (good for family)"), all.filter { !defaults.contains($0) })
      Text(tr("心电图永远不进圈子", "ECGs never go to a circle")).font(Typo.caption).foregroundColor(Palette.text2)
    }
  }

  private func group(_ title: String, _ scopes: [String]) -> some View {
    RowGroup(title: title) {
      ForEach(Array(scopes.enumerated()), id: \.element) { i, s in
        if i > 0 { RowDivider(inset: 0) }
        ToggleRow(title: CircleScope.label(s), isOn: Binding(get: { chosen.contains(s) }, set: { on in
          if on { chosen.insert(s) } else { chosen.remove(s) }
        }))
      }
    }
  }
}
