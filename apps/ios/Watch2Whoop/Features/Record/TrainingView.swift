import SwiftUI

/// 记录 → 训练: the muscle load map, today's plan with set-by-set logging (banned / caution exercises marked with the
/// user's own reason), the week, the user's exercise restrictions and the history calendar.
struct TrainingSection: View {
  @ObservedObject var store: TrainingStore
  @Binding var toast: Toast?
  @State private var range: MuscleLoad.Range = .week
  @State private var muscle: MuscleLoad.Muscle?
  @State private var detailDay: String?
  @State private var open: Set<String> = []
  @State private var allMuscles = false

  init(store: TrainingStore, toast: Binding<Toast?>) {
    self.store = store
    _toast = toast
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 22) {
      RecordStatusLine(offline: store.offline, refreshing: store.refreshing && store.session == nil)
      if let m = store.muscle { muscleCard(m) }
      todayCard.id("n5.today")
      if let w = store.week { weekCard(w).id("n5.week") }
      restrictions.id("n5.limits")
      TrainingCalendarView(store: store) { detailDay = $0 }.id("n5.calendar")
    }
    .task {
      await store.refresh()
      if open.isEmpty, let first = store.session?.loggable.first { open.insert(first.slug) }   // the first exercise opens by itself
    }
    .sheet(item: $muscle) { m in
      SheetContainer(title: m.name, close: { muscle = nil }) { MuscleSheet(m: m, range: range) }
        .presentationDetents([.medium])
    }
    .sheet(item: Binding(get: { detailDay.map { DayRef(day: $0) } }, set: { detailDay = $0?.day })) { ref in
      SheetContainer(title: tr("训练 · \(RecordDay.short(ref.day))", "Training · \(RecordDay.short(ref.day))"), close: { detailDay = nil }) {
        TrainingDaySheet(store: store, day: ref.day, plan: store.week?.days.first { $0.date == ref.day }?.planned ?? [])
      }
      .presentationDetents([.medium, .large])
    }
  }

  // MARK: muscle load

  private func muscleCard(_ m: MuscleLoad) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      SectionHeader(title: tr("肌肉负荷", "Muscle load"))
      Card {
        VStack(spacing: 12) {
          SegmentedControl(items: MuscleLoad.Range.allCases.map { ($0, $0.title) }, selection: $range)
          let regions = m.regions(range)
          HStack(spacing: 12) {
            ForEach([true, false], id: \.self) { front in
              VStack(spacing: 4) {
                if let side = MuscleBodies.shared.side(sex: m.sex, front: front) {
                  MuscleBodyView(side: side, regions: regions) { key in muscle = m.muscles.first { $0.key == key } }
                    .frame(height: 260)
                }
                Text(front ? tr("正面", "Front") : tr("背面", "Back")).font(Typo.label).foregroundColor(Palette.text2)
              }
              .frame(maxWidth: .infinity)
            }
          }
          legend
          let rows = m.muscles.filter { ($0.sets[range] ?? 0) > 0 || ($0.sportMin[range] ?? 0) > 0 }
            .sorted { ($0.sets[range] ?? 0) > ($1.sets[range] ?? 0) }
          if rows.isEmpty {
            EmptyNote(text: tr("这段时间没有逐组记录", "No sets logged in this range"))
          }
          VStack(spacing: 0) {
            ForEach(allMuscles ? rows : Array(rows.prefix(5))) { r in
              Button { muscle = r } label: {
                HStack(spacing: 8) {
                  Text(r.name).font(Typo.bodyStrong).foregroundColor(Palette.text)
                  Badge(label: MuscleLoad.statusTitle(r.status[range] ?? "none"), tone: MuscleLoad.tone(r.status[range] ?? "none"))
                  Spacer()
                  Text(tr("\(RecordFormat.num(r.sets[range], 1)) 组", "\(RecordFormat.num(r.sets[range], 1)) sets"))
                    .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
                  Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
                }
                .padding(.vertical, 10)
                .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
                .contentShape(Rectangle())
              }
              .buttonStyle(PressScale())
            }
          }
          if rows.count > 5 {
            Button(allMuscles ? tr("收起", "Less") : tr("全部 \(rows.count) 个肌群", "All \(rows.count) muscles")) { withAnimation { allMuscles.toggle() } }
              .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
          }
          if !m.unmatched.isEmpty {
            Text(tr("\(m.unmatched.count) 个动作没对上动作库，没有计入", "\(m.unmatched.count) exercises not in the library, not counted"))
              .font(Typo.caption).foregroundColor(Palette.text3).frame(maxWidth: .infinity, alignment: .leading)
          }
        }
      }
    }
  }

  private var legend: some View {
    HStack(spacing: 12) {
      ForEach([("none", tr("没练", "None")), ("low", tr("偏少", "Low")), ("ok", tr("合适", "On target")), ("high", tr("偏多", "High"))], id: \.0) { k, t in
        HStack(spacing: 4) {
          RoundedRectangle(cornerRadius: 3).fill(swatch(k)).frame(width: 10, height: 10)
          Text(t)
        }
      }
      HStack(spacing: 4) {
        RoundedRectangle(cornerRadius: 3).stroke(Palette.text, lineWidth: 1.5).frame(width: 10, height: 10)
        Text(tr("今天练了", "Today"))
      }
    }
    .font(.system(size: 11)).foregroundColor(Palette.text2)
  }

  private func swatch(_ k: String) -> Color {
    switch k {
    case "low": return Palette.train.opacity(0.4)
    case "ok": return Palette.good
    case "high": return Palette.bad
    default: return Palette.surface2
    }
  }

  // MARK: today

  @ViewBuilder private var todayCard: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: tr("今天", "Today"))
        if let s = store.session, !s.title.isEmpty {
          Text(s.title).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
        }
      }
      if let s = store.session {
        if s.exercises.isEmpty {
          Card { EmptyNote(text: s.restDay ? tr("休息", "Rest day") : tr("无计划", "No plan")) }
        } else {
          if let r = s.readiness { Banner(text: r.label, tone: r.tone) }
          VStack(spacing: 10) {
            ForEach(s.exercises) { e in
              ExerciseCard(e: e, rows: store.rows[e.slug] ?? [], canLog: s.canLog, open: open.contains(e.slug),
                           toggle: { if open.contains(e.slug) { open.remove(e.slug) } else { open.insert(e.slug) } },
                           edit: { change in store.edit(e.slug, change) })
            }
          }
          if s.canLog && !s.loggable.isEmpty { submitFooter(s) }
        }
      } else {
        Card { EmptyNote(text: store.refreshing ? "…" : tr("暂无", "None")) }
      }
    }
  }

  private func submitFooter(_ s: TrainingSession) -> some View {
    let done = store.rows.reduce(0) { n, kv in n + (s.loggable.contains { $0.slug == kv.key } ? kv.value.filter { $0.done && $0.hasValue }.count : 0) }
    let label = s.pendingSubmit != nil ? tr("提交中 · \(done) 组", "Submitting · \(done) sets")
      : s.submitted ? tr("已提交 \(done) 组", "Submitted \(done) sets")
      : s.changedSinceSubmit ? tr("重新提交 \(done) 组", "Submit again · \(done) sets") : tr("提交 \(done) 组", "Submit \(done) sets")
    return VStack(spacing: 6) {
      PrimaryButton(title: label) { submit(false) }
        .disabled(done == 0 || s.submitted || s.pendingSubmit != nil)
        .opacity(done == 0 || s.submitted || s.pendingSubmit != nil ? 0.45 : 1)
      if !store.saveState.isEmpty {
        Text(store.saveState).font(Typo.label).foregroundColor(Palette.text3)
      }
    }
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

  // MARK: week

  private func weekCard(_ w: TrainingWeek) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: tr("本周", "This week"))
        if let n = w.planName { Text(n).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1) }
      }
      Card {
        VStack(alignment: .leading, spacing: 12) {
          HStack(spacing: 4) {
            ForEach(w.days) { d in
              Button { detailDay = d.date } label: {
                VStack(spacing: 4) {
                  Text(Self.weekdayName(RecordDay.weekday(d.date))).font(.system(size: 11, weight: .medium)).foregroundColor(Palette.text3)
                  Text(String(Int(d.date.suffix(2)) ?? 0)).font(.system(size: 15, weight: d.date == store.today ? .bold : .medium).monospacedDigit())
                    .foregroundColor(d.date > store.today ? Palette.text3 : Palette.text)
                  Circle().fill(d.trained ? (TrainingMonth.tone(d.intensity) ?? .good).color : Palette.surface2).frame(width: 7, height: 7)
                  Text(d.label).font(.system(size: 10)).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.7)
                }
                .frame(maxWidth: .infinity).padding(.vertical, 6)
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(d.date == store.today ? Palette.bg : .clear))
              }
              .buttonStyle(PressScale())
            }
          }
          if let r = w.readiness { Badge(label: r.label, tone: r.tone) }
          if !w.volume.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
              HStack(spacing: 6) {
                ForEach(w.volume, id: \.0) { g in Badge(label: tr("\(g.0) \(g.1)组", "\(g.0) \(g.1) sets"), tone: g.2 ? .good : .text) }
              }
            }
          }
        }
      }
    }
  }

  static func weekdayName(_ wd: Int) -> String {
    let names = [tr("一", "Mo"), tr("二", "Tu"), tr("三", "We"), tr("四", "Th"), tr("五", "Fr"), tr("六", "Sa"), tr("日", "Su")]
    return names[max(0, min(6, wd - 1))]
  }

  // MARK: restrictions

  @ViewBuilder private var restrictions: some View {
    let rules = mergedRules
    if !rules.isEmpty {
      VStack(alignment: .leading, spacing: 10) {
        SectionHeader(title: tr("动作限制", "Exercise limits"))
        RowGroup {
          ForEach(Array(rules.enumerated()), id: \.element.id) { n, r in
            if n > 0 { RowDivider(inset: 0) }
            HStack(alignment: .top, spacing: 10) {
              Icon(r.banned ? .ban : .triangleAlert, size: 18).foregroundColor(r.banned ? Palette.bad : Palette.mid).padding(.top, 2)
              VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                  Text(r.name).font(Typo.body).foregroundColor(Palette.text).strikethrough(r.banned, color: Palette.text3)
                  Badge(label: r.banned ? tr("禁用", "Banned") : tr("注意", "Caution"), tone: r.banned ? .bad : .mid)
                }
                if let why = r.reason ?? r.cues { Text(why).font(Typo.caption).foregroundColor(Palette.text2) }
              }
              Spacer(minLength: 0)
            }
            .padding(.vertical, 11)
          }
        }
      }
    }
  }

  /// The user's own list plus the week's cautions (plan exercises with a restriction), once each.
  private var mergedRules: [ExerciseRule] {
    var out = store.rules
    for c in store.week?.cautions ?? [] where !out.contains(where: { $0.ref == c.slug || $0.name == c.name }) {
      out.append(ExerciseRule(ref: c.slug, name: c.name, banned: c.banned, reason: c.reason, cues: c.cues))
    }
    return out
  }
}

private struct DayRef: Identifiable {
  let day: String
  var id: String { day }
}

/// One planned exercise: name + restriction, prescription; opened, its sets (reps · kg · done) with add / remove.
struct ExerciseCard: View {
  let e: PlannedExercise
  let rows: [SetRow]
  let canLog: Bool
  let open: Bool
  let toggle: () -> Void
  let edit: ((inout [SetRow]) -> Void) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      Button(action: toggle) {
        HStack(spacing: 10) {
          VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
              Text(e.name).font(Typo.bodyStrong).foregroundColor(e.banned ? Palette.text2 : Palette.text)
                .strikethrough(e.banned, color: Palette.text3).lineLimit(2).multilineTextAlignment(.leading)
              if e.banned { Badge(label: tr("禁用", "Banned"), tone: .bad) } else if e.caution { Badge(label: tr("注意", "Caution"), tone: .mid) }
            }
            Text(e.banned ? tr("不要做", "Don't do it") : e.prescription).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
          }
          Spacer(minLength: 4)
          if !e.banned, !rows.isEmpty {
            let done = rows.filter(\.done).count
            Text("\(done)/\(rows.count)").font(Typo.caption.monospacedDigit())
              .foregroundColor(done >= rows.count ? Palette.good : Palette.text2)
          }
          Icon(open ? .chevronDown : .chevronRight, size: 14).foregroundColor(Palette.text3)
        }
        .padding(14)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      if open {
        VStack(alignment: .leading, spacing: 8) {
          if (e.banned || e.caution), let why = e.reason ?? e.cues {
            HStack(alignment: .top, spacing: 8) {
              Icon(e.banned ? .ban : .triangleAlert, size: 16).foregroundColor(e.banned ? Palette.bad : Palette.mid)
              Text(why).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
            }
          } else if let cues = e.cues {
            Text(cues).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
          }
          if !e.banned {
            setHeader
            ForEach(Array(rows.enumerated()), id: \.offset) { i, r in setRow(i, r) }
            if let last = e.last { Text(tr("上次 \(last)", "Last \(last)")).font(Typo.label).foregroundColor(Palette.text3) }
            if canLog {
              Button { edit { $0.append($0.last.map { SetRow(reps: $0.reps, weightKg: $0.weightKg, durationS: $0.durationS, done: false) } ?? e.target) } } label: {
                HStack(spacing: 6) {
                  Icon(.plus, size: 16)
                  Text(tr("一组", "Set"))
                }
                .font(.system(size: 14, weight: .medium)).foregroundColor(Palette.text)
                .padding(.vertical, 8).padding(.horizontal, 14)
                .background(Capsule().fill(Palette.bg))
              }
              .buttonStyle(PressScale())
            }
          }
        }
        .padding(.horizontal, 14).padding(.bottom, 14)
      }
    }
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }

  private var setHeader: some View {
    HStack(spacing: 8) {
      Text(tr("组", "Set")).frame(width: 26)
      if e.timed { Text(tr("秒", "Sec")).frame(maxWidth: .infinity) } else {
        Text(tr("次数", "Reps")).frame(maxWidth: .infinity)
        if e.weighted { Text(e.perSide ? tr("kg · 每侧", "kg · side") : "kg").frame(maxWidth: .infinity) }
      }
      Color.clear.frame(width: 40)
      Color.clear.frame(width: canLog ? 28 : 0)
    }
    .font(.system(size: 11, weight: .medium)).foregroundColor(Palette.text3)
  }

  private func setRow(_ i: Int, _ r: SetRow) -> some View {
    HStack(spacing: 8) {
      Text("\(i + 1)").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).frame(width: 26)
      if e.timed {
        field(r.durationS.map { RecordFormat.num($0) } ?? "", placeholder: RecordFormat.num(e.target.durationS), decimal: false) { v in
          edit { if i < $0.count { $0[i].durationS = Double(v) } }
        }
      } else {
        field(r.reps.map(String.init) ?? "", placeholder: e.target.reps.map(String.init) ?? "", decimal: false) { v in
          edit { if i < $0.count { $0[i].reps = Int(v) } }
        }
        if e.weighted {
          field(r.weightKg.map { RecordFormat.num($0, 2) } ?? "", placeholder: RecordFormat.num(e.target.weightKg, 1), decimal: true) { v in
            edit { if i < $0.count { $0[i].weightKg = Double(v.replacingOccurrences(of: ",", with: ".")) } }
          }
        }
      }
      Button { edit { if i < $0.count { $0[i].done.toggle() } } } label: {
        Icon(.check, size: 16).foregroundColor(r.done ? Palette.bg : Palette.text3)
          .frame(width: 40, height: 38)
          .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(r.done ? Palette.good : Palette.bg))
      }
      .buttonStyle(PressScale())
      .disabled(!canLog)
      .accessibilityLabel(tr("第 \(i + 1) 组做完了", "Set \(i + 1) done"))
      .accessibilityAddTraits(r.done ? .isSelected : [])
      if canLog {
        Button { edit { if i < $0.count { $0.remove(at: i) } } } label: {
          Icon(.x, size: 14).foregroundColor(Palette.text3).frame(width: 28, height: 38)
        }
        .accessibilityLabel(tr("删掉这组", "Remove this set"))
      }
    }
  }

  private func field(_ value: String, placeholder: String, decimal: Bool, set: @escaping (String) -> Void) -> some View {
    SetField(value: value, placeholder: placeholder, decimal: decimal, readOnly: !canLog, commit: set)
  }
}

/// A number box of a set row; writes on every change (the store saves a moment after the last one).
private struct SetField: View {
  let value: String
  let placeholder: String
  let decimal: Bool
  let readOnly: Bool
  let commit: (String) -> Void
  @State private var text = ""

  var body: some View {
    TextField(placeholder, text: $text)
      .keyboardType(decimal ? .decimalPad : .numberPad)
      .multilineTextAlignment(.center)
      .font(.system(size: 15, weight: .medium).monospacedDigit())
      .frame(maxWidth: .infinity, minHeight: 38)
      .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Palette.bg))
      .disabled(readOnly)
      .onAppear { text = value }
      .onChange(of: value) { v in if v != text, Double(v.replacingOccurrences(of: ",", with: ".")) != Double(text.replacingOccurrences(of: ",", with: ".")) { text = v } }
      .onChange(of: text) { t in if t != value { commit(t) } }
  }
}

/// One muscle: sets against the weekly range, volume, sports, last week.
private struct MuscleSheet: View {
  let m: MuscleLoad.Muscle
  let range: MuscleLoad.Range

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack {
        Badge(label: MuscleLoad.statusTitle(m.status[range] ?? "none"), tone: MuscleLoad.tone(m.status[range] ?? "none"))
        Spacer()
      }
      .padding(.bottom, 8)
      KeyValueRow(label: range.title, value: tr("\(RecordFormat.num(m.sets[range], 1)) 组", "\(RecordFormat.num(m.sets[range], 1)) sets"))
      if let lo = m.low, let hi = m.high {
        KeyValueRow(label: tr("每周推荐", "Weekly range"), value: tr("\(RecordFormat.num(lo))–\(RecordFormat.num(hi)) 组", "\(RecordFormat.num(lo))–\(RecordFormat.num(hi)) sets"))
      }
      KeyValueRow(label: tr("训练量", "Volume"), value: "\(RecordFormat.num(m.volume[range])) kg")
      if let p = m.prevWeek { KeyValueRow(label: tr("上周", "Last week"), value: tr("\(RecordFormat.num(p, 1)) 组", "\(RecordFormat.num(p, 1)) sets")) }
      if let s = m.sportMin[range], s > 0 { KeyValueRow(label: tr("运动", "Sports"), value: tr("\(RecordFormat.num(s)) 分钟", "\(RecordFormat.num(s)) min")) }
    }
  }
}
