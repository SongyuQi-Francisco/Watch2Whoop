import SwiftUI

/// 记录: 训练 · 日记, plus 饮食 while the `friends` flag puts 朋友 in the tab bar in its place (app.js applyTabs()).
struct RecordTab: View {
  let circles: Bool
  @SceneStorage("w2w.record.segment") private var segment: Segment = .training
  @StateObject private var training: TrainingStore
  @StateObject private var journal: JournalStore
  @StateObject private var food: FoodStore
  @State private var toast: Toast?
  @State private var devHealth: MeSheet.RecordKind?

  enum Segment: String, Hashable { case training, journal, food }

  init(session: AccountSession) {
    circles = session.circlesTab
    _training = StateObject(wrappedValue: TrainingStore(session: session))
    _journal = StateObject(wrappedValue: JournalStore(session: session))
    _food = StateObject(wrappedValue: FoodStore(session: session))
  }

  private var shown: Segment { segment == .food && !circles ? .training : segment }

  var body: some View {
    NavigationStack {
      PageScaffold(title: tr("记录", "Log")) {
        ScrollViewReader { proxy in
          VStack(alignment: .leading, spacing: 18) {
            SegmentedControl(items: items, selection: $segment)
            switch shown {
            case .training: TrainingSection(store: training, toast: $toast)
            case .journal: JournalSection(store: journal, toast: $toast)
            case .food: FoodContent(store: food, toast: $toast)
            }
          }
          .padding(.top, 4)
          .task { await devShot(proxy) }
        }
      }
      .refreshable {
        switch shown {
        case .training: await training.refresh()
        case .journal: await journal.refresh()
        case .food: await food.refresh()
        }
      }
      .toast($toast)
      .sheet(item: $devHealth) { k in
        SheetContainer(title: k.title, close: { devHealth = nil }) { HealthRecordView(kind: k, openLab: RecordDevShot.value("W2W_DEV_HEALTH")?.split(separator: ":").dropFirst().first.map(String.init)) }
          .presentationDetents([.large])
      }
    }
  }

  /// Debug screenshots (RecordDevShot): segment, a health record sheet, a scroll position.
  private func devShot(_ proxy: ScrollViewProxy) async {
    if let s = RecordDevShot.value("W2W_DEV_RECORD").flatMap(Segment.init(rawValue:)) { segment = s }
    if let h = RecordDevShot.value("W2W_DEV_HEALTH")?.split(separator: ":").first.flatMap({ MeSheet.RecordKind(rawValue: String($0)) }) {
      devHealth = h
    }
    if let anchor = RecordDevShot.value("W2W_DEV_SCROLL") {
      try? await Task.sleep(nanoseconds: 2_500_000_000)
      proxy.scrollTo(anchor, anchor: .top)
    }
  }

  private var items: [(Segment, String)] {
    var out: [(Segment, String)] = [(.training, tr("训练", "Training")), (.journal, tr("日记", "Journal"))]
    if circles { out.append((.food, tr("饮食", "Food"))) }
    return out
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
private struct TagCloud: View {
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
          .foregroundColor(on ? Palette.bg : Palette.text)
          .padding(.vertical, 8).padding(.horizontal, 12)
          .background(Capsule().fill(on ? Palette.text : Palette.surface))
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
