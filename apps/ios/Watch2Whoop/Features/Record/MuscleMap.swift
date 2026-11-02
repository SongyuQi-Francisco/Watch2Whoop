import SwiftUI

/// The muscle load map (`/training/muscle-load`, flag `muscle_load`), drawn natively: the body outlines of the Today
/// page's `muscle-body-data.js` (paths from @mjcdev/react-body-highlighter, MIT, see THIRD_PARTY_NOTICES.md) turned into
/// SwiftUI paths; each region coloured by this week's effective sets against the recommended range, today's outlined
/// (muscle-load.js / muscle-map.js).

/// The server's answer, reduced to what the map and the list need.
struct MuscleLoad: Equatable {
  enum Range: String, CaseIterable, Identifiable {
    case today, week, last7
    var id: String { rawValue }
    var title: String {
      switch self {
      case .today: return tr("今天", "Today")
      case .week: return tr("本周", "This week")
      case .last7: return tr("近 7 天", "Last 7 days")
      }
    }
  }

  struct Muscle: Equatable, Identifiable {
    var key: String
    var name: String
    var sets: [Range: Double]
    var volume: [Range: Double]
    var status: [Range: String]
    var low: Double?
    var high: Double?
    var trainedToday: Bool
    var sportMin: [Range: Double]
    var prevWeek: Double?
    var id: String { key }
  }

  var sex: String
  var muscles: [Muscle]
  var sports: [Range: [(String, Double)]]
  var unmatched: [String]

  static func == (a: MuscleLoad, b: MuscleLoad) -> Bool {
    a.sex == b.sex && a.muscles == b.muscles && a.unmatched == b.unmatched
  }

  static func parse(_ j: [String: Any]?) -> MuscleLoad? {
    guard let j, let list = j["muscles"] as? [[String: Any]] else { return nil }
    func byRange(_ o: Any?, _ field: String?) -> [Range: Double] {
      var out: [Range: Double] = [:]
      for r in Range.allCases {
        let v = (o as? [String: Any])?[r.rawValue]
        out[r] = RecordFormat.double(field.flatMap { (v as? [String: Any])?[$0] } ?? v) ?? 0
      }
      return out
    }
    let muscles = list.compactMap { m -> Muscle? in
      guard let key = m["key"] as? String else { return nil }
      let st = m["status"] as? [String: Any] ?? [:]
      let week = st["week"] as? String ?? "none", last7 = st["last7"] as? String ?? week
      let t = m["target"] as? [String: Any]
      return Muscle(key: key, name: m["name"] as? String ?? key, sets: byRange(m["ranges"], "sets"), volume: byRange(m["ranges"], "volume_kg"),
                    status: [.today: week, .week: week, .last7: last7], low: RecordFormat.double(t?["low"]),
                    high: RecordFormat.double(t?["high"]), trainedToday: (m["trained_today"] as? Bool) ?? false,
                    sportMin: byRange(m["sport_min"], nil), prevWeek: RecordFormat.double((m["prev_week"] as? [String: Any])?["sets"]))
    }
    var sports: [Range: [(String, Double)]] = [:]
    for r in Range.allCases {
      sports[r] = (((j["sports"] as? [String: Any])?[r.rawValue] as? [[String: Any]]) ?? []).compactMap { s in
        guard let n = s["name"] as? String else { return nil }
        return (n, RecordFormat.double(s["min"]) ?? 0)
      }
    }
    return MuscleLoad(sex: j["sex"] as? String ?? "male", muscles: muscles, sports: sports, unmatched: (j["unmatched"] as? [String]) ?? [])
  }

  /// Catalog muscle → regions of the body drawing (muscle-map.js BODY_REGIONS).
  static let bodyRegions: [String: [String]] = [
    "chest": ["chest"], "lats": ["upper-back"], "middle back": ["upper-back"], "lower back": ["lower-back"], "traps": ["trapezius"],
    "shoulders": ["deltoids"], "quadriceps": ["quadriceps"], "hamstrings": ["hamstring"], "glutes": ["gluteal"], "abductors": ["gluteal"],
    "calves": ["calves"], "biceps": ["biceps"], "triceps": ["triceps"], "forearms": ["forearm"], "abdominals": ["abs", "obliques"],
    "adductors": ["adductors"], "neck": ["neck"],
  ]

  struct RegionState: Equatable {
    var status: String
    var today: Bool
    var key: String
    var sets: Double
    var sport: Bool
  }

  private static let rank = ["none": 0, "low": 1, "ok": 2, "high": 3]

  /// Region → state (muscle-load.js regionStates): the muscle with the most sets decides; 今天 greys what was not
  /// trained today.
  func regions(_ range: Range) -> [String: RegionState] {
    var out: [String: RegionState] = [:]
    for m in muscles {
      let sets = m.sets[range] ?? 0
      let st = range == .today && !m.trainedToday ? "none" : (m.status[range] ?? "none")
      let sport = (m.sportMin[range] ?? 0) > 0
      for region in Self.bodyRegions[m.key] ?? [] {
        if let cur = out[region], !(sets > cur.sets || (sets == cur.sets && (Self.rank[st] ?? 0) > (Self.rank[cur.status] ?? 0))) {
          out[region]?.today = cur.today || m.trainedToday
          out[region]?.sport = cur.sport || sport
        } else {
          let cur = out[region]
          out[region] = RegionState(status: st, today: m.trainedToday || (cur?.today ?? false), key: m.key, sets: sets,
                                    sport: sport || (cur?.sport ?? false))
        }
      }
    }
    return out
  }

  static func tone(_ status: String) -> Tone {
    switch status {
    case "ok": return .good
    case "high": return .bad
    case "low": return .mid
    default: return .text
    }
  }

  static func statusTitle(_ status: String) -> String {
    switch status {
    case "ok": return tr("合适", "On target")
    case "high": return tr("偏多", "High")
    case "low": return tr("偏少", "Low")
    default: return tr("没练", "None")
    }
  }
}

// MARK: - body drawing

/// The male / female, front / back drawings, read once from the bundled `muscle-body-data.js` (the same file the Today
/// page uses, so the shapes never drift).
final class MuscleBodies {
  struct Side {
    var box: CGRect
    var outline: Path
    var base: Path
    var regions: [(String, Path)]
  }

  static let shared = MuscleBodies()
  private var cache: [String: Side] = [:]
  private let lock = NSLock()
  private lazy var raw: [String: Any]? = Self.load()

  static func load(bundle: Bundle = .main) -> [String: Any]? {
    guard let url = bundle.url(forResource: "muscle-body-data", withExtension: "js"),
          let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
    return parse(text)
  }

  /// `window.BodyMuscleBodies = {…};` → the object.
  static func parse(_ text: String) -> [String: Any]? {
    guard let a = text.range(of: "= {")?.lowerBound, let b = text.range(of: "}", options: .backwards)?.upperBound else { return nil }
    let json = text[text.index(after: a)..<b].trimmingCharacters(in: .whitespaces)
    return (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any]
  }

  func side(sex: String, front: Bool) -> Side? {
    let key = "\(sex == "female" ? "female" : "male").\(front ? "front" : "back")"
    lock.lock()
    defer { lock.unlock() }
    if let s = cache[key] { return s }
    guard let d = ((raw?[sex == "female" ? "female" : "male"] as? [String: Any])?[front ? "front" : "back"]) as? [String: Any],
          let s = Self.build(d) else { return nil }
    cache[key] = s
    return s
  }

  static func build(_ d: [String: Any]) -> Side? {
    let vb = ((d["vb"] as? String) ?? "").split(separator: " ").compactMap { Double($0) }
    guard vb.count == 4 else { return nil }
    var base = Path()
    for p in (d["base"] as? [String]) ?? [] { base.addPath(SVGPath.parse(p)) }
    let regions = ((d["muscles"] as? [[Any]]) ?? []).compactMap { m -> (String, Path)? in
      guard m.count == 2, let slug = m[0] as? String, let paths = m[1] as? [String] else { return nil }
      var path = Path()
      for p in paths { path.addPath(SVGPath.parse(p)) }
      return (slug, path)
    }
    return Side(box: CGRect(x: vb[0], y: vb[1], width: vb[2], height: vb[3]), outline: SVGPath.parse(d["outline"] as? String ?? ""),
                base: base, regions: regions)
  }
}

/// One side of the body, coloured by region; tap a region → its muscle.
struct MuscleBodyView: View {
  let side: MuscleBodies.Side
  let regions: [String: MuscleLoad.RegionState]
  var lineScale: CGFloat = 1
  var tap: ((String) -> Void)?

  var body: some View {
    GeometryReader { g in
      let t = transform(g.size)
      Canvas { ctx, _ in
        ctx.fill(side.base.applying(t), with: .color(Palette.text3.opacity(0.22)))
        // today's outlines last, so neighbours do not cover them
        for (slug, path) in side.regions.sorted(by: { (regions[$0.0]?.today == true ? 1 : 0) < (regions[$1.0]?.today == true ? 1 : 0) }) {
          let st = regions[slug]
          let p = path.applying(t)
          ctx.fill(p, with: .color(color(st?.status)))
          if st?.sport == true { ctx.fill(p, with: .color(Palette.text.opacity(0.12))) }
          ctx.stroke(p, with: .color(st?.today == true ? Palette.text : Palette.bg), lineWidth: (st?.today == true ? 1.8 : 0.8) * lineScale)
        }
        ctx.stroke(side.outline.applying(t), with: .color(Palette.text3), lineWidth: 1.2 * lineScale)
      }
      .contentShape(Rectangle())
      .gesture(SpatialTapGesture().onEnded { e in
        guard let tap else { return }
        let inv = t.inverted()
        let pt = e.location.applying(inv)
        if let hit = side.regions.last(where: { $0.1.contains(pt) }), let key = regions[hit.0]?.key { tap(key) }
      })
    }
    .aspectRatio(side.box.width / side.box.height, contentMode: .fit)
    .accessibilityHidden(true)
  }

  private func transform(_ size: CGSize) -> CGAffineTransform {
    let s = min(size.width / side.box.width, size.height / side.box.height)
    let dx = (size.width - side.box.width * s) / 2, dy = (size.height - side.box.height * s) / 2
    return CGAffineTransform(translationX: dx - side.box.minX * s, y: dy - side.box.minY * s).scaledBy(x: s, y: s)
  }

  private func color(_ status: String?) -> Color {
    switch status {
    case "low": return Palette.train.opacity(0.4)
    case "ok": return Palette.good
    case "high": return Palette.bad
    default: return Palette.surface2
    }
  }
}

/// SVG path data → SwiftUI `Path` (M L H V C S Q T A Z, absolute and relative; arcs become cubic Béziers).
enum SVGPath {
  static func parse(_ d: String) -> Path {
    var path = Path()
    var s = Scanner(Array(d.utf8))
    var cur = CGPoint.zero, start = CGPoint.zero
    var lastCtrl: CGPoint?, lastQuad: CGPoint?
    var cmd: UInt8 = 0
    while true {
      s.skipSeparators()
      guard let c = s.peek() else { break }
      if Scanner.isCommand(c) {
        cmd = c
        s.i += 1
      } else if cmd == 0 {
        break
      }
      let rel = cmd >= 97   // lowercase
      func pt() -> CGPoint? {
        guard let x = s.number(), let y = s.number() else { return nil }
        return rel ? CGPoint(x: cur.x + x, y: cur.y + y) : CGPoint(x: x, y: y)
      }
      switch cmd | 0x20 {
      case UInt8(ascii: "m"):
        guard let p = pt() else { return path }
        path.move(to: p)
        cur = p
        start = p
        cmd = rel ? UInt8(ascii: "l") : UInt8(ascii: "L")   // further pairs are line-tos
        lastCtrl = nil; lastQuad = nil
      case UInt8(ascii: "l"):
        guard let p = pt() else { return path }
        path.addLine(to: p)
        cur = p
        lastCtrl = nil; lastQuad = nil
      case UInt8(ascii: "h"):
        guard let x = s.number() else { return path }
        cur = CGPoint(x: rel ? cur.x + x : x, y: cur.y)
        path.addLine(to: cur)
        lastCtrl = nil; lastQuad = nil
      case UInt8(ascii: "v"):
        guard let y = s.number() else { return path }
        cur = CGPoint(x: cur.x, y: rel ? cur.y + y : y)
        path.addLine(to: cur)
        lastCtrl = nil; lastQuad = nil
      case UInt8(ascii: "c"):
        guard let c1 = pt(), let c2 = pt(), let p = pt() else { return path }
        path.addCurve(to: p, control1: c1, control2: c2)
        cur = p
        lastCtrl = c2; lastQuad = nil
      case UInt8(ascii: "s"):
        let c1 = lastCtrl.map { CGPoint(x: 2 * cur.x - $0.x, y: 2 * cur.y - $0.y) } ?? cur
        guard let c2 = pt(), let p = pt() else { return path }
        path.addCurve(to: p, control1: c1, control2: c2)
        cur = p
        lastCtrl = c2; lastQuad = nil
      case UInt8(ascii: "q"):
        guard let c1 = pt(), let p = pt() else { return path }
        path.addQuadCurve(to: p, control: c1)
        cur = p
        lastQuad = c1; lastCtrl = nil
      case UInt8(ascii: "t"):
        let c1 = lastQuad.map { CGPoint(x: 2 * cur.x - $0.x, y: 2 * cur.y - $0.y) } ?? cur
        guard let p = pt() else { return path }
        path.addQuadCurve(to: p, control: c1)
        cur = p
        lastQuad = c1; lastCtrl = nil
      case UInt8(ascii: "a"):
        guard let rx = s.number(), let ry = s.number(), let rot = s.number(), let large = s.flag(), let sweep = s.flag(),
              let p = pt() else { return path }
        arc(&path, from: cur, to: p, rx: rx, ry: ry, angle: rot, large: large, sweep: sweep)
        cur = p
        lastCtrl = nil; lastQuad = nil
      case UInt8(ascii: "z"):
        path.closeSubpath()
        cur = start
        lastCtrl = nil; lastQuad = nil
        cmd = 0
      default:
        return path
      }
    }
    return path
  }

  /// Endpoint arc (SVG F.6.5) as cubic Béziers, at most 90° each.
  static func arc(_ path: inout Path, from p0: CGPoint, to p1: CGPoint, rx: Double, ry: Double, angle: Double, large: Bool, sweep: Bool) {
    var rx = abs(rx), ry = abs(ry)
    if rx == 0 || ry == 0 || p0 == p1 {
      path.addLine(to: p1)
      return
    }
    let phi = angle * .pi / 180, cosP = cos(phi), sinP = sin(phi)
    let dx = (p0.x - p1.x) / 2, dy = (p0.y - p1.y) / 2
    let x1 = cosP * dx + sinP * dy, y1 = -sinP * dx + cosP * dy
    let lambda = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lambda > 1 {
      rx *= sqrt(lambda)
      ry *= sqrt(lambda)
    }
    let num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    let den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    var co = sqrt(max(0, num / den))
    if large == sweep { co = -co }
    let cx1 = co * rx * y1 / ry, cy1 = -co * ry * x1 / rx
    let cx = cosP * cx1 - sinP * cy1 + (p0.x + p1.x) / 2, cy = sinP * cx1 + cosP * cy1 + (p0.y + p1.y) / 2
    func ang(_ ux: Double, _ uy: Double, _ vx: Double, _ vy: Double) -> Double {
      let a = atan2(ux * vy - uy * vx, ux * vx + uy * vy)
      return a
    }
    let t1 = ang(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    var dt = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if !sweep && dt > 0 { dt -= 2 * .pi } else if sweep && dt < 0 { dt += 2 * .pi }
    let n = max(1, Int(ceil(abs(dt) / (.pi / 2))))
    let step = dt / Double(n)
    let k = 4.0 / 3.0 * tan(step / 4)
    var t = t1
    for _ in 0..<n {
      let c1 = cos(t), s1 = sin(t), c2 = cos(t + step), s2 = sin(t + step)
      func map(_ x: Double, _ y: Double) -> CGPoint {
        CGPoint(x: cx + rx * x * cosP - ry * y * sinP, y: cy + rx * x * sinP + ry * y * cosP)
      }
      path.addCurve(to: map(c2, s2), control1: map(c1 - k * s1, s1 + k * c1), control2: map(c2 + k * s2, s2 - k * c2))
      t += step
    }
  }

  /// Number scanner over the path bytes (SVG number grammar: `-.5`, `1e-3`, `1.5.5` = two numbers).
  struct Scanner {
    let b: [UInt8]
    var i = 0

    init(_ b: [UInt8]) { self.b = b }

    func peek() -> UInt8? { i < b.count ? b[i] : nil }

    static func isCommand(_ c: UInt8) -> Bool {
      switch c | 0x20 {
      case UInt8(ascii: "m"), UInt8(ascii: "l"), UInt8(ascii: "h"), UInt8(ascii: "v"), UInt8(ascii: "c"), UInt8(ascii: "s"),
           UInt8(ascii: "q"), UInt8(ascii: "t"), UInt8(ascii: "a"), UInt8(ascii: "z"):
        return c != UInt8(ascii: "e") && c != UInt8(ascii: "E")
      default:
        return false
      }
    }

    mutating func skipSeparators() {
      while i < b.count, b[i] == 32 || b[i] == 44 || b[i] == 9 || b[i] == 10 || b[i] == 13 { i += 1 }
    }

    mutating func flag() -> Bool? {
      skipSeparators()
      guard i < b.count, b[i] == UInt8(ascii: "0") || b[i] == UInt8(ascii: "1") else { return nil }
      defer { i += 1 }
      return b[i] == UInt8(ascii: "1")
    }

    mutating func number() -> Double? {
      skipSeparators()
      let s = i
      if i < b.count, b[i] == UInt8(ascii: "-") || b[i] == UInt8(ascii: "+") { i += 1 }
      var digits = false, dot = false
      while i < b.count {
        let c = b[i]
        if c >= 48 && c <= 57 { digits = true; i += 1 }
        else if c == UInt8(ascii: "."), !dot { dot = true; i += 1 }
        else { break }
      }
      if digits, i < b.count, b[i] == UInt8(ascii: "e") || b[i] == UInt8(ascii: "E") {
        var j = i + 1
        if j < b.count, b[j] == UInt8(ascii: "-") || b[j] == UInt8(ascii: "+") { j += 1 }
        if j < b.count, b[j] >= 48 && b[j] <= 57 {
          i = j
          while i < b.count, b[i] >= 48 && b[i] <= 57 { i += 1 }
        }
      }
      guard digits, let str = String(bytes: b[s..<i], encoding: .ascii) else {
        i = s
        return nil
      }
      return Double(str)
    }
  }
}
