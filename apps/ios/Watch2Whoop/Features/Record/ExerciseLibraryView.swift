import SwiftUI

/// 动作库: search by name or alias, browse by muscle group and equipment (the server searches; ~950 exercises). Search text,
/// filters and scroll position stay while an exercise is open, because this page stays on the navigation stack.
struct ExerciseLibraryView: View {
  let nav: TrainingNav
  @State private var query = ""
  @State private var group = ""
  @State private var equipment = ""
  @State private var list: [LibraryExercise]?
  @State private var failed: String?
  @State private var attempt = 0

  private var filters: String { "\(query)|\(group)|\(equipment)|\(attempt)" }

  var body: some View {
    TrainingPage(nav: nav, title: tr("动作库", "Exercise library")) {
      VStack(alignment: .leading, spacing: 10) {
        if nav.store.muscleOn {
          NavigationLink { CustomExercisesView(nav: nav) } label: {
            HStack(spacing: 8) {
              Icon(.dumbbell, size: 18)
              Text(tr("我的动作", "My exercises")).font(Typo.bodyStrong)
              Spacer()
              Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
            }
            .foregroundColor(Palette.text)
            .padding(.horizontal, 14).frame(minHeight: 46)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.inSheet))
          }
          .buttonStyle(PressScale())
        }
        searchField
        FilterChips(items: ExerciseVocab.groupFilters, selection: $group)
        FilterChips(items: ExerciseVocab.equipmentFilters, selection: $equipment)
        results.padding(.top, 2)
      }
    }
    .task(id: filters) { await load(typed: !query.isEmpty) }
  }

  private var searchField: some View {
    HStack(spacing: 8) {
      Icon(.search, size: 18).foregroundColor(Palette.text3)
      TextField(tr("搜中英文动作名、别名…", "Search exercise names, aliases…"), text: $query)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .font(Typo.body).foregroundColor(Palette.text)
      if !query.isEmpty {
        Button { query = "" } label: { Icon(.x, size: 16).foregroundColor(Palette.text3) }
          .accessibilityLabel(tr("清除", "Clear"))
      }
    }
    .padding(.horizontal, 14).frame(minHeight: 46)
    .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.inSheet))
  }

  @ViewBuilder private var results: some View {
    if let list {
      if list.isEmpty {
        EmptyNote(text: tr("没有匹配的动作", "No matching exercises"))
      } else {
        LazyVStack(spacing: 0) {
          ForEach(Array(list.enumerated()), id: \.element.id) { i, x in
            if i > 0 { RowDivider(inset: 0) }
            NavigationLink { ExerciseDetailView(nav: nav, slug: x.slug) } label: { LibraryRow(x: x) }
              .buttonStyle(PressScale())
          }
        }
        .padding(.horizontal, 14)
        .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.inSheet))
      }
    } else if let failed {
      RetryNote(text: failed) { attempt += 1 }
    } else {
      EmptyNote(text: "…")
    }
  }

  private func load(typed: Bool) async {
    if typed {   // while typing, one request a moment after the last key
      try? await Task.sleep(nanoseconds: 250_000_000)
      guard !Task.isCancelled else { return }
    }
    do {
      let found = try await nav.store.searchLibrary(query: query, group: group, equipment: equipment)
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

/// One choice out of a row of tags, scrolling sideways.
private struct FilterChips: View {
  let items: [(key: String, title: String)]
  @Binding var selection: String

  var body: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 6) {
        ForEach(items, id: \.key) { item in
          ToggleChip(title: item.title, on: item.key == selection) { selection = item.key }
        }
      }
    }
  }
}

private struct LibraryRow: View {
  let x: LibraryExercise

  var body: some View {
    HStack(spacing: 10) {
      VStack(alignment: .leading, spacing: 3) {
        HStack(spacing: 6) {
          Text(x.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(2).multilineTextAlignment(.leading)
          if x.custom { Badge(label: tr("我的", "Mine")) }
          ExerciseStatusBadge(status: x.status)
        }
        if !x.subtitle.isEmpty { Text(x.subtitle).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1) }
      }
      Spacer(minLength: 4)
      Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
    }
    .padding(.vertical, 11)
    .contentShape(Rectangle())
  }
}

// MARK: - pictures

enum ExerciseMedia {
  /// Demo images are `/app/media/…` on the server (public, no sign-in) or a full URL when the deployment keeps them elsewhere.
  static func url(_ s: String, base: URL) -> URL? { URL(string: s, relativeTo: base)?.absoluteURL }
}

/// A square picture of an exercise: its demo image, else its muscles on the small body (training-day.js picHtml).
struct PlanExerciseThumb: View {
  let thumb: String?
  let primary: [String]
  let secondary: [String]
  let base: URL
  var sex = "male"
  var side: CGFloat = 48

  var body: some View {
    ZStack {
      if let thumb, let url = ExerciseMedia.url(thumb, base: base) {
        Color.white
        AsyncImage(url: url) { image in
          image.resizable().scaledToFit()
        } placeholder: {
          Color.clear
        }
      } else if !primary.isEmpty, let front = MuscleBodies.shared.side(sex: sex, front: true), let back = MuscleBodies.shared.side(sex: sex, front: false) {
        Palette.surface2
        let regions = MuscleLoad.exerciseRegions(primary: primary, secondary: secondary)
        HStack(spacing: 1) {
          MuscleBodyView(side: front, regions: regions, lineScale: 0.3)
          MuscleBodyView(side: back, regions: regions, lineScale: 0.3)
        }
        .padding(3)
        .allowsHitTesting(false)   // it sits in the button of its row
      } else {
        Palette.surface2
      }
    }
    .frame(width: side, height: side)
    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    .accessibilityHidden(true)
  }
}

/// The demo pictures: start and end position alternate like a slow animation until the user swipes (app.js ex-car).
private struct DemoImages: View {
  let urls: [URL]
  let label: String
  @State private var page = 0
  @State private var auto = true
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
    TabView(selection: $page) {
      ForEach(Array(urls.enumerated()), id: \.offset) { i, url in
        AsyncImage(url: url) { image in
          image.resizable().scaledToFit()
        } placeholder: {
          ProgressView().tint(Palette.text3)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.white)
        .tag(i)
        .accessibilityLabel("\(label) \(i + 1)")
      }
    }
    .tabViewStyle(.page(indexDisplayMode: urls.count > 1 ? .always : .never))
    .indexViewStyle(.page(backgroundDisplayMode: .always))
    .frame(height: 230)
    .background(Color.white)
    .clipShape(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous))
    .simultaneousGesture(DragGesture(minimumDistance: 4).onChanged { _ in auto = false })
    .task(id: auto) {
      guard auto, urls.count > 1, !reduceMotion else { return }
      while !Task.isCancelled {
        try? await Task.sleep(nanoseconds: 1_600_000_000)
        guard !Task.isCancelled, auto else { return }
        withAnimation(.easeInOut(duration: 0.4)) { page = (page + 1) % urls.count }
      }
    }
  }
}

/// Front and back of the body with an exercise's muscles.
private struct ExerciseMuscleMap: View {
  let primary: [String]
  let secondary: [String]
  let sex: String

  var body: some View {
    let regions = MuscleLoad.exerciseRegions(primary: primary, secondary: secondary)
    HStack(spacing: 18) {
      ForEach([true, false], id: \.self) { front in
        VStack(spacing: 4) {
          if let side = MuscleBodies.shared.side(sex: sex, front: front) {
            MuscleBodyView(side: side, regions: regions).frame(height: 200)
          }
          Text(front ? tr("正面", "Front") : tr("背面", "Back")).font(Typo.label).foregroundColor(Palette.text2)
        }
      }
    }
    .frame(maxWidth: .infinity)
    .padding(.vertical, 10)
    .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
    .accessibilityHidden(true)
  }
}

// MARK: - 动作详情

/// One exercise: pictures, the user's restriction, recommended weight, muscles, cues, recent sets, steps and the safe
/// alternatives the server picked. `swapFrom`: opened as an alternative of that exercise, so it can replace it in the plan.
struct ExerciseDetailView: View {
  let nav: TrainingNav
  let slug: String
  var swapFrom: String?
  @State private var detail: ExerciseDetail?
  @State private var failed: String?
  @State private var swapping = false

  var body: some View {
    TrainingPage(nav: nav, title: detail?.name ?? tr("动作详情", "Exercise")) {
      if let d = detail {
        content(d)
      } else if let failed {
        RetryNote(text: failed) { Task { await load() } }
      } else {
        EmptyNote(text: "…")
      }
    }
    .task { await load() }
  }

  private func load() async {
    failed = nil
    let out = await nav.store.exerciseDetail(slug)
    detail = out.detail
    if out.detail == nil {
      failed = out.offline ? tr("离线，连上网络后重试", "Offline, retry when connected") : tr("暂无详情", "No details")
    }
  }

  private var sex: String { nav.store.muscle?.sex ?? "male" }

  private func content(_ d: ExerciseDetail) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      if let other = d.otherName { Text(other).font(Typo.caption).foregroundColor(Palette.text2) }
      let urls = d.images.compactMap { ExerciseMedia.url($0, base: nav.store.api.baseURL) }
      if !urls.isEmpty { DemoImages(urls: urls, label: d.name) }
      if !d.credits.isEmpty {
        Text("\(tr("图片来源", "Images")) · \(d.credits.joined(separator: " / "))").font(.system(size: 11)).foregroundColor(Palette.text3)
      }
      if d.noDemo { EmptyNote(text: tr("暂无演示", "No demo yet")) }
      WrapLayout(spacing: 6) {
        ExerciseStatusBadge(status: d.status, showActive: true)
        ForEach(d.equipment, id: \.self) { Badge(label: $0) }
        if let p = d.pattern { Badge(label: p) }
        ForEach(d.joints, id: \.self) { Badge(label: $0, tone: .bad) }
      }
      actions(d)
      facts(d)
      ExerciseMuscleMap(primary: d.primaryKeys, secondary: d.secondaryKeys, sex: sex)
      if let c = d.cues { LongRow(label: tr("动作要点", "Cues"), text: c) }
      recent(d)
      steps(d)
      alternatives(d)
    }
  }

  @ViewBuilder private func actions(_ d: ExerciseDetail) -> some View {
    if let from = swapFrom, !d.banned, !d.onlyInLog {
      PrimaryButton(title: tr("替换成这个", "Swap to this"), busy: swapping) { swap(from, to: d) }
    }
    if nav.store.addToPlanOn, !d.onlyInLog {
      if d.banned {
        Text(tr("这是禁用动作，不能加入训练计划", "This exercise is banned, so it can't be added to the plan")).font(Typo.caption).foregroundColor(Palette.bad)
      } else {
        NavigationLink { AddToPlanView(nav: nav, slug: d.slug) } label: {
          HStack(spacing: 8) {
            Icon(.plus, size: 18)
            Text(tr("加入训练计划", "Add to plan")).font(Typo.bodyStrong)
          }
          .foregroundColor(Palette.text)
          .frame(maxWidth: .infinity, minHeight: 46)
          .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.inSheet))
        }
        .buttonStyle(PressScale())
      }
    }
  }

  @ViewBuilder private func facts(_ d: ExerciseDetail) -> some View {
    VStack(alignment: .leading, spacing: 0) {
      if let r = d.reason { LongRow(label: tr("限制原因", "Why restricted"), text: r) }
      if !d.banned, let v = d.load?.value {
        KeyValueRow(label: tr("推荐重量", "Recommended weight"), value: [v, d.load?.note].compactMap { $0 }.joined(separator: "  "))
      }
      if !d.banned, let b = d.load?.basisText {
        KeyValueRow(label: tr("依据", "Based on"), value: [b, d.load?.basisNote].compactMap { $0 }.joined(separator: "  "))
      }
      if !d.primary.isEmpty { KeyValueRow(label: tr("主肌群", "Primary muscles"), value: d.primary.joined(separator: tr("、", ", "))) }
      if !d.secondary.isEmpty { KeyValueRow(label: tr("次肌群", "Secondary muscles"), value: d.secondary.joined(separator: tr("、", ", "))) }
    }
  }

  @ViewBuilder private func recent(_ d: ExerciseDetail) -> some View {
    if !d.recent.isEmpty {
      VStack(alignment: .leading, spacing: 0) {
        SectionHeader(title: tr("最近", "Recent")).padding(.bottom, 6)
        ForEach(d.recent) { r in KeyValueRow(label: RecordDay.short(r.date), value: r.sets) }
      }
      .padding(.top, 6)
    }
  }

  @ViewBuilder private func steps(_ d: ExerciseDetail) -> some View {
    if !d.steps.isEmpty {
      VStack(alignment: .leading, spacing: 8) {
        SectionHeader(title: tr("执行步骤", "How to do it"))
        ForEach(Array(d.steps.enumerated()), id: \.offset) { i, s in
          HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(i + 1).").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
            Text(s).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
          }
        }
      }
      .padding(.top, 8)
    }
  }

  @ViewBuilder private func alternatives(_ d: ExerciseDetail) -> some View {
    if !d.alternatives.isEmpty {
      VStack(alignment: .leading, spacing: 8) {
        SectionHeader(title: tr("同模式安全替代动作", "Safe alternatives (same pattern)"))
        SheetRows {
          ForEach(Array(d.alternatives.enumerated()), id: \.element.id) { i, a in
            if i > 0 { RowDivider(inset: 0) }
            NavigationLink { ExerciseDetailView(nav: nav, slug: a.slug, swapFrom: d.slug) } label: { alternativeRow(a) }
              .buttonStyle(PressScale())
          }
        }
      }
      .padding(.top, 10)
    }
  }

  private func alternativeRow(_ a: ExerciseAlternative) -> some View {
    HStack(spacing: 10) {
      PlanExerciseThumb(thumb: a.thumb, primary: a.primaryKeys, secondary: a.secondaryKeys, base: nav.store.api.baseURL, sex: sex, side: 40)
      VStack(alignment: .leading, spacing: 2) {
        HStack(spacing: 6) {
          Text(a.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
          ExerciseStatusBadge(status: a.status)
        }
        if !a.subtitle.isEmpty { Text(a.subtitle).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1) }
      }
      Spacer(minLength: 4)
      if a.status != "banned", let c = a.load?.chip {
        Text(c.text).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      }
      Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
    }
    .padding(.vertical, 9)
    .contentShape(Rectangle())
  }

  private func swap(_ from: String, to d: ExerciseDetail) {
    swapping = true
    Task {
      let error = await nav.store.swap(from, for: SwapCandidate(slug: d.slug, name: d.name))
      swapping = false
      if let error { nav.say(Toast(error)) } else { nav.finish(Toast(tr("已替换为 \(d.name)", "Replaced with \(d.name)"))) }
    }
  }
}

/// A label above a longer text, with a hairline above (cues, reasons).
struct LongRow: View {
  let label: String
  let text: String

  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(label).font(Typo.body).foregroundColor(Palette.text2)
      Text(text).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(.vertical, 10)
    .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
    .accessibilityElement(children: .combine)
  }
}

// MARK: - 加入训练计划 (exercise-plan.js openAdd, flag exercise_add_to_plan)

struct AddToPlanView: View {
  let nav: TrainingNav
  let slug: String
  @Environment(\.dismiss) private var dismiss
  @State private var options: AddPlanOptions?
  @State private var form: AddPlanForm?
  @State private var failed: String?
  @State private var busy = false

  var body: some View {
    TrainingPage(nav: nav, title: tr("加入训练计划", "Add to plan")) {
      if let o = options {
        content(o)
      } else if let failed {
        RetryNote(text: failed) { Task { await load() } }
      } else {
        EmptyNote(text: "…")
      }
    }
    .task { await load() }
  }

  private func load() async {
    failed = nil
    do {
      guard let o = try await nav.store.addOptions(slug) else {
        failed = tr("暂无", "None")
        return
      }
      options = o
      form = AddPlanForm(slug: o.slug, timed: o.timed, sets: o.sets.map(String.init) ?? "", repLow: o.repLow.map(String.init) ?? "",
                         repHigh: o.repHigh.map(String.init) ?? "", durationSec: o.durationSec.map(String.init) ?? "")
    } catch {
      failed = TrainingStore.message(error)
    }
  }

  @ViewBuilder private func content(_ o: AddPlanOptions) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      Text(o.name).font(Typo.bodyStrong).foregroundColor(Palette.text)
      if o.blocked {
        blocked(o)
      } else if !o.hasPlan {
        EmptyNote(text: tr("还没有周训练计划，先让 Jarvis 帮你做一个", "No weekly plan yet. Ask Jarvis to build one first"))
      } else if form != nil {
        picker(o)
      }
    }
  }

  private func blocked(_ o: AddPlanOptions) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      Banner(text: tr("这是禁用动作，不能加入训练计划", "This exercise is banned, so it can't be added to the plan"), tone: .bad)
      if let r = o.reason { Text(r).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true) }
      if !o.alternatives.isEmpty {
        SectionHeader(title: tr("可以改用", "Try instead")).padding(.top, 4)
        SheetRows {
          ForEach(Array(o.alternatives.enumerated()), id: \.element.id) { i, a in
            if i > 0 { RowDivider(inset: 0) }
            NavigationLink { ExerciseDetailView(nav: nav, slug: a.slug) } label: {
              HStack {
                Text(a.name).font(Typo.body).foregroundColor(Palette.text)
                Spacer()
                Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
              }
              .padding(.vertical, 11)
              .contentShape(Rectangle())
            }
            .buttonStyle(PressScale())
          }
        }
      }
    }
  }

  private var scope: Binding<AddPlanForm.Scope> {
    Binding(get: { form?.scope ?? .template }, set: { form?.scope = $0 })
  }

  private func text(_ path: WritableKeyPath<AddPlanForm, String>) -> Binding<String> {
    Binding(get: { form?[keyPath: path] ?? "" }, set: { form?[keyPath: path] = $0 })
  }

  private func picker(_ o: AddPlanOptions) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      SegmentedControl(items: [(AddPlanForm.Scope.template, tr("训练组（每周都有）", "Session (every week)")), (AddPlanForm.Scope.day, tr("只加到某一天", "Just one day"))],
                       selection: scope)
      SheetRows {
        if form?.scope == .template {
          if o.sessions.isEmpty { EmptyNote(text: tr("没有可选的", "Nothing to choose from")) }
          ForEach(Array(o.sessions.enumerated()), id: \.element.id) { i, s in
            if i > 0 { RowDivider(inset: 0) }
            option(title: "\(ExerciseVocab.weekday(s.dayOfWeek)) · \(s.name)",
                   subtitle: s.hasExercise ? tr("已包含", "Already in") : tr("\(s.count) 个动作", "\(s.count) exercises"),
                   on: form?.session == s, disabled: s.hasExercise) { form?.session = s }
          }
        } else {
          if o.days.isEmpty { EmptyNote(text: tr("没有可选的", "Nothing to choose from")) }
          ForEach(Array(o.days.enumerated()), id: \.element.id) { i, d in
            if i > 0 { RowDivider(inset: 0) }
            option(title: "\(ExerciseVocab.weekday(RecordDay.weekday(d.date))) · \(RecordDay.short(d.date))",
                   subtitle: d.hasExercise ? tr("已包含", "Already in") : d.names.isEmpty ? tr("休息日", "Rest day") : d.names.joined(separator: " / "),
                   on: form?.day == d.date, disabled: d.hasExercise) { form?.day = d.date }
          }
        }
      }
      HStack(alignment: .bottom, spacing: 8) {
        FormField(label: tr("组数", "Sets"), text: text(\.sets), keyboard: .numberPad, background: Palette.inSheet)
        if form?.timed == true {
          FormField(label: tr("时长（秒）", "Duration (s)"), text: text(\.durationSec), keyboard: .numberPad, background: Palette.inSheet)
        } else {
          FormField(label: tr("次数", "Reps"), text: text(\.repLow), keyboard: .numberPad, background: Palette.inSheet)
          FormField(label: "–", text: text(\.repHigh), keyboard: .numberPad, background: Palette.inSheet)
        }
      }
      PrimaryButton(title: busy ? tr("加入中…", "Adding…") : tr("加入计划", "Add to plan"), busy: busy) { submit() }
    }
  }

  private func option(title: String, subtitle: String, on: Bool, disabled: Bool, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      HStack(spacing: 10) {
        Icon(.circleCheck, size: 18).foregroundColor(Palette.good).opacity(on ? 1 : 0)
        VStack(alignment: .leading, spacing: 2) {
          Text(title).font(Typo.body).foregroundColor(disabled ? Palette.text3 : Palette.text).multilineTextAlignment(.leading)
          Text(subtitle).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(2).multilineTextAlignment(.leading)
        }
        Spacer(minLength: 0)
      }
      .padding(.vertical, 10)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .disabled(disabled)
    .accessibilityAddTraits(on ? .isSelected : [])
  }

  private func submit() {
    guard let form else { return }
    let req = form.request()
    guard let body = req.body else {
      if let e = req.error { nav.say(Toast(e)) }
      return
    }
    let where_ = form.scope == .day
      ? form.day.map { "\(ExerciseVocab.weekday(RecordDay.weekday($0))) \(RecordDay.short($0))" } ?? ""
      : form.session.map { "\(ExerciseVocab.weekday($0.dayOfWeek)) · \($0.name)" } ?? ""
    busy = true
    Task {
      let error = await nav.store.addToPlan(body)
      busy = false
      if let error {
        nav.say(Toast(error))
      } else {
        nav.say(Toast(tr("已加入 \(where_)", "Added to \(where_)")))
        dismiss()
      }
    }
  }
}

// MARK: - 肌肉详情

/// One muscle (muscle-load.js openMuscle): its numbers, the muscles drawn with it, the last session, the sports, recommended
/// exercises (the server leaves banned ones out) and the question for Jarvis.
struct MuscleDetailView: View {
  let nav: TrainingNav
  let muscle: MuscleLoad.Muscle
  let range: MuscleLoad.Range
  @State private var key: String
  @State private var detail: MuscleDetail?
  @State private var offline = false

  init(nav: TrainingNav, muscle: MuscleLoad.Muscle, range: MuscleLoad.Range) {
    self.nav = nav
    self.muscle = muscle
    self.range = range
    _key = State(initialValue: muscle.key)
  }

  private var row: MuscleLoad.Muscle? { nav.store.muscle?.muscles.first { $0.key == key } ?? (key == muscle.key ? muscle : nil) }

  var body: some View {
    TrainingPage(nav: nav, title: detail?.name ?? row?.name ?? muscle.name) {
      VStack(alignment: .leading, spacing: 12) {
        siblings
        if let d = detail { numbers(d) } else if let r = row { basics(r) }
        if offline, detail == nil { EmptyNote(text: tr("离线，连上网络后重试", "Offline, retry when connected")) }
        if let d = detail {
          last(d)
          sports(d)
          recommended(d)
        }
        AskJarvisButton(title: tr("让 Jarvis 分析我的肌群训练", "Ask Jarvis to analyse my muscle training"), prompt: MusclePrompts.analyse, ask: nav.ask)
          .padding(.top, 4)
      }
    }
    .task(id: key) {
      let out = await nav.store.muscleDetail(key)
      detail = out.detail
      offline = out.offline
    }
  }

  @ViewBuilder private var siblings: some View {
    let others = MuscleLoad.siblings(of: muscle.key)
    if !others.isEmpty {
      WrapLayout(spacing: 6) {
        ForEach([muscle.key] + others, id: \.self) { k in
          ToggleChip(title: nav.store.muscle?.muscles.first { $0.key == k }?.name ?? k, on: k == key) { key = k }
        }
      }
    }
  }

  private func sets(_ n: Double?) -> String { tr("\(RecordFormat.num(n, 1)) 组", "\(RecordFormat.num(n, 1)) sets") }

  private func numbers(_ d: MuscleDetail) -> some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack(alignment: .firstTextBaseline) {
        Text(tr("本周", "This week")).font(Typo.body).foregroundColor(Palette.text2)
        Spacer(minLength: 12)
        Badge(label: MuscleLoad.statusTitle(d.status), tone: MuscleLoad.tone(d.status))
        Text("\(sets(d.weekSets)) · \(RecordFormat.num(d.weekKg)) kg").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text)
      }
      .padding(.vertical, 10)
      if let lo = d.low, let hi = d.high {
        KeyValueRow(label: tr("每周推荐", "Weekly range"), value: tr("\(RecordFormat.num(lo))–\(RecordFormat.num(hi)) 组", "\(RecordFormat.num(lo))–\(RecordFormat.num(hi)) sets"))
      }
      KeyValueRow(label: tr("上周", "Last week"), value: "\(sets(d.prevSets)) · \(RecordFormat.num(d.prevKg)) kg\(deltaText(d.delta))")
      KeyValueRow(label: tr("近 7 天", "Last 7 days"), value: "\(sets(d.last7Sets)) · \(RecordFormat.num(d.last7Kg)) kg")
    }
  }

  private func deltaText(_ delta: Double?) -> String {
    guard let delta, delta != 0 else { return "" }
    return " (\(delta > 0 ? "+" : "")\(sets(delta)))"
  }

  /// From the map's own numbers until the detail is here, and while offline.
  private func basics(_ r: MuscleLoad.Muscle) -> some View {
    VStack(alignment: .leading, spacing: 0) {
      KeyValueRow(label: range.title, value: sets(r.sets[range]))
      if let lo = r.low, let hi = r.high {
        KeyValueRow(label: tr("每周推荐", "Weekly range"), value: tr("\(RecordFormat.num(lo))–\(RecordFormat.num(hi)) 组", "\(RecordFormat.num(lo))–\(RecordFormat.num(hi)) sets"))
      }
      KeyValueRow(label: tr("训练量", "Volume"), value: "\(RecordFormat.num(r.volume[range])) kg")
      if let p = r.prevWeek { KeyValueRow(label: tr("上周", "Last week"), value: sets(p)) }
      if let s = r.sportMin[range], s > 0 { KeyValueRow(label: tr("运动", "Sports"), value: tr("\(RecordFormat.num(s)) 分钟", "\(RecordFormat.num(s)) min")) }
    }
  }

  @ViewBuilder private func last(_ d: MuscleDetail) -> some View {
    VStack(alignment: .leading, spacing: 4) {
      HStack {
        Text(tr("最近一次练", "Last trained")).font(Typo.body).foregroundColor(Palette.text2)
        Spacer()
        Text(d.lastDate.map(RecordDay.short) ?? tr("近 4 周没练", "Not trained in the last 4 weeks")).font(Typo.body.monospacedDigit()).foregroundColor(Palette.text)
      }
      ForEach(d.last) { e in
        Text(lastLine(e)).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      }
    }
    .padding(.vertical, 10)
    .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
  }

  private func lastLine(_ e: MuscleDetail.LastExercise) -> String {
    var parts = [e.name, tr("\(e.sets) 组", "\(e.sets) sets")]
    if let t = e.top { parts.append("\(t) kg") }
    if !e.primary { parts.append(tr("辅助", "assist")) }
    return parts.joined(separator: " · ")
  }

  @ViewBuilder private func sports(_ d: MuscleDetail) -> some View {
    let list = d.sports[range] ?? []
    if !list.isEmpty {
      VStack(alignment: .leading, spacing: 4) {
        Text("\(tr("运动", "Sports")) · \(range.title)").font(Typo.body).foregroundColor(Palette.text2)
        ForEach(list) { s in
          let role = s.primary ? tr("主要", "main") : tr("辅助", "assist")
          Text("\(s.name) · \(tr("\(RecordFormat.num(s.minutes)) 分钟", "\(RecordFormat.num(s.minutes)) min")) · \(role)")
            .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
        }
      }
      .padding(.vertical, 10)
      .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
    }
  }

  private func recommended(_ d: MuscleDetail) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      SectionHeader(title: tr("推荐动作", "Suggested exercises"))
      if d.recommended.isEmpty {
        EmptyNote(text: tr("暂无", "None"))
      } else {
        SheetRows {
          ForEach(Array(d.recommended.enumerated()), id: \.element.id) { i, x in
            if i > 0 { RowDivider(inset: 0) }
            NavigationLink { ExerciseDetailView(nav: nav, slug: x.slug) } label: {
              HStack(spacing: 8) {
                Text(x.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(2).multilineTextAlignment(.leading)
                if let e = x.equipment { Text(e).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1) }
                Spacer(minLength: 4)
                if x.caution { Badge(label: tr("注意", "Caution"), tone: .mid) }
                Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
              }
              .padding(.vertical, 11)
              .contentShape(Rectangle())
            }
            .buttonStyle(PressScale())
          }
        }
      }
    }
    .padding(.top, 4)
  }
}
