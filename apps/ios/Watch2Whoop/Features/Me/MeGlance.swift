import SwiftUI

/// What the AI face of 我 shows: the AI in use, and the fallback AI's allowance left this month when that is the one.
struct MeAIFace: Equatable {
  /// The AI in use (its label); nil = none chosen.
  var name: String?
  var fallback = false
  /// The fallback AI's allowance left this month, 0–100 (`/ai-providers` → fallback.remaining_pct).
  var remainingPct: Int?
  /// The fallback AI's money this month (2.1.0: `spent_cny` / `quota_cny` of the ai-choice option or of fallback); both
  /// or neither, so the face shows 「¥1.2 / ¥3」 or the percentage of an older server.
  var spentCny: Double? = nil
  var quotaCny: Double? = nil

  /// Pure (unit-tested in `MeGlanceTests`).
  static func of(choice: AIChoice?, list: AIProviderList?) -> MeAIFace? {
    guard let choice else { return nil }
    guard let cur = choice.options.first(where: { $0.current }) else { return MeAIFace() }
    guard cur.key == "fallback" else { return MeAIFace(name: cur.label, fallback: false, remainingPct: nil) }
    var face = MeAIFace(name: cur.label, fallback: true, remainingPct: list?.fallback?.remainingPct)
    let spent = cur.spentCny ?? list?.fallback?.spentCny
    let quota = cur.quotaCny ?? list?.fallback?.quotaCny
    if let spent, let quota, quota > 0 {
      face.spentCny = spent
      face.quotaCny = quota
    }
    return face
  }

  /// 「¥1.2」, 「¥3」 (one decimal, none when whole).
  static func money(_ v: Double) -> String {
    let r = (v * 10).rounded() / 10
    if r == r.rounded() { return "¥\(Int(r))" }
    return String(format: "¥%.1f", r)
  }

  /// Little left (under a fifth): the gauge turns `mid`.
  var low: Bool {
    if let spentCny, let quotaCny { return quotaCny - spentCny < quotaCny * 0.2 }
    return (remainingPct ?? 100) < 20
  }
}

/// What the 我 first glance asks of the page around it.
struct MeGlanceActions {
  var profile: () -> Void
  var syncCenter: () -> Void
  var syncNow: () -> Void
  var inbox: () -> Void
  var ai: () -> Void
  /// 共享给我的 (the people who share with me).
  var shared: () -> Void
  /// 谁能看我 (2.1.0 `audience`).
  var audience: () -> Void = {}
}

/// The first glance of 我 (docs/design/2.0.1 `07-me`): avatar + name (tap = profile) and the sync capsule, the sync dial
/// (last 30 days, missing days in `mid`; tap = sync center), one row of faces 消息 · AI · 共享. The rows of 我 follow under it.
struct MeGlance: View {
  let session: AccountSession
  @ObservedObject var inbox: InboxStore
  let run: MeGlanceActions
  @State private var missing: MissingDays?
  @State private var ai: MeAIFace?

  var body: some View {
    VStack(spacing: 0) {
      header
      dial.padding(.top, Rhythm.gap(32))
      faces.padding(.top, Rhythm.gap(40))
    }
    .task { await load() }
  }

  private var header: some View {
    HStack(spacing: 8) {
      Button(action: run.profile) {
        HStack(spacing: 10) {
          Text(String((session.displayName ?? "?").prefix(1)).uppercased())
            .font(.system(size: 15, weight: .semibold)).foregroundColor(Palette.text)
            .frame(width: 30, height: 30)
            .background(Circle().fill(Palette.surface2))
          Text(session.displayName ?? tr("我的账号", "My account")).font(Typo.header).foregroundColor(Palette.text).lineLimit(1)
          Icon(.chevronRight, size: 13).foregroundColor(Palette.text3)
        }
        .frame(minHeight: 44)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("打开个人资料", "Open your profile"))
      Spacer(minLength: 0)
      TodaySyncCapsule(card: nil, page: session.sync, tap: run.syncNow, center: run.syncCenter)
    }
    .frame(height: 44)
  }

  private var dial: some View {
    let days = missing.map { SyncDays.of($0, fallbackEnd: LocalDay.add(LocalDay.string(Date(), .current), -1)) }
    let side = TodayGlancePage.dialSide
    return Button(action: run.syncCenter) {
      SyncDaysDial(days: days, side: side) {
        VStack(spacing: 0) {
          Text(tr("同步天数", "Days synced")).font(.system(size: 13, weight: .medium)).tracking(2).foregroundColor(Palette.text2)
          if let days, days.counted > 0 {
            Text("\(days.synced)").font(Typo.hero(100 * side / 280)).tracking(-3).foregroundColor(Palette.text)
              .lineLimit(1).minimumScaleFactor(0.6)
              .overlay(alignment: .topTrailing) {
                Text("/\(days.counted)").font(.system(size: 20, weight: .light).monospacedDigit()).foregroundColor(Palette.text2)
                  .fixedSize().offset(x: 34, y: 12)
              }
          } else {
            Rectangle().fill(Palette.text2).frame(width: 40, height: 2).padding(.vertical, 40)
          }
          if let days {
            Text(SyncGlanceRules.verdict(days)).font(Typo.verdict).foregroundColor(Palette.text).padding(.top, 10)
          }
        }
      }
    }
    .buttonStyle(PressScale())
    .frame(maxWidth: .infinity)
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(dialLabel(days))
    .accessibilityAddTraits(.isButton)
  }

  private func dialLabel(_ d: SyncDays?) -> String {
    guard let d, d.counted > 0 else { return tr("同步天数 —", "Days synced —") }
    return tr("同步天数 \(d.synced)/\(d.counted)，\(SyncGlanceRules.verdict(d))", "Days synced \(d.synced) of \(d.counted), \(SyncGlanceRules.verdict(d))")
  }

  private var faces: some View {
    HStack(spacing: 8) {
      if inbox.on {
        Button(action: run.inbox) {
          // 2.1.0: who the unread are from (`unread_from`); none from an older server
          Face(icon: .inbox, label: tr("消息", "Messages"), number: "\(inbox.unread)", unit: tr("条未读", "unread"),
               dot: inbox.unread > 0 ? .bad : nil) { FaceRow(people: inbox.unreadFaces, max: 3) }
        }
        .buttonStyle(PressScale())
      }
      if let ai {
        Button(action: run.ai) {
          if let spent = ai.spentCny, let quota = ai.quotaCny {
            Face(icon: .orbit, label: tr("AI 额度", "AI allowance"), number: MeAIFace.money(spent), unit: "/ \(MeAIFace.money(quota))") {
              BulletGauge(value: min(spent, quota), max: quota, step: quota / 4, majors: [quota / 2], tone: ai.low ? .mid : .text)
            }
          } else if let pct = ai.remainingPct {
            Face(icon: .orbit, label: tr("AI 额度", "AI allowance"), number: "\(pct)", unit: tr("% 剩余", "% left")) {
              BulletGauge(value: Double(pct), max: 100, step: 25, majors: [50], tone: pct < 20 ? .mid : .text)
            }
          } else {
            Face(icon: .orbit, label: "AI", number: ai.name ?? tr("未设置", "Not set"), dimmed: ai.name == nil) { Color.clear }
          }
        }
        .buttonStyle(PressScale())
      }
      if let a = session.audience {
        // 2.1.0 (`07-me`): who can see me, their faces; 共享给我的 stays a row of 我 below
        Button(action: run.audience) {
          Face(icon: .users, label: tr("共享", "Sharing"), number: "\(a.count)", unit: tr("人可看", "can see"), dimmed: a.count == 0) {
            FaceRow(people: a.people, max: 5)
          }
        }
        .buttonStyle(PressScale())
      } else if !session.viewable.isEmpty {
        Button(action: run.shared) {
          Face(icon: .users, label: tr("共享", "Shared"), number: "\(session.viewable.count)", unit: tr("人给我看", "people")) {
            HStack(spacing: -6) {
              ForEach(session.viewable.prefix(4)) { p in
                AvatarView(url: p.avatar, name: p.shownName, size: 24).overlay(Circle().stroke(Palette.surface, lineWidth: 1.5))
              }
            }
          }
        }
        .buttonStyle(PressScale())
      }
    }
  }

  private func load() async {
    if let j = try? await session.api.json(.get(Endpoint.syncMissingDays)) { missing = MissingDays(j) }
    let choice = try? await session.api.send(.get(Endpoint.aiChoice), as: AIChoice.self)
    var list: AIProviderList?
    if let cur = choice?.options.first(where: { $0.current }), cur.key == "fallback", cur.spentCny == nil {
      list = try? await session.api.send(.get(Endpoint.aiProviders), as: AIProviderList.self)
    }
    ai = MeAIFace.of(choice: choice, list: list)
  }
}
