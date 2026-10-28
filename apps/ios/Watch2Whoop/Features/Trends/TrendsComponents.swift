import SwiftUI

/// A row of pill buttons, one pressed (the page's `.seg`).
struct SegControl<T: Hashable>: View {
  let items: [(T, String)]
  let selected: T
  let pick: (T) -> Void

  var body: some View {
    HStack(spacing: 2) {
      ForEach(Array(items.enumerated()), id: \.offset) { _, item in
        let on = item.0 == selected
        Button { pick(item.0) } label: {
          Text(item.1).font(.system(size: 13, weight: on ? .semibold : .medium))
            .foregroundColor(on ? Palette.text : Palette.text2)
            .frame(maxWidth: .infinity, minHeight: 32)
            .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(on ? Palette.bg : Color.clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressScale())
        .accessibilityAddTraits(on ? [.isButton, .isSelected] : .isButton)
      }
    }
    .padding(3)
    .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
  }
}

/// One Trends card: title (opens its explanation), the period figure, the "vs 上期" change or the touched day, the chart.
struct TrendCardView: View {
  let card: TrendCardModel
  let open: (TrendsSheet) -> Void
  /// Not observed here: only the read-out and the chart overlay redraw while the finger moves.
  @State private var selection = ChartSelection()

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(alignment: .top, spacing: 8) {
        VStack(alignment: .leading, spacing: 2) {
          Button { open(card.sheet ?? .explain(key: card.explain, title: card.title)) } label: {
            HStack(spacing: 4) {
              Text(card.title).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
              Icon(.info, size: 12).foregroundColor(Palette.text3)
            }
          }
          .buttonStyle(PressScale())
          .accessibilityLabel(card.title)
          HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(card.value ?? "—").font(.system(size: 24, weight: .semibold).monospacedDigit())
              .foregroundColor(card.value == nil ? Palette.text3 : Palette.text).lineLimit(1).minimumScaleFactor(0.6)
            if let u = card.unit, card.value != nil { Text(u).font(Typo.unit).foregroundColor(Palette.text2).lineLimit(1) }
            if let s = card.sub { Text(s).font(Typo.unit).foregroundColor(Palette.text2) }
          }
        }
        Spacer(minLength: 4)
        TrendReadOut(base: card.base, tip: card.tip, selection: selection)
      }
      if let spec = card.spec {
        TrendChartBody(spec: spec, selection: selection).equatable()
      } else if let e = card.empty {
        Text(e).font(Typo.caption).foregroundColor(Palette.text3)
      }
      extra
      if !card.legend.isEmpty { TrendLegend(items: card.legend) }
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }

  @ViewBuilder private var extra: some View {
    switch card.extra {
    case .none: EmptyView()
    case .zones(let z): ZoneCounts(zones: z)
    case .hrZones(let z): HRZonesView(zones: z)
    case .regularity(let r): RegularityBars(nights: r.nights).frame(height: 64)
    }
  }
}

/// Top right of a card: the touched day and its value, else the change against the previous period.
struct TrendReadOut: View {
  let base: (String, Tone)?
  let tip: (Int) -> String
  @ObservedObject var selection: ChartSelection

  var body: some View {
    Group {
      if let i = selection.index {
        Text(tip(i)).foregroundColor(Palette.text)
      } else if let b = base {
        Text(b.0).foregroundColor(b.1 == .text ? Palette.text2 : b.1.color)
      }
    }
    .font(Typo.label.monospacedDigit())
    .multilineTextAlignment(.trailing)
    .lineLimit(2)
    .padding(.top, 2)
  }
}

struct TrendLegend: View {
  let items: [TrendCardModel.Legend]
  var body: some View {
    HStack(spacing: 12) {
      ForEach(items, id: \.self) { l in
        HStack(spacing: 4) {
          RoundedRectangle(cornerRadius: 2).fill((l.tone == .text ? Palette.text2 : l.tone.color).opacity(l.faint ? 0.35 : 1))
            .frame(width: 10, height: l.faint ? 8 : 3)
          Text(l.text).font(.system(size: 11)).foregroundColor(Palette.text2)
        }
      }
      Spacer(minLength: 0)
    }
  }
}

/// Green / yellow / red day counts under the recovery chart.
struct ZoneCounts: View {
  let zones: TrendSeries.Zones
  var body: some View {
    let total = Double(max(1, zones.green + zones.yellow + zones.red))
    VStack(alignment: .leading, spacing: 6) {
      GeometryReader { g in
        HStack(spacing: 2) {
          let parts: [(Int, Tone)] = [(zones.green, .good), (zones.yellow, .mid), (zones.red, .bad)]
          ForEach(Array(parts.enumerated()), id: \.offset) { _, p in
            if p.0 > 0 { Capsule().fill(p.1.color).frame(width: max(2, g.size.width * Double(p.0) / total - 2)) }
          }
        }
      }
      .frame(height: 6)
      HStack(spacing: 14) {
        Text("\(zones.green) \(tr("绿", "green"))")
        Text("\(zones.yellow) \(tr("黄", "yellow"))")
        Text("\(zones.red) \(tr("红", "red"))")
      }
      .font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text2)
    }
    .padding(.top, 2)
  }
}

/// Heart-rate zones: the five zones over the range, then the last eight weeks (app.js hrZonesHtml).
struct HRZonesView: View {
  let zones: TrendsData.HRZones

  var body: some View {
    let total = max(1, zones.totalMin.reduce(0, +))
    VStack(alignment: .leading, spacing: 8) {
      ZoneStack(minutes: zones.totalMin).frame(height: 10)
      HStack(spacing: 6) {
        ForEach(Array(zones.labels.enumerated()), id: \.offset) { i, l in
          VStack(spacing: 1) {
            Text("\(Int(((zones.totalMin[safe: i] ?? 0) / total * 100).rounded()))%").font(.system(size: 12, weight: .semibold).monospacedDigit())
            Text(l).font(.system(size: 9)).foregroundColor(Palette.text3).lineLimit(1).minimumScaleFactor(0.7)
          }
          .frame(maxWidth: .infinity)
        }
      }
      .foregroundColor(Palette.text)
      ForEach(Array(zones.weeks.suffix(8).enumerated()), id: \.offset) { _, w in
        HStack(spacing: 8) {
          Text(TodayModel.shortDay(w.week)).font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text2).frame(width: 40, alignment: .leading)
          ZoneStack(minutes: w.minutes).frame(height: 6)
        }
      }
    }
  }
}

/// Five zones side by side, darker for harder zones.
struct ZoneStack: View {
  let minutes: [Double]
  var body: some View {
    GeometryReader { g in
      let total = max(1, minutes.reduce(0, +))
      HStack(spacing: 1) {
        ForEach(Array(minutes.enumerated()), id: \.offset) { i, m in
          if m > 0 { Rectangle().fill(Palette.train.opacity(0.25 + Double(i) * 0.18)).frame(width: max(1, g.size.width * m / total - 1)) }
        }
      }
      .clipShape(Capsule())
    }
  }
}

/// Bedtime → wake of the last nights, 18:00 to 16:00 next day (app.js regularityHtml).
struct RegularityBars: View {
  let nights: [ClosedRange<Double>]

  var body: some View {
    Canvas { ctx, size in
      let span = 22.0 * 60, labelH: CGFloat = 12
      let rowH = min(4, (size.height - labelH) / CGFloat(max(1, nights.count)) - 0.6)
      for (i, n) in nights.enumerated() {
        let x0 = CGFloat(n.lowerBound / span) * size.width, x1 = CGFloat(n.upperBound / span) * size.width
        let r = CGRect(x: x0, y: CGFloat(i) * (rowH + 0.6), width: max(1, x1 - x0), height: max(1, rowH))
        ctx.fill(Path(roundedRect: r, cornerRadius: 1.5), with: .color(Palette.sleep))
      }
      for (h, t) in [(6.0, "0:00"), (12.0, "6:00"), (18.0, "12:00")] {
        ctx.draw(Text(t).font(.system(size: 9)).foregroundColor(Palette.text3), at: CGPoint(x: CGFloat(h * 60 / span) * size.width, y: size.height - 5))
      }
    }
    .accessibilityHidden(true)
  }
}

/// A status card (as of the end date): label, short value in its colour, the small line under it.
struct StatusTileView: View {
  let tile: TrendStatus.Tile
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      VStack(alignment: .leading, spacing: 4) {
        HStack(spacing: 4) {
          Text(tile.label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
          if tile.local { Circle().fill(Palette.text3).frame(width: 4, height: 4).accessibilityLabel(tr("本机计算", "On this phone")) }
        }
        HStack(alignment: .firstTextBaseline, spacing: 2) {
          Text(tile.value).font(.system(size: 19, weight: .semibold).monospacedDigit())
            .foregroundColor(tile.tone == .text ? Palette.text : tile.tone.color).lineLimit(1).minimumScaleFactor(0.6)
          if let u = tile.unit { Text(u).font(Typo.unit).foregroundColor(Palette.text2) }
        }
        if let s = tile.sub { Text(s).font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text3).lineLimit(1) }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(12)
      .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .combine)
  }
}

/// The 「问 Jarvis」 bar (flag `ui_ai_first`): the question goes to the AskAI hook (N6 opens Jarvis with it).
struct AskJarvisBar: View {
  let example: String
  @Environment(\.askAI) private var askAI
  @State private var text = ""
  @State private var copied = false

  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      HStack(spacing: 8) {
        Icon(.sparkles, size: 16).foregroundColor(Palette.text2)
        TextField(tr("问 Jarvis：\(example)", "Ask Jarvis: \(example)"), text: $text)
          .font(Typo.body)
          .submitLabel(.send)
          .onSubmit(send)
      }
      .padding(.horizontal, 14).frame(minHeight: 44)
      .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
      if copied { Text(tr("问题已复制", "Question copied")).font(Typo.label).foregroundColor(Palette.text3).padding(.leading, 4) }
    }
  }

  private func send() {
    let q = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !q.isEmpty else { return }
    askAI(q)
    copied = !askAI.connected
    text = ""
  }
}
