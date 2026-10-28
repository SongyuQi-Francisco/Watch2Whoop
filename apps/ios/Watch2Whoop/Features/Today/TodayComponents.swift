import SwiftUI

// Small building blocks of the Today tab, its sheets and the sleep detail page (the page's .badge, .scale, .kv, spark
// and tile markup). Kept here rather than in DesignSystem until a second page needs them.

/// A short coloured label (the page's `.badge good|mid|bad|plain`).
struct ToneBadge: View {
  let text: String
  var tone: Tone = .text

  var body: some View {
    Text(text).font(.system(size: 12, weight: .semibold)).lineLimit(1)
      .foregroundColor(tone == .text ? Palette.text2 : tone.color)
      .padding(.horizontal, 8).padding(.vertical, 3)
      .background(Capsule().fill(background))
  }

  private var background: Color {
    switch tone {
    case .good: return Palette.goodSoft
    case .mid: return Palette.midSoft
    case .bad: return Palette.badSoft
    default: return Palette.surface2
    }
  }
}

/// Level bar: colour segments between cuts and a tick for the value (app.js scaleBar), with the cut labels below.
struct ScaleBar: View {
  struct Segment { var to: Double; var color: Color }
  let value: Double?
  let lo: Double
  let hi: Double
  let segments: [Segment]
  let labels: [(Double, String)]

  var body: some View {
    VStack(spacing: 4) {
      GeometryReader { g in
        let w = g.size.width
        ZStack(alignment: .leading) {
          ForEach(Array(segments.enumerated()), id: \.offset) { i, s in
            let from = i == 0 ? lo : segments[i - 1].to
            Capsule().fill(s.color)
              .frame(width: max(2, x(s.to, w) - x(from, w) - 2), height: 8)
              .offset(x: x(from, w))
          }
          if let value {
            RoundedRectangle(cornerRadius: 1.5).fill(Palette.text).frame(width: 3, height: 16)
              .offset(x: min(w - 3, max(0, x(value, w) - 1.5)))
          }
        }
        .frame(height: 16)
      }
      .frame(height: 16)
      GeometryReader { g in
        ForEach(Array(labels.enumerated()), id: \.offset) { _, l in
          Text(l.1).font(.system(size: 10, weight: .medium).monospacedDigit()).foregroundColor(Palette.text3)
            .fixedSize()
            .position(x: min(g.size.width - 8, max(8, x(l.0, g.size.width))), y: 6)
        }
      }
      .frame(height: 12)
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(value.map { TodayModel.fmt($0, digits: 1) } ?? "—")
  }

  private func x(_ v: Double, _ w: CGFloat) -> CGFloat { CGFloat((min(hi, max(lo, v)) - lo) / (hi - lo)) * w }

  static func recovery(_ v: Double?) -> ScaleBar {
    ScaleBar(value: v, lo: 0, hi: 100,
             segments: [Segment(to: 34, color: Palette.bad.opacity(0.55)), Segment(to: 67, color: Palette.mid.opacity(0.55)),
                        Segment(to: 100, color: Palette.good.opacity(0.55))],
             labels: [(0, "0"), (34, "34"), (67, "67"), (100, "100")])
  }

  static func strain(_ v: Double?) -> ScaleBar {
    ScaleBar(value: v, lo: 0, hi: 21,
             segments: [Segment(to: 10, color: Palette.surface2), Segment(to: 14, color: Palette.train.opacity(0.3)),
                        Segment(to: 18, color: Palette.train.opacity(0.55)), Segment(to: 21, color: Palette.train.opacity(0.85))],
             labels: [(0, "0"), (10, "10"), (14, "14"), (18, "18"), (21, "21")])
  }

  static func tsb(_ v: Double?) -> ScaleBar {
    ScaleBar(value: v, lo: -45, hi: 25,
             segments: [Segment(to: -30, color: Palette.bad.opacity(0.55)), Segment(to: -10, color: Palette.mid.opacity(0.55)),
                        Segment(to: 10, color: Palette.surface2), Segment(to: 25, color: Palette.good.opacity(0.55))],
             labels: [(-30, "−30"), (-10, "−10"), (10, "+10")])
  }

  static func coach(_ v: Double?, low: Double, high: Double) -> ScaleBar {
    ScaleBar(value: v, lo: 0, hi: 21,
             segments: [Segment(to: low, color: Palette.surface2), Segment(to: high, color: Palette.good.opacity(0.55)),
                        Segment(to: 21, color: Palette.surface2)],
             labels: [(0, "0"), (low, TodayModel.fmt(low)), (high, TodayModel.fmt(high)), (21, "21")])
  }
}

/// A key–value row (the page's `.kv`): label left, value right; tappable rows open an explain sheet.
struct KVRow: View {
  let label: String
  var value: String?
  var unit: String?
  var tone: Tone = .text
  var badge: (String, Tone)?
  var action: (() -> Void)?

  var body: some View {
    let row = HStack(spacing: 6) {
      Text(label).font(Typo.body).foregroundColor(Palette.text2)
      if action != nil { Icon(.chevronRight, size: 13).foregroundColor(Palette.text3) }
      Spacer(minLength: 8)
      if let badge { ToneBadge(text: badge.0, tone: badge.1) }
      HStack(alignment: .firstTextBaseline, spacing: 2) {
        Text(value ?? "—").font(Typo.bodyStrong.monospacedDigit())
          .foregroundColor(value == nil ? Palette.text3 : (tone == .text ? Palette.text : tone.color))
        if let unit, value != nil { Text(unit).font(Typo.unit).foregroundColor(Palette.text2) }
      }
    }
    .padding(.vertical, 10)
    .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .bottom)
    .contentShape(Rectangle())
    if let action { Button(action: action) { row }.buttonStyle(PressScale()) } else { row }
  }
}

/// A tiny line chart of recent values (the status cards' spark, app.js sparkSvg).
struct Sparkline: View {
  let values: [Double?]
  var tone: Tone = .text

  var body: some View {
    Canvas { ctx, size in
      let pts = values.enumerated().compactMap { i, v in v.map { (i, $0) } }
      guard pts.count > 1, let lo = pts.map(\.1).min(), let hi = pts.map(\.1).max() else { return }
      let n = max(1, values.count - 1), span = hi - lo == 0 ? 1 : hi - lo
      var p = Path()
      for (k, (i, v)) in pts.enumerated() {
        let pt = CGPoint(x: CGFloat(i) / CGFloat(n) * size.width, y: size.height - 1 - CGFloat((v - lo) / span) * (size.height - 2))
        if k == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
      }
      ctx.stroke(p, with: .color(tone == .text ? Palette.text3 : tone.color), style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round))
    }
    .accessibilityHidden(true)
  }
}

/// A number tile you can tap (MetricTile inside a button).
struct TapTile: View {
  let metric: TodayModel.Metric
  let action: (TodayModel.Action) -> Void

  var body: some View {
    let tile = MetricTile(label: metric.label, value: metric.value, unit: metric.unit, tone: metric.tone)
    if let a = metric.action {
      Button { action(a) } label: { tile }.buttonStyle(PressScale())
    } else {
      tile
    }
  }
}

/// A section title with an optional trailing button.
struct SectionTitle: View {
  let title: String
  var trailing: String?
  var action: (() -> Void)?

  var body: some View {
    HStack {
      SectionHeader(title: title)
      if let trailing, let action {
        Button(action: action) { Text(trailing).font(Typo.label).foregroundColor(Palette.text2) }
          .buttonStyle(PressScale())
      }
    }
  }
}

/// The 「问 AI」 button (Jarvis is black and white: text colour on the inverted background).
struct AskAIButton: View {
  let prompt: String
  @Environment(\.askAI) private var askAI
  @State private var copied = false

  var body: some View {
    Button {
      askAI(prompt)
      if !askAI.connected { copied = true }
    } label: {
      HStack(spacing: 6) {
        Icon(.sparkles, size: 16)
        Text(copied ? tr("问题已复制", "Question copied") : tr("问 AI", "Ask AI")).font(Typo.bodyStrong)
      }
      .frame(maxWidth: .infinity, minHeight: 44)
      .foregroundColor(Palette.bg)
      .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.text))
    }
    .buttonStyle(PressScale())
  }
}
