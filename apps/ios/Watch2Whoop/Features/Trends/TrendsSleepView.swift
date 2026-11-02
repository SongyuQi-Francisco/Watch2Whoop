import Charts
import GRDB
import SwiftUI

/// The last 14 nights (`/sleep/nights?days=14&end=`): averages, stages per night, bedtime → wake, and the list of
/// nights (a night opens N3's sleep detail page). The page's sleepHtml.
struct SleepNights: Equatable {
  struct Night: Equatable, Identifiable {
    var id: String { date }
    var date: String
    var start: String?
    var end: String?
    var asleep: Double?
    var deep: Double?, core: Double?, rem: Double?, unspecified: Double?, awake: Double?
    var naps: [ClosedRange<Double>]

    var hasMain: Bool { start != nil }
  }

  var nights: [Night]

  init(json: [String: Any]) {
    nights = ((json["nights"] as? [[String: Any]]) ?? []).compactMap { n in
      guard let date = n["date"] as? String else { return nil }
      let m = n["main"] as? [String: Any]
      let naps: [ClosedRange<Double>] = ((n["naps"] as? [[String: Any]]) ?? []).compactMap { p in
        guard let a = (p["start"] as? String).flatMap(Self.minutes), let b = (p["end"] as? String).flatMap(Self.minutes) else { return nil }
        let x = Self.fromEvening(a), y = Self.fromEvening(b)
        return y > x ? x...y : nil
      }
      return Night(date: String(date.prefix(10)), start: m?["start"] as? String, end: m?["end"] as? String,
                   asleep: TodayModel.num(m?["asleep_min"]), deep: TodayModel.num(m?["deep_min"]), core: TodayModel.num(m?["core_min"]),
                   rem: TodayModel.num(m?["rem_min"]), unspecified: TodayModel.num(m?["unspecified_min"]),
                   awake: TodayModel.num(m?["awake_min"]), naps: naps)
    }
  }

  /// "23:40" → minutes after midnight.
  static func minutes(_ hm: String) -> Double? {
    let p = hm.split(separator: ":").compactMap { Double($0) }
    return p.count >= 2 ? p[0] * 60 + p[1] : nil
  }

  /// Minutes after 20:00 (the window chart runs 20:00 → 16:00 next day).
  static func fromEvening(_ m: Double) -> Double {
    let evening: Double = 1200
    let shifted: Double = m < evening ? m + 1440 : m
    return Swift.min(evening, Swift.max(0, shifted - evening))
  }

  /// Mean on the 24-hour circle (bedtimes cross midnight).
  static func circularMean(_ times: [Double]) -> Double? {
    guard !times.isEmpty else { return nil }
    var x = 0.0, y = 0.0
    for t in times { let a = t / 1440 * 2 * .pi; x += cos(a); y += sin(a) }
    var a = atan2(y, x)
    if a < 0 { a += 2 * .pi }
    return (a / (2 * .pi) * 1440).rounded().truncatingRemainder(dividingBy: 1440)
  }

  static func clock(_ m: Double?) -> String? {
    guard let m else { return nil }
    let r = Int(m) % 1440
    return String(format: "%02d:%02d", r / 60, r % 60)
  }

  var mains: [Night] { nights.filter(\.hasMain) }
  var avgSleep: Double? {
    guard !mains.isEmpty else { return nil }
    let total: Double = mains.reduce(0) { $0 + ($1.asleep ?? 0) }
    return total / Double(mains.count)
  }
  var avgBed: Double? { Self.circularMean(mains.compactMap { $0.start.flatMap(Self.minutes) }) }
  var avgWake: Double? { Self.circularMean(mains.compactMap { $0.end.flatMap(Self.minutes) }) }
}

struct TrendsSleepView: View {
  let end: String?
  let openNight: (String) -> Void
  @EnvironmentObject private var session: AccountSession
  @State private var nights: SleepNights?
  @State private var cancellable: AnyDatabaseCancellable?

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if let s = nights {
        content(s)
      } else {
        ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 40)
      }
    }
    .onAppear { observe(end) }
    .onChange(of: end) { e in observe(e); Task { await pull(e) } }
    .task { await pull(end) }
  }

  private func observe(_ e: String?) {
    let key = TrendsDocs.sleepNights(e), local = SeriesLocal.nightsKey(e)
    cancellable = ValueObservation.tracking { db in try SeriesLocal.shown(db, server: key, local: local) }
      .removeDuplicates()
      .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { rec in
        let n = rec?.json.map(SleepNights.init(json:))
        if n != nights { nights = n }
      })
  }

  /// The server's nights, then (package `series` on) the phone's own (M3 `SeriesLocal`).
  private func pull(_ e: String?) async {
    _ = await DocPuller(api: session.api, db: session.db).pull([DocFetch(TrendsDocs.sleepNights(e), Endpoint.sleepNights(end: e))])
    await SeriesLocal.refresh(session: session, [.nights(end: e)], userToday: nil)
  }

  @ViewBuilder private func content(_ s: SleepNights) -> some View {
    HStack(spacing: 8) {
      MetricTile(label: tr("平均睡眠", "Avg sleep"), value: TrendsFormat.hm(s.avgSleep))
      MetricTile(label: tr("平均入睡", "Avg bedtime"), value: SleepNights.clock(s.avgBed))
      MetricTile(label: tr("平均醒来", "Avg wake"), value: SleepNights.clock(s.avgWake))
    }
    .padding(.top, 4)
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: tr("阶段", "Stages"))
        Text(tr("\(s.mains.count)/\(s.nights.count) 晚", "\(s.mains.count)/\(s.nights.count) nights")).font(Typo.label).foregroundColor(Palette.text3)
      }
      Card(padding: 14) {
        VStack(alignment: .leading, spacing: 8) {
          StagesChart(nights: s.nights).frame(height: 130)
          HStack(spacing: 12) {
            let keys: [(String, String)] = [("deep", tr("深睡", "Deep")), ("core", tr("核心", "Core")), ("rem", "REM"), ("awake", tr("清醒", "Awake"))]
            ForEach(Array(keys.enumerated()), id: \.offset) { _, k in
              HStack(spacing: 4) {
                RoundedRectangle(cornerRadius: 2).fill(StageStyle.color(k.0)).frame(width: 10, height: 8)
                Text(k.1).font(.system(size: 11)).foregroundColor(Palette.text2)
              }
            }
          }
        }
      }
    }
    .padding(.top, 22)
    VStack(alignment: .leading, spacing: 10) {
      SectionHeader(title: tr("作息", "Schedule"))
      Card(padding: 14) { WindowChart(nights: s.nights).frame(height: CGFloat(s.nights.count) * 11 + 20) }
    }
    .padding(.top, 22)
    Card(padding: 4) {
      VStack(spacing: 0) {
        ForEach(s.nights.reversed()) { n in
          Button { openNight(n.date) } label: {
            HStack(spacing: 10) {
              Text("\(TodayModel.shortDay(n.date)) \(TodayView.weekday(n.date))").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text)
                .frame(width: 92, alignment: .leading)
              Text(n.hasMain ? "\(n.start ?? "")–\(n.end ?? "")" : "—").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
              Spacer()
              Text(n.hasMain ? (TrendsFormat.hm(n.asleep) ?? "") : "").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
              Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
            }
            .padding(.horizontal, 12).padding(.vertical, 11)
            .contentShape(Rectangle())
          }
          .buttonStyle(PressScale())
        }
      }
    }
    .padding(.top, 22)
  }
}

/// Stacked minutes per stage, one bar per night (app.js stagesChart); 8 h guide.
struct StagesChart: View {
  let nights: [SleepNights.Night]

  private struct Part: Identifiable { let id: String; let i: Int; let stage: String; let y0: Double; let y1: Double }

  var body: some View {
    let parts = Self.parts(nights)
    let top = max(600, parts.map(\.y1).max() ?? 0)
    VStack(spacing: 4) {
    Chart {
      RuleMark(y: .value("m", 480)).foregroundStyle(Palette.text3.opacity(0.5)).lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
        .annotation(position: .top, alignment: .trailing) { Text("8h").font(.system(size: 9)).foregroundColor(Palette.text3) }
      ForEach(parts) { p in
        RectangleMark(xStart: .value("n", Double(p.i) + 0.12), xEnd: .value("n", Double(p.i) + 0.88), yStart: .value("m", p.y0), yEnd: .value("m", p.y1))
          .foregroundStyle(StageStyle.color(p.stage))
      }
    }
    .chartXScale(domain: 0...Double(max(1, nights.count)))
    .chartYScale(domain: 0...top)
    .chartYAxis(.hidden)
    .chartXAxis(.hidden)
    .accessibilityLabel(tr("最近 \(nights.count) 晚的睡眠阶段", "Sleep stages of the last \(nights.count) nights"))
    HStack {
      Text(TodayModel.shortDay(nights.first?.date))
      Spacer()
      if nights.count >= 3 { Text(TodayModel.shortDay(nights[(nights.count - 1) / 2].date)); Spacer() }
      Text(TodayModel.shortDay(nights.last?.date))
    }
    .font(.system(size: 10).monospacedDigit())
    .foregroundColor(Palette.text3)
    .accessibilityHidden(true)
    }
  }

  private static func parts(_ nights: [SleepNights.Night]) -> [Part] {
    var out: [Part] = []
    for (i, n) in nights.enumerated() where n.hasMain {
      let parts: [Double?] = [n.deep, n.core, n.rem, n.unspecified]
      let staged: Double = parts.reduce(0) { $0 + ($1 ?? 0) }
      var seq: [(String, Double?)] = [("unspecified", n.asleep), ("awake", n.awake)]
      if staged > 0 {
        seq = [("deep", n.deep), ("core", n.core), ("rem", n.rem), ("unspecified", n.unspecified), ("awake", n.awake)]
      }
      var y = 0.0
      for (st, v) in seq {
        guard let v, v > 0 else { continue }
        out.append(Part(id: "\(i)-\(st)", i: i, stage: st, y0: y, y1: y + v))
        y += v
      }
    }
    return out
  }
}

/// Bedtime → wake per night on a 20:00 → 16:00 axis, naps thinner (app.js windowChart).
struct WindowChart: View {
  let nights: [SleepNights.Night]

  private struct Bar: Identifiable { let id: String; let row: Int; let x0: Double; let x1: Double; let nap: Bool }

  var body: some View {
    let bars = Self.bars(nights)
    Chart {
      ForEach(bars) { b in
        RectangleMark(xStart: .value("t", b.x0), xEnd: .value("t", max(b.x1, b.x0 + 4)),
                      yStart: .value("r", Double(b.row) + (b.nap ? 0.35 : 0.15)), yEnd: .value("r", Double(b.row) + (b.nap ? 0.65 : 0.85)))
          .foregroundStyle(b.nap ? StageStyle.color("rem") : StageStyle.color("deep"))
          .cornerRadius(3)
      }
    }
    .chartXScale(domain: 0...(20.0 * 60))
    .chartYScale(domain: .automatic(includesZero: true, reversed: true))
    .chartYAxis(.hidden)
    .chartXAxis {
      AxisMarks(values: stride(from: 0.0, through: 1200, by: 240).map { $0 }) { v in
        AxisGridLine().foregroundStyle(Palette.hair)
        AxisValueLabel(anchor: v.index == 0 ? .topLeading : v.index == v.count - 1 ? .topTrailing : .top) { Text(String(format: "%02d", (20 + Int(v.as(Double.self) ?? 0) / 60) % 24)).font(.system(size: 10)).foregroundColor(Palette.text3) }
      }
    }
    .accessibilityLabel(tr("入睡和醒来时间", "Bedtime and wake time"))
  }

  private static func bars(_ nights: [SleepNights.Night]) -> [Bar] {
    var out: [Bar] = []
    for (i, n) in nights.enumerated() {
      for (k, nap) in n.naps.enumerated() { out.append(Bar(id: "\(i)-n\(k)", row: i, x0: nap.lowerBound, x1: nap.upperBound, nap: true)) }
      if let a = n.start.flatMap(SleepNights.minutes), let b = n.end.flatMap(SleepNights.minutes) {
        out.append(Bar(id: "\(i)", row: i, x0: SleepNights.fromEvening(a), x1: SleepNights.fromEvening(b), nap: false))
      }
    }
    return out
  }
}
