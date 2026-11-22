import SwiftUI

/// Sheets opened from the Today tab (one at a time).
enum TodaySheet: Identifiable, Equatable {
  case load, calendar, weight
  /// The reminders, each one expanding in the list (2.1.0: no sheet of its own any more).
  case reminders
  case explain(kind: String, key: String, title: String)
  /// A status card of 今日核心 / 需要注意 (#357), the same sheet as Trends'.
  case status(TrendStatus.Tile)
  /// Stress, the sleep coach, body age, … (TodayDetails.swift).
  case detail(TodayDetail)

  var id: String {
    switch self {
    case .load: return "load"
    case .calendar: return "calendar"
    case .weight: return "weight"
    case .reminders: return "reminders"
    case .explain(let k, let key, _): return "explain:\(k):\(key)"
    case .status(let t): return "status:\(t.key)"
    case .detail(let d): return "detail:\(d.id)"
    }
  }
}

/// The body of each sheet, inside the page's sheet container.
struct TodaySheetView: View {
  let sheet: TodaySheet
  let model: TodayModel
  @ObservedObject var store: TodayStore
  let open: (TodaySheet) -> Void
  let close: () -> Void
  @Environment(\.memberView) private var member

  var body: some View {
    SheetContainer(title: title, close: close) { content }
      .presentationDetents(detents)
      .presentationDragIndicator(.visible)
  }

  private var detents: Set<PresentationDetent> {
    switch sheet {
    case .load: return [.medium]
    case .calendar, .weight: return [.large]
    case .detail(let d) where d.large: return [.large]
    default: return [.medium, .large]
    }
  }

  private var title: String {
    let d = model.detail
    switch sheet {
    case .load: return tr("训练负荷", "Training load")
    case .calendar: return tr("选择日期", "Pick a day")
    case .weight: return tr("记体重", "Log weight")
    case .reminders: return "\(tr("提醒", "Reminders")) · \(model.reminders.count)"
    case .explain(_, _, let t): return t
    case .status(let t): return t.label
    case .detail(let d): return d.title
    }
  }

  @ViewBuilder private var content: some View {
    let d = model.detail
    let day = model.isPast ? model.date : nil
    switch sheet {
    case .load:
      VStack(alignment: .leading, spacing: 0) {
        ScaleBar.tsb(d.tsb).padding(.vertical, 8)
        KVRow(label: tr("状态", "Status"), value: d.loadLabel)
        KVRow(label: tr("CTL 体能", "CTL fitness"), value: d.ctl.map { TodayModel.fmt($0) })
        KVRow(label: tr("ATL 疲劳", "ATL fatigue"), value: d.atl.map { TodayModel.fmt($0) })
        KVRow(label: "TSB", value: d.tsb.map { ($0 > 0 ? "+" : "") + TodayModel.fmt($0) })
        if d.loadMissingDays > 0 { ToneBadge(text: tr("缺 \(d.loadMissingDays) 天", "\(d.loadMissingDays) days missing"), tone: .mid).padding(.top, 10) }
      }
    case .reminders:
      ReminderList(reminders: model.reminders, set: model.isPast || member != nil ? nil : { id, status in store.setReminder(id, status: status) })
    case .explain(let kind, let key, _):
      ExplainBody(kind: kind, key: key, day: day)
    case .status(let t):
      StatusTileBody(tile: t, day: day)
    case .calendar:
      MonthPicker(selected: store.date, userToday: store.userToday) { day in
        store.select(day)
        close()
      }
    case .weight:
      WeightSheet(today: store.userToday) { close() }
    case .detail where member != nil:
      EmptyView()   // these read my own tables
    case .detail(let detail):
      TodayDetailBody(detail: detail, date: model.date ?? store.userToday, past: day, coach: model.tonight, open: open, close: close)
    }
  }
}

/// One vital of the health monitor: value, the personal normal range (60 days ± 2 SD) and where today sits in it.
struct MonitorRow: View {
  let vital: TodayModel.Vital
  let explain: (String) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(spacing: 6) {
        if let k = vital.explainKey {
          Button { explain(k) } label: {
            HStack(spacing: 4) {
              Text(vital.name).font(Typo.body).foregroundColor(Palette.text2)
              Icon(.chevronRight, size: 13).foregroundColor(Palette.text3)
            }
          }
          .buttonStyle(PressScale())
        } else {
          Text(vital.name).font(Typo.body).foregroundColor(Palette.text2)
        }
        Spacer()
        if vital.off { ToneBadge(text: (vital.value ?? 0) > (vital.high ?? .infinity) ? tr("偏高", "High") : tr("偏低", "Low"), tone: .bad) }
        if let v = vital.value {
          HStack(alignment: .firstTextBaseline, spacing: 2) {
            Text(TodayModel.fmt(v, digits: 1)).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
            if let u = vital.unit { Text(u).font(Typo.unit).foregroundColor(Palette.text2) }
          }
        } else {
          Text("—").font(Typo.bodyStrong).foregroundColor(Palette.text3)
        }
      }
      if let v = vital.value, let lo = vital.low, let hi = vital.high {
        RangeBar(value: v, low: lo, high: hi, off: vital.off)
      }
    }
  }
}

/// The personal normal range as a band with a dot for the value (app.js monitorHtml vrange).
struct RangeBar: View {
  let value: Double
  let low: Double
  let high: Double
  let off: Bool

  var body: some View {
    GeometryReader { g in
      let span = high - low == 0 ? 1 : high - low, lo = low - span * 0.5, hi = high + span * 0.5
      let x = { (v: Double) in CGFloat((min(hi, max(lo, v)) - lo) / (hi - lo)) * g.size.width }
      ZStack(alignment: .topLeading) {
        Capsule().fill(Palette.surface2).frame(height: 6).offset(y: 2)
        Capsule().fill(Palette.good.opacity(0.35)).frame(width: x(high) - x(low), height: 6).offset(x: x(low), y: 2)
        Circle().fill(off ? Palette.bad : Palette.text).frame(width: 10, height: 10).offset(x: x(value) - 5)
        Group {
          Text(TodayModel.fmt(low, digits: 1)).position(x: x(low), y: 20)
          Text(TodayModel.fmt(high, digits: 1)).position(x: x(high), y: 20)
        }
        .font(.system(size: 10, weight: .medium).monospacedDigit()).foregroundColor(Palette.text3)
      }
    }
    .frame(height: 26)
    .accessibilityHidden(true)
  }
}

/// The reminders, newest first: a tap opens one in place (its level, its date and, on my own today, 已看 / 不再提醒), a
/// second tap closes it. 2.1.0 (owner's answer 5): a single reminder no longer opens a layer of its own.
struct ReminderList: View {
  let reminders: [TodayModel.Reminder]
  /// Marks a reminder (`seen`, `new`, `dismissed`); nil = read only (a look-back day, someone else's page).
  var set: ((Int, String) -> Void)?
  @State private var open: Int?

  var body: some View {
    VStack(spacing: 8) {
      ForEach(reminders) { r in row(r) }
    }
  }

  private func row(_ r: TodayModel.Reminder) -> some View {
    let on = open == r.id
    return VStack(alignment: .leading, spacing: 12) {
      Button { withAnimation(.easeOut(duration: 0.2)) { open = on ? nil : r.id } } label: {
        HStack(alignment: .top, spacing: 10) {
          Circle().fill(r.tone == .text ? Palette.text3 : r.tone.color).frame(width: 8, height: 8).padding(.top, 6)
          Text(r.title).font(on ? .system(size: 16, weight: .semibold) : Typo.body)
            .foregroundColor(r.status == "seen" && !on ? Palette.text2 : Palette.text)
            .lineLimit(on ? nil : 2).multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
          Spacer(minLength: 6)
          Icon(.chevronDown, size: 16).foregroundColor(Palette.text3).rotationEffect(.degrees(on ? 180 : 0)).padding(.top, 2)
        }
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityValue(on ? tr("已展开", "Expanded") : tr("已收起", "Collapsed"))
      if on {
        HStack(spacing: 6) {
          ToneBadge(text: Self.levelLabel(r.level), tone: r.tone)
          if let d = r.date { ToneBadge(text: TodayModel.shortDay(d)) }
        }
        if let set {
          HStack(spacing: 10) {
            if r.status == "new" { action(tr("已看", "Seen")) { set(r.id, "seen"); open = nil } }
            else { action(tr("标为未读", "Mark unread")) { set(r.id, "new"); open = nil } }
            action(tr("不再提醒", "Dismiss")) { set(r.id, "dismissed"); open = nil }
          }
        }
      }
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }

  static func levelLabel(_ l: String) -> String {
    switch l {
    case "alert": return tr("重要", "Important")
    case "warning": return tr("注意", "Heads-up")
    default: return tr("提示", "Tip")
    }
  }

  private func action(_ title: String, _ run: @escaping () -> Void) -> some View {
    Button(action: run) {
      Text(title).font(Typo.bodyStrong).foregroundColor(Palette.text)
        .frame(maxWidth: .infinity, minHeight: 44)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.inSheet))
    }
    .buttonStyle(PressScale())
  }
}
