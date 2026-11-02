import SwiftUI

/// 「今天吃了」 → 选择 (issue #93, flag `meal_merge`): pick entries of the day, then 「存成模板」 / 「合并成一条」
/// (`POST /nutrition/combine`, online only, docs/ios-native-app.md 5.5). Undo of a merge: `DELETE /nutrition/merges/{id}`.
enum FoodCombine {
  enum Action: String, Identifiable {
    case template, merge
    var id: String { rawValue }
  }

  /// The longest name the server takes for each (app.js combineSheet).
  static func nameLimit(_ a: Action) -> Int { a == .merge ? 50 : 30 }

  /// The entries' names joined, with a trailing portion such as 「（170 g）」 taken off each.
  static func defaultName(_ entries: [FoodEntry], action: Action) -> String {
    // i18n-ok: a pattern over the server's data, not text the user reads
    let portion = #"\s*[\x{FF08}(][^\x{FF08}\x{FF09}()]*\d\s*(?:g|\x{514B}|ml|\x{4EFD}|\x{4E2A})[^\x{FF08}\x{FF09}()]*[\x{FF09})]\s*$"#
    let names = entries.map { $0.food.replacingOccurrences(of: portion, with: "", options: .regularExpression) }
    return String(names.joined(separator: " + ").prefix(nameLimit(action)))
  }
}

/// The name of the new template / merged entry; what it adds up to above it.
struct FoodCombineSheet: View {
  let action: FoodCombine.Action
  let picked: [FoodEntry]
  @ObservedObject var store: FoodStore
  let done: (Toast?) -> Void
  @State private var name: String
  @State private var busy = false
  @State private var message: String?

  init(action: FoodCombine.Action, picked: [FoodEntry], store: FoodStore, done: @escaping (Toast?) -> Void) {
    self.action = action
    self.picked = picked
    self.store = store
    self.done = done
    _name = State(initialValue: FoodCombine.defaultName(picked, action: action))
  }

  private var summary: String {
    let total = picked.reduce(Macros()) { $0 + $1.macros }
    return [tr("\(picked.count) 条", "\(picked.count) entries"), total.kcal.map { "\(RecordFormat.num($0)) kcal" },
            total.protein.map { tr("蛋白 \(RecordFormat.num($0, 1)) g", "protein \(RecordFormat.num($0, 1)) g") }]
      .compactMap { $0 }.joined(separator: " · ")
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text(summary).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      FormField(label: tr("名字", "Name"), text: $name, background: Palette.inSheet)
      PrimaryButton(title: action == .merge ? tr("合并", "Merge") : tr("存成模板", "Save as template"), busy: busy) { submit() }
      if let message { Text(message).font(Typo.caption).foregroundColor(Palette.bad) }
    }
  }

  private func submit() {
    busy = true
    message = nil
    let limit = FoodCombine.nameLimit(action)
    let clean = String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(limit))
    Task {
      let out = await store.combine(picked, action: action, name: clean.isEmpty ? nil : clean)
      busy = false
      if let err = out.error {
        message = err
        return
      }
      if let id = out.mergeId {
        done(Toast(tr("已合并 · \(out.name)", "Merged · \(out.name)"), actionTitle: tr("撤销", "Undo")) {
          Task { _ = await store.split(mergeId: id) }
        })
      } else {
        done(Toast(tr("已存成模板 · \(out.name)", "Saved as template · \(out.name)")))
      }
    }
  }
}

/// A quick meal's foods with their grams, then 「记录」 (its grams can be changed for this logging). Opens first when the
/// grams are still a guess (「克数待确认」).
struct MealTemplateSheet: View {
  /// The server's limit for one food (memory_api.py MealLogIn items): above it the log comes back rejected.
  static let maxGrams = 2000.0

  let template: MealTemplate
  let log: ([Double?]) -> Void
  @State private var grams: [String]
  @State private var message: String?

  init(template: MealTemplate, log: @escaping ([Double?]) -> Void) {
    self.template = template
    self.log = log
    _grams = State(initialValue: template.foods.map { $0.grams.map { RecordFormat.num($0, 1) } ?? "" })
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      if template.needsConfirmation { Badge(label: tr("克数待确认", "Grams to confirm"), tone: .mid) }
      ForEach(Array(template.foods.enumerated()), id: \.offset) { i, f in
        HStack(spacing: 10) {
          VStack(alignment: .leading, spacing: 2) {
            Text(f.name).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
            Text([f.macros.kcal.map { "\(RecordFormat.num($0)) kcal" },
                  f.macros.protein.map { tr("蛋白 \(RecordFormat.num($0, 1)) g", "protein \(RecordFormat.num($0, 1)) g") }]
              .compactMap { $0 }.joined(separator: " · "))
              .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
          }
          Spacer(minLength: 8)
          TextField("0", text: binding(i))
            .keyboardType(.decimalPad).multilineTextAlignment(.trailing).font(Typo.body.monospacedDigit())
            .padding(.horizontal, 10).frame(width: 76, height: 40)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Palette.inSheet))
            .accessibilityLabel(tr("\(f.name) 克数", "\(f.name) grams"))
          Text("g").font(Typo.caption).foregroundColor(Palette.text2)
        }
        .padding(.vertical, 4)
      }
      if let message { Text(message).font(Typo.caption).foregroundColor(Palette.bad) }
      PrimaryButton(title: tr("记录", "Log")) { submit() }.padding(.top, 6)
    }
  }

  private func binding(_ i: Int) -> Binding<String> {
    Binding(get: { grams[i] }, set: { grams[i] = $0 })
  }

  private func submit() {
    var out: [Double?] = []
    for (i, s) in grams.enumerated() {
      let t = s.replacingOccurrences(of: ",", with: ".").trimmingCharacters(in: .whitespaces)
      if t.isEmpty {
        out.append(template.foods[i].grams)
      } else if let v = Double(t), v >= 0, v <= Self.maxGrams {
        // An untouched field keeps the saved value (its text is rounded for display).
        out.append(s == template.foods[i].grams.map { RecordFormat.num($0, 1) } ? template.foods[i].grams : v)
      } else {
        message = tr("克数要在 0–\(Int(Self.maxGrams)) 之间", "Grams must be 0–\(Int(Self.maxGrams))")
        return
      }
    }
    log(out)
  }
}

/// 「全部」: every quick meal, most used first: tap to log, 「克数」 for its foods.
struct AllTemplatesSheet: View {
  let templates: [MealTemplate]
  let tap: (MealTemplate) -> Void
  let edit: (MealTemplate) -> Void

  var body: some View {
    VStack(spacing: 0) {
      ForEach(templates) { t in
        HStack(spacing: 10) {
          Button { tap(t) } label: {
            VStack(alignment: .leading, spacing: 2) {
              HStack(spacing: 6) {
                Text(t.name).font(Typo.body).foregroundColor(Palette.text).multilineTextAlignment(.leading)
                if let p = t.pending { PendingMark(needsAttention: p.needsAttention) }
              }
              Text(MealTemplateStyle.subtitle(t)).font(Typo.caption.monospacedDigit())
                .foregroundColor(MealTemplateStyle.needsAttention(t) ? Palette.mid : Palette.text2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
          }
          .buttonStyle(PressScale())
          Button { edit(t) } label: {
            Text(tr("克数", "Grams")).font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text)
              .padding(.vertical, 7).padding(.horizontal, 12)
              .background(Capsule().fill(Palette.inSheet))
          }
          .buttonStyle(PressScale())
        }
        .padding(.vertical, 10)
        .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
      }
    }
  }
}

/// What a quick meal's chip says (app.js mealSub): 已记 ✓ / 待确认 / kcal.
enum MealTemplateStyle {
  static func subtitle(_ t: MealTemplate) -> String {
    if t.isLogged { return tr("已记 ✓", "Logged ✓") }
    if t.needsConfirmation { return tr("待确认", "To confirm") }
    return "\(RecordFormat.num(t.totals.kcal)) kcal"
  }

  static func needsAttention(_ t: MealTemplate) -> Bool { t.needsConfirmation && !t.isLogged }
}
