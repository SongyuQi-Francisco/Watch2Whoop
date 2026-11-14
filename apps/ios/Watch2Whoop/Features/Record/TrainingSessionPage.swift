import SwiftUI

/// Today's session (docs/design/2.0.1 `11-training`), pushed from the set ring: the ring and the bodies fill set by set,
/// the done / planned sets, one row per exercise (its muscles, its sets as dots, the next set's weight × reps); a row opens
/// the set editor (the 2.0.0 exercise card: reps · kg · done, add / remove a set, swap, how-to, remove from the plan).
/// 「完成训练」 submits the day (the same draft + submit as before).
struct TrainingSessionPage: View {
  @ObservedObject var store: TrainingStore
  @Binding var toast: Toast?
  @State private var editing: String?
  @State private var sheet: TrainingSheet?
  @State private var removing: RemoveTarget?

  var body: some View {
    RecordPage(title: title) {
      VStack(spacing: 0) {
        ring.padding(.top, 20)
        count.padding(.top, 18)
        if let s = store.session { content(s) } else { EmptyNote(text: store.refreshing ? "…" : tr("暂无", "None")).padding(.top, 24) }
      }
    }
    .task { await store.refresh() }
    .trainingSheets(store: store, sheet: $sheet, toast: $toast)
    .sheet(item: Binding(get: { editing.map { Slug(slug: $0) } }, set: { editing = $0?.slug })) { ref in
      ExerciseEditSheet(store: store, slug: ref.slug, close: { editing = nil }, guide: { slug in after { sheet = .exercise(slug) } },
                        remove: { t in after { removing = t } }, say: { toast = $0 })
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
    .modifier(RemoveExerciseDialogs(target: $removing, store: store, say: { toast = $0 }))
  }

  /// Closes the editor first: a second sheet cannot open over it.
  private func after(_ run: @escaping () -> Void) {
    editing = nil
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.45, execute: run)
  }

  private var title: String {
    guard let s = store.session, !s.title.isEmpty else { return tr("今天的训练", "Today's training") }
    return TrainingWeek.shortPlan(s.title)
  }

  private var ring: some View {
    let s = store.session
    let arcs = s.map { RecordGlance.arcs(RecordGlance.sets($0, rows: store.rows)) } ?? []
    let planned = s.map { RecordGlance.regionShares($0.exercises, rows: store.rows) } ?? [:]
    let done = s.map { RecordGlance.regionShares($0.exercises, rows: store.rows, doneOnly: true) } ?? [:]
    let side = min(260, TrainingRoot.ringSide * 0.86)
    // planned muscles dim, the ones already worked fill in as sets are ticked off
    return SetRing(arcs: arcs, side: side) {
      BodyPair(sex: store.muscle?.sex ?? "male", spacing: side * 0.08) { region in
        if let d = done[region], d > 0 { return Palette.train.opacity(RecordGlance.shareOpacity(d)) }
        if planned[region] != nil { return Palette.train.opacity(0.22) }
        return nil
      }
      .frame(width: side * 0.7, height: side * 0.6)
    }
    .frame(maxWidth: .infinity)
    .accessibilityHidden(true)
  }

  private var count: some View {
    let s = store.session
    return HStack(alignment: .firstTextBaseline, spacing: 4) {
      Text("\(s?.doneSets ?? 0)").font(.system(size: 44, weight: .light).monospacedDigit()).foregroundColor(Palette.text)
      Text("/\(s?.totalSets ?? 0) \(tr("组", "sets"))").font(.system(size: 20, weight: .regular).monospacedDigit()).foregroundColor(Palette.text2)
    }
    .accessibilityElement(children: .combine)
  }

  @ViewBuilder private func content(_ s: TrainingSession) -> some View {
    VStack(alignment: .leading, spacing: 14) {
      if let r = s.readiness { Banner(text: r.label, tone: r.tone) }
      if s.exercises.isEmpty {
        EmptyNote(text: s.restDay ? tr("休息", "Rest day") : tr("无计划", "No plan"))
        if let w = store.week, !w.splits.isEmpty, !w.hasPlan {
          SecondaryButton(icon: .calendarDays, title: tr("选周计划", "Pick a weekly plan")) { sheet = .split }
        }
      } else {
        rows(s)
        if s.canLog && !s.loggable.isEmpty { footer(s) }
      }
      if s.canEditPlan, !s.removed.isEmpty {
        RemovedRow(removed: s.removed) { slug in
          Task { if let error = await store.restore(slug, day: s.day) { toast = Toast(error) } }
        }
      }
    }
    .padding(.top, 18)
  }

  private func rows(_ s: TrainingSession) -> some View {
    VStack(spacing: 0) {
      ForEach(Array(s.exercises.enumerated()), id: \.element.id) { i, e in
        Rectangle().fill(Palette.hair).frame(height: 1)
        row(e, superset: i > 0 && e.group != nil && s.exercises[i - 1].group == e.group)
      }
      Rectangle().fill(Palette.hair).frame(height: 1)
    }
  }

  private func row(_ e: PlannedExercise, superset: Bool) -> some View {
    let sets = store.rows[e.slug] ?? []
    return Button { editing = e.slug } label: {
      HStack(spacing: 12) {
        PlanExerciseThumb(thumb: nil, primary: e.primaryMuscles, secondary: e.secondaryMuscles, base: store.api.baseURL,
                          sex: store.muscle?.sex ?? "male", side: 44)
        HStack(spacing: 4) {
          if superset { Icon(.link, size: 13).foregroundColor(Palette.text2) }
          if e.banned { Icon(.ban, size: 14).foregroundColor(Palette.bad) } else if e.caution { Icon(.triangleAlert, size: 14).foregroundColor(Palette.mid) }
          Text(e.name).font(.system(size: 17, weight: .medium)).foregroundColor(e.banned || allDone(sets) ? Palette.text2 : Palette.text)
            .strikethrough(e.banned, color: Palette.text3).lineLimit(1).minimumScaleFactor(0.8)
        }
        Spacer(minLength: 6)
        if !e.banned {
          SetDots(rows: sets)
          setValue(e, sets).frame(minWidth: 86, alignment: .trailing)
        }
        Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
      }
      .padding(.vertical, 10)
      .frame(minHeight: 64)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(e.name) \(sets.filter(\.done).count)/\(sets.count)")
    .accessibilityAddTraits(.isButton)
  }

  private func allDone(_ sets: [SetRow]) -> Bool { !sets.isEmpty && sets.allSatisfy(\.done) }

  /// The next set to do (else the last): 60 kg × 8, or 45 s.
  private func setValue(_ e: PlannedExercise, _ sets: [SetRow]) -> some View {
    let r = sets.first { !$0.done } ?? sets.last ?? e.target
    return HStack(alignment: .firstTextBaseline, spacing: 3) {
      if e.timed {
        Text(RecordFormat.num(r.durationS ?? e.target.durationS)).font(Typo.row).foregroundColor(Palette.text)
        Text(tr("秒", "s")).font(Typo.unit).foregroundColor(Palette.text2)
      } else {
        if e.weighted, let kg = r.weightKg ?? e.target.weightKg {
          Text(RecordFormat.num(kg, 1)).font(Typo.row).foregroundColor(Palette.text)
          Text("kg").font(Typo.unit).foregroundColor(Palette.text2)
          Text("× \((r.reps ?? e.target.reps).map(String.init) ?? "—")").font(.system(size: 15).monospacedDigit()).foregroundColor(Palette.text2)
        } else {
          Text((r.reps ?? e.target.reps).map(String.init) ?? "—").font(Typo.row).foregroundColor(Palette.text)
          Text(tr("次", "reps")).font(Typo.unit).foregroundColor(Palette.text2)
        }
      }
    }
    .lineLimit(1)
  }

  private func footer(_ s: TrainingSession) -> some View {
    let done = s.doneSets
    let blocked = done == 0 || s.submitted || s.pendingSubmit != nil
    let label = s.pendingSubmit != nil ? tr("提交中", "Submitting")
      : s.submitted ? tr("已完成", "Done")
      : s.changedSinceSubmit ? tr("再次完成", "Finish again") : tr("完成训练", "Finish workout")
    return VStack(spacing: 8) {
      if let log = s.trainingLog, log != "connected" {
        SecondaryButton(title: tr("先连接训练记录", "Connect the training log first")) { sheet = .log }
      }
      Button { submit(false) } label: {
        HStack(spacing: 8) {
          Icon(.check, size: 17)
          Text(label).font(.system(size: 17, weight: .semibold))
        }
        .foregroundColor(Palette.text)
        .frame(maxWidth: .infinity, minHeight: 56)
        .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
      }
      .buttonStyle(PressScale())
      .disabled(blocked)
      .opacity(blocked ? 0.45 : 1)
    }
    .padding(.top, 6)
  }

  private func submit(_ allow: Bool) {
    Task {
      let (out, draft) = await store.submit(allowDuplicate: allow)
      if out.sent, draft, !allow {
        toast = Toast(tr("今天已经记过这些动作", "These exercises are already logged today"), actionTitle: tr("仍然提交", "Submit anyway")) {
          submit(true)
        }
      } else {
        toast = Toast(out.sent ? tr("已提交", "Submitted") : out.message)
      }
    }
  }
}

private struct Slug: Identifiable {
  let slug: String
  var id: String { slug }
}

/// The set editor of one exercise (a row of the session): the 2.0.0 exercise card, open, on a sheet.
private struct ExerciseEditSheet: View {
  @ObservedObject var store: TrainingStore
  let slug: String
  let close: () -> Void
  let guide: (String) -> Void
  let remove: (RemoveTarget) -> Void
  let say: (Toast) -> Void

  var body: some View {
    let s = store.session
    let e = s?.exercises.first { $0.slug == slug }
    SheetContainer(title: e?.name ?? "", close: close) {
      if let s, let e {
        ExerciseCard(e: e, rows: store.rows[e.slug] ?? [], canLog: s.canLog, open: true, toggle: {},
                     edit: { change in store.edit(e.slug, change) },
                     swapTo: s.canEditPlan ? { c in swap(e, c) } : nil,
                     baseURL: store.api.baseURL, sex: store.muscle?.sex ?? "male",
                     guide: e.unresolved ? nil : { guide(e.slug) },
                     remove: s.canEditPlan ? { remove(target(e, s)) } : nil)
        if !store.saveState.isEmpty {
          Text(store.saveState).font(Typo.label).foregroundColor(Palette.text2).padding(.top, 6)
        }
      }
    }
  }

  private func target(_ e: PlannedExercise, _ s: TrainingSession) -> RemoveTarget {
    RemoveTarget(slug: e.slug, name: e.name, date: s.day, isToday: s.day == store.today, dayOfWeek: e.dayOfWeek, sessionName: e.sessionName,
                 sessionTitle: e.sessionTitle, addedForDay: e.addedForDay)
  }

  private func swap(_ e: PlannedExercise, _ c: SwapCandidate) {
    Task {
      let error = await store.swap(e.slug, for: c)
      say(Toast(error ?? tr("已换成 \(c.name)", "Swapped for \(c.name)")))
      if error == nil { close() }
    }
  }
}
