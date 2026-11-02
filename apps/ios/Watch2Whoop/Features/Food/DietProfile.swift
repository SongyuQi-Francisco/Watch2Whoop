import SwiftUI

/// 「我的饮食档案」 (issue #32, assistant/diet_profile.py): one line above 饮食 and 采购, tapping it opens collapsible blocks
/// (targets, why these numbers, constraints, a day of eating, when to adjust, version log). Content is written by the user's
/// AI (`update_diet_profile`); this page only reads. Same reading as the page (web/static/diet-profile.js).

/// `GET /diet-profile?brief=true`: what the one-line entry needs.
struct DietProfileBrief: Equatable {
  var version: String?
  var sections: Int
  var goalFrom: String?
  var updatedOn: String?

  static func parse(_ j: [String: Any]?) -> DietProfileBrief? {
    guard let j else { return nil }
    return DietProfileBrief(version: j["version"] as? String, sections: (j["sections"] as? NSNumber)?.intValue ?? 0,
                            goalFrom: j["goal_effective_from"] as? String, updatedOn: j["updated_on"] as? String)
  }

  /// The profile's own version, else the goal version in effect; 「未建立」 when there is neither.
  var meta: String {
    guard version != nil || sections > 0 || goalFrom != nil else { return tr("未建立", "Not set up") }
    let label = version ?? goalFrom.map { tr("目标 \(RecordDay.short($0)) 起", "Goal from \(RecordDay.short($0))") } ?? ""
    guard let u = updatedOn else { return label }
    let updated = tr("（\(RecordDay.short(u)) 更新）", " (updated \(RecordDay.short(u)))")
    return label + updated
  }
}

/// `GET /diet-profile`.
struct DietProfile: Equatable {
  struct Row: Equatable, Identifiable {
    var label: String
    var value: String
    var sub: String?
    var id: String { label + value + (sub ?? "") }
  }

  struct Tile: Equatable, Identifiable {
    var label: String
    var value: String
    var unit: String
    var id: String { label }
  }

  /// A line of 「健康约束」 / 「什么时候调整」: the rule and what it rests on, one under the other.
  struct Stacked: Equatable, Identifiable {
    var title: String
    var detail: String
    var id: String { title + detail }
  }

  struct Rule: Equatable, Identifiable {
    var text: String
    var since: String?
    var basis: String?
    var id: String { text }
  }

  struct Version: Equatable, Identifiable {
    var at: String
    var label: String
    var changes: String
    var reason: String?
    var id: String { at + label + changes }
  }

  var version: String?
  var hidesGoals = false

  var goalRows: [Row] = []
  var goalNote: String?
  var goalText: String?

  var energyTiles: [Tile] = []
  var energyCaption: String?
  var planTiles: [Tile] = []
  var planFast = false
  var weightRows: [Row] = []
  var whyNote: String?
  var whyText: String?

  var rules: [Rule] = []
  var constraints: [Stacked] = []
  var constraintsText: String?

  var dayRows: [Row] = []
  var staples: [String] = []
  var dayText: String?

  var adjust: [Stacked] = []
  var adjustText: String?

  var log: [Version] = []

  static func parse(_ p: [String: Any]?) -> DietProfile? {
    guard let p else { return nil }
    let n = RecordFormat.double
    var sections: [String: [String: Any]] = [:]
    for s in (p["sections"] as? [[String: Any]]) ?? [] {
      if let k = s["section"] as? String { sections[k] = s }
    }
    func data(_ k: String) -> [String: Any]? { sections[k]?["data"] as? [String: Any] }
    /// A section the AI wrote as text only.
    func text(_ k: String) -> String? { data(k) == nil ? sections[k]?["text"] as? String : nil }
    func stacked(_ k: String, _ a: String, _ b: String) -> [Stacked] {
      ((data(k)?["items"] as? [[String: Any]]) ?? []).map { Stacked(title: ($0[a] as? String) ?? "—", detail: ($0[b] as? String) ?? "") }
    }

    var out = DietProfile(version: p["version"] as? String, hidesGoals: ((p["hidden"] as? [String]) ?? []).contains("goals"))

    // current goals
    let g = p["goals"] as? [String: Any]
    let sets = (g?["sets"] as? [[String: Any]]) ?? []
    if !sets.isEmpty {
      out.goalRows = sets.map { s in
        let bits: [String?] = [
          n(s["protein_g"]).map { tr("蛋白 \(RecordFormat.num($0))", "protein \(RecordFormat.num($0))") },
          n(s["carbs_g"]).map { tr("碳水 \(RecordFormat.num($0))", "carbs \(RecordFormat.num($0))") },
          n(s["fat_g"]).map { tr("脂肪 \(RecordFormat.num($0)) g", "fat \(RecordFormat.num($0)) g") },
          n(s["sat_fat_max_g"]).map { tr("饱和脂肪 ≤ \(RecordFormat.num($0)) g", "sat. fat ≤ \(RecordFormat.num($0)) g") },
          n(s["added_sugar_max_g"]).map { tr("添加糖 ≤ \(RecordFormat.num($0)) g", "added sugar ≤ \(RecordFormat.num($0)) g") },
          n(s["fiber_min_g"]).map { tr("纤维 ≥ \(RecordFormat.num($0)) g", "fibre ≥ \(RecordFormat.num($0)) g") },
          (s["days"] as? NSNumber).map { tr("\($0.intValue) 天 / 周", "\($0.intValue) days / week") },
        ]
        return Row(label: (s["name"] as? String) ?? tr("目标", "Target"), value: "\(RecordFormat.num(n(s["calories"]))) kcal",
                   sub: bits.compactMap { $0 }.joined(separator: " · "))
      }
      if let from = g?["effective_from"] as? String {
        out.goalRows.append(Row(label: tr("生效", "In effect"), value: tr("\(RecordDay.short(from)) 起", "from \(RecordDay.short(from))"),
                                sub: g?["reason"] as? String))
      }
    } else if g != nil {
      out.goalRows.append(Row(label: tr("目标", "Target"), value: "—"))
    }
    let t = data("targets")
    let water = n(t?["water_ml"]) ?? sets.compactMap { n($0["water_ml"]) }.first
    if let water { out.goalRows.append(Row(label: tr("喝水", "Water"), value: "\(RecordFormat.num(water)) ml")) }
    if let steps = n(t?["steps"]) { out.goalRows.append(Row(label: tr("步数", "Steps"), value: RecordFormat.num(steps))) }
    out.goalNote = t?["note"] as? String
    out.goalText = text("targets")

    // why these numbers
    if let e = p["energy"] as? [String: Any], n(e["total_kcal"]) != nil {
      out.energyTiles = [Tile(label: tr("基础代谢", "Basal"), value: RecordFormat.num(n(e["basal_kcal"])), unit: "kcal"),
                         Tile(label: tr("活动", "Active"), value: RecordFormat.num(n(e["active_kcal"])), unit: "kcal"),
                         Tile(label: tr("总消耗", "Total burn"), value: RecordFormat.num(n(e["total_kcal"])), unit: "kcal")]
      if let days = (e["days"] as? NSNumber)?.intValue, let start = e["start"] as? String, let end = e["end"] as? String {
        let range = "\(RecordDay.short(start))–\(RecordDay.short(end))"
        out.energyCaption = tr("近 \(days) 天平均 · \(range)", "Average of \(days) days · \(range)")
      }
    }
    if let plan = p["plan"] as? [String: Any], let d = n(plan["deficit_kcal"]), let l = n(plan["loss_kg_per_week"]) {
      let deficit = d >= 0 ? "−\(RecordFormat.num(d))" : "+\(RecordFormat.num(-d))"
      let loss = l >= 0 ? "−\(RecordFormat.num(l, 2))" : "+\(RecordFormat.num(-l, 2))"
      out.planTiles = [Tile(label: tr("目标", "Target"), value: RecordFormat.num(n(plan["target_kcal"])), unit: "kcal"),
                       Tile(label: tr("每天", "Per day"), value: deficit, unit: "kcal"),
                       Tile(label: tr("预计每周", "Expected per week"), value: loss, unit: "kg")]
      out.planFast = (plan["fast"] as? Bool) ?? false
    }
    let rat = data("rationale")
    let weight = n((p["weight"] as? [String: Any])?["kg"]), goalWeight = n(rat?["goal_weight_kg"])
    if weight != nil || goalWeight != nil {
      out.weightRows.append(Row(label: tr("体重", "Weight"), value: "\(RecordFormat.num(weight, 1)) → \(RecordFormat.num(goalWeight, 1)) kg"))
    }
    for c in (rat?["checkpoints"] as? [[String: Any]]) ?? [] {
      let date = (c["date"] as? String).map(RecordDay.short) ?? "—"
      out.weightRows.append(Row(label: tr("检查点 \(date)", "Check \(date)"), value: n(c["weight_kg"]).map { "\(RecordFormat.num($0, 1)) kg" } ?? "",
                                sub: c["note"] as? String))
    }
    out.whyNote = rat?["note"] as? String
    out.whyText = text("rationale")

    // constraints the app enforces, then what the AI wrote
    let cr = p["constraint_rules"] as? [String: Any]
    out.rules = ((cr?["current"] as? [[String: Any]]) ?? []).compactMap { c in
      (c["text"] as? String).map { Rule(text: $0, since: c["effective_from"] as? String, basis: c["basis"] as? String) }
    }
    out.constraints = stacked("constraints", "rule", "basis")
    out.constraintsText = text("constraints")

    // a day of eating
    let day = data("day_plan")
    out.dayRows = ((day?["meals"] as? [[String: Any]]) ?? []).map { m in
      let protein = n(m["protein_g"]).map { " · " + tr("蛋白 \(RecordFormat.num($0)) g", "protein \(RecordFormat.num($0)) g") } ?? ""
      return Row(label: (m["meal"] as? String) ?? "—", value: n(m["kcal"]).map { "\(RecordFormat.num($0)) kcal" } ?? "",
                 sub: ((m["foods"] as? String) ?? "") + protein)
    }
    out.staples = (day?["staples"] as? [String]) ?? []
    out.dayText = text("day_plan")

    out.adjust = stacked("adjust_rules", "when", "action")
    out.adjustText = text("adjust_rules")

    // version log: profile versions, goal versions, constraints that ended; newest first
    var log: [Version] = ((p["versions"] as? [[String: Any]]) ?? []).map {
      Version(at: RecordFormat.string($0["changed_on"]) ?? "", label: ($0["version"] as? String) ?? "", changes: ($0["changes"] as? String) ?? "",
              reason: $0["reason"] as? String)
    }
    for h in (p["goal_history"] as? [[String: Any]]) ?? [] {
      let tk = n(h["training_kcal"]), rk = n(h["rest_kcal"]), td = (h["training_days"] as? NSNumber)?.intValue ?? 0
      let kcal = td > 0 && tk != rk
        ? tr("训练日 \(RecordFormat.num(tk)) / 休息日 \(RecordFormat.num(rk)) kcal", "Training \(RecordFormat.num(tk)) / rest \(RecordFormat.num(rk)) kcal")
        : "\(RecordFormat.num(rk)) kcal"
      log.append(Version(at: RecordFormat.string(h["effective_from"]) ?? "", label: tr("目标", "Goal"), changes: kcal, reason: h["reason"] as? String))
    }
    for c in (cr?["history"] as? [[String: Any]]) ?? [] {
      log.append(Version(at: RecordFormat.string(c["effective_to"]) ?? "", label: tr("约束", "Limit"), changes: (c["text"] as? String) ?? "",
                         reason: tr("到此为止", "Ended")))
    }
    out.log = log.sorted { $0.at > $1.at }
    return out
  }

  var isEmpty: Bool {
    goalRows.isEmpty && goalText == nil && energyTiles.isEmpty && planTiles.isEmpty && weightRows.isEmpty && whyText == nil && whyNote == nil
      && rules.isEmpty && constraints.isEmpty && constraintsText == nil && dayRows.isEmpty && dayText == nil && staples.isEmpty
      && adjust.isEmpty && adjustText == nil && log.isEmpty
  }
}

/// Which blocks of the sheet are open (this phone only). All open except the version log.
enum DietProfileBlocks {
  private static let key = "w2w.dietProfile.open"
  private static let closedByDefault: Set<String> = ["versions"]

  static func isOpen(_ block: String, _ defaults: UserDefaults = .standard) -> Bool {
    (defaults.dictionary(forKey: key)?[block] as? Bool) ?? !closedByDefault.contains(block)
  }

  static func set(_ block: String, open: Bool, _ defaults: UserDefaults = .standard) {
    var d = defaults.dictionary(forKey: key) ?? [:]
    d[block] = open
    defaults.set(d, forKey: key)
  }
}

/// The sheet: the cached profile at once, then the server's.
struct DietProfileSheet: View {
  @ObservedObject var store: FoodStore
  @State private var profile: DietProfile?
  @State private var loaded = false

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      if let p = profile, !p.isEmpty { blocks(p) } else if loaded { EmptyNote(text: tr("暂无", "None")) } else { ProgressView().frame(maxWidth: .infinity) }
    }
    .task {
      profile = store.dietProfile()
      if let d = await store.loadDietProfile() { profile = d }
      loaded = true
    }
  }

  @ViewBuilder private func blocks(_ p: DietProfile) -> some View {
    if !p.hidesGoals, !p.goalRows.isEmpty || p.goalText != nil {
      Block(key: "goals", title: tr("当前目标", "Current goals")) {
        rows(p.goalRows)
        note(p.goalNote)
        paragraph(p.goalText)
      }
    }
    if !p.energyTiles.isEmpty || !p.planTiles.isEmpty || !p.weightRows.isEmpty || p.whyText != nil || p.whyNote != nil {
      Block(key: "why", title: tr("为什么是这个数", "Why these numbers")) {
        tiles(p.energyTiles)
        if let c = p.energyCaption { Text(c).font(Typo.caption).foregroundColor(Palette.text3) }
        tiles(p.planTiles)
        if p.planFast { Badge(label: tr("减得偏快", "Losing fast"), tone: .bad) }
        rows(p.weightRows)
        note(p.whyNote)
        paragraph(p.whyText)
      }
    }
    if !p.rules.isEmpty || !p.constraints.isEmpty || p.constraintsText != nil {
      Block(key: "constraints", title: tr("健康约束", "Health limits")) {
        if !p.rules.isEmpty { chips(p.rules.map(\.text), tone: .mid) }
        ForEach(p.rules.filter { $0.basis != nil }) { r in
          let since = r.since.map { tr("自 \(RecordDay.short($0))", "since \(RecordDay.short($0))") }
          Text([r.text, since, r.basis].compactMap { $0 }.joined(separator: " · ")).font(Typo.caption).foregroundColor(Palette.text2)
        }
        stacked(p.constraints)
        paragraph(p.constraintsText)
      }
    }
    if !p.dayRows.isEmpty || !p.staples.isEmpty || p.dayText != nil {
      Block(key: "day_plan", title: tr("一天怎么吃", "A day of eating")) {
        rows(p.dayRows)
        if !p.staples.isEmpty { chips(p.staples, tone: .text) }
        paragraph(p.dayText)
      }
    }
    if !p.adjust.isEmpty || p.adjustText != nil {
      Block(key: "adjust_rules", title: tr("什么时候调整", "When to adjust")) {
        stacked(p.adjust)
        paragraph(p.adjustText)
      }
    }
    if !p.log.isEmpty {
      Block(key: "versions", title: tr("版本记录", "Version log"), count: p.log.count) {
        ForEach(p.log) { v in
          VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
              Text(v.label).font(Typo.bodyStrong).foregroundColor(Palette.text)
              Text(RecordDay.short(v.at)).font(Typo.caption).foregroundColor(Palette.text3)
            }
            Text(v.changes).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
            if let r = v.reason { Text(r).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true) }
          }
          .padding(.vertical, 8)
          .frame(maxWidth: .infinity, alignment: .leading)
          .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
        }
      }
    }
  }

  @ViewBuilder private func rows(_ xs: [DietProfile.Row]) -> some View {
    ForEach(xs) { r in
      HStack(alignment: .firstTextBaseline, spacing: 12) {
        VStack(alignment: .leading, spacing: 2) {
          Text(r.label).font(Typo.body).foregroundColor(Palette.text)
          if let s = r.sub, !s.isEmpty { Text(s).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true) }
        }
        Spacer(minLength: 8)
        Text(r.value).font(Typo.body.monospacedDigit()).foregroundColor(Palette.text).multilineTextAlignment(.trailing)
      }
      .padding(.vertical, 8)
      .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
    }
  }

  @ViewBuilder private func tiles(_ xs: [DietProfile.Tile]) -> some View {
    if !xs.isEmpty {
      HStack(spacing: 8) { ForEach(xs) { MetricTile(label: $0.label, value: $0.value, unit: $0.unit) } }
    }
  }

  private func stacked(_ xs: [DietProfile.Stacked]) -> some View {
    ForEach(xs) { x in
      VStack(alignment: .leading, spacing: 2) {
        Text(x.title).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
        if !x.detail.isEmpty { Text(x.detail).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true) }
      }
      .padding(.vertical, 8)
      .frame(maxWidth: .infinity, alignment: .leading)
      .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
    }
  }

  private func chips(_ xs: [String], tone: Tone) -> some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 6) { ForEach(xs, id: \.self) { Badge(label: $0, tone: tone) } }
    }
  }

  @ViewBuilder private func note(_ s: String?) -> some View {
    if let s, !s.isEmpty { Text(s).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true) }
  }

  @ViewBuilder private func paragraph(_ s: String?) -> some View {
    if let s, !s.isEmpty { Text(s).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true) }
  }
}

/// One collapsible block; its state is remembered on this phone.
private struct Block<Content: View>: View {
  let key: String
  let title: String
  var count: Int?
  @ViewBuilder var content: () -> Content
  @State private var open: Bool

  init(key: String, title: String, count: Int? = nil, @ViewBuilder content: @escaping () -> Content) {
    self.key = key
    self.title = title
    self.count = count
    self.content = content
    _open = State(initialValue: DietProfileBlocks.isOpen(key))
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      Button {
        withAnimation(.easeOut(duration: 0.15)) { open.toggle() }
        DietProfileBlocks.set(key, open: open)
      } label: {
        HStack(spacing: 8) {
          Text(title).font(Typo.bodyStrong).foregroundColor(Palette.text)
          Spacer()
          if let count { Text("\(count)").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text3) }
          Icon(open ? .chevronDown : .chevronRight, size: 16).foregroundColor(Palette.text3)
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityAddTraits(open ? .isSelected : [])
      if open { content() }
    }
    .padding(.horizontal, 14).padding(.vertical, 6)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.inSheet))
  }
}
