import SwiftUI

/// The day head of the strain page (it was the strain half sheet of 2.0.1, `03-strain-open`): today's number against the
/// target or the expected strain, the curve of the day, then steps, active energy, exercise and the training state.
struct StrainDayHead: View {
  let model: TodayModel
  /// A row of the head: its metric page (steps, active energy), the explanation (exercise), the load sheet (training state).
  let open: (TodayGlance.Row) -> Void

  var body: some View {
    let s = model.glance.strain
    VStack(alignment: .leading, spacing: 0) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text(s.text ?? "—").font(.system(size: 56, weight: .ultraLight).monospacedDigit()).tracking(-1.5).foregroundColor(Palette.text)
        Text(model.isPast ? tr("全天", "Day") : tr("现在", "Now")).font(Typo.label).foregroundColor(Palette.text2)
        Spacer()
        if let day = model.glance.strainDay, let f = day.forecast {
          Text("···").font(.system(size: 15, weight: .heavy)).foregroundColor(Palette.train)
          Text(tr("预计", "Expected")).font(Typo.label).foregroundColor(Palette.text2)
          Text(TodayModel.fmt(f, digits: 1)).font(.system(size: 22, weight: .regular).monospacedDigit()).foregroundColor(Palette.text)
          if let n = TodayGlanceRules.forecastCount(day) { Text(n).font(Typo.label).foregroundColor(Palette.text2) }
        } else if let t = s.target {
          Text(tr("目标", "Target")).font(Typo.label).foregroundColor(Palette.text2)
          Text("\(TodayModel.fmt(t.lowerBound, digits: 1))–\(TodayModel.fmt(t.upperBound, digits: 1))")
            .font(.system(size: 22, weight: .regular).monospacedDigit()).foregroundColor(Palette.text)
        }
      }
      .frame(minHeight: 60)
      if let day = model.glance.strainDay {
        StrainDayChart(day: day, target: s.target, max: s.max, majors: s.majors, live: !model.isPast).frame(height: 120).padding(.top, 12)
      } else {
        BulletGauge(value: s.value, max: s.max, target: s.target, step: 1, majors: s.majors, tone: s.tone).padding(.top, 14)
      }
      VStack(spacing: 0) {
        ForEach(model.glance.strainRows) { r in L1RowView(row: r, labelWidth: LevelParams.rowLabelWidth) { open(r) } }
      }
      .padding(.top, 12)
    }
  }
}

/// The night head of the sleep pages: last night's time asleep, the hypnogram and need / deep / regularity / lowest heart
/// rate (the sleep half sheet of 2.0.1), then the doors to the night page (night by night, every detail of the night) and,
/// on my own 今天, to the bedtime goal.
struct SleepNightHead: View {
  let session: AccountSession
  let date: String?
  let explain: (String, String) -> Void
  let night: (String?) -> Void
  var bedtime: (() -> Void)?

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      TodaySleepL1(session: session, date: date, explain: explain)
      door(.moon, tr("整晚", "The night")) { night(date) }
      if let bedtime { door(.clock, tr("入睡目标", "Bedtime goal"), bedtime) }
    }
  }

  private func door(_ icon: Icon.Name, _ title: String, _ run: @escaping () -> Void) -> some View {
    Button(action: run) {
      HStack(spacing: 10) {
        Icon(icon, size: 16).foregroundColor(Palette.text2)
        Text(title).font(.system(size: 15, weight: .medium)).foregroundColor(Palette.text)
        Spacer()
        Icon(.chevronRight, size: 13).foregroundColor(Palette.text3)
      }
      .frame(minHeight: 46)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .overlay(alignment: .bottom) { Rectangle().fill(Palette.hair).frame(height: 1) }
  }
}

/// Recovery's contributors over the range: each one's mean z-score against the baseline and the latest day's (the recovery
/// sheet of 2.0.0, `TrendsSheetView.recovery`; on the recovery page under its chart).
struct RecoveryContributors: View {
  let series: TrendSeries?

  var body: some View {
    let rows = Self.rows(series)
    if !rows.isEmpty {
      VStack(alignment: .leading, spacing: 12) {
        Text(tr("相对基线（z）", "Against baseline (z)")).font(Typo.section).foregroundColor(Palette.text2)
        ForEach(Array(rows.enumerated()), id: \.offset) { _, r in
          VStack(alignment: .leading, spacing: 5) {
            HStack {
              Text(r.label).font(Typo.body).foregroundColor(Palette.text)
              Spacer()
              Text(TrendsFormat.signed(r.mean, digits: 2)).font(Typo.bodyStrong.monospacedDigit())
                .foregroundColor(r.mean > 0 ? Palette.good : r.mean < 0 ? Palette.bad : Palette.text2)
              Text("· \(tr("最近", "latest")) \(r.last.map { TrendsFormat.signed($0, digits: 2) } ?? "—")")
                .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text3)
            }
            DiffBar(fraction: max(-1, min(1, r.mean / 2)), tone: r.mean > 0 ? .good : r.mean < 0 ? .bad : .text)
          }
        }
      }
    }
  }

  /// (label, mean z over the range, the latest day's z) per contributor that has readings.
  static func rows(_ s: TrendSeries?) -> [(label: String, mean: Double, last: Double?)] {
    let names: [(String, String)] = [("hrv", "HRV"), ("rhr", tr("静息心率", "Resting HR")), ("resp_rate", tr("呼吸频率", "Respiratory rate")),
                                     ("sleep_performance", tr("睡眠表现", "Sleep performance"))]
    let zs = (s?.contributors ?? []).compactMap { $0 }
    let last = (s?.contributors ?? []).last { $0 != nil } ?? nil
    return names.compactMap { k, label in
      let xs = zs.compactMap { $0[k] }
      return xs.isEmpty ? nil : (label, xs.reduce(0, +) / Double(xs.count), last?[k])
    }
  }
}
