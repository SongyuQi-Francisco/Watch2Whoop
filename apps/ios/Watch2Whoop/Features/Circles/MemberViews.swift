import SwiftUI
import UIKit

/// Member cards (friends/cards.py): my card first ("what friends see"), then every co-member: today's recovery / sleep /
/// strain, "trained", the last 7 days of recovery, when data last came in; a block the person does not share with me is
/// not drawn, just marked 「未共享」. Cheers 👏 💤 💪 once a day each.
struct MemberList: View {
  @ObservedObject var store: CirclesStore
  @State private var remarkFor: MemberCard?
  @State private var remark = ""

  var body: some View {
    VStack(spacing: 12) {
      if let o = store.overview {
        ForEach(o.everyone) { m in
          MemberCardView(member: m, kinds: o.nudgeKinds, store: store) {
            remark = m.remark ?? ""
            remarkFor = m
          }
        }
      } else {
        ProgressView().tint(Palette.text2).padding(.vertical, 40)
      }
    }
    .alert(tr("备注名", "Nickname"), isPresented: Binding(get: { remarkFor != nil }, set: { if !$0 { remarkFor = nil } })) {
      TextField(remarkFor?.displayName ?? "", text: $remark)
      Button(tr("保存", "Save")) {
        if let m = remarkFor { Task { await store.setRemark(m.id, String(remark.prefix(store.overview?.remarkMax ?? 20))) } }
      }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      Text(tr("只有你看得到", "Only you see it"))
    }
  }
}

struct MemberCardView: View {
  let member: MemberCard
  let kinds: [String]
  @ObservedObject var store: CirclesStore
  let editRemark: () -> Void
  @State private var day: MemberCard.Scores.Day?

  var body: some View {
    let m = member
    let sc = m.isHidden("scores") ? nil : m.scores
    VStack(alignment: .leading, spacing: 12) {
      HStack(spacing: 10) {
        AvatarView(url: m.avatar, name: m.displayName, size: 40)
        VStack(alignment: .leading, spacing: 2) {
          Text(m.isSelf ? tr("我", "Me") : m.shownName).font(.system(size: 16, weight: .semibold)).foregroundColor(Palette.text)
            .lineLimit(1)
          if m.isSelf {
            Text(tr("朋友看到的你", "What friends see")).font(.system(size: 12)).foregroundColor(Palette.text2)
          } else if let r = m.remark, !r.isEmpty {
            Text(m.displayName).font(.system(size: 12)).foregroundColor(Palette.text2).lineLimit(1)
          }
        }
        Spacer(minLength: 4)
        hero(sc)
        if !m.isSelf {
          ModerationMenu(store: store, kind: .profile, target: m.id, user: m.id) {
            if store.remarksOn { Button(tr("设置备注名", "Set a nickname"), action: editRemark) }
          }
        }
      }
      if let sc, sc.state == "scored" {
        HStack(spacing: 0) {
          number(sc.recovery.map { "\(Int($0.rounded()))" }, tr("恢复", "Recovery"), tone: Tone(zone: sc.zone))
          number(sc.sleepMin.map(FeedFormat.sleep), tr("睡眠", "Sleep"), tone: .text)
          number(sc.strain.map { FeedFormat.number($0, digits: 1) }, tr("负荷", "Strain"), tone: .text)
          if m.trained == true && !m.isHidden("training") { Chip(label: tr("已训练", "Trained"), tone: .train) }
        }
      } else if m.trained == true && !m.isHidden("training") {
        Chip(label: tr("已训练", "Trained"), tone: .train)
      }
      if let sc, !sc.week.isEmpty { strip(sc.week) }
      if let sc, let ago = syncAgo(sc, today: m.date) {
        Text(tr("最近同步 \(ago)", "Last sync \(ago)")).font(.system(size: 12)).foregroundColor(Palette.text3)
      }
      if m.isSelf, let s = m.seen, s.count == 0 {
        Text(tr("还没有朋友看到你，邀请朋友加入圈子吧", "No friends see you yet: invite someone to a circle"))
          .font(.system(size: 12)).foregroundColor(Palette.text2)
      }
      footer
    }
    .padding(14)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    .sheet(item: Binding(get: { day.map(DayRef.init) }, set: { if $0 == nil { day = nil } })) { ref in
      DaySheet(name: m.isSelf ? tr("我", "Me") : m.shownName, day: ref.day)
    }
  }

  @ViewBuilder private func hero(_ sc: MemberCard.Scores?) -> some View {
    if sc == nil {
      Text(tr("未共享", "Not shared")).font(.system(size: 13)).foregroundColor(Palette.text3)
    } else if let sc, sc.state == "pending" {
      Text(tr("今天的分数计算中", "Scoring today")).font(.system(size: 13)).foregroundColor(Palette.text3)
    } else if let sc, sc.state != "scored" {
      let last = sc.lastSyncDate ?? sc.lastDate
      Text(last.map { tr("未同步 · 上次 \(FeedFormat.day($0))", "Not synced · last \(FeedFormat.day($0))") } ?? tr("未同步", "Not synced"))
        .font(.system(size: 13)).foregroundColor(Palette.text3)
    } else if let sc, let r = sc.recovery {
      VStack(alignment: .trailing, spacing: 0) {
        Text("\(Int(r.rounded()))").font(.system(size: 24, weight: .bold).monospacedDigit()).foregroundColor(Tone(zone: sc.zone).color)
        if let l = sc.label { Text(l).font(.system(size: 11, weight: .medium)).foregroundColor(Palette.text2) }
      }
    }
  }

  private func number(_ v: String?, _ label: String, tone: Tone) -> some View {
    VStack(alignment: .leading, spacing: 1) {
      Text(v ?? "—").font(.system(size: 18, weight: .semibold).monospacedDigit()).foregroundColor(v == nil ? Palette.text3 : tone.color)
      Text(label).font(.system(size: 11, weight: .medium)).foregroundColor(Palette.text2)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  /// The last 7 days of recovery: one dot per day in its zone colour; tap a day for its numbers.
  private func strip(_ week: [MemberCard.Scores.Day]) -> some View {
    HStack(spacing: 0) {
      ForEach(Array(week.enumerated()), id: \.offset) { _, d in
        Button { day = d } label: {
          VStack(spacing: 4) {
            Circle().fill(d.zone == nil ? Palette.surface2 : Tone(zone: d.zone).color).frame(width: 10, height: 10)
            Text(Self.weekday(d.date)).font(.system(size: 10, weight: d.today ? .semibold : .regular))
              .foregroundColor(d.today ? Palette.text : Palette.text3)
          }
          .frame(maxWidth: .infinity, minHeight: 34)
          .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(Self.weekday(d.date)) \(d.recovery.map { "\(Int($0))" } ?? "—")")
      }
    }
  }

  @ViewBuilder private var footer: some View {
    if member.isSelf {
      let got = kinds.compactMap { k -> String? in
        guard let who = member.received?[k], !who.isEmpty else { return nil }
        return "\(Self.emoji(k)) \(who.count)"
      }
      if !got.isEmpty {
        Text(got.joined(separator: "   ")).font(.system(size: 14))
      }
    } else {
      HStack(spacing: 8) {
        ForEach(kinds, id: \.self) { k in
          let sent = member.sent.contains(k)
          Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            Task { await store.nudge(member.id, kind: k) }
          } label: {
            Text(Self.emoji(k)).font(.system(size: 18))
              .frame(width: 44, height: 36)
              .background(Capsule().fill(sent ? Palette.surface2 : Palette.bg))
              .opacity(sent ? 0.45 : 1)
          }
          .buttonStyle(PressScale())
          .disabled(sent)
          .accessibilityLabel(Self.nudgeLabel(k))
        }
        Spacer()
      }
    }
  }

  static func emoji(_ k: String) -> String {
    switch k {
    case "clap": return "\u{1F44F}"
    case "sleep": return "\u{1F4A4}"
    case "train": return "\u{1F4AA}"
    default: return k
    }
  }

  static func nudgeLabel(_ k: String) -> String {
    switch k {
    case "clap": return tr("棒", "Well done")
    case "sleep": return tr("早点睡", "Sleep early")
    case "train": return tr("一起练", "Train together")
    default: return k
    }
  }

  static func weekday(_ iso: String) -> String {
    guard let d = FeedFormat.dayFormatter.date(from: String(iso.prefix(10))) else { return "" }
    let f = DateFormatter()
    f.locale = Locale(identifier: Lang.isZh ? "zh_CN" : "en_US")
    f.setLocalizedDateFormatFromTemplate("EEEEE")
    return f.string(from: d)
  }

  private func syncAgo(_ sc: MemberCard.Scores, today: String?) -> String? {
    guard sc.state == "scored" || sc.state == "pending", let mins = sc.lastSyncMin, let day = sc.lastSyncDate else { return nil }
    if mins < 1 { return tr("刚刚", "just now") }
    if mins < 60 { return tr("\(mins) 分钟前", "\(mins) min ago") }
    if day == today { return tr("\(mins / 60) 小时前", "\(mins / 60) h ago") }
    return FeedFormat.day(day)
  }
}

private struct DayRef: Identifiable {
  let day: MemberCard.Scores.Day
  var id: String { day.date }
}

private struct DaySheet: View {
  let name: String
  let day: MemberCard.Scores.Day
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    SheetContainer(title: "\(name) · \(FeedFormat.day(day.date))", close: { dismiss() }) {
      HStack(spacing: 10) {
        MetricTile(label: tr("恢复", "Recovery"), value: day.recovery.map { "\(Int($0.rounded()))" }, tone: Tone(zone: day.zone))
        MetricTile(label: tr("睡眠", "Sleep"), value: day.sleepMin.map(FeedFormat.sleep))
        MetricTile(label: tr("负荷", "Strain"), value: day.strain.map { FeedFormat.number($0, digits: 1) })
      }
    }
    .presentationDetents([.height(200)])
    .presentationDragIndicator(.visible)
  }
}

// MARK: - leaderboard

/// One board (friends/leaderboard.py): 0–100 against each person's own baseline; "not shared" / "not enough data" last.
struct BoardView: View {
  @ObservedObject var store: CirclesStore

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 8) {
          ForEach(store.board?.boards ?? ["body", "activity", "lifestyle", "recomp", "longevity"], id: \.self) { b in
            PillButton(title: Self.name(b), on: b == store.boardName) { store.setBoard(b) }
          }
        }
      }
      if let b = store.board, b.board == store.boardName {
        Text(b.windowDays == 28 ? tr("近 4 周 · 和自己的基线比", "Last 4 weeks · against each person's own baseline")
                                : tr("近 7 天 · 和自己的基线比", "Last 7 days · against each person's own baseline"))
          .font(.system(size: 12)).foregroundColor(Palette.text2)
        VStack(spacing: 0) {
          ForEach(Array(b.rows.enumerated()), id: \.element.id) { i, r in
            if i > 0 { Divider().overlay(Palette.hair) }
            row(r, board: b.board)
          }
        }
        .padding(.horizontal, 14)
        .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
      } else {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.vertical, 30)
      }
    }
  }

  private func row(_ r: Leaderboard.Row, board: String) -> some View {
    let ok = r.status == "ok"
    return HStack(spacing: 10) {
      Group {
        if ok, let rank = r.rank, rank <= 3 {
          Icon(.trophy, size: 18).foregroundColor(rank == 1 ? Palette.mid : Palette.text2)
        } else {
          Text(ok ? "\(r.rank ?? 0)" : "·").font(.system(size: 14, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text2)
        }
      }
      .frame(width: 22)
      AvatarView(url: r.avatar, name: r.displayName, size: 32)
      VStack(alignment: .leading, spacing: 2) {
        Text(r.isSelf ? tr("我", "Me") : r.shownName).font(.system(size: 15, weight: .semibold)).foregroundColor(Palette.text).lineLimit(1)
        Text(ok ? Self.sub(board, r.sub) : r.status == "not_shared" ? tr("未共享", "Not shared") : tr("数据不足", "Not enough data"))
          .font(.system(size: 12)).foregroundColor(Palette.text2).lineLimit(1)
      }
      Spacer(minLength: 6)
      if ok, let s = r.score {
        VStack(alignment: .trailing, spacing: 4) {
          Text("\(Int(s))").font(.system(size: 17, weight: .bold).monospacedDigit()).foregroundColor(Palette.text)
          GeometryReader { g in
            ZStack(alignment: .leading) {
              Capsule().fill(Palette.surface2)
              Capsule().fill(Palette.good).frame(width: max(2, g.size.width * CGFloat(min(100, max(0, s)) / 100)))
            }
          }
          .frame(width: 56, height: 4)
        }
      } else {
        Text("—").foregroundColor(Palette.text3)
      }
    }
    .padding(.vertical, 10)
    .opacity(ok ? 1 : 0.6)
    .background(r.isSelf ? Palette.surface2.opacity(0.0) : .clear)
  }

  static func name(_ b: String) -> String {
    switch b {
    case "body": return tr("身体状态", "Body")
    case "activity": return tr("运动", "Activity")
    case "lifestyle": return tr("认真生活", "Lifestyle")
    case "recomp": return tr("瘦体增肌", "Recomp")
    case "longevity": return tr("抗衰老", "Longevity")
    default: return b
    }
  }

  /// The board's second line (app.js boardSub).
  static func sub(_ board: String, _ s: [String: Double]) -> String {
    func n(_ k: String) -> Int { Int((s[k] ?? 0).rounded()) }
    func f(_ k: String, _ d: Int) -> String { FeedFormat.number(s[k] ?? 0, digits: d) }
    func signed(_ k: String, _ d: Int) -> String { ((s[k] ?? 0) > 0 ? "+" : "") + f(k, d) }
    // values first: a quote inside an interpolation would hide the tr() from scripts/check_sources.py
    let days = n("days"), green = n("green_days"), train = n("training_days"), onTime = n("on_time")
    let food = n("food_met"), protein = n("protein_met"), acwr = f("acwr", 2), volume = f("volume_ratio", 2)
    let rate = signed("rate_pct_week", 2), gap = signed("gap_change_years", 1), pace = f("pace", 2)
    switch board {
    case "body":
      var parts = [tr("绿灯 \(green)/7", "Green \(green)/7")]
      if let m = s["sleep_avg_min"] {
        let t = FeedFormat.sleep(m)
        parts.append(tr("睡眠 \(t)", "Sleep \(t)"))
      }
      return parts.joined(separator: " · ")
    case "activity":
      return tr("负荷比 \(acwr) · 训练 \(train) 天", "Load ratio \(acwr) · \(train) training days")
    case "lifestyle":
      return tr("按时睡 \(onTime)/\(days) · 饮食达标 \(food)/\(days)", "Bed on time \(onTime)/\(days) · Diet \(food)/\(days)")
    case "recomp":
      return tr("\(rate)%/周 · 训练量 ×\(volume) · 蛋白 \(protein)/\(days)", "\(rate)%/wk · volume ×\(volume) · protein \(protein)/\(days)")
    case "longevity":
      var parts = [tr("近 4 周身体年龄 \(gap) 岁", "Body age \(gap) y in 4 weeks")]
      if s["pace"] != nil { parts.append(tr("衰老速度 \(pace)", "Pace of ageing \(pace)")) }
      return parts.joined(separator: " · ")
    default:
      return ""
    }
  }
}

/// A selectable pill (circle switcher, segments, boards): black when on, surface when off (the page's `.seg`).
struct PillButton: View {
  let title: String
  var icon: Icon.Name?
  var avatar: (url: String?, name: String)?
  let on: Bool
  var badge = false
  let action: () -> Void

  var body: some View {
    Button(action: {
      UISelectionFeedbackGenerator().selectionChanged()
      action()
    }) {
      HStack(spacing: 6) {
        if let avatar { AvatarView(url: avatar.url, name: avatar.name, size: 20) }
        if let icon { Icon(icon, size: 14) }
        Text(title).font(.system(size: 14, weight: .medium)).lineLimit(1)
        if badge { Circle().fill(Palette.bad).frame(width: 7, height: 7) }
      }
      .foregroundColor(on ? Palette.bg : Palette.text2)
      .padding(.vertical, 8).padding(.horizontal, avatar == nil ? 14 : 10)
      .background(Capsule().fill(on ? Palette.text : Palette.surface))
    }
    .buttonStyle(PressScale())
    .accessibilityAddTraits(on ? .isSelected : [])
  }
}
