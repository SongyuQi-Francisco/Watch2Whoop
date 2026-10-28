import SwiftUI

/// Sheets opened from the Today tab (one at a time).
enum TodaySheet: Identifiable, Equatable {
  case recovery, strain, load, coach, monitor, calendar, weight, reminders
  case reminder(Int)
  case explain(kind: String, key: String, title: String)

  var id: String {
    switch self {
    case .recovery: return "recovery"
    case .strain: return "strain"
    case .load: return "load"
    case .coach: return "coach"
    case .monitor: return "monitor"
    case .calendar: return "calendar"
    case .weight: return "weight"
    case .reminders: return "reminders"
    case .reminder(let id): return "reminder:\(id)"
    case .explain(let k, let key, _): return "explain:\(k):\(key)"
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

  var body: some View {
    SheetContainer(title: title, close: close) { content }
      .presentationDetents(detents)
      .presentationDragIndicator(.visible)
  }

  private var detents: Set<PresentationDetent> {
    switch sheet {
    case .reminder, .load, .coach: return [.medium]
    case .calendar, .weight: return [.large]
    default: return [.medium, .large]
    }
  }

  private var title: String {
    let d = model.detail
    switch sheet {
    case .recovery: return tr("恢复", "Recovery")
    case .strain: return d.strainDate.map { "\(tr("负荷", "Strain")) · \(TodayModel.shortDay($0))" } ?? tr("负荷", "Strain")
    case .load: return tr("训练负荷", "Training load")
    case .coach: return tr("负荷目标", "Strain target")
    case .monitor: return tr("健康监测", "Health monitor")
    case .calendar: return tr("选择日期", "Pick a day")
    case .weight: return tr("记体重", "Log weight")
    case .reminders: return "\(tr("提醒", "Reminders")) · \(model.reminders.count)"
    case .reminder: return tr("提醒", "Reminder")
    case .explain(_, _, let t): return t
    }
  }

  @ViewBuilder private var content: some View {
    let d = model.detail
    let day = model.isPast ? model.date : nil
    switch sheet {
    case .recovery:
      VStack(alignment: .leading, spacing: 0) {
        ScaleBar.recovery(d.recovery ?? d.lastRecovery).padding(.vertical, 8)
        if d.recovery != nil {
          KVRow(label: tr("恢复分", "Recovery"), value: d.recovery.map { TodayModel.fmt($0) }, badge: d.zoneLabel.map { ($0, d.zone) })
        } else {
          KVRow(label: tr("恢复分", "Recovery"), value: nil, badge: model.word.map { ($0, .text) })
          if let l = d.lastRecovery {
            KVRow(label: tr("上次", "Last"), value: "\(TodayModel.fmt(l)) · \(TodayModel.shortDay(d.lastDate))")
          }
        }
        KVRow(label: "HRV", value: d.hrv.map { TodayModel.fmt($0) }, unit: "ms") { open(.explain(kind: "metric", key: "hrv", title: "HRV")) }
        KVRow(label: tr("静息心率", "Resting HR"), value: d.rhr.map { TodayModel.fmt($0) }, unit: "bpm") {
          open(.explain(kind: "metric", key: "resting_hr", title: tr("静息心率", "Resting HR")))
        }
        flags(d.flags)
        ExplainBody(kind: "metric", key: "recovery", day: day, head: false).padding(.top, 8)
      }
    case .strain:
      VStack(alignment: .leading, spacing: 0) {
        ScaleBar.strain(d.strain).padding(.vertical, 8)
        KVRow(label: "Strain", value: d.strain.map { TodayModel.fmt($0, digits: 1) })
        KVRow(label: tr("肌肉负荷", "Muscle load"), value: d.muscleLoad.map { TodayModel.fmt($0) }, unit: "kg")
        flags(d.strainFlags)
        ExplainBody(kind: "metric", key: "strain", day: day, head: false).padding(.top, 8)
      }
    case .load:
      VStack(alignment: .leading, spacing: 0) {
        ScaleBar.tsb(d.tsb).padding(.vertical, 8)
        KVRow(label: tr("状态", "Status"), value: d.loadLabel)
        KVRow(label: tr("CTL 体能", "CTL fitness"), value: d.ctl.map { TodayModel.fmt($0) })
        KVRow(label: tr("ATL 疲劳", "ATL fatigue"), value: d.atl.map { TodayModel.fmt($0) })
        KVRow(label: "TSB", value: d.tsb.map { ($0 > 0 ? "+" : "") + TodayModel.fmt($0) })
        if d.loadMissingDays > 0 { ToneBadge(text: tr("缺 \(d.loadMissingDays) 天", "\(d.loadMissingDays) days missing"), tone: .mid).padding(.top, 10) }
      }
    case .coach:
      if let c = model.coach {
        VStack(alignment: .leading, spacing: 0) {
          ScaleBar.coach(c.current, low: c.low, high: c.high).padding(.vertical, 8)
          KVRow(label: tr("区间", "Range"), value: "\(TodayModel.fmt(c.low))–\(TodayModel.fmt(c.high))")
          KVRow(label: tr("现在", "Now"), value: c.current.map { TodayModel.fmt($0, digits: 1) }, badge: (c.stateLabel, c.tone))
        }
      }
    case .monitor:
      VStack(alignment: .leading, spacing: 14) {
        ForEach(model.vitals) { v in MonitorRow(vital: v) { k in open(.explain(kind: "metric", key: k, title: v.name)) } }
      }
      .padding(.top, 6)
    case .reminders:
      VStack(spacing: 8) {
        ForEach(model.reminders) { r in ReminderRow(reminder: r) { open(.reminder(r.id)) } }
      }
    case .reminder(let id):
      if let r = model.reminders.first(where: { $0.id == id }) {
        VStack(alignment: .leading, spacing: 12) {
          Text(r.title).font(.system(size: 17, weight: .semibold)).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
          HStack(spacing: 6) {
            ToneBadge(text: levelLabel(r.level), tone: r.tone)
            if let d = r.date { ToneBadge(text: TodayModel.shortDay(d)) }
          }
          if !model.isPast {
            HStack(spacing: 10) {
              if r.status == "new" { action(tr("已看", "Seen")) { store.setReminder(id, status: "seen"); close() } }
              else { action(tr("标为未读", "Mark unread")) { store.setReminder(id, status: "new"); close() } }
              action(tr("不再提醒", "Dismiss")) { store.setReminder(id, status: "dismissed"); close() }
            }
            .padding(.top, 6)
          }
        }
      }
    case .explain(let kind, let key, _):
      ExplainBody(kind: kind, key: key, day: day)
    case .calendar:
      MonthPicker(selected: store.date, userToday: store.userToday) { day in
        store.select(day)
        close()
      }
    case .weight:
      WeightSheet(today: store.userToday) { close() }
    }
  }

  private func levelLabel(_ l: String) -> String {
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

  @ViewBuilder private func flags(_ f: [String]) -> some View {
    if !f.isEmpty {
      HStack(spacing: 6) { ForEach(f, id: \.self) { ToneBadge(text: $0) } }.padding(.top, 10)
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

/// A reminder card row: level dot, title, chevron (app.js reminderRow).
struct ReminderRow: View {
  let reminder: TodayModel.Reminder
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 10) {
        Circle().fill(reminder.tone == .text ? Palette.text3 : reminder.tone.color).frame(width: 8, height: 8)
        Text(reminder.title).font(Typo.body).foregroundColor(reminder.status == "seen" ? Palette.text2 : Palette.text)
          .lineLimit(2).multilineTextAlignment(.leading)
        Spacer(minLength: 6)
        Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
      }
      .padding(14)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    }
    .buttonStyle(PressScale())
  }
}
