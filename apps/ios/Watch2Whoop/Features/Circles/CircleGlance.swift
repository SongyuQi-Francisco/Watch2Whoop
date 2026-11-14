import SwiftUI

/// Display constants of the 2.0.1 圈子 first glance (docs/design/2.0.1 `06-circles`). Not health thresholds.
enum CircleGlanceParams {
  /// Smallest gap between two avatars on the dial, as a share of the circle (a 40 pt avatar on a 280 pt dial).
  static let minGap = 0.045
  /// Avatar size on the dial at 280 pt.
  static let avatar: CGFloat = 40
  /// People drawn on the dial at most (the rest are in 成员).
  static let maxOnDial = 12
}

/// One person on the circle dial.
struct CircleDialPerson: Equatable, Identifiable {
  let id: Int
  let name: String
  let avatar: String?
  let isSelf: Bool
  let trained: Bool
  /// Where on the dial (0…1 from 12 o'clock): their recovery / 100, spread so avatars never overlap.
  let at: Double
}

/// Pure rules of the 圈子 first glance (unit-tested in `CircleGlanceTests`).
enum CircleGlanceRules {
  /// Me first, then the others of the overview (the selected circle's, or everyone's).
  static func people(_ o: MemberOverview?) -> [MemberCard] {
    guard let o else { return [] }
    var out: [MemberCard] = []
    if let me = o.me { out.append(me) }
    for m in o.members where !m.isSelf { out.append(m) }
    return out
  }

  /// 「3/6 今天练了」.
  static func trained(_ people: [MemberCard]) -> (trained: Int, total: Int) {
    (people.filter { $0.trained == true }.count, people.count)
  }

  static func mean(_ xs: [Double?]) -> Double? {
    let v = xs.compactMap { $0 }
    return v.isEmpty ? nil : v.reduce(0, +) / Double(v.count)
  }

  /// The people with a shared recovery, placed at it on the 0–100 scale, pushed apart by at least `minGap`.
  static func dial(_ people: [MemberCard], minGap: Double = CircleGlanceParams.minGap,
                   limit: Int = CircleGlanceParams.maxOnDial) -> [CircleDialPerson] {
    var placed: [(MemberCard, Double)] = []
    for p in people {
      if let r = p.scores?.recovery { placed.append((p, Swift.min(1, Swift.max(0, r / 100)))) }
    }
    placed.sort { $0.1 < $1.1 }
    placed = Array(placed.prefix(limit))
    var out: [CircleDialPerson] = []
    var last = -1.0
    for (p, f) in placed {
      let at = last < 0 ? f : Swift.max(f, last + minGap)
      last = at
      out.append(CircleDialPerson(id: p.id, name: p.displayName, avatar: p.avatar, isSelf: p.isSelf || p.shared == nil,
                                  trained: p.trained == true, at: Swift.min(at, 0.999)))
    }
    return out
  }

  /// My place on the leaderboard: (rank, of how many ranked); nil when I am not ranked.
  static func myRank(_ b: Leaderboard?) -> (rank: Int, of: Int)? {
    guard let b, let me = b.rows.first(where: { $0.isSelf }), let r = me.rank else { return nil }
    return (r, b.rows.filter { $0.rank != nil }.count)
  }

  /// The leaderboard's scores in rank order (the 排行 face's bars), mine marked.
  static func rankBars(_ b: Leaderboard?, limit: Int = 6) -> [(score: Double, mine: Bool)] {
    guard let b else { return [] }
    let ranked = b.rows.filter { $0.rank != nil && $0.score != nil }.sorted { ($0.rank ?? 0) < ($1.rank ?? 0) }
    return ranked.prefix(limit).map { ($0.score ?? 0, $0.isSelf) }
  }
}

/// The circle dial: the recovery scale (ticks, zone edges), each person's avatar at their recovery (ring in `train` =
/// trained today, mine dashed), the trained count in the middle.
struct CircleDial: View {
  let people: [CircleDialPerson]
  let trained: Int
  let total: Int
  let side: CGFloat
  let tap: (CircleDialPerson) -> Void

  var body: some View {
    let k = side / 280, r = side / 2 - 2
    ZStack {
      TickDial(fraction: 0, tone: .text, zones: TodayGlanceRules.recoveryZones.map { $0 / 100 }, side: side) {
        VStack(spacing: 0) {
          Text(tr("今天练了", "Trained today")).font(.system(size: 13, weight: .medium)).tracking(2).foregroundColor(Palette.text2)
          Text("\(trained)").font(Typo.hero(100 * k)).tracking(-3 * k).foregroundColor(Palette.text)
            .overlay(alignment: .topTrailing) {
              Text("/\(total)").font(.system(size: 20 * k, weight: .light).monospacedDigit()).foregroundColor(Palette.text2)
                .fixedSize().offset(x: 30 * k, y: 12 * k)
            }
        }
      }
      ForEach(people) { p in
        let a = Angle.degrees(p.at * 360 - 90).radians
        Button { tap(p) } label: { avatar(p, size: CircleGlanceParams.avatar * k) }
          .buttonStyle(PressScale())
          .offset(x: CGFloat(cos(a)) * r, y: CGFloat(sin(a)) * r)
          .accessibilityLabel(p.name)
      }
    }
    .frame(width: side, height: side)
  }

  private func avatar(_ p: CircleDialPerson, size: CGFloat) -> some View {
    AvatarView(url: p.avatar, name: p.isSelf ? tr("我", "Me") : p.name, size: size)
      .background(Circle().fill(Palette.surface))
      .overlay(
        Circle().stroke(p.trained ? Palette.train : Palette.track,
                        style: StrokeStyle(lineWidth: 2, dash: p.isSelf ? [2, 3] : []))
      )
      .frame(width: max(44, size), height: max(44, size))
      .contentShape(Circle())
  }
}

/// A reading of the circle: the group's mean, one dot per person on a 1.5 pt track (mine in the category colour), a tick
/// scale below.
struct GroupGauge: View {
  let values: [Double]
  let mine: Double?
  let mean: Double?
  let max: Double
  let step: Double
  let tone: Tone

  var body: some View {
    GeometryReader { g in
      let w = g.size.width
      let x = { (v: Double) in CGFloat(TodayGlanceRules.fraction(v, max: max)) * w }
      ZStack(alignment: .topLeading) {
        Rectangle().fill(Palette.track).frame(width: w, height: 1.5).offset(y: 5.25)
        if let mean { Rectangle().fill(Palette.tickMajor).frame(width: 1.5, height: 12).offset(x: x(mean) - 0.75) }
        ForEach(Array(values.enumerated()), id: \.offset) { _, v in
          Circle().fill(Palette.text2).frame(width: 7, height: 7).offset(x: x(v) - 3.5, y: 2.5)
        }
        if let mine { Circle().fill(tone.color).frame(width: 9, height: 9).offset(x: x(mine) - 4.5, y: 1.5) }
        Canvas { ctx, size in
          var v = 0.0
          while v <= max + 0.0001 {
            let px = Swift.min(size.width - 0.75, Swift.max(0.75, CGFloat(v / max) * size.width))
            var p = Path()
            p.move(to: CGPoint(x: px, y: 0))
            p.addLine(to: CGPoint(x: px, y: 4))
            ctx.stroke(p, with: .color(Palette.tick), lineWidth: 1)
            v += step
          }
        }
        .frame(width: w, height: 6)
        .offset(y: 16)
      }
    }
    .frame(height: 22)
    .accessibilityHidden(true)
  }
}

/// The 排行 face's bars: the board's scores in rank order, mine in ink, the others in `past`.
struct RankBars: View {
  let bars: [(score: Double, mine: Bool)]

  var body: some View {
    let top = bars.map(\.score).max() ?? 1
    HStack(alignment: .bottom, spacing: 0) {
      ForEach(Array(bars.enumerated()), id: \.offset) { i, b in
        if i > 0 { Spacer(minLength: 2) }
        Capsule().fill(b.mine ? Palette.text : Palette.past)
          .frame(width: 7, height: Swift.max(7, CGFloat(top > 0 ? b.score / top : 0) * 26))
      }
    }
    .frame(height: 26, alignment: .bottom)
    .accessibilityHidden(true)
  }
}
