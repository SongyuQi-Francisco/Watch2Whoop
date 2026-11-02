import SwiftUI

/// 快捷记录: the most used quick meals in a sideways strip, then 「全部」. One component everywhere (饮食 and the Today card, as
/// on the page): a tap logs it (with 撤销 in the toast); a logged one offers 再记一份 / 撤销; one whose grams are a guess opens
/// its foods first.
struct QuickMeals: View {
  @ObservedObject var store: FoodStore
  @Binding var toast: Toast?
  /// What a card stands on: it needs to differ from what is behind the strip.
  var cardBackground = Palette.surface
  @State private var logged: MealTemplate?
  @State private var busy = false
  @State private var sheet: Sheet?

  enum Sheet: Identifiable {
    case template(MealTemplate)
    case all

    var id: String {
      switch self {
      case .template(let t): return "template.\(t.id)"
      case .all: return "all"
      }
    }
  }

  var body: some View {
    let sorted = MealTemplate.byUse(store.templates)
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 10) {
        ForEach(sorted.prefix(MealTemplate.stripCount)) { t in
          Button { tap(t) } label: { TemplateCard(t: t, background: cardBackground) }
            .buttonStyle(PressScale())
            .disabled(busy)
        }
        if sorted.count > MealTemplate.stripCount {
          Button { sheet = .all } label: {
            VStack(alignment: .leading, spacing: 6) {
              Text(tr("全部", "All")).font(Typo.bodyStrong).foregroundColor(Palette.text)
              Text("\(sorted.count)").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
            }
            .frame(width: 72, alignment: .topLeading)
            .frame(minHeight: 72, alignment: .topLeading)
            .padding(14)
            .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(cardBackground))
          }
          .buttonStyle(PressScale())
        }
      }
    }
    .sheet(item: $sheet) { s in sheetView(s) }
    .confirmationDialog(logged.map { tr("今天已记过「\($0.name)」", "\($0.name) is logged today") } ?? "",
                        isPresented: Binding(get: { logged != nil }, set: { if !$0 { logged = nil } }), titleVisibility: .visible) {
      if let t = logged {
        Button(tr("再记一份", "Log another")) { log(t, again: true) }
        Button(tr("撤销今天这次", "Undo today's"), role: .destructive) { undo(t) }
      }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    }
  }

  @ViewBuilder private func sheetView(_ s: Sheet) -> some View {
    switch s {
    case .template(let t):
      SheetContainer(title: t.name, close: { sheet = nil }) {
        MealTemplateSheet(template: t) { grams in
          sheet = nil
          log(t, again: false, grams: grams)
        }
      }
      .presentationDetents([.medium, .large])
      .presentationDragIndicator(.visible)
    case .all:
      SheetContainer(title: tr("快捷记录", "Quick log"), close: { sheet = nil }) {
        AllTemplatesSheet(templates: MealTemplate.byUse(store.templates), tap: { t in
          sheet = nil
          tap(t)
        }, edit: { t in
          sheet = .template(t)
        })
      }
      .presentationDetents([.medium, .large])
      .presentationDragIndicator(.visible)
    }
  }

  private func tap(_ t: MealTemplate) {
    if t.isLogged {
      logged = t
    } else if t.needsConfirmation {
      sheet = .template(t)
    } else {
      log(t, again: false)
    }
  }

  private func log(_ t: MealTemplate, again: Bool, grams: [Double?] = []) {
    busy = true
    Task {
      defer { busy = false }
      let out = await store.log(t, again: again, grams: grams)
      if out.conflict {
        store.drop(out)
        toast = Toast(tr("今天已记过「\(t.name)」", "\(t.name) is already logged today"), actionTitle: tr("再记一份", "Log another")) {
          log(t, again: true, grams: grams)
        }
      } else if out.sent || out.queued {
        toast = Toast(out.sent ? tr("已记 · \(t.name)", "Logged · \(t.name)") : out.message, actionTitle: tr("撤销", "Undo")) {
          undo(t)
        }
      } else {
        toast = Toast(out.message)
      }
    }
  }

  private func undo(_ t: MealTemplate) {
    Task {
      let cur = store.templates.first { $0.id == t.id } ?? t
      toast = Toast(await store.undoLog(cur) ? tr("已撤销", "Undone") : tr("撤销要联网，稍后再试", "Undo needs a connection, try again later"))
    }
  }
}

/// A quick-meal card in the strip: name, kcal · protein, 「已记」 when logged today, 「待确认」 when its grams are a guess.
private struct TemplateCard: View {
  let t: MealTemplate
  let background: Color

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(spacing: 6) {
        Text(t.name).font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(1)
        if let p = t.pending { PendingMark(needsAttention: p.needsAttention) }
      }
      Text("\(RecordFormat.num(t.totals.kcal)) kcal · \(RecordFormat.num(t.totals.protein)) g")
        .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      HStack(spacing: 6) {
        Text(t.meal.title).font(Typo.label).foregroundColor(Palette.text3)
        if t.isLogged {
          Badge(label: tr("已记", "Logged"), tone: .good)
        } else if t.needsConfirmation {
          Badge(label: tr("待确认", "To confirm"), tone: .mid)
        }
      }
    }
    .frame(width: 150, alignment: .topLeading)
    .frame(minHeight: 72, alignment: .topLeading)
    .padding(14)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(background))
  }
}
