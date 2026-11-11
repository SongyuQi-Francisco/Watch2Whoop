import SwiftUI

/// Jarvis → 记训练 → 计划 (docs/design/2.0.1/19-plan.html): this week as seven bars (planned sets a day; today outlined, other
/// planned days filled, days without a plan a dot), today's exercises with their sets and weight, a swap on every row (the
/// exercise's page with the alternatives the server picked; picking one replaces it in the plan), 3 / 4 / 5 天 (the built-in
/// splits of that many days, PUT /training/plan) and 开始训练 (记录 → 训练). Everything comes from TrainingStore.
struct CockpitPlanSheet: View {
  @StateObject private var store: TrainingStore
  let close: () -> Void
  let start: () -> Void
  @State private var toast: Toast?
  @State private var busy = false

  init(session: AccountSession, close: @escaping () -> Void, start: @escaping () -> Void) {
    _store = StateObject(wrappedValue: TrainingStore(session: session))
    self.close = close
    self.start = start
  }

  /// `4 × 8` → 4.
  static func sets(_ text: String?) -> Int {
    guard let t = text, let n = Int(t.prefix { $0.isNumber }) else { return 0 }
    return n
  }

  /// Planned sets of one day of the week.
  static func plannedSets(_ d: TrainingWeek.Day) -> Int {
    d.sessions.flatMap(\.exercises).reduce(0) { $0 + sets($1.setsText) }
  }

  /// Days of the built-in splits on offer, ascending (3 / 4 / 5).
  static func dayCounts(_ splits: [SplitOption]) -> [Int] {
    Array(Set(splits.map(\.days.count))).filter { $0 > 0 }.sorted()
  }

  /// The split a day count switches to: the plan's own when it already has that many days, else the picker's first choice.
  static func split(days n: Int, in splits: [SplitOption], current: String?) -> SplitOption? {
    let same = splits.filter { $0.days.count == n }
    return same.first { $0.type == current } ?? same.first { $0.type == SplitOption.initial(same, current: current) } ?? same.first
  }

  var body: some View {
    // finish(nil) = the close button; finish(message) = a page that changed the plan (a swap): say it, stay
    let nav = TrainingNav(store: store, finish: { t in if let t { toast = t } else { close() } }, say: { toast = $0 }, ask: nil)
    NavigationStack {
      TrainingPage(nav: nav, title: tr("本周计划", "This week")) {
        VStack(alignment: .leading, spacing: 20) {
          daysPicker
          readings
          if let w = store.week { WeekBars(week: w, today: store.today).frame(height: 146) }
          today(nav)
          PrimaryButton(title: tr("开始训练", "Start workout"), busy: false) { close(); start() }
            .disabled(store.session?.restDay ?? true)
        }
      }
    }
    .tint(Palette.text)
    .toast($toast)
    .task { await store.refresh() }
  }

  @ViewBuilder private var daysPicker: some View {
    let splits = store.week?.splits ?? []
    let counts = Self.dayCounts(splits)
    if !counts.isEmpty {
      let current = splits.first { $0.type == store.week?.splitType }?.days.count
      HStack(spacing: 2) {
        ForEach(counts, id: \.self) { n in
          Button { choose(n) } label: {
            Text(tr("\(n) 天", "\(n) days")).font(Typo.label)
              .foregroundColor(n == current ? Palette.onAccent : Palette.text)
              .frame(maxWidth: .infinity, minHeight: 32)
              .background(Capsule().fill(n == current ? Palette.accent : Color.clear))
          }
          .buttonStyle(PressScale())
          .disabled(busy || n == current)
          .accessibilityAddTraits(n == current ? .isSelected : [])
        }
      }
      .padding(2)
      .background(Capsule().fill(Palette.inSheet))
      .opacity(busy ? 0.5 : 1)
    }
  }

  private var readings: some View {
    let s = store.session
    let w = store.week
    return HStack(alignment: .firstTextBaseline) {
      HStack(alignment: .firstTextBaseline, spacing: 4) {
        Text("\(s?.doneSets ?? 0)").font(Typo.tileValue).foregroundColor(Palette.text)
        Text(tr("/ \(s?.totalSets ?? 0) 组", "/ \(s?.totalSets ?? 0) sets")).font(Typo.unit).foregroundColor(Palette.text2)
      }
      Spacer()
      HStack(alignment: .firstTextBaseline, spacing: 4) {
        Text("\(w?.trainedDays ?? 0)").font(Typo.tileValue).foregroundColor(Palette.text)
        Text(tr("/ \(w?.plannedDays ?? 0) 天", "/ \(w?.plannedDays ?? 0) days")).font(Typo.unit).foregroundColor(Palette.text2)
      }
    }
  }

  @ViewBuilder private func today(_ nav: TrainingNav) -> some View {
    if let s = store.session, !s.restDay, !s.loggable.isEmpty {
      HStack(alignment: .firstTextBaseline) {
        Text(tr("今天 · \(s.title)", "Today · \(s.title)")).font(Typo.bodyStrong).foregroundColor(Palette.text)
        Spacer()
        Text(s.minutes.map { tr("\(s.totalSets) 组 · 约 \($0) 分", "\(s.totalSets) sets · ~\($0) min") } ?? tr("\(s.totalSets) 组", "\(s.totalSets) sets"))
          .font(Typo.label).foregroundColor(Palette.text2)
      }
      VStack(spacing: 0) {
        ForEach(s.loggable) { e in
          HStack(spacing: 10) {
            Text(e.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
            Spacer(minLength: 8)
            Text("\(e.targetSets) × \(e.target.reps.map(String.init) ?? "—")").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
            if let kg = e.target.weightKg {
              HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(RecordFormat.num(kg, 1)).font(Typo.bodyStrong.monospacedDigit())
                Text("kg").font(Typo.unit).foregroundColor(Palette.text2)
              }
              .foregroundColor(Palette.text).frame(minWidth: 52, alignment: .trailing)
            }
            NavigationLink { ExerciseDetailView(nav: nav, slug: e.slug) } label: {
              Icon(.refreshCw, size: 16).foregroundColor(Palette.text2).frame(width: 36, height: 36)
            }
            .accessibilityLabel(tr("换掉 \(e.name)", "Swap \(e.name)"))
          }
          .padding(.vertical, 8)
          Divider().overlay(Palette.hair)
        }
      }
    } else if store.session?.restDay == true {
      Text(tr("今天休息", "Rest day")).font(Typo.bodyStrong).foregroundColor(Palette.text2)
    }
  }

  private func choose(_ n: Int) {
    guard let w = store.week, let s = Self.split(days: n, in: w.splits, current: w.splitType) else { return }
    busy = true
    Task {
      defer { busy = false }
      let out = await store.activate(s)
      toast = Toast(out.message)
      if out.ok { await store.refresh() }
    }
  }
}

/// Seven bars: planned sets a day. Today outlined in the ink, other planned days filled in the history grey, no plan = a dot.
private struct WeekBars: View {
  let week: TrainingWeek
  let today: String

  var body: some View {
    let maxSets = max(1, week.days.map(CockpitPlanSheet.plannedSets).max() ?? 1)
    HStack(alignment: .bottom, spacing: 0) {
      ForEach(week.days) { d in
        let n = CockpitPlanSheet.plannedSets(d), isToday = d.date == today
        VStack(spacing: 6) {
          if n > 0 {
            Text("\(n)").font(Typo.label.monospacedDigit()).foregroundColor(isToday ? Palette.text : Palette.text2)
            RoundedRectangle(cornerRadius: 6, style: .continuous)
              .fill(isToday ? Color.clear : Palette.text3.opacity(d.trained ? 0.9 : 0.5))
              .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).stroke(isToday ? Palette.text : Color.clear, lineWidth: 1.5))
              .frame(width: 22, height: 81 * CGFloat(n) / CGFloat(maxSets))
          } else {
            Circle().fill(Palette.text3).frame(width: 4, height: 4)
          }
          Text(ExerciseVocab.weekday(RecordDay.weekday(d.date))).font(Typo.label).foregroundColor(isToday ? Palette.text : Palette.text2)
          Text(d.label).font(.system(size: 10, weight: .medium)).foregroundColor(isToday ? Palette.text : Palette.text2).lineLimit(1)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
      }
    }
  }
}
