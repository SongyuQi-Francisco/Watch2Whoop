import SwiftUI

/// Interactive cards in the Jarvis chat (body_engine ai/cards.py, `card` events). The server builds every card from the
/// user's own data and the model only picks which, so nothing here draws model text as markup: buttons either send the
/// app's own sentence back as the user's message or open one of the app's pages.
///
/// Native: `nativeTypes` (this file, JarvisBodyCards.swift, JarvisFoodCards.swift). A type this app does not know yet (they
/// keep growing on the server) gets `GenericCard`: a generic title, the date, and only the top-level fields that have a
/// translated label, plus its buttons. It never shows a raw field name.
enum JarvisCards {
  static let nativeTypes: Set<String> = [
    "training", "meal", "metric", "sleep", "actions", "muscle", "goals", "suggest", "recipe", "recipes", "templates", "shopping",
    "recovery", "strain", "stress", "healthspan", "trend", "weight", "lab", "supplements", "water", "journal", "report", "hr_zones",
    "vo2max", "monitor", "data_status", "product",
  ]

  /// Drawn without the card's own frame: they hold the Trends tab's cards, which have theirs (ai.js BARE).
  static let bare: Set<String> = ["recovery", "strain", "trend", "weight", "vo2max", "hr_zones"]

  static func title(_ type: String) -> String {
    switch type {
    case "training": return tr("训练", "Training")
    case "meal": return tr("饮食", "Food")
    case "metric": return tr("指标", "Metric")
    case "sleep": return tr("睡眠", "Sleep")
    case "muscle": return tr("肌肉负荷", "Muscle load")
    case "goals": return tr("饮食目标", "Nutrition goals")
    case "suggest": return tr("推荐餐", "Suggestions")
    case "recipe": return tr("食谱", "Recipe")
    case "recipes": return tr("食谱", "Recipes")
    case "templates": return tr("快捷记录", "Quick meals")
    case "shopping": return tr("采购清单", "Shopping list")
    case "recovery": return tr("恢复", "Recovery")
    case "strain": return tr("负荷", "Strain")
    case "stress": return tr("压力", "Stress")
    case "healthspan": return tr("身体年龄", "Body age")
    case "trend": return tr("趋势", "Trend")
    case "weight": return tr("体重", "Weight")
    case "lab": return tr("化验", "Lab")
    case "supplements": return tr("补剂", "Supplements")
    case "water": return tr("喝水", "Water")
    case "journal": return tr("日记", "Journal")
    case "report": return tr("报告", "Report")
    case "hr_zones": return tr("心率区间分布", "Heart-rate zones")
    case "vo2max": return "VO₂max"
    case "monitor": return tr("健康监测", "Health monitor")
    case "data_status": return tr("数据状态", "Data status")
    case "product": return tr("商品", "Product")
    default: return tr("卡片", "Card")
    }
  }

  /// A metric's name: the app's own, then the series' label on a trend card; a bare key is never shown or sent.
  static func metricName(_ key: String?, card: JSONValue? = nil) -> String {
    switch key {
    case "recovery": return tr("恢复", "Recovery")
    case "strain": return tr("负荷", "Strain")
    case "sleep": return tr("睡眠", "Sleep")
    case "hrv": return "HRV"
    case "rhr": return tr("静息心率", "Resting HR")
    case "weight": return tr("体重", "Weight")
    default:
      guard let key else { return "" }
      return TrendCards.conf[key]?.label ?? card?["trend"]?["series"]?[key]?["label"]?.string ?? tr("这个指标", "this metric")
    }
  }

  /// The `send` keys (cards.SEND_KEYS) → button label and the sentence sent (ai.js SEND).
  static func send(_ key: String, _ c: JSONValue) -> (String, String)? {
    let name = c["name"]?.string ?? ""
    let id = c["id"]?.string ?? ""
    let metric = metricName(c["metric"]?.string, card: c)
    let test = c["test_name"]?.string ?? ""
    let date = c["date"]?.string ?? ""
    switch key {
    case "adjust_training": return (tr("按恢复调整", "Adjust to recovery"), tr("按今天的恢复调整一下训练", "Adjust today's training to my recovery"))
    case "next_meal": return (tr("下一餐吃什么", "Next meal"), tr("下一餐吃什么合适", "What should my next meal be"))
    case "analyze_muscles": return (tr("分析肌群训练", "Analyse muscles"), tr("请分析我的肌群训练：哪些偏少、哪些偏多、和目标差在哪、具体怎么改（避开我的禁用动作）。要改训练计划的话先给我看改动，我确认后再改。", "Analyse my muscle training: which groups get too little or too much, how far from my goals, and what to change (avoid my banned exercises). Show me any plan change before making it."))
    case "explain_trend": return (tr("解释这个变化", "Explain this"), tr("解释一下\(metric)最近的变化", "Explain the recent change in my \(metric)"))
    case "more_meals": return (tr("换一个", "Something else"), tr("换几道别的推荐", "Suggest a few other dishes"))
    case "log_recipe": return (tr("记下这餐", "Log this meal"), tr("按食谱「\(name)」记一份（食谱 id \(id)）", "Log one serving of the recipe \"\(name)\" (recipe id \(id))"))
    case "recipe_steps": return (tr("怎么做", "How to cook"), tr("「\(name)」怎么做？", "How do I cook \"\(name)\"?"))
    case "shopping_plan": return (tr("还缺什么", "What's missing"), tr("看看这周的采购清单还缺什么", "What is still missing from this week's shopping list"))
    case "undo_meal": return (tr("撤销", "Undo"), tr("撤销刚才记的这餐", "Undo the meal I just logged"))
    case "explain_recovery": return (tr("为什么是这个分", "Why this score"), tr("解释一下我今天的恢复分：哪些因素拉高、哪些拉低", "Explain today's recovery score: what pushed it up and what pulled it down"))
    case "explain_stress": return (tr("压力从哪来", "Where's the stress from"), tr("我今天的压力从哪来，怎么降下来", "Where does today's stress come from, and how do I bring it down"))
    case "improve_body_age": return (tr("怎么变年轻", "Get younger"), tr("我的身体年龄哪几项拖后腿，先改哪一项最划算", "Which parts hold my body age back, and which is worth fixing first"))
    case "improve_vo2max": return (tr("怎么提高", "How to improve"), tr("我的 VO₂max 怎么提高，按我现在的训练给个计划", "How do I raise my VO₂max? Give me a plan based on my current training"))
    case "explain_monitor": return (tr("要紧吗", "Does it matter"), tr("我今天的生命体征有异常吗，要不要注意", "Is anything off in my vital signs today, should I watch it"))
    case "explain_lab": return (tr("解读一下", "Explain"), tr("解读一下我的\(test)历次化验", "Explain my \(test) results over time"))
    case "log_supplements": return (tr("记今天的补剂", "Log today's supplements"), tr("今天的补剂都吃了", "I took all my supplements today"))
    case "explain_journal": return (tr("怎么用这些", "What to do"), tr("根据日记分析，哪些习惯最值得保持或改掉", "From my journal analysis, which habits are most worth keeping or dropping"))
    case "explain_report": return (tr("总结一下", "Sum up"), c["period"]?.string == "month" ? tr("总结一下我这个月", "Sum up my month") : tr("总结一下我这周", "Sum up my week"))
    case "explain_sleep": return (tr("分析这晚", "Analyse this night"), tr("分析一下我 \(date) 醒来的那晚睡眠", "Analyse the night I woke up on \(date)"))
    case "log_product": return (tr("记一次", "Log it"), tr("按商品库「\(name)」（商品 id \(id)）的营养记一次，先问我吃了多少", "Log \"\(name)\" (product id \(id)) with its nutrition; ask me how much I had first"))
    case "record_price": return nil   // needs a photo of the label: the composer's camera does that
    default: return nil
    }
  }

  /// The `open` pages (cards.OPEN_PAGES) → button label. The app decides where each goes (JarvisRouter.open(page:)).
  static func openLabel(_ page: String) -> String? {
    switch page {
    case "record.training": return tr("去记录", "Log it")
    case "food": return tr("打开饮食", "Open food")
    case "trends": return tr("看趋势", "See trends")
    case "today": return tr("回到今天", "Back to today")
    case "sleep": return tr("睡眠详情", "Sleep detail")
    case "profile": return tr("设置性别", "Set your sex")
    case "food.recipes": return tr("全部食谱", "All recipes")
    case "food.shopping": return tr("打开采购", "Open shopping")
    case "recipe": return tr("打开食谱", "Open recipe")
    case "healthspan": return tr("打开身体年龄", "Open body age")
    case "lab": return tr("全部记录", "All results")
    case "trends.report": return tr("打开报告", "Open report")
    case "supplements": return tr("我的补剂", "My supplements")
    case "data_status": return tr("数据状态", "Data status")
    default: return nil
    }
  }

  static func sleepNote(_ key: String) -> String? {
    [
      "short_of_need": tr("睡得不够", "Short of need"), "weak_duration": tr("时长拉分", "Duration"), "weak_stages": tr("阶段拉分", "Stages"),
      "weak_consistency": tr("规律性拉分", "Consistency"), "weak_interruptions": tr("中途醒来拉分", "Wake-ups"),
      "deep_low": tr("深睡偏少", "Little deep sleep"), "rem_low": tr("REM 偏少", "Little REM"), "efficiency_low": tr("效率偏低", "Low efficiency"),
      "latency_long": tr("入睡慢", "Slow to fall asleep"), "waso_long": tr("夜里醒得多", "Awake a lot"), "bedtime_late": tr("睡得晚", "Late bedtime"),
      "hrv_low": tr("HRV 偏低", "Low HRV"), "rhr_high": tr("心率偏高", "High heart rate"), "resp_high": tr("呼吸偏快", "Fast breathing"),
      "temp_off": tr("体温异常", "Temperature off"), "spo2_low": tr("血氧偏低", "Low SpO₂"), "illness": tr("可能生病", "Maybe ill"),
    ][key]
  }

  static func num(_ v: Double?, _ digits: Int = 0) -> String {
    guard let v else { return "—" }
    return String(format: "%.\(digits)f", v)
  }

  static func hm(_ minutes: Double?) -> String {
    guard let m = minutes else { return "—" }
    return "\(Int(m) / 60):" + String(format: "%02d", Int(m.rounded()) % 60)
  }

  /// The server's sleep performance is a fraction (0.82), shown as a percentage (ai.js recoveryBody).
  static func percent(_ v: Double?) -> Double? { v.map { $0 * 100 } }

  /// A night's stages in the sleep page's model, for its `Hypnogram`.
  static func stages(_ main: JSONValue) -> [SleepNightModel.Stage] {
    let rows: [JSONValue] = main["stages"]?.array ?? []
    return rows.compactMap { s -> SleepNightModel.Stage? in
      guard let st = s["stage"]?.string, let o = s["offset_min"]?.double, let mins = s["minutes"]?.double else { return nil }
      let from: String = s["start"]?.string ?? "", to: String = s["end"]?.string ?? ""
      return SleepNightModel.Stage(stage: st, offset: o, minutes: mins, start: from, end: to)
    }
  }

  static func sleepPart(_ key: String) -> String {
    switch key {
    case "duration": return tr("时长", "Duration")
    case "stages": return tr("阶段", "Stages")
    case "consistency": return tr("规律性", "Consistency")
    case "interruptions": return tr("中途醒来", "Wake-ups")
    default: return tr("其他", "Other")
    }
  }

  /// `2026-01-02` → `01-02`.
  static func mmdd(_ iso: String?) -> String? {
    guard let s = iso, s.count >= 10 else { return nil }
    return String(s.dropFirst(5).prefix(5))
  }
}

/// One card in the chat: its body by type, then its buttons.
struct JarvisCardView: View {
  let card: JSONValue
  var busy = false
  let send: (String) -> Void
  /// A page of the app (JarvisRouter.open(page:card:)) and the card or row it is about.
  let open: (String, JSONValue) -> Void

  private var type: String { card["type"]?.string ?? "" }

  var body: some View {
    if JarvisCards.bare.contains(type) {
      VStack(alignment: .leading, spacing: 8) {
        content
        actions
      }
    } else {
      VStack(alignment: .leading, spacing: 10) {
        if type != "actions" { content }   // only buttons: no empty row above them
        actions
      }
      .jarvisCardFrame()
    }
  }

  /// Equatable, so a streaming answer redraws the chat without parsing every card above it again.
  private var content: some View {
    JarvisCardBody(card: card, busy: busy, send: send, open: open).equatable()
  }

  @ViewBuilder private var actions: some View {
    let acts = buttons
    if !acts.isEmpty {
      FlowRow(spacing: 8) {
        ForEach(Array(acts.enumerated()), id: \.offset) { _, a in
          Button(a.label) { a.run() }
            .font(.system(size: 13, weight: .medium))
            .foregroundColor(Palette.text)
            .padding(.vertical, 7).padding(.horizontal, 12)
            .overlay(Capsule().strokeBorder(Palette.text.opacity(a.send ? 0.35 : 0.8), lineWidth: 1))
            .buttonStyle(PressScale())
            .disabled(a.send && busy)
        }
      }
    }
  }

  private struct Act { let label: String; let send: Bool; let run: () -> Void }

  private var buttons: [Act] {
    (card["actions"]?.array ?? []).compactMap { a in
      switch a["kind"]?.string {
      case "open":
        guard let p = a["page"]?.string, let l = JarvisCards.openLabel(p) else { return nil }
        return Act(label: l, send: false) { open(p, card) }
      case "send":
        if let k = a["key"]?.string {
          guard let (l, t) = JarvisCards.send(k, card) else { return nil }
          return Act(label: l, send: true) { send(t) }
        }
        guard let l = a["label"]?.string, let t = a["text"]?.string, !l.isEmpty, !t.isEmpty else { return nil }
        return Act(label: l, send: true) { send(t) }
      default:
        return nil
      }
    }
  }
}

/// The body of a card by type. The closures only reach the chat and the router, which stay the same for the card's life.
private struct JarvisCardBody: View, Equatable {
  let card: JSONValue
  let busy: Bool
  let send: (String) -> Void
  let open: (String, JSONValue) -> Void

  static func == (a: JarvisCardBody, b: JarvisCardBody) -> Bool { a.busy == b.busy && a.card == b.card }

  var body: some View {
    switch card["type"]?.string ?? "" {
    case "training": TrainingCard(c: card)
    case "meal": MealCard(c: card, busy: busy, send: send)
    case "metric": MetricCard(c: card)
    case "sleep": SleepCard(c: card)
    case "actions": EmptyView()
    case "product": ProductCard(c: card)
    case "recovery": RecoveryCard(c: card, open: open)
    case "water": WaterCard(c: card)
    case "supplements": SupplementsCard(c: card)
    case "muscle": MuscleJarvisCard(c: card)
    case "goals": GoalsJarvisCard(c: card)
    case "suggest": SuggestJarvisCard(c: card, busy: busy, send: send, open: open)
    case "recipe": RecipeJarvisCard(c: card)
    case "recipes": RecipesJarvisCard(c: card, open: open)
    case "templates": TemplatesJarvisCard(c: card, busy: busy, send: send)
    case "shopping": ShoppingJarvisCard(c: card)
    case "strain": StrainJarvisCard(c: card, open: open)
    case "stress": StressJarvisCard(c: card)
    case "healthspan": HealthspanJarvisCard(c: card)
    case "trend": TrendJarvisCard(c: card, key: card["metric"]?.string ?? "", open: open)
    case "weight": TrendJarvisCard(c: card, key: "weight", open: open)
    case "vo2max": VO2JarvisCard(c: card, open: open)
    case "hr_zones": HRZonesJarvisCard(c: card, open: open)
    case "lab": LabJarvisCard(c: card)
    case "journal": JournalJarvisCard(c: card)
    case "report": ReportJarvisCard(c: card)
    case "monitor": MonitorJarvisCard(c: card, open: open)
    case "data_status": DataStatusJarvisCard(c: card)
    default: GenericCard(c: card)
    }
  }
}

// MARK: - pieces

extension View {
  /// The frame of a card in the chat: surface background, tile corners.
  func jarvisCardFrame() -> some View {
    padding(14)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
  }
}

struct CardHead: View {
  let title: String
  var date: String?
  var badge: String?
  /// A coloured state label; nil = the plain outline.
  var badgeTone: Tone?

  var body: some View {
    HStack(spacing: 8) {
      Text(title).font(Typo.section).foregroundColor(Palette.text2)
      if let d = JarvisCards.mmdd(date) { Text(d).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3) }
      Spacer(minLength: 4)
      if let badge, !badge.isEmpty {
        if let badgeTone { ToneBadge(text: badge, tone: badgeTone) } else { OutlineBadge(text: badge) }
      }
    }
  }
}

struct OutlineBadge: View {
  let text: String
  var body: some View {
    Text(text).font(.system(size: 11, weight: .semibold)).foregroundColor(Palette.text)
      .padding(.vertical, 3).padding(.horizontal, 8)
      .overlay(Capsule().strokeBorder(Palette.text.opacity(0.5), lineWidth: 1))
  }
}

/// Value against a goal, one ink: the bar is the text colour, the track a hairline.
struct CardMeter: View {
  let label: String
  let value: Double?
  let goal: Double?
  let unit: String

  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack {
        Text(label).font(Typo.caption).foregroundColor(Palette.text2)
        Spacer()
        Text("\(JarvisCards.num(value))\(goal.map { " / \(JarvisCards.num($0))" } ?? "")\(unit.isEmpty ? "" : " \(unit)")")
          .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text)
      }
      if let g = goal, g > 0 {
        GeometryReader { geo in
          ZStack(alignment: .leading) {
            Capsule().fill(Palette.surface2)
            Capsule().fill((value ?? 0) > g * 1.1 ? Palette.mid : Palette.text).frame(width: geo.size.width * min(1, max(0, (value ?? 0) / g)))
          }
        }
        .frame(height: 4)
      }
    }
  }
}

struct BigNumber: View {
  let value: String
  var unit: String?
  var note: String?

  var body: some View {
    HStack(alignment: .firstTextBaseline, spacing: 4) {
      Text(value).font(.system(size: 28, weight: .bold).monospacedDigit()).foregroundColor(Palette.text)
      if let unit, !unit.isEmpty { Text(unit).font(Typo.unit).foregroundColor(Palette.text2) }
      Spacer(minLength: 8)
      if let note { Text(note).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2) }
    }
  }
}

/// A thin line through the known values (gaps skipped), last point marked.
struct CardSparkline: View {
  let values: [Double?]

  var body: some View {
    GeometryReader { g in
      let known = values.compactMap { $0 }
      if let lo = known.min(), let hi = known.max(), values.count > 1 {
        let span = max(hi - lo, 1e-9)
        let pt: (Int, Double) -> CGPoint = { i, v in
          CGPoint(x: g.size.width * CGFloat(i) / CGFloat(values.count - 1), y: 4 + (g.size.height - 8) * CGFloat(1 - (v - lo) / span))
        }
        Path { p in
          var drawing = false
          for (i, v) in values.enumerated() {
            guard let v else { drawing = false; continue }
            if drawing { p.addLine(to: pt(i, v)) } else { p.move(to: pt(i, v)); drawing = true }
          }
        }
        .stroke(Palette.text, style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
        if let i = values.lastIndex(where: { $0 != nil }), let v = values[i] {
          Circle().fill(Palette.text).frame(width: 6, height: 6).position(pt(i, v))
        }
      }
    }
    .frame(height: 56)
    .accessibilityHidden(true)
  }
}

/// Buttons that wrap onto the next line (iOS 16 has no Layout-based flow in SwiftUI's library).
struct FlowRow: Layout {
  var spacing: CGFloat = 8

  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    let w = proposal.width ?? .infinity
    var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, widest: CGFloat = 0
    for s in subviews {
      let z = s.sizeThatFits(.unspecified)
      if x > 0 && x + z.width > w { y += row + spacing; x = 0; row = 0 }
      x += z.width + spacing
      row = max(row, z.height)
      widest = max(widest, x - spacing)
    }
    return CGSize(width: min(widest, w), height: y + row)
  }

  func placeSubviews(in b: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
    var x = b.minX, y = b.minY, row: CGFloat = 0
    for s in subviews {
      let z = s.sizeThatFits(.unspecified)
      if x > b.minX && x + z.width > b.maxX { y += row + spacing; x = b.minX; row = 0 }
      s.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(z))
      x += z.width + spacing
      row = max(row, z.height)
    }
  }
}

// MARK: - native types

private struct TrainingCard: View {
  let c: JSONValue

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: JarvisCards.title("training"), date: c["date"]?.string, badge: c["readiness"]?["label"]?.string)
      let sub = ([c["plan"]?.string] + (c["sessions"]?.array ?? []).map(\.string)).compactMap { $0 }.filter { !$0.isEmpty }
      let sets = c["log"]?["done_sets"]?.int ?? 0
      let log: String? = c["log"]?["status"]?.string == "submitted" ? tr("已提交", "Submitted")
        : sets > 0 ? tr("已记 \(sets) 组", "\(sets) sets logged") : nil
      if !sub.isEmpty || log != nil {
        Text((sub + [log].compactMap { $0 }).joined(separator: " · ")).font(Typo.caption).foregroundColor(Palette.text2)
      }
      let ex = c["exercises"]?.array ?? []
      if ex.isEmpty {
        Text(c["rest_day"]?.bool == true ? tr("休息日", "Rest day") : tr("这天没有训练计划", "No training planned that day"))
          .font(Typo.caption).foregroundColor(Palette.text2)
      }
      ForEach(Array(ex.prefix(8).enumerated()), id: \.offset) { _, e in
        HStack(alignment: .firstTextBaseline) {
          Text((Lang.isZh ? e["name_zh"]?.string : e["name_en"]?.string) ?? e["name_zh"]?.string ?? "")
            .font(Typo.body).foregroundColor(e["status"]?.string == "banned" ? Palette.text3 : Palette.text)
            .strikethrough(e["status"]?.string == "banned")
          Spacer(minLength: 8)
          if let t = e["target"]?.string { Text(t).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2) }
        }
      }
      if ex.count > 8 { Text(tr("还有 \(ex.count - 8) 个", "\(ex.count - 8) more")).font(Typo.caption).foregroundColor(Palette.text3) }
    }
  }
}

private struct MealCard: View {
  let c: JSONValue
  let busy: Bool
  let send: (String) -> Void
  @State private var openRow: Int?

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      CardHead(title: JarvisCards.title("meal"), date: c["date"]?.string)
      CardMeter(label: tr("热量", "Energy"), value: c["totals"]?["calories"]?.double, goal: c["goal"]?["calories"]?.double, unit: "kcal")
      CardMeter(label: tr("蛋白", "Protein"), value: c["totals"]?["protein_g"]?.double, goal: c["goal"]?["protein_g"]?.double, unit: "g")
      Text(tr("碳水", "Carbs") + " \(JarvisCards.num(c["totals"]?["carbs_g"]?.double)) g · " + tr("脂肪", "Fat") + " \(JarvisCards.num(c["totals"]?["fat_g"]?.double)) g")
        .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      let entries = c["entries"]?.array ?? []
      let acts = (c["entry_actions"]?.array ?? []).compactMap(\.string)
      if entries.isEmpty { Text(tr("这天还没有记录", "Nothing logged that day")).font(Typo.caption).foregroundColor(Palette.text2) }
      ForEach(Array(entries.prefix(6).enumerated()), id: \.offset) { i, e in
        VStack(alignment: .leading, spacing: 6) {
          HStack(alignment: .firstTextBaseline) {
            Text(e["food"]?.string ?? "").font(Typo.body).foregroundColor(Palette.text).lineLimit(2)
            if let q = e["quantity"]?.double {
              Text("\(JarvisCards.num(q, q.rounded() == q ? 0 : 1)) \(e["unit"]?.string ?? "")").font(Typo.caption).foregroundColor(Palette.text2)
            }
            Spacer(minLength: 8)
            Text("\(JarvisCards.num(e["calories"]?.double)) kcal").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
          }
          .contentShape(Rectangle())
          .onTapGesture { if !acts.isEmpty && e["id"] != nil { withAnimation(.easeOut(duration: 0.15)) { openRow = openRow == i ? nil : i } } }
          if openRow == i { rowActions(e, acts) }
        }
      }
      if entries.count > 6 { Text(tr("还有 \(entries.count - 6) 条", "\(entries.count - 6) more")).font(Typo.caption).foregroundColor(Palette.text3) }
    }
  }

  /// A logged row's portion / delete buttons: the sentence names the row by its own fields and id (ai.js ROW).
  private func rowActions(_ e: JSONValue, _ acts: [String]) -> some View {
    let d = c["date"]?.string ?? "", food = e["food"]?.string ?? "", id = e["id"]?.string ?? ""
    var out: [(String, String)] = []
    if acts.contains("portion") {
      for x in ["0.5", "1.5", "2"] {
        out.append(("×\(x)", tr("把 \(d) 记的「\(food)」改成原来的 \(x) 倍（id \(id)）", "Change \"\(food)\" logged on \(d) to \(x)× (id \(id))")))
      }
    }
    if acts.contains("delete") { out.append((tr("删除", "Delete"), tr("删掉 \(d) 记的「\(food)」（id \(id)）", "Delete \"\(food)\" logged on \(d) (id \(id))"))) }
    return HStack(spacing: 8) {
      ForEach(Array(out.enumerated()), id: \.offset) { _, a in
        Button(a.0) { send(a.1) }
          .font(.system(size: 12, weight: .medium)).foregroundColor(Palette.text)
          .padding(.vertical, 5).padding(.horizontal, 10)
          .overlay(Capsule().strokeBorder(Palette.text.opacity(0.35), lineWidth: 1))
          .disabled(busy)
      }
    }
  }
}

private struct MetricCard: View {
  let c: JSONValue

  var body: some View {
    let d = c["digits"]?.int ?? 0
    let values = (c["values"]?.array ?? []).map(\.double)
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: JarvisCards.metricName(c["metric"]?.string), badge: tr("近 \(values.count) 天", "\(values.count) days"))
      BigNumber(value: JarvisCards.num(c["latest"]?.double, d), unit: c["unit"]?.string,
                note: tr("均值", "Avg") + " " + JarvisCards.num(c["avg"]?.double, d))
      CardSparkline(values: values)
    }
  }
}

private struct SleepCard: View {
  let c: JSONValue

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: JarvisCards.title("sleep"), date: c["date"]?.string,
               badge: c["score"]?.double.map { tr("睡眠分", "Score") + " " + JarvisCards.num($0) })
      if let m = c["main"], !m.isNull {
        BigNumber(value: JarvisCards.hm(m["asleep_min"]?.double), unit: "\(m["start"]?.string ?? "") → \(m["end"]?.string ?? "")",
                  note: c["need_min"]?.double.map { tr("需求", "Need") + " " + JarvisCards.hm($0) })
        let stages = JarvisCards.stages(m)
        if !stages.isEmpty {
          Hypnogram(stages: stages, total: max(1, m["in_bed_min"]?.double ?? 1), start: m["start"]?.string ?? "", end: m["end"]?.string ?? "")
        }
        if let more = c["more"], !more.isNull {
          SleepMore(c: c, main: m, more: more)
        } else if let eff = m["efficiency_pct"]?.double {
          Text(tr("效率", "Efficiency") + " \(JarvisCards.num(eff))%").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
        }
        let keys = Array((c["notes"]?.array ?? []).filter { $0["tone"]?.string != "good" }.compactMap { $0["key"]?.string }
          .filter { JarvisCards.sleepNote($0) != nil }.prefix(3))
        if !keys.isEmpty {
          FlowRow(spacing: 6) {
            ForEach(keys, id: \.self) { OutlineBadge(text: JarvisCards.sleepNote($0) ?? $0) }
            // 「可能生病」 is an estimate: its short label opens the not-medical-advice sheet
            if keys.contains("illness") { DisclaimerTag(kind: .medical) }
          }
        }
      } else {
        Text(tr("这晚没有手表睡眠数据", "No watch sleep data that night")).font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
  }
}

/// Efficiency, time to fall asleep and wake-ups against the 7-night average, and the sleep score's parts (ai.js sleepMoreHtml).
private struct SleepMore: View {
  let c: JSONValue
  let main: JSONValue
  let more: JSONValue

  var body: some View {
    let a = c["avg7"], parts = (c["parts"]?.array ?? []).filter { $0["score"]?.double != nil }
    let avgEff = a?["efficiency_pct"]?.double.map { tr("平均 \(JarvisCards.num($0))%", "Avg \(JarvisCards.num($0))%") }
    let waso = more["waso_min"]?.double.map { tr("\(JarvisCards.num($0)) 分钟", "\(JarvisCards.num($0)) min") }
    VStack(alignment: .leading, spacing: 8) {
      HStack(alignment: .top, spacing: 0) {
        cell(tr("效率", "Efficiency"), main["efficiency_pct"]?.double, "%", avgEff)
        cell(tr("入睡耗时", "To fall asleep"), more["latency_min"]?.double, tr("分", "min"), nil)
        cell(tr("夜醒", "Wake-ups"), more["awakenings"]?.double, tr("次", ""), waso)
      }
      if let bed = a?["bedtime"]?.string {
        Text(tr("近 7 晚", "Last 7 nights") + " \(bed) → \(a?["wake"]?.string ?? "")").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      }
      ForEach(Array(parts.enumerated()), id: \.offset) { _, p in
        CardMeter(label: JarvisCards.sleepPart(p["key"]?.string ?? ""), value: p["score"]?.double, goal: 100, unit: "")
      }
    }
  }

  private func cell(_ l: String, _ v: Double?, _ u: String, _ sub: String?) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(l).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.8)
      HStack(alignment: .firstTextBaseline, spacing: 2) {
        Text(JarvisCards.num(v)).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
        if v != nil, !u.isEmpty { Text(u).font(Typo.unit).foregroundColor(Palette.text2) }
      }
      if let sub { Text(sub).font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text3).lineLimit(1) }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}

/// Today's score with its parts above the last two weeks (ai.js recoveryBody).
private struct RecoveryCard: View {
  let c: JSONValue
  let open: (String, JSONValue) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      VStack(alignment: .leading, spacing: 8) {
        CardHead(title: JarvisCards.title("recovery"), date: c["date"]?.string, badge: c["word"]?.string)
        BigNumber(value: JarvisCards.num(c["recovery"]?.double), unit: "%",
                  note: c["last"]?.double.map { tr("上次", "Last") + " \(JarvisCards.num($0))%" })
        HStack(spacing: 16) {
          kv("HRV", c["hrv_ms"]?.double, "ms")
          kv(tr("静息心率", "Resting HR"), c["rhr"]?.double, "bpm")
          kv(tr("睡眠表现", "Sleep"), JarvisCards.percent(c["sleep_performance"]?.double), "%")
        }
      }
      .jarvisCardFrame()
      if let d = JarvisCards.trendsData(c), let m = TrendCards.recovery(d), m.spec != nil {
        TrendCardBody(model: m, open: open)
      }
    }
  }

  private func kv(_ l: String, _ v: Double?, _ u: String) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(l).font(Typo.label).foregroundColor(Palette.text2)
      Text("\(JarvisCards.num(v)) \(u)").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
    }
  }
}

private struct WaterCard: View {
  let c: JSONValue

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: JarvisCards.title("water"), date: c["date"]?.string)
      CardMeter(label: tr("今天", "Today"), value: c["total_ml"]?.double, goal: c["goal_ml"]?.double, unit: "ml")
      let entries = c["entries"]?.array ?? []
      if !entries.isEmpty {
        Text(entries.prefix(6).compactMap { e -> String? in
          guard let ml = e["ml"]?.double else { return nil }
          let at = e["at"]?.string.map { String($0.dropFirst(11).prefix(5)) } ?? ""
          return "\(at) \(JarvisCards.num(ml))"
        }.joined(separator: " · ")).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      }
    }
  }
}

private struct SupplementsCard: View {
  let c: JSONValue

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: JarvisCards.title("supplements"), date: c["date"]?.string)
      ForEach(Array((c["items"]?.array ?? []).prefix(10).enumerated()), id: \.offset) { _, s in
        HStack(spacing: 8) {
          if s["taken"]?.bool == true { Icon(.check, size: 16).foregroundColor(Palette.text) }
          else { Circle().strokeBorder(Palette.text3, lineWidth: 1).frame(width: 14, height: 14).padding(1) }
          Text(s["name"]?.string ?? "").font(Typo.body).foregroundColor(Palette.text)
          Spacer(minLength: 8)
          Text([s["dose"]?.string, s["timing"]?.string].compactMap { $0 }.joined(separator: " · "))
            .font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1)
        }
      }
    }
  }
}

private struct ProductCard: View {
  let c: JSONValue

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      Text(c["name"]?.string ?? JarvisCards.title("product")).font(Typo.bodyStrong).foregroundColor(Palette.text)
      let sub = [c["brand"]?.string, c["size"]?.string].compactMap { $0 }.filter { !$0.isEmpty }
      if !sub.isEmpty { Text(sub.joined(separator: " · ")).font(Typo.caption).foregroundColor(Palette.text2) }
      let allergens = (c["allergens"]?.array ?? []).compactMap(\.string)
      if !allergens.isEmpty { FlowRow(spacing: 6) { ForEach(allergens, id: \.self) { OutlineBadge(text: $0) } } }
      if let n = c["nutrition"], let per = n["per100"], !per.isNull {
        VStack(alignment: .leading, spacing: 4) {
          let unit = n["unit"]?.string ?? "g"
          Text(tr("每 100 \(unit)", "Per 100 \(unit)")).font(Typo.label).foregroundColor(Palette.text2)
          HStack(spacing: 0) {
            nut(tr("热量", "kcal"), per["kcal"]?.double, "")
            nut(tr("蛋白", "Protein"), per["protein_g"]?.double, "g")
            nut(tr("碳水", "Carbs"), per["carbs_g"]?.double, "g")
            nut(tr("脂肪", "Fat"), per["fat_g"]?.double, "g")
          }
        }
      }
      let stores = c["stores"]?.array ?? []
      if !stores.isEmpty {
        VStack(alignment: .leading, spacing: 6) {
          Text(tr("价格", "Prices")).font(Typo.label).foregroundColor(Palette.text2)
          ForEach(Array(stores.prefix(5).enumerated()), id: \.offset) { _, s in
            HStack {
              Text(s["label"]?.string ?? "—").font(Typo.body).foregroundColor(s["stale"]?.bool == true ? Palette.text3 : Palette.text)
              if s["lowest"]?.bool == true { OutlineBadge(text: tr("最低", "Lowest")) }
              if s["half_price"]?.bool == true || s["special"]?.bool == true { OutlineBadge(text: tr("特价", "Special")) }
              Spacer()
              Text(money(s["price"]?.double, s["currency"]?.string)).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
            }
          }
          Text(tr("价格来自你记录的小票 / 截图，不是实时价", "Prices come from your own receipts and screenshots, not live"))
            .font(Typo.label).foregroundColor(Palette.text3)
        }
      }
      if let h = c["history"], !h.isNull {
        HStack(spacing: 0) {
          nut(tr("最低", "Low"), h["min"]?.double, "", money: true)
          nut(tr("平均", "Avg"), h["avg"]?.double, "", money: true)
          nut(tr("最近价", "Latest"), h["latest"]?.double, "", money: true)
        }
      }
    }
  }

  private func money(_ v: Double?, _ cur: String?) -> String {
    guard let v else { return "—" }
    let sym = ["AUD": "A$", "CNY": "¥", "USD": "$", "NZD": "NZ$", "EUR": "€", "GBP": "£", "JPY": "¥", "MYR": "RM"][cur ?? "AUD"] ?? ""
    return sym + String(format: "%.2f", v)
  }

  private func nut(_ l: String, _ v: Double?, _ u: String, money m: Bool = false) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(l).font(Typo.label).foregroundColor(Palette.text2)
      Text(m ? money(v, c["stores"]?[0]?["currency"]?.string) : "\(JarvisCards.num(v, 1))\(u)")
        .font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}

/// A card type this version does not know yet (an older app, a newer server): a generic title, the date, and only the
/// top-level fields below, under a translated label. Raw field names never reach the screen.
struct GenericCard: View {
  let c: JSONValue

  /// Field → short label, in display order.
  static var labels: [(String, String)] {
    [("name", tr("名称", "Name")), ("score", tr("分数", "Score")), ("value", tr("数值", "Value")), ("current", tr("现在", "Now")),
     ("latest", tr("最新", "Latest")), ("avg", tr("平均", "Average")), ("total", tr("合计", "Total")), ("goal", tr("目标", "Goal")),
     ("target", tr("目标", "Target")), ("percent", tr("百分比", "Percent")), ("count", tr("次数", "Count")), ("status", tr("状态", "Status")),
     ("level", tr("等级", "Level"))]
  }

  /// The labelled scalar fields that are present (at most six).
  static func rows(_ c: JSONValue) -> [(String, String)] {
    labels.compactMap { key, label -> (String, String)? in
      switch c[key] {
      case .number(let n): return (label, JarvisCards.num(n, n.rounded() == n ? 0 : 1))
      case .string(let s) where !s.isEmpty && s.count <= 40 && !isCode(s): return (label, s)
      default: return nil
      }
    }
    .prefix(6).map { $0 }
  }

  /// `in_range`, `no_raw_hr`: a code for the app, not a word for the user.
  static func isCode(_ s: String) -> Bool { s.range(of: "^[a-z0-9]+(_[a-z0-9]+)+$", options: .regularExpression) != nil }

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: JarvisCards.title(c["type"]?.string ?? ""), date: c["date"]?.string ?? c["end"]?.string)
      ForEach(Array(Self.rows(c).enumerated()), id: \.offset) { _, r in
        HStack {
          Text(r.0).font(Typo.caption).foregroundColor(Palette.text2)
          Spacer()
          Text(r.1).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text)
        }
      }
    }
  }
}
