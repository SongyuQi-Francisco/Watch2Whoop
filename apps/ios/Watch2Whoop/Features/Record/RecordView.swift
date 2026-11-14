import SwiftUI

/// 记录, 2.0.1 (docs/design/2.0.1 `10-food`, `11`–`15`): 训练 | 饮食, switched by the header's two-way control (it replaces
/// the date; the sync capsule stays on the right). Each view keeps the tab-root rhythm: one hero, readings, three faces,
/// the rest below the fold. 训练's faces push the weekly muscle map, the history and the library; the ring pushes today's
/// session. 日记 has no segment any more: it opens as a sheet from 饮食 (below the fold), and an old link to it lands there.
struct RecordTab: View {
  @SceneStorage("w2w.record.segment") private var segment: Segment = .training
  @StateObject private var training: TrainingStore
  @StateObject private var journal: JournalStore
  @StateObject private var food: FoodStore
  @EnvironmentObject private var app: AppModel
  @State private var toast: Toast?
  @State private var devHealth: MeSheet.RecordKind?
  @State private var path: [RecordRoute] = []
  @State private var journalOpen = false
  private let session: AccountSession

  /// `journal` stays for saved state and links of 2.0.0: it shows 饮食 with 日记 open.
  enum Segment: String, Hashable { case training, journal, food }

  init(session: AccountSession) {
    self.session = session
    _training = StateObject(wrappedValue: TrainingStore(session: session))
    _journal = StateObject(wrappedValue: JournalStore(session: session))
    _food = StateObject(wrappedValue: FoodStore(session: session))
  }

  var body: some View {
    NavigationStack(path: $path) {
      root
        .toolbar(.hidden, for: .navigationBar)
        .navigationDestination(for: RecordRoute.self) { r in page(r) }
    }
    .toast($toast)
    .onReceive(JarvisPageLink.shared.$segment) { s in   // a Jarvis card's button (also when this tab is created by that tap)
      guard let s else { return }
      JarvisPageLink.shared.segment = nil
      path.removeAll()
      segment = s
    }
    .onChange(of: segment) { s in if s == .journal { openJournal() } }
    .onAppear { if segment == .journal { openJournal() } }
    .sheet(isPresented: $journalOpen) {
      SheetContainer(title: tr("日记", "Journal"), close: { journalOpen = false }) {
        JournalSection(store: journal, toast: $toast)
      }
      .presentationDetents([.large])
    }
    .sheet(item: $devHealth) { k in
      SheetContainer(title: k.title, close: { devHealth = nil }) { HealthRecordView(kind: k, openLab: RecordDevShot.value("W2W_DEV_HEALTH")?.split(separator: ":").dropFirst().first.map(String.init)) }
        .presentationDetents([.large])
    }
  }

  private func openJournal() {
    segment = .food
    journalOpen = true
  }

  private var shown: Segment { segment == .training ? .training : .food }

  private var root: some View {
    ScrollViewReader { proxy in
      ScrollView(.vertical, showsIndicators: false) {
        VStack(alignment: .leading, spacing: 0) {
          header
          switch shown {
          case .training: TrainingRoot(store: training, toast: $toast) { path.append($0) }
          default: FoodContent(store: food, toast: $toast, glance: true, openJournal: { journalOpen = true })
          }
          // scrolled to the end, nothing sits under the Jarvis key (direction.md 2.4)
          Color.clear.frame(height: 140)
        }
        .padding(.horizontal, Metrics.gutter)
        .frame(maxWidth: Metrics.maxContentWidth)
        .frame(maxWidth: .infinity)
        .task { await devShot(proxy) }
      }
      .background(Palette.bg.ignoresSafeArea())
      .statusBarCover()
      .refreshable {
        switch shown {
        case .training: await training.refresh()
        default: await food.refresh()
        }
      }
    }
  }

  /// The two-way switch + the sync capsule (tap = sync now or retry, hold = sync center).
  private var header: some View {
    HStack(spacing: 8) {
      RecordSegmentSwitch(selection: $segment)
      Spacer(minLength: 0)
      TodaySyncCapsule(card: nil, page: session.sync, tap: syncNow, center: { app.route = .syncCenter })
    }
    .frame(height: 44)
  }

  private func syncNow() {
    Task {
      _ = await HealthSyncService.shared.syncNow()
      await training.refresh()
      await food.refresh()
    }
  }

  @ViewBuilder private func page(_ r: RecordRoute) -> some View {
    switch r {
    case .session: TrainingSessionPage(store: training, toast: $toast)
    case .muscles: MuscleWeekPage(store: training, toast: $toast) { path.append($0) }
    case .allMuscles: AllMusclesPage(store: training, toast: $toast)
    case .history: TrainingHistoryPage(store: training, toast: $toast)
    case .library: LibraryPage(store: training, toast: $toast)
    case .week: TrainingWeekPage(store: training, toast: $toast)
    }
  }

  /// Debug screenshots (RecordDevShot): segment, a page of 训练 (`W2W_DEV_RECORD_PAGE=session|muscles|history|library|week`),
  /// a health record sheet, a scroll position.
  private func devShot(_ proxy: ScrollViewProxy) async {
    if let s = RecordDevShot.value("W2W_DEV_RECORD").flatMap(Segment.init(rawValue:)) { segment = s }
    if let p = RecordDevShot.value("W2W_DEV_RECORD_PAGE") {
      let routes: [String: RecordRoute] = ["session": .session, "muscles": .muscles, "all": .allMuscles, "history": .history,
                                           "library": .library, "week": .week]
      if let r = routes[p], path.isEmpty { path = [r] }
    }
    if let h = RecordDevShot.value("W2W_DEV_HEALTH")?.split(separator: ":").first.flatMap({ MeSheet.RecordKind(rawValue: String($0)) }) {
      devHealth = h
    }
    if let anchor = RecordDevShot.value("W2W_DEV_SCROLL") {
      try? await Task.sleep(nanoseconds: 2_500_000_000)
      proxy.scrollTo(anchor, anchor: .top)
    }
  }
}

// MARK: - 日记

struct JournalSection: View {
  @ObservedObject var store: JournalStore
  @Binding var toast: Toast?
  @State private var showAll = false
  @State private var numberItem: JournalItem?
  @State private var event = false

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      HStack {
        DayStepper(day: store.day, today: store.today) { store.select($0) }
        Spacer()
      }
      RecordStatusLine(offline: store.offline, refreshing: store.refreshing && store.journal == nil)
      if let j = store.journal {
        VStack(alignment: .leading, spacing: 10) {
          HStack {
            SectionHeader(title: tr("常用", "Frequent"))
            Button(showAll ? tr("收起", "Less") : tr("全部", "All")) { withAnimation { showAll.toggle() } }
              .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
          }
          TagCloud(items: j.frequent, journal: j, tap: tap)
        }
        if showAll {
          ForEach(j.grouped(), id: \.0) { cat, items in
            VStack(alignment: .leading, spacing: 10) {
              SectionHeader(title: JournalItem.categoryTitle(cat))
              TagCloud(items: items, journal: j, tap: tap)
            }
          }
        }
        RowGroup {
          ListRow(icon: .heartPulse, title: tr("记身体事件", "Log a body event")) { event = true }
        }
      } else {
        Card { EmptyNote(text: store.refreshing ? "…" : tr("暂无", "None")) }
      }
    }
    .task { await store.refresh() }
    .sheet(item: $numberItem) { it in
      SheetContainer(title: it.short, close: { numberItem = nil }) {
        JournalNumberSheet(item: it, day: store.day, value: store.journal?.values[it.key]) { v in
          numberItem = nil
          Task {
            let out = await store.set(it.key, v)
            let shown = "\(RecordFormat.num(v, 1))\(it.unit ?? "")"
            toast = Toast(out.sent ? (v == nil ? tr("已清除", "Cleared") : tr("已记录 \(shown)", "Logged \(shown)")) : out.message)
          }
        }
      }
      .presentationDetents([.medium])
    }
    .sheet(isPresented: $event) {
      SheetContainer(title: tr("身体事件", "Body event"), close: { event = false }) {
        EventForm { msg in
          event = false
          if let msg { toast = Toast(msg) }
        }
      }
      .presentationDetents([.large])
    }
  }

  private func tap(_ it: JournalItem) {
    guard it.isBool else { numberItem = it; return }
    let prev = store.journal?.values[it.key]
    let on = prev == 1
    Task {
      let out = await store.set(it.key, on ? nil : 1)
      let name = it.short
      toast = Toast(out.sent || out.queued ? (on ? tr("已撤销 · \(name)", "Undone · \(name)") : tr("已记录 · \(name)", "Logged · \(name)")) : out.message,
                    actionTitle: tr("撤销", "Undo")) {
        Task { await store.set(it.key, prev) }
      }
    }
  }
}

/// Wrapping tags: a behaviour is ticked (filled) or not; a number shows its value.
struct TagCloud: View {
  let items: [JournalItem]
  let journal: JournalDay
  let tap: (JournalItem) -> Void

  var body: some View {
    WrapLayout(spacing: 8) {
      ForEach(items) { it in
        let v = journal.values[it.key]
        let on = it.isBool ? v == 1 : v != nil
        Button { tap(it) } label: {
          HStack(spacing: 5) {
            Text(it.short).font(.system(size: 14, weight: .medium))
            if !it.isBool, let v { Text("\(RecordFormat.num(v, 1))\(it.unit ?? "")").font(.system(size: 13).monospacedDigit()).opacity(0.8) }
            if let p = journal.pending[it.key] { PendingMark(needsAttention: p.needsAttention) }
          }
          .foregroundColor(on ? Palette.onAccent : Palette.text)
          .padding(.vertical, 8).padding(.horizontal, 12)
          .background(Capsule().fill(on ? Palette.accent : Palette.surface))
        }
        .buttonStyle(PressScale())
        .accessibilityAddTraits(on ? .isSelected : [])
      }
    }
  }
}

/// A number item (water litres, red meat grams …) of one day.
private struct JournalNumberSheet: View {
  let item: JournalItem
  let day: String
  let value: Double?
  let save: (Double?) -> Void
  @State private var text: String
  @State private var error: String?
  @FocusState private var focused: Bool

  init(item: JournalItem, day: String, value: Double?, save: @escaping (Double?) -> Void) {
    self.item = item
    self.day = day
    self.value = value
    self.save = save
    _text = State(initialValue: value.map { RecordFormat.num($0, 2) } ?? "")
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text("\(item.label)\(item.unit.map { " (\($0))" } ?? "") · \(RecordDay.short(day))").font(Typo.label).foregroundColor(Palette.text2)
      TextField(tr("没记录", "Not logged"), text: $text)
        .keyboardType(.decimalPad).focused($focused)
        .font(.system(size: 28, weight: .semibold).monospacedDigit())
        .padding(.horizontal, 14).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.inSheet))
      if let error { Text(error).font(Typo.caption).foregroundColor(Palette.bad) }
      PrimaryButton(title: tr("保存", "Save")) {
        let raw = text.replacingOccurrences(of: ",", with: ".").trimmingCharacters(in: .whitespaces)
        if raw.isEmpty { save(nil); return }
        guard let v = Double(raw), v >= 0 else { error = tr("请输入不小于 0 的数", "Enter a number ≥ 0"); return }
        save(v)
      }
      if value != nil {
        SecondaryButton(title: tr("清除", "Clear"), tone: .bad, background: Palette.inSheet) { save(nil) }
      }
    }
    .onAppear { focused = true }
  }
}

/// Left-to-right wrapping layout (iOS 16 Layout) for tags.
struct WrapLayout: Layout {
  var spacing: CGFloat = 8

  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    let width = proposal.width ?? .infinity
    var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, maxX: CGFloat = 0
    for s in subviews {
      let size = s.sizeThatFits(.unspecified)
      if x > 0, x + size.width > width { x = 0; y += row + spacing; row = 0 }
      x += size.width + spacing
      maxX = max(maxX, x - spacing)
      row = max(row, size.height)
    }
    return CGSize(width: min(width, maxX), height: y + row)
  }

  func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
    var x = bounds.minX, y = bounds.minY, row: CGFloat = 0
    for s in subviews {
      let size = s.sizeThatFits(.unspecified)
      if x > bounds.minX, x + size.width > bounds.maxX { x = bounds.minX; y += row + spacing; row = 0 }
      s.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
      x += size.width + spacing
      row = max(row, size.height)
    }
  }
}
