import SwiftUI

/// Jarvis → 记一餐 → 更多 (docs/design/2.0.1/20-templates.html): every quick meal, most used first, filtered by meal or by
/// name; + logs it at once (FoodStore.log, local first), and the row turns into 「已记到…」 with 撤销 (FoodStore.undoLog).
struct CockpitTemplatesSheet: View {
  let close: () -> Void
  @StateObject private var store: FoodStore
  @State private var query = ""
  @State private var filter = Filter.common
  /// Rows being written (one write per row at a time; another row does not free this one).
  @State private var busy: Set<Int> = []
  /// What this sheet logged: template id → that logging (its server log id or queued copy). Only these offer 撤销.
  @State private var mine: [Int: (logId: Int?, clientId: String?)] = [:]
  @State private var toast: Toast?

  enum Filter: Hashable {
    case common
    case meal(MealSlot)
  }

  init(session: AccountSession, close: @escaping () -> Void) {
    self.close = close
    _store = StateObject(wrappedValue: FoodStore(session: session, cardOnly: true))
  }

  private var filters: [(Filter, String)] {
    [(.common, tr("常用", "Usual")), (.meal(.breakfast), tr("早", "Bkfst")), (.meal(.lunch), tr("午", "Lunch")),
     (.meal(.dinner), tr("晚", "Dinner")), (.meal(.snack), tr("加餐", "Snack"))]
  }

  /// The rows shown: matching the search, of the picked meal (常用 = all, most used first).
  static func rows(_ all: [MealTemplate], query: String, filter: Filter) -> [MealTemplate] {
    let q = query.trimmingCharacters(in: .whitespaces).lowercased()
    return MealTemplate.byUse(all).filter { t in
      (q.isEmpty || t.name.lowercased().contains(q) || t.items.lowercased().contains(q))
        && { if case .meal(let m) = filter { return t.meal == m } else { return true } }()
    }
  }

  var body: some View {
    SheetContainer(title: tr("快记", "Quick log"), close: close) {
      VStack(alignment: .leading, spacing: 14) {
        HStack(spacing: 8) {
          Icon(.search, size: 16).foregroundColor(Palette.text3)
          TextField(tr("搜一餐", "Search meals"), text: $query).font(Typo.body)
        }
        .padding(.vertical, 11).padding(.horizontal, 14)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.inSheet))
        SegmentedControl(items: filters, selection: $filter)
        let rows = Self.rows(store.templates, query: query, filter: filter)
        if rows.isEmpty {
          Text(store.templates.isEmpty ? tr("还没有快捷餐", "No quick meals yet") : tr("没有匹配的", "Nothing matches"))
            .font(Typo.caption).foregroundColor(Palette.text3).padding(.vertical, 24).frame(maxWidth: .infinity)
        } else {
          VStack(spacing: 0) {
            ForEach(rows) { t in
              row(t)
              if t.id != rows.last?.id { Divider().overlay(Palette.hair) }
            }
          }
        }
      }
    }
    .toast($toast)
    .task { await store.refresh() }
  }

  private func row(_ t: MealTemplate) -> some View {
    HStack(spacing: 12) {
      VStack(alignment: .leading, spacing: 6) {
        HStack(spacing: 8) {
          Text(t.name).font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(1)
          if t.isLogged {
            Text(tr("已记到\(t.meal.title)", "Logged to \(t.meal.title)")).font(Typo.label).foregroundColor(Palette.good)
          }
        }
        if t.isLogged && mine[t.id] != nil {   // 撤销 only for what this sheet just logged
          Button { undo(t) } label: {
            HStack(spacing: 4) { Icon(.undo2, size: 12); Text(tr("撤销", "Undo")).font(Typo.label) }
              .foregroundColor(Palette.text2)
          }
          .buttonStyle(PressScale())
          .disabled(busy.contains(t.id))
        } else {
          MacroBar(macros: t.totals).frame(width: 96, height: 6)
        }
      }
      Spacer(minLength: 8)
      HStack(alignment: .firstTextBaseline, spacing: 3) {
        Text(t.totals.kcal.map { RecordFormat.num($0) } ?? "—").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
        Text("kcal").font(Typo.unit).foregroundColor(Palette.text2)
      }
      Button { log(t) } label: {
        Icon(t.isLogged ? .check : .plus, size: 18)
          .foregroundColor(t.isLogged ? Palette.onAccent : Palette.text)
          .frame(width: 36, height: 36)
          .background(Circle().fill(t.isLogged ? Palette.good : Palette.inSheet))
      }
      .buttonStyle(PressScale())
      .disabled(t.isLogged || busy.contains(t.id))
      .accessibilityLabel(t.isLogged ? tr("已记 \(t.name)", "\(t.name) logged") : tr("记一份 \(t.name)", "Log \(t.name)"))
    }
    .padding(.vertical, 12)
    .opacity(busy.contains(t.id) ? 0.5 : 1)
  }

  private func log(_ t: MealTemplate) {
    guard !busy.contains(t.id), !t.isLogged else { return }   // a double tap writes once
    busy.insert(t.id)
    Task {
      defer { busy.remove(t.id) }
      let before = t.todayLog
      let out = await store.log(t)
      if out.conflict {
        store.drop(out)
        toast = Toast(tr("今天已记过「\(t.name)」", "\(t.name) is already logged today"))
      } else if out.sent || out.queued {
        JarvisHaptics.notice.notificationOccurred(.success)
        let logId = CockpitController.thisWrite(before: before, after: store.templates.first { $0.id == t.id }?.todayLog)
        let clientId = out.queued ? out.clientId : nil
        if logId != nil || clientId != nil { mine[t.id] = (logId, clientId) }
        if out.queued { toast = Toast(out.message) }
      } else {
        toast = Toast(out.message)
      }
    }
  }

  private func undo(_ t: MealTemplate) {
    guard let w = mine[t.id], !busy.contains(t.id) else { return }
    busy.insert(t.id)
    Task {
      defer { busy.remove(t.id) }
      let ok = await store.undoMealLog(id: w.logId, clientId: w.clientId)   // this write only
      if ok { mine[t.id] = nil }
      toast = Toast(ok ? tr("已撤销", "Undone") : tr("撤销要联网，稍后再试", "Undo needs a connection, try again later"))
    }
  }
}

/// Protein, carbs and fat as shares of their energy (4 / 4 / 9 kcal per gram), one ink at three strengths.
struct MacroBar: View {
  let macros: Macros

  static func shares(_ m: Macros) -> [Double] {
    let e = [(m.protein ?? 0) * 4, (m.carbs ?? 0) * 4, (m.fat ?? 0) * 9]
    let sum = e.reduce(0, +)
    return sum > 0 ? e.map { $0 / sum } : []
  }

  var body: some View {
    GeometryReader { g in
      let s = Self.shares(macros), gap: CGFloat = 2
      let w = g.size.width - gap * CGFloat(max(0, s.count - 1))
      HStack(spacing: gap) {
        ForEach(Array(s.enumerated()), id: \.offset) { i, x in
          Capsule().fill(Palette.text.opacity([1, 0.55, 0.28][i])).frame(width: max(0, w * CGFloat(x)))
        }
      }
    }
    .accessibilityHidden(true)
  }
}
