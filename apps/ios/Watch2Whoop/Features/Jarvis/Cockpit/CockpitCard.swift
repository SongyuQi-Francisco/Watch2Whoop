import SwiftUI

/// The reply card that unfolds above the core (jarvis-cockpit.html: "a line leaves the core, widens, unfolds upwards").
/// `widthShare` and `heightShare` are the machine's springs; each row fades in as the card's top passes it (`rv`).
struct CockpitCardFrame: View {
  let reply: CockpitReply
  @ObservedObject var controller: CockpitController
  let widthShare: Double
  let heightShare: Double
  let reduceMotion: Bool

  private let W = CockpitGeometry.cardWidth
  private var H: CGFloat { reply.height }
  /// The unfolded height now.
  private var h: CGFloat { reduceMotion ? H : max(0, H * CGFloat(heightShare)) }
  private var w: CGFloat { reduceMotion ? W : W * CGFloat(widthShare) }

  var body: some View {
    let contentOpacity = reduceMotion ? widthShare : min(1, max(0, widthShare * 3 - 2))
    ZStack(alignment: .bottom) {
      CockpitCardShape(chamfer: min(18, h / 2))
        .fill(CockpitPalette.panel.opacity(0.97))
        .overlay(CockpitCardShape(chamfer: min(18, h / 2)).stroke(CockpitPalette.accent.opacity(0.5), lineWidth: 1))
        .overlay(alignment: .topLeading) {
          Capsule().fill(CockpitPalette.accent).frame(width: 44 * CGFloat(min(1, widthShare)), height: 2).offset(x: 12, y: -1)
        }
        .frame(width: w, height: h)
      content
        .frame(width: W, height: H, alignment: .topLeading)
        .frame(width: w, height: h, alignment: .top)
        .clipped()
        .opacity(contentOpacity)
    }
    .frame(width: W, height: H, alignment: .bottom)
    .shadow(color: CockpitPalette.accent.opacity(0.25), radius: 6)
    .environment(\.colorScheme, .dark)
  }

  /// A row whose bottom sits `bottom` points below the card's top shows once the card is that tall.
  private func rv(_ bottom: CGFloat) -> Double { min(1, max(0, Double((h - bottom) / 26))) }

  @ViewBuilder private var content: some View {
    switch reply {
    case .answer(let a): answer(a)
    case .meal(let m): meal(m)
    case .set(let s): set(s)
    }
  }

  // MARK: - 问答结论

  private func answer(_ a: CockpitReply.Answer) -> some View {
    let tall = a.gauge != nil || !a.readings.isEmpty
    return VStack(alignment: .leading, spacing: 0) {
      HStack(spacing: 8) {
        CockpitTriangle().stroke(CockpitPalette.accent, style: StrokeStyle(lineWidth: 1.4, lineJoin: .round)).frame(width: 12, height: 10)
        Text(a.question.isEmpty ? "Jarvis" : a.question).font(.system(size: 12, weight: .medium)).foregroundColor(CockpitPalette.dim).lineLimit(1)
      }
      .frame(height: 20).padding(.top, 14).opacity(rv(28))
      HStack(alignment: .center, spacing: 12) {
        VStack(alignment: .leading, spacing: 6) {
          Text(a.verdict).font(.system(size: 30, weight: .semibold)).foregroundColor(CockpitPalette.ink).lineLimit(1).minimumScaleFactor(0.6)
            .opacity(rv(78))
          if let sub = a.sub {
            Text(sub).font(.system(size: 13, weight: .medium)).foregroundColor(CockpitPalette.dim).lineLimit(1).opacity(rv(102))
          }
        }
        Spacer(minLength: 0)
        if let g = a.gauge {
          ZStack {
            Circle().stroke(Color.white.opacity(0.14), lineWidth: 4)
            Circle().trim(from: 0, to: CGFloat(g.fraction * rv(110)))
              .stroke(g.tone == .text ? CockpitPalette.good : g.tone.color, style: StrokeStyle(lineWidth: 4, lineCap: .round))
              .rotationEffect(.degrees(-90))
            Text(g.value).font(.system(size: 17, weight: .semibold)).foregroundColor(CockpitPalette.ink).opacity(rv(80))
          }
          .frame(width: 52, height: 52)
          .accessibilityElement(children: .ignore)
          .accessibilityLabel(tr("恢复 \(g.value)%", "Recovery \(g.value)%"))
        }
      }
      .padding(.top, 14)
      if tall {
        Rectangle().fill(Color.white.opacity(0.1 * rv(122))).frame(height: 1).padding(.top, 14)
        HStack(spacing: 0) {
          ForEach(Array(a.readings.prefix(3).enumerated()), id: \.offset) { _, r in
            HStack(alignment: .firstTextBaseline, spacing: 5) {
              Text(r.value).font(.system(size: 17, weight: .semibold)).foregroundColor(CockpitPalette.ink)
              Text(r.label).font(.system(size: 10.5, weight: .medium)).foregroundColor(CockpitPalette.dim)
            }
            .frame(width: 104, alignment: .leading)
            .accessibilityElement(children: .combine)
          }
          Spacer(minLength: 0)
        }
        .padding(.top, 10).opacity(rv(150))
      }
      Spacer(minLength: 8)
      HStack {
        if let p = a.primary {
          Button { controller.openPrimary(p.page) } label: {
            Text(p.title).font(.system(size: 13, weight: .semibold)).foregroundColor(CockpitPalette.accent)
              .frame(minWidth: 112, minHeight: 32).padding(.horizontal, 6)
              .background(Capsule().fill(CockpitPalette.accent.opacity(0.1)))
              .overlay(Capsule().stroke(CockpitPalette.accent.opacity(0.8 * rv(196)), lineWidth: 1))
          }
          .buttonStyle(PressScale())
        }
        Spacer()
        Button { controller.openChat() } label: {
          Text(tr("展开对话 ›", "Open chat ›")).font(.system(size: 12, weight: .medium)).foregroundColor(CockpitPalette.dim)
            .frame(minHeight: 32).contentShape(Rectangle())
        }
        .buttonStyle(PressScale())
      }
      .padding(.bottom, 16).opacity(rv(196))
    }
    .padding(.horizontal, 20)
  }

  // MARK: - 已记到…

  private func logged(_ title: String) -> some View {
    HStack(spacing: 6) {
      Icon(.circleCheck, size: 16).foregroundColor(CockpitPalette.good)
      Text(title).font(.system(size: 12, weight: .medium)).foregroundColor(CockpitPalette.dim).lineLimit(1)
      Spacer()
      if controller.canUndo {
        Button { controller.undoReply() } label: {
          HStack(spacing: 4) {
            Icon(.undo2, size: 12)
            Text(tr("撤销", "Undo")).font(.system(size: 12, weight: .medium))
          }
          .foregroundColor(CockpitPalette.ink)
          .frame(width: 66, height: 26)
          .overlay(Capsule().stroke(Color.white.opacity(0.25), lineWidth: 1))
          .contentShape(Rectangle())
          .opacity(controller.undoing ? 0.4 : 1)
        }
        .buttonStyle(PressScale())
        .disabled(controller.undoing)
        .frame(minHeight: 44)
      }
    }
    .frame(height: 44)
    .padding(.top, 1)
    .opacity(rv(28))
  }

  private func meal(_ m: CockpitReply.Meal) -> some View {
    VStack(alignment: .leading, spacing: 0) {
      logged(tr("已记到\(m.slot)", "Logged to \(m.slot)"))
      HStack(alignment: .firstTextBaseline) {
        Text(m.name).font(.system(size: 26, weight: .semibold)).foregroundColor(CockpitPalette.ink).lineLimit(1).minimumScaleFactor(0.6)
        Spacer()
        if let k = m.kcal {
          Text(CockpitText.kcal(k)).font(.system(size: 26, weight: .semibold)).foregroundColor(CockpitPalette.ink)
          Text("kcal").font(.system(size: 11, weight: .medium)).foregroundColor(CockpitPalette.dim)
        }
      }
      .padding(.top, 8).opacity(rv(80))
      HStack(spacing: 16) {
        ForEach(Array(m.macros.enumerated()), id: \.offset) { _, x in
          VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
              Text(x.label).font(.system(size: 10.5, weight: .medium)).foregroundColor(CockpitPalette.dim)
              Text(x.grams.map { "\(RecordFormat.num($0)) g" } ?? "—").font(.system(size: 13, weight: .semibold)).foregroundColor(CockpitPalette.ink)
            }
            ZStack(alignment: .leading) {
              Capsule().fill(Color.white.opacity(0.12))
              if let f = x.fraction { Capsule().fill(CockpitPalette.ink).frame(width: 92 * CGFloat(f * rv(140))) }
            }
            .frame(width: 92, height: 3)
          }
          .accessibilityElement(children: .combine)
        }
      }
      .padding(.top, 14).opacity(rv(112))
      dayBar(m).padding(.top, 20).opacity(rv(176))
      Spacer(minLength: 0)
    }
    .padding(.horizontal, 20)
  }

  private func dayBar(_ m: CockpitReply.Meal) -> some View {
    let bw: CGFloat = W - 40
    let full = max(m.goal ?? 0, m.dayAfter, 1)
    let a0 = bw * CGFloat(min(1, m.dayBefore / full)), a1 = bw * CGFloat(min(1, (m.dayAfter - m.dayBefore) / full)) * CGFloat(rv(176))
    return VStack(alignment: .leading, spacing: 10) {
      ZStack(alignment: .leading) {
        Capsule().fill(Color.white.opacity(0.12)).frame(width: bw)
        Capsule().fill(Color.white.opacity(0.55)).frame(width: a0)
        Capsule().fill(CockpitPalette.accent).frame(width: max(0, min(a1, bw - a0 - 2))).offset(x: a0 + 2)
      }
      .frame(height: 4)
      Text(m.goal.map { tr("今天 \(CockpitText.kcal(m.dayAfter)) / \(CockpitText.kcal($0)) kcal", "Today \(CockpitText.kcal(m.dayAfter)) / \(CockpitText.kcal($0)) kcal") }
           ?? tr("今天 \(CockpitText.kcal(m.dayAfter)) kcal", "Today \(CockpitText.kcal(m.dayAfter)) kcal"))
        .font(.system(size: 11, weight: .medium)).foregroundColor(CockpitPalette.dim)
    }
  }

  // MARK: - 已记一组

  private func set(_ s: CockpitReply.SetLog) -> some View {
    VStack(alignment: .leading, spacing: 0) {
      logged(s.session.isEmpty ? tr("已记一组", "Set logged") : tr("已记到 \(s.session)", "Logged to \(s.session)"))
      HStack(alignment: .firstTextBaseline) {
        Text(s.exercise).font(.system(size: 26, weight: .semibold)).foregroundColor(CockpitPalette.ink).lineLimit(1).minimumScaleFactor(0.6)
        Spacer()
        Text(s.set).font(.system(size: 20, weight: .semibold)).foregroundColor(CockpitPalette.ink)
      }
      .padding(.top, 8).opacity(rv(80))
      HStack(spacing: 8) {
        HStack(spacing: 8) {
          ForEach(0..<max(s.planned, s.index), id: \.self) { i in
            Circle()
              .fill(i < s.index - 1 ? CockpitPalette.past.opacity(0.55) : i == s.index - 1 ? CockpitPalette.accent : Color.white.opacity(0.14))
              .frame(width: 10, height: 10)
              .opacity(rv(124 + CGFloat(i) * 4))
          }
        }
        .accessibilityHidden(true)
        Text(tr("第 \(s.index) 组", "Set \(s.index)")).font(.system(size: 12, weight: .medium)).foregroundColor(CockpitPalette.dim)
        Spacer()
        Text(tr("\(s.done) / \(s.total) 组", "\(s.done) / \(s.total) sets")).font(.system(size: 12, weight: .medium)).foregroundColor(CockpitPalette.dim)
      }
      .padding(.top, 22).opacity(rv(130))
      Spacer(minLength: 0)
    }
    .padding(.horizontal, 20)
  }
}

/// The card's outline: rounded corners, the top right cut at 45° (the prototype's chamfer).
struct CockpitCardShape: Shape {
  var chamfer: CGFloat

  func path(in r: CGRect) -> Path {
    var p = Path()
    let rr: CGFloat = 6
    p.move(to: CGPoint(x: r.minX, y: r.minY + rr))
    p.addQuadCurve(to: CGPoint(x: r.minX + rr, y: r.minY), control: CGPoint(x: r.minX, y: r.minY))
    p.addLine(to: CGPoint(x: r.maxX - chamfer, y: r.minY))
    p.addLine(to: CGPoint(x: r.maxX, y: r.minY + chamfer))
    p.addLine(to: CGPoint(x: r.maxX, y: r.maxY - rr))
    p.addQuadCurve(to: CGPoint(x: r.maxX - rr, y: r.maxY), control: CGPoint(x: r.maxX, y: r.maxY))
    p.addLine(to: CGPoint(x: r.minX + rr, y: r.maxY))
    p.addQuadCurve(to: CGPoint(x: r.minX, y: r.maxY - rr), control: CGPoint(x: r.minX, y: r.maxY))
    p.closeSubpath()
    return p
  }
}

/// The inverted triangle of the core, as a small mark.
struct CockpitTriangle: Shape {
  func path(in r: CGRect) -> Path {
    var p = Path()
    p.move(to: CGPoint(x: r.minX, y: r.minY))
    p.addLine(to: CGPoint(x: r.maxX, y: r.minY))
    p.addLine(to: CGPoint(x: r.midX, y: r.maxY))
    p.closeSubpath()
    return p
  }
}
