import SwiftUI

/// The exercise library (docs/design/2.0.1 `15-exercises`), pushed from the 动作库 face: search, muscle-group chips (and the
/// equipment chips of 2.0.0), one row per exercise with its muscles on the small body, its group and equipment. A row opens
/// the exercise (how-to, restriction, alternatives, add to the plan) as a sheet; 「+」 opens 我的动作 (custom exercises).
struct LibraryPage: View {
  @ObservedObject var store: TrainingStore
  @Binding var toast: Toast?
  @State private var query = ""
  @State private var group = ""
  @State private var equipment = ""
  @State private var list: [LibraryExercise]?
  @State private var failed: String?
  @State private var attempt = 0
  @State private var sheet: TrainingSheet?

  private var filters: String { "\(query)|\(group)|\(equipment)|\(attempt)" }

  var body: some View {
    RecordPage(title: tr("动作库", "Exercise library"), trailing: { addButton }) {
      VStack(alignment: .leading, spacing: 12) {
        searchField.padding(.top, 16)
        chips(ExerciseVocab.groupFilters, selection: $group, big: true)
        chips(ExerciseVocab.equipmentFilters, selection: $equipment, big: false)
        results
      }
    }
    .task(id: filters) { await load(typed: !query.isEmpty) }
    .trainingSheets(store: store, sheet: $sheet, toast: $toast)
  }

  @ViewBuilder private var addButton: some View {
    if store.muscleOn {
      Button { sheet = .custom } label: {
        Icon(.plus, size: 20).foregroundColor(Palette.text2)
          .frame(width: 44, height: 44)
          .background(Circle().fill(Palette.surface2))
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("我的动作", "My exercises"))
    }
  }

  private var searchField: some View {
    HStack(spacing: 10) {
      Icon(.search, size: 20).foregroundColor(Palette.text2)
      TextField(tr("搜索", "Search"), text: $query)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .font(.system(size: 17)).foregroundColor(Palette.text)
      if !query.isEmpty {
        Button { query = "" } label: { Icon(.x, size: 16).foregroundColor(Palette.text2).frame(width: 32, height: 44) }
          .accessibilityLabel(tr("清除", "Clear"))
      }
    }
    .padding(.horizontal, 16).frame(minHeight: 56)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }

  private func chips(_ items: [(key: String, title: String)], selection: Binding<String>, big: Bool) -> some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        ForEach(items, id: \.key) { item in
          let on = item.key == selection.wrappedValue
          Button { selection.wrappedValue = item.key } label: {
            Text(item.title).font(.system(size: big ? 16 : 14, weight: .medium))
              .foregroundColor(on ? Palette.onAccent : (big ? Palette.text : Palette.text2))
              .padding(.horizontal, big ? 16 : 12).frame(height: big ? 44 : 34)
              .background(Capsule().fill(on ? Palette.accent : Palette.surface))
          }
          .buttonStyle(PressScale())
          .accessibilityAddTraits(on ? .isSelected : [])
        }
      }
    }
  }

  @ViewBuilder private var results: some View {
    if let list {
      if list.isEmpty {
        EmptyNote(text: tr("没有匹配的动作", "No matching exercises"))
      } else {
        LazyVStack(spacing: 0) {
          ForEach(list) { x in
            Rectangle().fill(Palette.hair).frame(height: 1)
            row(x)
          }
          Rectangle().fill(Palette.hair).frame(height: 1)
        }
      }
    } else if let failed {
      RetryNote(text: failed) { attempt += 1 }
    } else {
      EmptyNote(text: "…")
    }
  }

  private func row(_ x: LibraryExercise) -> some View {
    Button { sheet = .exercise(x.slug) } label: {
      HStack(spacing: 14) {
        PlanExerciseThumb(thumb: nil, primary: x.primary, secondary: x.secondary, base: store.api.baseURL,
                          sex: store.muscle?.sex ?? "male", side: 52)
        VStack(alignment: .leading, spacing: 4) {
          HStack(spacing: 6) {
            Text(x.name).font(.system(size: 18, weight: .medium)).foregroundColor(Palette.text).lineLimit(1)
            if x.custom { Badge(label: tr("我的", "Mine")) }
            ExerciseStatusBadge(status: x.status)
          }
          if !x.subtitle.isEmpty { Text(x.subtitle).font(.system(size: 15)).foregroundColor(Palette.text2).lineLimit(1) }
        }
        Spacer(minLength: 4)
        Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
      }
      .padding(.vertical, 12)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
  }

  private func load(typed: Bool) async {
    if typed {   // while typing, one request a moment after the last key
      try? await Task.sleep(nanoseconds: 250_000_000)
      guard !Task.isCancelled else { return }
    }
    do {
      let found = try await store.searchLibrary(query: query, group: group, equipment: equipment)
      guard !Task.isCancelled else { return }
      list = found
      failed = nil
    } catch {
      guard !Task.isCancelled else { return }
      list = nil
      failed = TrainingStore.message(error)
    }
  }
}
