import Charts
import SwiftUI
import UIKit

/// One chart of the Trends tab (Swift Charts): layers of bars / lines / bands on a shared day axis, like the page's
/// tChart. The marks are built once per spec; the touch read-out (tap, or press and slide) is drawn in an overlay that
/// redraws on its own, so moving the finger never rebuilds the marks of a 365-day chart.
struct ChartLayer: Equatable {
  enum Kind: Equatable { case line, avg, bars, dots, band, range }
  var kind: Kind
  var values: [Double?] = []
  var lo: [Double?] = []
  var hi: [Double?] = []
  var ranges: [ClosedRange<Double>?] = []
  var tone: Tone = .text
  /// Bars: a tone per day (recovery colours, strain in its target band).
  var tones: [Tone]? = nil
  /// Faint (the 7-day mean line, the baseline band).
  var faint = false
  var dots = true
  /// The value the read-out marks.
  var main = false
  /// Right axis 0–21 (strain) drawn over the left axis' range.
  var rightAxis = false
}

struct ChartSpec: Equatable {
  var id: String
  var dates: [String]
  var layers: [ChartLayer]
  var min: Double?
  var max: Double?
  /// Reference band (normal range), drawn behind the data.
  var zone: ClosedRange<Double>?
  /// Horizontal guide lines with a label.
  var guides: [Double] = []
  var height: CGFloat = 112
  var label = ""
  /// Day labels under the plot (first, middle, last).
  var xAxis = true

  /// y range: every reading, plus the reference band where it is nearest to them (the page's chartRange, FB-2).
  static func range(_ nums: [Double], min omin: Double?, max omax: Double?, zone: ClosedRange<Double>?) -> ClosedRange<Double> {
    let data = nums.filter(\.isFinite)
    var vals = data
    if let z = zone {
      if data.isEmpty { vals += [z.lowerBound, z.upperBound] }
      else if z.lowerBound > data.max()! { vals.append(z.lowerBound) }
      else if z.upperBound < data.min()! { vals.append(z.upperBound) }
    }
    guard let dlo = vals.min(), let dhi = vals.max() else {
      let lo = omin ?? 0
      return lo...(omax ?? lo + 1)
    }
    var lo = omin.map { Swift.min($0, dlo) } ?? dlo, hi = omax.map { Swift.max($0, dhi) } ?? dhi
    let span = hi - lo
    var pad: Double = span * 0.12
    if pad == 0 { pad = abs(hi) * 0.05 }
    if pad == 0 { pad = 1 }
    if omin == nil || dlo < omin! { lo -= pad }
    if omax == nil || dhi > omax! { hi += pad }
    return lo...hi
  }

  var yDomain: ClosedRange<Double> {
    var nums: [Double] = []
    for l in layers where !l.rightAxis {
      switch l.kind {
      case .range: nums += l.ranges.compactMap { $0 }.flatMap { [$0.lowerBound, $0.upperBound] }
      default: nums += (l.values + l.lo + l.hi).compactMap { $0 }
      }
    }
    return Self.range(nums, min: min, max: max, zone: zone)
  }
}

/// The marks. Equatable on the spec, so a parent redraw (the read-out moving) does not rebuild them. x is the slot
/// number (a day, or a week for weekly bars): slot i spans [i, i + 1], so bars, bands and lines share one axis.
struct TrendChartBody: View, Equatable {
  let spec: ChartSpec
  let selection: ChartSelection

  static func == (a: TrendChartBody, b: TrendChartBody) -> Bool { a.spec == b.spec && a.selection === b.selection }

  struct Pt: Identifiable, Equatable { let id: Int; let y: Double; let seg: Int; var x: Double { Double(id) + 0.5 } }
  struct Box: Identifiable { let id: Int; let y0: Double; let y1: Double }

  /// Lines and bands keep at most this many points: a year of days drawn as ~140 points looks the same at phone width
  /// and keeps a card's first frame short while scrolling (the read-out still reads every day).
  static let lineLimit = 140

  /// Largest-Triangle-Three-Buckets per run of consecutive days (gaps stay gaps; peaks and dips are kept).
  static func downsample(_ pts: [Pt], limit: Int) -> [Pt] {
    guard pts.count > limit else { return pts }
    var out: [Pt] = []
    out.reserveCapacity(limit + 8)
    var runs: [[Pt]] = []
    for p in pts { if runs.last?.last?.seg == p.seg { runs[runs.count - 1].append(p) } else { runs.append([p]) } }
    for run in runs {
      let quota = Swift.max(2, Int((Double(limit) * Double(run.count) / Double(pts.count)).rounded()))
      out += lttb(run, quota)
    }
    return out
  }

  private static func lttb(_ d: [Pt], _ m: Int) -> [Pt] {
    let n = d.count
    guard n > m else { return d }
    guard m >= 3 else { return [d[0], d[n - 1]] }
    var out = [d[0]]
    let every = Double(n - 2) / Double(m - 2)
    var a = 0
    for i in 0..<(m - 2) {
      // average of the next bucket (the last point for the last bucket)
      let ns = Int(Double(i + 1) * every) + 1, ne = Swift.min(Int(Double(i + 2) * every) + 1, n)
      var ax = 0.0, ay = 0.0, cnt = 0.0
      for j in ns..<Swift.max(ns, ne) { ax += d[j].x; ay += d[j].y; cnt += 1 }
      if cnt == 0 { ax = d[n - 1].x; ay = d[n - 1].y } else { ax /= cnt; ay /= cnt }
      // the point of this bucket with the largest triangle
      let s = Int(Double(i) * every) + 1, e = Swift.min(Int(Double(i + 1) * every) + 1, n - 1)
      var best = s, area = -1.0
      for j in s..<Swift.max(s + 1, e) {
        let ar = abs((d[a].x - ax) * (d[j].y - d[a].y) - (d[a].x - d[j].x) * (ay - d[a].y))
        if ar > area { area = ar; best = j }
      }
      out.append(d[best])
      a = best
    }
    out.append(d[n - 1])
    return out
  }

  /// Bars are drawn one per day up to a year (measured: a year of bars costs no more per card than a month, the fixed
  /// cost of a chart dominates). Longer series would be drawn as groups of a few days (their mean, the most common
  /// colour); the read-out always reads single days.
  static let barLimit = 400

  /// A bar from the base line to its value (a zero value still shows as a sliver).
  struct BarSlot: Identifiable { let id: Int; let start: Int; let end: Int; let y0: Double; let y1: Double; let tone: Tone? }

  static func barSlots(_ l: ChartLayer, y: ClosedRange<Double>, base: Double, limit: Int) -> [BarSlot] {
    let sliver: Double = (y.upperBound - y.lowerBound) * 0.01
    let n = l.values.count
    let k = n > limit ? Int((Double(n) / Double(limit)).rounded(.up)) : 1
    var out: [BarSlot] = []
    var i = 0
    while i < n {
      let j = Swift.min(n, i + k)
      var sum = 0.0, cnt = 0.0, tones: [Tone: Int] = [:]
      for d in i..<j {
        guard let v = l.values[d] else { continue }
        sum += v; cnt += 1
        if let t = l.tones?[safe: d] { tones[t, default: 0] += 1 }
      }
      if cnt > 0 {
        let tone = tones.max { a, b in a.value < b.value || (a.value == b.value && a.key.rawValue > b.key.rawValue) }?.key
        let v: Double = mapped(sum / cnt, right: l.rightAxis, y: y)
        let lo: Double = Swift.min(base, v)
        let hi: Double = v == base ? base + sliver : Swift.max(base, v)
        out.append(BarSlot(id: i, start: i, end: j, y0: lo, y1: hi, tone: l.tones == nil ? nil : tone))
      }
      i = j
    }
    return out
  }

  /// Every k-th box of a smooth band (the baseline moves slowly), first and last kept.
  static func thin(_ b: [Box], limit: Int) -> [Box] {
    guard b.count > limit else { return b }
    let k = Int((Double(b.count) / Double(limit)).rounded(.up))
    return b.enumerated().filter { $0.offset % k == 0 || $0.offset == b.count - 1 }.map(\.element)
  }

  var body: some View {
    let y = spec.yDomain
    let n = spec.dates.count
    VStack(spacing: 0) {
    Chart {
      if let z = spec.zone {
        RectangleMark(xStart: .value("x", 0.0), xEnd: .value("x", Double(n)),
                      yStart: .value("v", Swift.max(y.lowerBound, z.lowerBound)), yEnd: .value("v", Swift.min(y.upperBound, z.upperBound)))
          .foregroundStyle(Palette.good.opacity(0.10))
      }
      ForEach(spec.guides, id: \.self) { g in
        RuleMark(y: .value("v", g)).foregroundStyle(Palette.text3.opacity(0.6)).lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
      }
      ForEach(Array(spec.layers.enumerated()), id: \.offset) { li, l in
        layerMarks(l, index: li, y: y, n: n)
      }
    }
    .chartXScale(domain: 0...Double(Swift.max(1, n)))
    .chartYScale(domain: y)
    // No axes inside the chart: the plot fills the frame, so the touch layer maps x / y itself (no ChartProxy, no
    // GeometryReader per axis) and the day labels are three plain texts. This halves a card's first-frame cost.
    .chartXAxis(.hidden)
    .chartYAxis(.hidden)
    .frame(height: spec.height - (spec.xAxis ? 16 : 0))
    .overlay(alignment: .bottom) { Rectangle().fill(Palette.hair).frame(height: 1) }
    .overlay { ChartTouchLayer(spec: spec, y: y, selection: selection) }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(spec.label)
    if spec.xAxis, let first = spec.dates.first, let last = spec.dates.last {
      let year = TrendsFormat.spansYear(first, last)
      HStack {
        Text(TrendsFormat.day(first, year: year))
        Spacer(minLength: 4)
        if n >= 3 { Text(TrendsFormat.day(spec.dates[(n - 1) / 2], year: year)); Spacer(minLength: 4) }
        if n >= 2 { Text(TrendsFormat.day(last, year: year)) }
      }
      .font(.system(size: 10).monospacedDigit())
      .foregroundColor(Palette.text3)
      .padding(.top, 4)
      .accessibilityHidden(true)
    }
    }
  }

  @ChartContentBuilder
  private func layerMarks(_ l: ChartLayer, index li: Int, y: ClosedRange<Double>, n: Int) -> some ChartContent {
    let color = l.tone == .text ? Palette.text2 : l.tone.color
    switch l.kind {
    case .band:
      ForEach(Self.thin(boxes(l.lo, l.hi, y: y, right: false), limit: Self.lineLimit)) { b in
        AreaMark(x: .value("x", Double(b.id) + 0.5), yStart: .value("v", b.y0), yEnd: .value("v", b.y1))
          .foregroundStyle(Palette.text3.opacity(0.16))
      }
    case .range:
      ForEach(boxes(l.ranges.map { $0?.lowerBound }, l.ranges.map { $0?.upperBound }, y: y, right: l.rightAxis)) { b in
        RectangleMark(xStart: .value("x", Double(b.id)), xEnd: .value("x", Double(b.id + 1)), yStart: .value("v", b.y0), yEnd: .value("v", b.y1))
          .foregroundStyle(Palette.train.opacity(0.14))
      }
    case .bars:
      let base = Swift.max(y.lowerBound, Swift.min(y.upperBound, 0))
      let inset = n > 60 ? 0.05 : 0.17
      ForEach(Self.barSlots(l, y: y, base: base, limit: Self.barLimit)) { b in
        RectangleMark(xStart: .value("x", Double(b.start) + inset), xEnd: .value("x", Double(b.end) - inset),
                      yStart: .value("v", b.y0), yEnd: .value("v", b.y1))
          .foregroundStyle(b.tone.map { $0 == .text ? Palette.text3.opacity(0.7) : $0.color } ?? color.opacity(0.55))
          .cornerRadius(n > 60 ? 0.5 : 2)
      }
    case .dots:
      ForEach(points(l.values, y: y, right: l.rightAxis)) { p in
        PointMark(x: .value("x", p.x), y: .value("v", p.y)).symbolSize(22).foregroundStyle(color)
      }
    case .line, .avg:
      ForEach(Self.downsample(points(l.values, y: y, right: l.rightAxis), limit: Self.lineLimit)) { p in
        LineMark(x: .value("x", p.x), y: .value("v", p.y), series: .value("s", "\(li)-\(p.seg)"))
          .foregroundStyle(l.kind == .avg && l.tone == .text ? Palette.text : color.opacity(l.kind == .line && l.faint ? 0.5 : 1))
          .lineStyle(StrokeStyle(lineWidth: l.kind == .avg ? 2 : 1.2, lineCap: .round, lineJoin: .round))
          .interpolationMethod(.monotone)
      }
      if l.kind == .line, l.dots, n <= 31 {
        ForEach(points(l.values, y: y, right: l.rightAxis)) { p in
          PointMark(x: .value("x", p.x), y: .value("v", p.y)).symbolSize(9).foregroundStyle(color)
        }
      }
    }
  }

  /// Right-axis values (0–21) mapped onto the left axis' range, as the page draws them.
  static func mapped(_ v: Double, right: Bool, y: ClosedRange<Double>) -> Double {
    right ? y.lowerBound + Swift.max(0, Swift.min(21, v)) / 21 * (y.upperBound - y.lowerBound) : v
  }

  /// Non-nil values with their run number, so a gap breaks the line.
  private func points(_ vals: [Double?], y: ClosedRange<Double>, right: Bool) -> [Pt] {
    var out: [Pt] = [], seg = 0, pen = false
    out.reserveCapacity(vals.count)
    for (i, v) in vals.enumerated() {
      guard let v else { if pen { seg += 1 }; pen = false; continue }
      out.append(Pt(id: i, y: Self.mapped(v, right: right, y: y), seg: seg))
      pen = true
    }
    return out
  }

  /// One box per slot (baseline band, target band), clipped to the plot.
  private func boxes(_ lo: [Double?], _ hi: [Double?], y: ClosedRange<Double>, right: Bool) -> [Box] {
    var out: [Box] = []
    for i in 0..<Swift.min(lo.count, hi.count) {
      guard let a = lo[i], let b = hi[i] else { continue }
      let y0 = Swift.max(y.lowerBound, Self.mapped(a, right: right, y: y)), y1 = Swift.min(y.upperBound, Self.mapped(b, right: right, y: y))
      if y1 > y0 { out.append(Box(id: i, y0: y0, y1: y1)) }
    }
    return out
  }

}

/// Which day the finger is on (nil = none). One per card; the header and the overlay observe it.
final class ChartSelection: ObservableObject {
  @Published var index: Int?
}

/// Tap a day to read it, or press and slide along the chart; scrolling the page is not blocked (no plain drag here).
/// The plot fills the view (no axes), so slot i's centre is at (i + 0.5) / n of the width.
struct ChartTouchLayer: View {
  let spec: ChartSpec
  let y: ClosedRange<Double>
  @ObservedObject var selection: ChartSelection
  @State private var feedback = UISelectionFeedbackGenerator()

  var body: some View {
    GeometryReader { geo in
      let w = geo.size.width, h = geo.size.height, n = spec.dates.count
      ZStack(alignment: .topLeading) {
        Rectangle().fill(Color.clear).contentShape(Rectangle())
          .onTapGesture { loc in
            let i = index(at: loc.x, width: w)
            selection.index = (i == selection.index) ? nil : i
          }
          .gesture(LongPressGesture(minimumDuration: 0.18).sequenced(before: DragGesture(minimumDistance: 0))
            .onChanged { v in
              if case .second(true, let drag) = v, let drag { pick(index(at: drag.location.x, width: w)) }
              else if case .second(true, nil) = v { feedback.prepare() }
            })
        if let i = selection.index, i < n, n > 0 {
          let x = (Double(i) + 0.5) / Double(n) * w
          Rectangle().fill(Palette.text2).frame(width: 1, height: h)
            .position(x: x, y: h / 2)
            .allowsHitTesting(false)
          if let v = mainValue(i) {
            Circle().fill(Palette.text).frame(width: 7, height: 7)
              .overlay(Circle().stroke(Palette.bg, lineWidth: 1.5))
              .position(x: x, y: (1 - (v - y.lowerBound) / Swift.max(1e-9, y.upperBound - y.lowerBound)) * h)
              .allowsHitTesting(false)
          }
        }
      }
    }
  }

  private func pick(_ i: Int?) {
    guard i != selection.index else { return }
    selection.index = i
    feedback.selectionChanged()
  }

  private func index(at x: CGFloat, width: CGFloat) -> Int? {
    let n = spec.dates.count
    guard n > 0, width > 0 else { return nil }
    return Swift.max(0, Swift.min(n - 1, Int((x / width * CGFloat(n)).rounded(.down))))
  }

  private func mainValue(_ i: Int) -> Double? {
    guard let l = spec.layers.first(where: \.main) ?? spec.layers.first(where: { $0.kind != .band && $0.kind != .range }),
          let v = l.values[safe: i] ?? nil else { return nil }
    return TrendChartBody.mapped(v, right: l.rightAxis, y: y)
  }
}

/// A chart with its own selection, for places that only need the marks (sheets).
struct TrendChart: View {
  let spec: ChartSpec
  let selection: ChartSelection

  var body: some View {
    TrendChartBody(spec: spec, selection: selection).equatable()
  }
}
