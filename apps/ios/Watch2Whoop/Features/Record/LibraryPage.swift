import SwiftUI

/// The exercise library (docs/design/2.0.1 `15-exercises`), pushed from the 动作库 face: search, muscle-group chips (and the
/// equipment chips of 2.0.0), one row per exercise with its muscles on the small body, its group and equipment. A row opens
/// the exercise (how-to, restriction, alternatives, add to the plan) as a sheet; 「+」 opens 我的动作 (custom exercises).
/// 2.1.0: each row has a star (local first, put back when the write is refused), the star at the top shows only the
/// starred ones (`favorites=true`); both only on a server that sends `favorite`.
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
  @State private var favoritesOnly = false
  /// Stars tapped on this page (they win over the list until it is loaded again).
  @State private var tapped: [String: Bool] = [:]
  /// The server sends `favorite` (seen once on this page).
  @State private var starsOn = false

  private var filters: String { "\(query)|\(group)|\(equipment)|\(favoritesOnly)|\(attempt)" }

  var body: some View {
    RecordPage(title: tr("动作库", "Exercise library"), trailing: { HStack(spacing: 8) { filterButton; addButton } }) {
      VStack(alignment: .leading, spacing: 12) {
        searchField.padding(.top, 16)
        chips(ExerciseVocab.groupFilters, selection: $group, big: true)
        chips(ExerciseVocab.equipmentFilters, selection: $equipment, big: false)
        results
      }
    }
    .task(id: filters) { await load(typed: !query.isEmpty) }
    .onAppear { if RecordDevShot.value("W2W_DEV_LIBRARY_STARS") != nil { favoritesOnly = true } }   // simulator screenshots
    .trainingSheets(store: store, sheet: $sheet, toast: $toast)
  }

  @ViewBuilder private var filterButton: some View {
    if starsOn {
      Button { favoritesOnly.toggle() } label: {
        Icon(.star, size: 20).foregroundColor(favoritesOnly ? Palette.onAccent : Palette.text2)
          .frame(width: 44, height: 44)
          .background(Circle().fill(favoritesOnly ? Palette.accent : Palette.surface2))
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("只看收藏", "Starred only"))
      .accessibilityAddTraits(favoritesOnly ? .isSelected : [])
    }
  }

  private func star(_ x: LibraryExercise) -> Bool? {
    ExerciseFavorites.shown(server: x.favorite, pending: store.pendingStars[x.slug], tapped: tapped[x.slug])
  }

  private func toggleStar(_ x: LibraryExercise) {
    let on = !(star(x) ?? false)
    tapped[x.slug] = on
    Task {
      if await !store.setStar(x.slug, on) {
        tapped[x.slug] = !on
        toast = Toast(tr("没收藏上，再试一次", "Not saved, try again"))
      }
    }
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
    if let all = list {
      let list = favoritesOnly ? ExerciseFavorites.filtered(all, stars: star) : all
      if list.isEmpty {
        EmptyNote(text: favoritesOnly ? tr("还没有收藏", "Nothing starred yet") : tr("没有匹配的动作", "No matching exercises"))
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
    // the star is a button of its own beside the row's (a button inside a button's label does not get its taps)
    HStack(spacing: 0) {
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
        }
        .padding(.vertical, 12)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      if let on = star(x) {
        Button { toggleStar(x) } label: {
          Icon(.star, size: 18).foregroundColor(on ? Palette.mid : Palette.text3).frame(width: 40, height: 52)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressScale())
        .accessibilityLabel(on ? tr("取消收藏 \(x.name)", "Unstar \(x.name)") : tr("收藏 \(x.name)", "Star \(x.name)"))
        .accessibilityAddTraits(on ? .isSelected : [])
      }
      Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
        .frame(height: 52).contentShape(Rectangle())
        .onTapGesture { sheet = .exercise(x.slug) }
        .accessibilityHidden(true)
    }
  }

  private func load(typed: Bool) async {
    if typed {   // while typing, one request a moment after the last key
      try? await Task.sleep(nanoseconds: 250_000_000)
      guard !Task.isCancelled else { return }
    }
    do {
      let found = try await store.searchLibrary(query: query, group: group, equipment: equipment, favorites: favoritesOnly)
      guard !Task.isCancelled else { return }
      list = found
      tapped = [:]
      if found.contains(where: { $0.favorite != nil }) { starsOn = true }
      failed = nil
    } catch {
      guard !Task.isCancelled else { return }
      list = nil
      failed = TrainingStore.message(error)
    }
  }
}
