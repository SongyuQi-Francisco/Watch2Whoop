import Charts
import GRDB
import SwiftUI

/// Loads one night: the cached document at once, then a fresh one into `docs` (`sleep:night:<date>`; the latest night
/// when the date is not known yet).
@MainActor
final class SleepNightStore: ObservableObject {
  @Published private(set) var model: SleepNightModel?
  @Published private(set) var error: String?
  @Published private(set) var date: String?
  private var cancellable: AnyDatabaseCancellable?

  func show(_ session: AccountSession, date: String?) {
    self.date = date
    error = nil
    let key = TodayDocs.sleepNight(date)
    // the phone's night (M4, `sleepnight.local:<day>`) when package `coach` is primary, else the server's
    cancellable = ValueObservation.tracking { db in try CoachLocal.shownNight(db, serverKey: key, day: date) }
      .removeDuplicates()
      .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] rec in
        guard let json = rec?.json else { return }
        let m = SleepNightModel(json: json)
        if self?.model != m { self?.model = m }
      })
    if model?.date != date { model = (try? session.db.doc(key))?.json.map(SleepNightModel.init(json:)) }
    Task {
      let out = await DocPuller(api: session.api, db: session.db).pull([DocFetch(key, Endpoint.sleepNight(date))])
      // the latest night is also kept under its own date, so ‹ › back to it is instant
      if date == nil, let json = try? session.db.doc(key)?.json, let d = json["date"] as? String,
         let data = try? JSONSerialization.data(withJSONObject: json) {
        try? session.db.saveDoc(TodayDocs.sleepNight(d), json: data)
      }
      CoachLocal.refreshNight(session: session, day: date)
      if model == nil {
        if out.removed.contains(key) { error = tr("这晚没有数据", "No data for this night") }
        else if out.offline { error = tr("离线，连上网络后重试", "Offline, retry when connected") }
      }
    }
  }
}

/// The sleep detail page, a sub-page of Today (the page's sleep-detail.js): swipe or ‹ › between nights.
struct SleepDetailView: View {
  let initialDate: String?
  let explain: (String, String) -> Void
  @EnvironmentObject private var session: AccountSession
  @StateObject private var store = SleepNightStore()

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 0) {
        nav
        if let m = store.model {
          if m.hasMain { content(m) } else { empty(tr("这晚没有手表睡眠数据", "No watch sleep for this night")) }
        } else if let e = store.error {
          empty(e)
        } else {
          ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 60)
        }
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(tr("睡眠", "Sleep"))
    .navigationBarTitleDisplayMode(.inline)
    .simultaneousGesture(DragGesture(minimumDistance: 40).onEnded { v in
      guard abs(v.translation.width) > 70, abs(v.translation.height) < abs(v.translation.width) * 0.6, let m = store.model else { return }
      if v.translation.width < 0, let n = m.next { store.show(session, date: n) }
      else if v.translation.width > 0, let p = m.prev { store.show(session, date: p) }
    })
    .onAppear { if store.model == nil { store.show(session, date: initialDate) } }
  }

  private var nav: some View {
    HStack {
      stepButton(.chevronLeft, target: store.model?.prev, label: tr("前一晚", "Previous night"))
      Spacer()
      Text(dayTitle).font(Typo.bodyStrong).foregroundColor(Palette.text)
      Spacer()
      stepButton(.chevronRight, target: store.model?.next, label: tr("后一晚", "Next night"))
    }
    .padding(.vertical, 10)
  }

  private var dayTitle: String {
    guard let d = store.model?.date ?? store.date else { return "" }
    return TodayModel.shortDay(d)
  }

  private func stepButton(_ icon: Icon.Name, target: String?, label: String) -> some View {
    Button { if let target { store.show(session, date: target) } } label: {
      Icon(icon, size: 18).foregroundColor(target == nil ? Palette.text3 : Palette.text)
        .frame(width: 36, height: 36).background(Circle().fill(Palette.surface))
    }
    .buttonStyle(PressScale())
    .disabled(target == nil)
    .accessibilityLabel(label)
  }

  private func empty(_ text: String) -> some View {
    Card { HStack { Text("—").foregroundColor(Palette.text3); Text(text).font(Typo.caption).foregroundColor(Palette.text2) } }
  }

  @ViewBuilder private func content(_ m: SleepNightModel) -> some View {
    // hero: bedtime → wake, asleep; in bed, efficiency, latency, awakenings
    Card {
      VStack(alignment: .leading, spacing: 14) {
        HStack(alignment: .bottom) {
          VStack(alignment: .leading, spacing: 2) {
            Text(tr("入睡 → 醒来", "Bedtime → wake")).font(Typo.label).foregroundColor(Palette.text2)
            Text("\(m.start) → \(m.end)").font(.system(size: 22, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
          }
          Spacer()
          VStack(alignment: .trailing, spacing: 2) {
            Text(tr("睡着", "Asleep")).font(Typo.label).foregroundColor(Palette.text2)
            Text(m.asleep.map(TodayModel.hm) ?? "—").font(.system(size: 22, weight: .bold).monospacedDigit()).foregroundColor(Palette.sleep)
          }
        }
        HStack(spacing: 8) {
          cell(tr("在床", "In bed"), m.inBed.map(TodayModel.hm), nil, nil)
          cell(tr("效率", "Efficiency"), m.efficiency.map { TodayModel.fmt($0) }, "%", "sleep_efficiency")
          cell(tr("入睡用时", "Latency"), m.latency.map { TodayModel.fmt($0) }, tr("分", "min"), nil)
          cell(tr("夜醒", "Wake-ups"), m.awakenings.map { TodayModel.fmt($0) }, tr("次", "×"), nil)
        }
      }
    }
    if !m.naps.isEmpty {
      HStack(spacing: 6) { ForEach(m.naps, id: \.self) { ToneBadge(text: "\(tr("小睡", "Nap")) \($0)") } }.padding(.top, 10)
    }

    section(tr("睡眠阶段", "Stages"))
    Card {
      VStack(alignment: .leading, spacing: 14) {
        if m.stages.isEmpty { ToneBadge(text: tr("无阶段时间轴", "No stage timeline")) }
        else { Hypnogram(stages: m.stages, total: max(1, m.inBed ?? 1), start: m.start, end: m.end) }
        if m.staged { CompositionBar(parts: m.composition) }
        HStack(alignment: .top) {
          ForEach(Array(m.composition.enumerated()), id: \.offset) { _, c in
            VStack(alignment: .leading, spacing: 3) {
              HStack(spacing: 5) {
                RoundedRectangle(cornerRadius: 2).fill(StageStyle.color(c.0)).frame(width: 9, height: 9)
                Text(SleepNightModel.stageName(c.0)).font(Typo.label).foregroundColor(Palette.text2)
              }
              Text(TodayModel.hm(c.1)).font(.system(size: 17, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
              Text("\(TodayModel.fmt(c.2))%").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
          }
        }
      }
    }

    if let sc = m.score {
      section(tr("睡眠分", "Sleep score"))
      Button { explain("sleep_score", tr("睡眠分", "Sleep score")) } label: {
        Card {
          VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 16) {
              Text(TodayModel.fmt(sc)).font(.system(size: 40, weight: .bold).monospacedDigit()).foregroundColor(scoreTone(sc).color)
              VStack(spacing: 7) { ForEach(m.parts) { p in PartBar(part: p) } }
            }
            HStack(spacing: 8) {
              mini(tr("需求", "Need"), m.need.map(TodayModel.hm))
              mini(tr("实际", "Actual"), m.actual.map(TodayModel.hm))
              mini(tr("表现", "Performance"), m.performance.map { "\(TodayModel.fmt($0))%" })
              mini(tr("睡眠债", "Debt"), m.debt.map(TodayModel.hm))
            }
          }
        }
      }
      .buttonStyle(PressScale())
    }

    section(tr("和平时比", "Against usual"))
    Card {
      VStack(spacing: 0) {
        Grid(alignment: .trailing, horizontalSpacing: 10, verticalSpacing: 8) {
          GridRow {
            Text("").gridColumnAlignment(.leading)
            head(tr("这晚", "Night")); head(tr("7 晚均", "7-night")); head(tr("30 晚均", "30-night"))
          }
          ForEach(m.compare) { r in
            GridRow {
              Text(r.label).font(Typo.caption).foregroundColor(Palette.text2).gridColumnAlignment(.leading)
              Text(r.night).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
              Text(r.avg7).font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
              Text(r.avg30).font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
            }
          }
        }
        KVRow(label: tr("规律性", "Consistency"),
              value: "\(m.regularity7.map { "\(TodayModel.fmt($0))%" } ?? "—") · \(m.regularity30.map { "\(TodayModel.fmt($0))%" } ?? "—")",
              unit: "7d · 30d") { explain("sleep_consistency", tr("规律性", "Consistency")) }
          .padding(.top, 8)
      }
    }

    if !m.vitals.isEmpty {
      HStack {
        SectionHeader(title: tr("夜间指标", "Overnight"))
        Text(tr("vs 30 晚均", "vs 30-night")).font(Typo.label).foregroundColor(Palette.text3)
      }
      .padding(.top, 22).padding(.bottom, 10)
      LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
        ForEach(m.vitals) { v in
          VStack(alignment: .leading, spacing: 4) {
            Text(v.label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
              Text(v.value).font(Typo.tileValue).foregroundColor(Palette.text).lineLimit(1).minimumScaleFactor(0.6)
              Text(v.unit).font(Typo.unit).foregroundColor(Palette.text2)
            }
            if let d = v.delta { Text(d).font(Typo.label.monospacedDigit()).foregroundColor(v.worse ? Palette.mid : Palette.text3) }
          }
          .frame(maxWidth: .infinity, alignment: .leading)
          .padding(12)
          .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
        }
      }
    }

    NavigationLink { BedtimeGoalPage() } label: { BedtimeGoalRow() }
      .buttonStyle(PressScale())
      .padding(.top, 22)

    if let p = m.aiPrompt { AskAIButton(prompt: p).padding(.top, 24) }
  }

  private func section(_ t: String) -> some View { SectionHeader(title: t).padding(.top, 22).padding(.bottom, 10) }

  private func head(_ t: String) -> some View { Text(t).font(Typo.label).foregroundColor(Palette.text3) }

  private func cell(_ label: String, _ v: String?, _ unit: String?, _ xp: String?) -> some View {
    let body = VStack(alignment: .leading, spacing: 3) {
      Text(label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.8)
      HStack(alignment: .firstTextBaseline, spacing: 1) {
        Text(v ?? "—").font(.system(size: 17, weight: .semibold).monospacedDigit()).foregroundColor(v == nil ? Palette.text3 : Palette.text)
        if let unit, v != nil { Text(unit).font(Typo.unit).foregroundColor(Palette.text2) }
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    return Group {
      if let xp { Button { explain(xp, label) } label: { body }.buttonStyle(PressScale()) } else { body }
    }
  }

  private func mini(_ label: String, _ v: String?) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.8)
      Text(v ?? "—").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  private func scoreTone(_ s: Double) -> Tone { s >= 80 ? .good : s >= 60 ? .mid : .bad }
}

/// Stage colours of the page (app.css .hy-*): deep / core / REM are the sleep colour at three strengths, awake is mid.
enum StageStyle {
  static func color(_ stage: String) -> Color {
    switch stage {
    case "deep": return Palette.sleep
    case "core", "unspecified": return Palette.sleep.opacity(0.62)
    case "rem": return Palette.sleep.opacity(0.36)
    default: return Palette.mid.opacity(0.8)
    }
  }
}

/// The stage timeline (Swift Charts): one lane per stage, a bar per segment, minutes since falling asleep on x.
struct Hypnogram: View {
  let stages: [SleepNightModel.Stage]
  let total: Double
  let start: String
  let end: String

  var body: some View {
    VStack(spacing: 4) {
      Chart {
        ForEach(Array(stages.enumerated()), id: \.offset) { _, s in
          BarMark(xStart: .value("start", s.offset), xEnd: .value("end", s.offset + max(s.minutes, 1.2)),
                  y: .value("stage", SleepNightModel.stageName(lane(s.stage))), height: .fixed(12))
            .foregroundStyle(StageStyle.color(s.stage))
            .clipShape(RoundedRectangle(cornerRadius: 3))
        }
      }
      .chartXScale(domain: 0...total)
      .chartYScale(domain: SleepNightModel.lanes.map(SleepNightModel.stageName))
      .chartXAxis(.hidden)
      .chartYAxis {
        AxisMarks(position: .leading) { _ in
          AxisGridLine().foregroundStyle(Palette.hair)
          AxisValueLabel().font(.system(size: 10, weight: .medium)).foregroundStyle(Palette.text3)
        }
      }
      .frame(height: 112)
      HStack {
        Text(start); Spacer(); Text(end)
      }
      .font(.system(size: 11, weight: .medium).monospacedDigit()).foregroundColor(Palette.text3)
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(tr("睡眠阶段图 \(start) 到 \(end)", "Sleep stages \(start) to \(end)"))
  }

  private func lane(_ s: String) -> String { s == "unspecified" ? "core" : s }
}

/// Share of each stage as one rounded bar.
struct CompositionBar: View {
  let parts: [(String, Double, Double)]

  var body: some View {
    GeometryReader { g in
      let total = max(1, parts.reduce(0) { $0 + $1.1 })
      HStack(spacing: 1) {
        ForEach(Array(parts.enumerated()), id: \.offset) { _, p in
          if p.1 > 0 { Rectangle().fill(StageStyle.color(p.0)).frame(width: max(0.5, g.size.width * p.1 / total - 1)) }
        }
      }
    }
    .frame(height: 10)
    .clipShape(Capsule())
    .accessibilityHidden(true)
  }
}

/// One part of the sleep score (duration / stages / consistency / interruptions) with its weight.
struct PartBar: View {
  let part: SleepNightModel.Part

  var body: some View {
    HStack(spacing: 8) {
      HStack(spacing: 3) {
        Text(SleepNightModel.partName(part.key)).font(Typo.label).foregroundColor(Palette.text2)
        Text("\(TodayModel.fmt(part.weight * 100))%").font(.system(size: 10, weight: .medium)).foregroundColor(Palette.text3)
      }
      .frame(width: 104, alignment: .leading)
      GeometryReader { g in
        ZStack(alignment: .leading) {
          Capsule().fill(Palette.surface2)
          Capsule().fill(tone.color).frame(width: g.size.width * CGFloat(min(100, max(0, part.score ?? 0)) / 100))
        }
      }
      .frame(height: 6)
      Text(part.score.map { TodayModel.fmt($0) } ?? "—").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text)
        .frame(width: 26, alignment: .trailing)
    }
  }

  private var tone: Tone {
    guard let s = part.score else { return .text }
    return s >= 80 ? .good : s >= 60 ? .mid : .bad
  }
}
