import SwiftUI

/// 今天页的饮食卡 (app.js dayCardHtml): what was eaten against the goal, the three macros, and on today the quick meals. A tap
/// opens the day's list; an entry opens the same sheet as on 饮食. Reads the today document the Today tab already keeps, with the
/// writes still in the queue laid over it, so a meal logged here (or on 饮食) shows at once.
struct TodayFoodCard: View {
  /// The day the Today tab shows; nil = the user's today.
  let day: String?
  @Binding var toast: Toast?
  @StateObject private var store: FoodStore
  @State private var sheet: Sheet?

  enum Sheet: Identifiable {
    case day
    case entry(FoodEntry)

    var id: String {
      switch self {
      case .day: return "day"
      case .entry(let e): return "entry.\(e.id)"
      }
    }
  }

  init(session: AccountSession, day: String?, toast: Binding<Toast?>) {
    self.day = day
    _toast = toast
    _store = StateObject(wrappedValue: FoodStore(session: session, cardOnly: true))
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      // No share or the food log can't be read: no card, as on the page
      if let n = store.nutrition, n.error == nil {
        VStack(alignment: .leading, spacing: 10) {
          SectionHeader(title: tr("饮食", "Food"))
          VStack(alignment: .leading, spacing: 14) {
            Button { sheet = .day } label: { numbers(n) }.buttonStyle(PressScale())
            if store.isToday && !store.templates.isEmpty {
              QuickMeals(store: store, toast: $toast, cardBackground: Palette.bg)
            }
          }
          .padding(16)
          .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
        }
        .padding(.top, 22)
      }
    }
    .task(id: day) {
      store.select(day ?? store.today)
      #if DEBUG
      if RecordDevShot.value("W2W_DEV_TODAYFOOD") == "day" { sheet = .day }
      #endif
      await store.refresh()
    }
    .onReceive(NotificationCenter.default.publisher(for: JarvisRouter.wroteNotification)) { _ in
      Task { await store.refresh() }
    }
    .sheet(item: $sheet) { s in sheetView(s) }
  }

  private func numbers(_ n: NutritionDay) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text(tr("热量", "Energy")).font(Typo.label).foregroundColor(Palette.text2)
        if n.notLogged { Badge(label: tr("未记录", "Not logged")) }
        Spacer(minLength: 4)
        Text(RecordFormat.num(n.intake.kcal)).font(.system(size: 22, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
        Text("/ \(RecordFormat.num(n.goal.kcal)) kcal").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
        Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
      }
      ProgressBar(fraction: n.kcalFraction, tone: n.kcalOver ? .bad : .good)
      HStack(spacing: 12) {
        macro(tr("蛋白", "Protein"), n.intake.protein, n.goal.protein)
        macro(tr("碳水", "Carbs"), n.intake.carbs, n.goal.carbs)
        macro(tr("脂肪", "Fat"), n.intake.fat, n.goal.fat)
      }
    }
    .contentShape(Rectangle())
    .accessibilityElement(children: .combine)
  }

  private func macro(_ label: String, _ value: Double?, _ goal: Double?) -> some View {
    VStack(alignment: .leading, spacing: 4) {
      HStack(spacing: 4) {
        Text(label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
        Spacer(minLength: 0)
        Text("\(RecordFormat.num(value))/\(RecordFormat.num(goal))").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1)
      }
      ProgressBar(fraction: (goal ?? 0) > 0 ? (value ?? 0) / goal! : 0, tone: .text)
    }
    .frame(maxWidth: .infinity)
  }

  @ViewBuilder private func sheetView(_ s: Sheet) -> some View {
    switch s {
    case .day:
      if let n = store.nutrition {
        SheetContainer(title: "\(tr("饮食", "Food")) · \(RecordDay.short(n.date))", close: { sheet = nil }) {
          dayList(n)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
      }
    case .entry(let e):
      SheetContainer(title: e.food, close: { sheet = nil }) {
        FoodEntrySheet(entry: e, store: store) { msg in
          sheet = nil
          if let msg { toast = Toast(msg) }
        }
      }
      .presentationDetents([.medium, .large])
      .presentationDragIndicator(.visible)
    }
  }

  private func dayList(_ n: NutritionDay) -> some View {
    VStack(alignment: .leading, spacing: 0) {
      KeyValueRow(label: tr("热量", "Energy"), value: "\(RecordFormat.num(n.intake.kcal)) / \(RecordFormat.num(n.goal.kcal)) kcal")
      KeyValueRow(label: tr("蛋白质", "Protein"), value: "\(RecordFormat.num(n.intake.protein)) / \(RecordFormat.num(n.goal.protein)) g")
      KeyValueRow(label: tr("碳水", "Carbs"), value: "\(RecordFormat.num(n.intake.carbs)) / \(RecordFormat.num(n.goal.carbs)) g")
      KeyValueRow(label: tr("脂肪", "Fat"), value: "\(RecordFormat.num(n.intake.fat)) / \(RecordFormat.num(n.goal.fat)) g")
      if !n.entries.isEmpty {
        SectionHeader(title: tr("\(n.entries.count) 项", "\(n.entries.count) items")).padding(.top, 16).padding(.bottom, 6)
        ForEach(n.entries) { e in
          Button { if !e.isLocal { sheet = .entry(e) } } label: {
            HStack(spacing: 8) {
              Text([e.time, e.food].compactMap { $0 }.joined(separator: " ")).font(Typo.body).foregroundColor(Palette.text)
                .lineLimit(2).multilineTextAlignment(.leading)
              if let p = e.pending { PendingMark(needsAttention: p.needsAttention) }
              Spacer(minLength: 8)
              Text("\(RecordFormat.num(e.macros.kcal)) kcal").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
              if !e.isLocal { Icon(.chevronRight, size: 14).foregroundColor(Palette.text3) }
            }
            .padding(.vertical, 10)
            .contentShape(Rectangle())
            .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
          }
          .buttonStyle(PressScale())
        }
      }
    }
  }
}
