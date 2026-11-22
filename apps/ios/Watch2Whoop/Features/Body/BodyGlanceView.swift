import SwiftUI

/// What the 身体 first glance asks of `TrendsView` (the tab's state stays there).
struct BodyGlanceActions {
  var setRange: (TrendRange) -> Void
  var pickEnd: () -> Void
  var backToToday: () -> Void
  /// A door to a level below (2.1.0, drilldown option C′): a metric page (the hero's number, a face), level 2 in a mode
  /// (周报 = 报告). The second value is the door it grows out of (`.zoomDoor`).
  var level: (LevelRoute, String?) -> Void
  /// Level 2 in the mode last used (the bars, the ladder).
  var all: () -> Void
  var syncNow: () -> Void
  var syncCenter: () -> Void
}

/// The first glance of 身体 (docs/design/2.0.1 `05-body`, drilldown `c-body` C′): range menu + the depth ladder + sync
/// capsule, the selected score over the range (mean, change vs the previous period, bars on your usual; swipe or page dots
/// = 恢复 / 睡眠 / 负荷), one row of faces (心肺, 身体年龄, 周报). Nothing below it. The score's label carries the door mark
/// 「›」 and opens its metric page, the bars open level 2 (every trend of the range, 睡眠, 报告), each face its own page.
struct BodyGlancePage: View {
  let page: TrendsPage?
  let range: TrendRange
  let end: String?
  let loading: Bool
  let session: AccountSession
  let run: BodyGlanceActions
  @SceneStorage("w2w.body.score") private var score: BodyScore = .recovery
  @Environment(\.askAI) private var askAI

  var body: some View {
    VStack(spacing: 0) {
      header
      hero.padding(.top, Rhythm.gap(32))
      faces.padding(.top, Rhythm.gap(40))
    }
  }

  // MARK: header: range menu (7 / 30 / 90 / 365, the end date) + sync capsule

  private var header: some View {
    HStack(spacing: 8) {
      Menu {
        ForEach(BodyParams.menuRanges) { r in
          Button { run.setRange(r) } label: { Text(Self.rangeTitle(r)) }
        }
        Button(action: run.pickEnd) { Text(tr("选择结束日期", "Pick the end date")) }
      } label: {
        HStack(spacing: 4) {
          Text(Self.rangeTitle(range)).font(Typo.header).foregroundColor(Palette.text)
          Icon(.chevronDown, size: 13).foregroundColor(Palette.text2)
        }
        .frame(minHeight: 44)
        .contentShape(Rectangle())
      }
      .accessibilityLabel(tr("选择时间范围", "Pick the range"))
      if let end {
        Button(action: run.backToToday) { Chip(label: tr("截至 \(TodayModel.shortDay(end))", "To \(TodayModel.shortDay(end))")) }
          .buttonStyle(PressScale())
          .accessibilityLabel(tr("回到今天", "Back to today"))
      }
      Spacer(minLength: 0)
      // a look-back's 「截至」 chip takes the middle: the ladder moves next to the capsule
      if end != nil { ladder }
      TodaySyncCapsule(card: nil, page: session.sync, tap: run.syncNow, center: run.syncCenter)
    }
    .frame(height: 44)
    .overlay { if end == nil { ladder } }
  }

  private var ladder: some View { LevelLadder(level: 1, down: run.all) }

  /// 「近 30 天」 / "Last 30 d"; the one-day range of 2.0.0 reads 「今天」.
  static func rangeTitle(_ r: TrendRange) -> String {
    r == .today ? tr("今天", "Today") : tr("近 \(r.rawValue) 天", "Last \(r.rawValue) d")
  }

  // MARK: hero

  @ViewBuilder private var hero: some View {
    let h = page.map { BodyGlanceRules.hero($0.data, score: score, range: range) }
    VStack(alignment: .leading, spacing: 0) {
      Button(action: openScore) {
        VStack(alignment: .leading, spacing: 0) {
          HStack(spacing: 3) {
            Text(score.label).font(.system(size: 13, weight: .medium)).tracking(2).foregroundColor(Palette.text2)
            DoorMark()
          }
          .frame(height: 16)
          mean(h).padding(.top, 4)
        }
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .zoomDoor("glance.hero")
      .accessibilityHint(tr("打开\(score.label)的趋势", "Opens the \(score.label.lowercased()) trend"))
      Button(action: run.all) {
        UsualBars(values: h?.bars ?? [], usual: h?.usual, max: score.max, tone: h?.lastTone ?? .text)
          .frame(height: BodyParams.barsHeightHere)
          .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .zoomDoor("glance.bars")
      .accessibilityLabel(tr("全部趋势", "All trends"))
      dateRow(h?.dates ?? []).padding(.top, 6)
      pageDots.padding(.top, Rhythm.gap(20))
    }
    .contentShape(Rectangle())
    .gesture(DragGesture(minimumDistance: 24).onEnded { g in
      guard abs(g.translation.width) > abs(g.translation.height) else { return }
      step(g.translation.width < 0 ? 1 : -1)
    })
    .contextMenu {
      Button { askAI(prompt(h)) } label: { Text(tr("问 Jarvis：\(score.label)", "Ask Jarvis: \(score.label)")) }
      Button(action: openScore) { Text(tr("看详情", "Details")) }
      Button(action: run.all) { Text(tr("全部趋势", "All trends")) }
    }
    .accessibilityAction(named: Text(tr("下一个分数", "Next score"))) { step(1) }
    .accessibilityAction(named: Text(tr("上一个分数", "Previous score"))) { step(-1) }
  }

  @ViewBuilder private func mean(_ h: BodyHero?) -> some View {
    let k = Rhythm.heroScale
    HStack(alignment: .lastTextBaseline, spacing: 0) {
      if let m = h?.mean {
        Text(TodayModel.fmt(m, digits: score.digits)).font(Typo.hero(100 * k)).tracking(-3 * k).foregroundColor(Palette.text)
          .lineLimit(1).minimumScaleFactor(0.6)
          .overlay(alignment: .topTrailing) {
            if score.percent {
              Text("%").font(.system(size: 20 * k, weight: .light)).foregroundColor(Palette.text2).offset(x: 18 * k, y: 12 * k)
            }
          }
      } else {
        Text(loading ? " " : "—").font(Typo.hero(100 * k)).foregroundColor(Palette.text2)
      }
      if let d = h?.delta {
        VStack(alignment: .leading, spacing: 3) {
          Text(BodyGlanceRules.signed(d, digits: score.digits)).font(.system(size: 22, weight: .medium).monospacedDigit())
            .foregroundColor(h?.deltaTone == .text ? Palette.text : (h?.deltaTone ?? .text).color)
          Text(tr("比上期", "vs before")).font(Typo.label).foregroundColor(Palette.text2)
        }
        .padding(.leading, (score.percent ? 34 : 18) * k)
      }
      Spacer(minLength: 0)
    }
    .frame(height: 100 * k)
    .accessibilityElement(children: .combine)
  }

  private func dateRow(_ dates: [String]) -> some View {
    HStack(spacing: 0) {
      ForEach(Array(dates.enumerated()), id: \.offset) { i, d in
        if i > 0 { Spacer(minLength: 4) }
        Text(d)
      }
    }
    .font(Typo.axis).foregroundColor(Palette.text2)
    .frame(height: 12)
  }

  private var pageDots: some View {
    HStack(spacing: 8) {
      ForEach(BodyScore.allCases) { s in
        Button { withAnimation(.easeOut(duration: 0.2)) { score = s } } label: {
          Circle().fill(s == score ? Palette.text : Palette.past).frame(width: 6, height: 6)
            .frame(width: 22, height: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(s.label)
        .accessibilityAddTraits(s == score ? .isSelected : [])
      }
    }
    .frame(maxWidth: .infinity)
  }

  private func step(_ by: Int) {
    let all = BodyScore.allCases
    guard let i = all.firstIndex(of: score) else { return }
    let j = (i + by + all.count) % all.count
    withAnimation(.easeOut(duration: 0.2)) { score = all[j] }
  }

  /// The score's own metric page, in the tab's range.
  private func openScore() { run.level(.metric(score.seriesKey), "glance.hero") }

  private func prompt(_ h: BodyHero?) -> String {
    let n = BodyGlanceRules.window(range)
    guard let m = h?.mean else { return tr("我近 \(n) 天的\(score.label)怎么样？", "How was my \(score.label) over \(n) days?") }
    let v = TodayModel.fmt(m, digits: score.digits)
    return tr("我近 \(n) 天\(score.label) \(v)，帮我解读一下", "My \(score.label) over \(n) days is \(v). What does it mean?")
  }

  // MARK: faces: 心肺 (HRV), 身体年龄, 周报

  private var faces: some View {
    HStack(spacing: 8) {
      if let d = page?.data, let hrv = BodyGlanceRules.hrv(d) {
        Button { run.level(.metric("hrv"), "glance.cardio") } label: {
          Face(icon: .heart, label: tr("心肺", "Cardio"), number: TodayModel.fmt(hrv.last), unit: "ms") {
            BandSpark(values: hrv.values, usual: hrv.usual)
          }
        }
        .buttonStyle(PressScale())
        .zoomDoor("glance.cardio")
      }
      BodyAgeFace(session: session) { run.level(.metric("body_age"), "glance.bodyAge") }.zoomDoor("glance.bodyAge")
      if let d = page?.data {
        let days = BodyGlanceRules.trainingDays(d)
        if days.contains(where: { $0 != nil }) {
          Button { run.level(.all(.report), "glance.report") } label: {
            Face(icon: .calendarDays, label: tr("周报", "Weekly"), number: "\(days.filter { $0 == true }.count)",
                 unit: tr("天训练", "d trained")) {
              WeekTrainingDots(days: days)
            }
          }
          .buttonStyle(PressScale())
          .zoomDoor("glance.report")
        }
      }
    }
  }
}

/// The 身体年龄 face: the body age, and a gauge of calendar age ± 8 years (centre tick = calendar age, bar from there to the
/// body age: green = younger, red = older). No body age yet: no face.
private struct BodyAgeFace: View {
  let open: () -> Void
  @StateObject private var store = DetailStore<BodyAgeModel>()
  private let session: AccountSession

  init(session: AccountSession, open: @escaping () -> Void) {
    self.session = session
    self.open = open
  }

  var body: some View {
    Group {
      if let m = store.model, let age = m.bodyAge {
        Button(action: open) {
          Face(icon: .personStanding, label: tr("身体年龄", "Body age"), number: TodayModel.fmt(age, digits: 1), unit: tr("岁", "y")) {
            if let c = m.chronoAge { AgeGauge(body: age, chrono: c) } else { Color.clear }
          }
        }
        .buttonStyle(PressScale())
      }
    }
    .onAppear {
      store.start(session, fetch: BodyAgeDetail.fetch, parse: BodyAgeModel.init(json:)) { try BodyAgeDetail.read($0) }
    }
  }
}

/// Calendar age as the middle tick, body age as a bar from it (bullet gauge grammar, direction.md 2.5).
struct AgeGauge: View {
  let body_: Double
  let chrono: Double

  init(body b: Double, chrono c: Double) {
    body_ = b
    chrono = c
  }

  var body: some View {
    GeometryReader { g in
      let w = g.size.width
      let p = BodyGlanceRules.agePositions(body: body_, chrono: chrono)
      let lo = CGFloat(Swift.min(p.body, p.chrono)) * w, hi = CGFloat(Swift.max(p.body, p.chrono)) * w
      let tone: Tone = body_ < chrono ? .good : body_ > chrono ? .bad : .text
      ZStack(alignment: .topLeading) {
        Rectangle().fill(Palette.track).frame(width: w, height: 1.5).offset(y: 5.25)
        Capsule().fill(tone.color).frame(width: Swift.max(4, hi - lo), height: 4).offset(x: lo, y: 4)
        Rectangle().fill(Palette.tickMajor).frame(width: 1.5, height: 12).offset(x: w / 2 - 0.75)
        Canvas { ctx, size in
          for i in 0...8 {
            let x = Swift.min(size.width - 0.5, CGFloat(i) / 8 * size.width)
            let major = i % 4 == 0
            var path = Path()
            path.move(to: CGPoint(x: x, y: 0))
            path.addLine(to: CGPoint(x: x, y: major ? 5 : 3))
            ctx.stroke(path, with: .color(major ? Palette.tickMajor : Palette.tick), lineWidth: 1)
          }
        }
        .frame(width: w, height: 5)
        .offset(y: 17)
      }
    }
    .frame(height: 22)
    .accessibilityHidden(true)
  }
}

/// Seven dots, one per day: filled `train` = trained, ring = no training, faint = no reading.
struct WeekTrainingDots: View {
  let days: [Bool?]

  var body: some View {
    HStack(spacing: 0) {
      ForEach(Array(days.enumerated()), id: \.offset) { i, d in
        if i > 0 { Spacer(minLength: 2) }
        if d == true {
          Circle().fill(Palette.train).frame(width: 9, height: 9)
        } else {
          Circle().stroke(Palette.track, lineWidth: 1.5).frame(width: 7.5, height: 7.5).opacity(d == nil ? 0.5 : 1)
        }
      }
    }
    .frame(height: 10)
    .accessibilityHidden(true)
  }
}
