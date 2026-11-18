import SwiftUI

/// The muscle load map with its range control and list. `open` and `goals` nil = read-only (someone else's page).
struct MuscleLoadCard: View {
  let load: MuscleLoad
  var open: ((MuscleLoad.Muscle, MuscleLoad.Range) -> Void)?
  var goals: (() -> Void)?
  @State private var range: MuscleLoad.Range = .week
  @State private var allMuscles = false

  var body: some View {
    let m = load
    VStack(alignment: .leading, spacing: 10) {
      SectionHeader(title: tr("肌肉负荷", "Muscle load"))
      Card {
        VStack(spacing: 12) {
          SegmentedControl(items: MuscleLoad.Range.card.map { ($0, $0.title) }, selection: $range)
          let regions = m.regions(range)
          HStack(spacing: 12) {
            ForEach([true, false], id: \.self) { front in
              VStack(spacing: 4) {
                if let side = MuscleBodies.shared.side(sex: m.sex, front: front) {
                  MuscleBodyView(side: side, regions: regions) { key in openMuscle(m, key) }
                    .frame(height: 260)
                }
                Text(front ? tr("正面", "Front") : tr("背面", "Back")).font(Typo.label).foregroundColor(Palette.text2)
              }
              .frame(maxWidth: .infinity)
            }
          }
          legend
          if !m.sexSet && goals != nil {
            Text(tr("按男性图显示，可在个人资料里设置性别", "Shown on the male body; set your sex in your profile"))
              .font(Typo.caption).foregroundColor(Palette.text3).frame(maxWidth: .infinity, alignment: .leading)
          }
          if let sports = sportsLine(m) {
            Text(sports).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).frame(maxWidth: .infinity, alignment: .leading)
          }
          let rows = m.muscles.filter { ($0.sets[range] ?? 0) > 0 || ($0.sportMin[range] ?? 0) > 0 }
            .sorted { ($0.sets[range] ?? 0) > ($1.sets[range] ?? 0) }
          if rows.isEmpty {
            EmptyNote(text: tr("这段时间没有逐组记录", "No sets logged in this range"))
          }
          VStack(spacing: 0) {
            ForEach(allMuscles ? rows : Array(rows.prefix(5))) { r in
              Button { open?(r, range) } label: {
                VStack(alignment: .leading, spacing: 3) {
                  HStack(spacing: 8) {
                    Text(r.name).font(Typo.bodyStrong).foregroundColor(Palette.text)
                    if (r.sets[range] ?? 0) > 0 {
                      Badge(label: MuscleLoad.statusTitle(r.status[range] ?? "none"), tone: MuscleLoad.tone(r.status[range] ?? "none"))
                    } else {
                      Badge(label: tr("运动", "Sports"))
                    }
                    Spacer()
                    Text(tr("\(RecordFormat.num(r.sets[range], 1)) 组", "\(RecordFormat.num(r.sets[range], 1)) sets"))
                      .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
                    Text("\(RecordFormat.num(r.volume[range])) kg")
                      .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
                    if open != nil { Icon(.chevronRight, size: 14).foregroundColor(Palette.text3) }
                  }
                  Text(rowDetail(r)).font(.system(size: 12).monospacedDigit()).foregroundColor(Palette.text3)
                }
                .padding(.vertical, 10)
                .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
                .contentShape(Rectangle())
              }
              .buttonStyle(PressScale())
              .disabled(open == nil)
            }
          }
          if rows.count > 5 {
            Button(allMuscles ? tr("收起", "Less") : tr("全部 \(rows.count) 个肌群", "All \(rows.count) muscles")) { withAnimation { allMuscles.toggle() } }
              .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
          }
          let rest = m.muscles.filter { ($0.sets[range] ?? 0) <= 0 && ($0.sportMin[range] ?? 0) <= 0 }.map(\.name)
          if !rest.isEmpty, !rows.isEmpty {
            Text("\(tr("没练", "None"))\(tr("：", ": "))\(rest.joined(separator: tr("、", ", ")))")
              .font(.system(size: 12)).foregroundColor(Palette.text3).frame(maxWidth: .infinity, alignment: .leading)
          }
          if !m.unmatched.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
              Text(unmatchedLine(m)).font(Typo.caption).foregroundColor(Palette.text3).frame(maxWidth: .infinity, alignment: .leading)
              AskJarvisButton(title: tr("让 Jarvis 补进动作库", "Ask Jarvis to add them to the library"), prompt: MusclePrompts.fill(m.unmatched), background: Palette.bg)
            }
          }
          AskJarvisButton(title: tr("让 Jarvis 分析我的肌群训练", "Ask Jarvis to analyse my muscle training"), prompt: MusclePrompts.analyse, background: Palette.bg)
          if let goals { SecondaryButton(icon: .settings, title: tr("训练设置", "Training settings"), background: Palette.bg, action: goals) }
        }
      }
    }
  }

  private func openMuscle(_ m: MuscleLoad, _ key: String) {
    if let row = m.muscles.first(where: { $0.key == key }) { open?(row, range) }
  }

  private func sportsLine(_ m: MuscleLoad) -> String? {
    let list = m.sports[range] ?? []
    guard !list.isEmpty else { return nil }
    let items = list.map { s, minutes -> String in
      let text = "\(s) \(tr("\(RecordFormat.num(minutes)) 分钟", "\(RecordFormat.num(minutes)) min"))"
      return m.unmappedSports.contains(s) ? "\(text) (\(tr("不计入肌群", "not counted for muscles")))" : text
    }
    return "\(tr("运动", "Sports"))\(tr("：", ": "))\(items.joined(separator: " · "))"
  }

  /// 每周推荐 8–16 组 · 上周 6 组 · 较上周 +2 · 运动 30 分钟.
  private func rowDetail(_ r: MuscleLoad.Muscle) -> String {
    var parts: [String] = []
    if let lo = r.low, let hi = r.high { parts.append(tr("每周推荐 \(RecordFormat.num(lo))–\(RecordFormat.num(hi)) 组", "Target \(RecordFormat.num(lo))–\(RecordFormat.num(hi)) sets a week")) }
    if range == .week, let p = r.prevWeek {
      parts.append(tr("上周 \(RecordFormat.num(p, 1)) 组", "Last week \(RecordFormat.num(p, 1)) sets"))
      if let d = r.delta, d != 0 {
        let v = "\(d > 0 ? "+" : "")\(RecordFormat.num(d, 1))"
        parts.append(tr("较上周 \(v)", "vs last week \(v)"))
      }
    }
    if let s = r.sportMin[range], s > 0 { parts.append(tr("运动 \(RecordFormat.num(s)) 分钟", "Activity \(RecordFormat.num(s)) min")) }
    return parts.joined(separator: " · ")
  }

  private func unmatchedLine(_ m: MuscleLoad) -> String {
    let n = m.unmatched.count
    let names = m.unmatched.prefix(6).joined(separator: tr("、", ", ")) + (n > 6 ? "…" : "")
    return "\(tr("\(n) 个动作没对上动作库，没有计入", "\(n) exercises not in the library, not counted"))\(tr("：", ": "))\(names)"
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
      HStack(spacing: 4) {
        RoundedRectangle(cornerRadius: 3).fill(Palette.text.opacity(0.22)).frame(width: 10, height: 10)
        Text(tr("运动", "Sports"))
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
}

/// The week strip: plan label per day, trained days, the week's numbers. The links nil = read-only (someone else's page).
struct TrainingWeekCard: View {
  let week: TrainingWeek
  let today: String
  let openDay: (String) -> Void
  var split: (() -> Void)?
  var library: (() -> Void)?
  var connectLog: (() -> Void)?

  var body: some View {
    let w = week
    return VStack(alignment: .leading, spacing: 10) {
      HStack(spacing: 14) {
        SectionHeader(title: tr("本周", "This week"))
        if !w.splits.isEmpty, let split {
          weekLink(w.hasPlan ? tr("切换分化", "Change split") : tr("选周计划", "Pick a weekly plan"), action: split)
        }
        if let library { weekLink(tr("动作库", "Exercise library"), action: library) }
      }
      if let n = w.planName { Text(n).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1) }
      Card {
        VStack(alignment: .leading, spacing: 12) {
          HStack(spacing: 4) {
            ForEach(w.days) { d in
              Button { openDay(d.date) } label: {
                VStack(spacing: 4) {
                  Text(RecordDay.weekdayName(RecordDay.weekday(d.date))).font(.system(size: 11, weight: .medium)).foregroundColor(Palette.text3)
                  Text(String(Int(d.date.suffix(2)) ?? 0)).font(.system(size: 15, weight: d.date == today ? .bold : .medium).monospacedDigit())
                    .foregroundColor(d.date > today ? Palette.text3 : Palette.text)
                  Circle().fill(d.trained ? (TrainingMonth.tone(d.intensity) ?? .good).color : Palette.surface2).frame(width: 7, height: 7)
                  Text(d.label).font(.system(size: 10)).foregroundColor(d.changedFrom != nil ? Palette.mid : Palette.text2).lineLimit(1).minimumScaleFactor(0.7)
                }
                .frame(maxWidth: .infinity).padding(.vertical, 6)
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(d.date == today ? Palette.bg : .clear))
              }
              .buttonStyle(PressScale())
            }
          }
          HStack(spacing: 6) {
            WeekNumber(label: tr("训练", "Trained"), value: String(w.trainedDays), unit: tr("天", "d"))
            WeekNumber(label: tr("时长", "Time"), value: w.totalMinutes > 0 ? RecordFormat.num(w.totalMinutes / 60, 1) : nil, unit: "h")
            WeekNumber(label: tr("消耗", "Burned"), value: w.totalKcal.map { RecordFormat.num($0) }, unit: "kcal")
            WeekNumber(label: tr("计划", "Plan"), value: w.plannedDays > 0 ? String(w.plannedDays) : nil, unit: tr("天", "d"))
          }
          if let r = w.readiness { Badge(label: r.label, tone: r.tone) }
          if !w.volume.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
              HStack(spacing: 6) {
                ForEach(w.volume, id: \.0) { g in Badge(label: tr("\(g.0) \(g.1)组", "\(g.0) \(g.1) sets"), tone: g.2 ? .good : .text) }
              }
            }
          }
          if w.loadHistoryError, w.hasPlan { Badge(label: tr("训练记录读不到 · 重量为估算", "Training log unreadable · weights are estimates"), tone: .mid) }
        }
      }
      if let log = w.log, !log.connected, let connectLog {
        RowGroup {
          Button(action: connectLog) {
            HStack(spacing: 8) {
              Text(tr("训练记录", "Training log")).font(Typo.body).foregroundColor(Palette.text)
              Spacer()
              Badge(label: log.word.text, tone: log.word.tone)
              Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
            }
            .padding(.vertical, 12)
            .contentShape(Rectangle())
          }
          .buttonStyle(PressScale())
        }
      }
    }
  }

  private func weekLink(_ title: String, action: @escaping () -> Void) -> some View {
    Button(title, action: action)
      .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
      .lineLimit(1)
      .fixedSize()
  }
}

/// The exercises a user should avoid or be careful with. `open` nil = read-only (someone else's page).
struct ExerciseLimitsCard: View {
  let rules: [ExerciseRule]
  var open: ((ExerciseRule) -> Void)?

  var body: some View {
    if !rules.isEmpty {
      VStack(alignment: .leading, spacing: 10) {
        SectionHeader(title: tr("动作限制", "Exercise limits"))
        RowGroup {
          ForEach(Array(rules.enumerated()), id: \.element.id) { n, r in
            if n > 0 { RowDivider(inset: 0) }
            Button { open?(r) } label: {
              HStack(alignment: .top, spacing: 10) {
                Icon(r.banned ? .ban : .triangleAlert, size: 18).foregroundColor(r.banned ? Palette.bad : Palette.mid).padding(.top, 2)
                VStack(alignment: .leading, spacing: 3) {
                  HStack(spacing: 6) {
                    Text(r.name).font(Typo.body).foregroundColor(Palette.text).strikethrough(r.banned, color: Palette.text3)
                    Badge(label: r.banned ? tr("禁用", "Banned") : tr("注意", "Caution"), tone: r.banned ? .bad : .mid)
                  }
                  if let why = r.reason ?? r.cues { Text(why).font(Typo.caption).foregroundColor(Palette.text2).multilineTextAlignment(.leading) }
                }
                Spacer(minLength: 0)
                if open != nil { Icon(.chevronRight, size: 14).foregroundColor(Palette.text3).padding(.top, 4) }
              }
              .padding(.vertical, 11)
              .contentShape(Rectangle())
            }
            .buttonStyle(PressScale())
            .disabled(open == nil)
          }
        }
      }
    }
  }
}

extension ExerciseRule {
  /// The user's own list plus the week's cautions (plan exercises with a restriction), once each.
  static func merged(_ rules: [ExerciseRule], week: TrainingWeek?) -> [ExerciseRule] {
    var out = rules
    for c in week?.cautions ?? [] {
      if let i = out.firstIndex(where: { $0.ref == c.slug }) {
        out[i].name = c.name   // the plan's name, in the app's language
      } else if !out.contains(where: { $0.name == c.name }) {
        out.append(ExerciseRule(ref: c.slug, name: c.name, banned: c.banned, reason: c.reason, cues: c.cues))
      }
    }
    return out
  }
}

/// A number of the week (app.js msum).
private struct WeekNumber: View {
  let label: String
  let value: String?
  let unit: String

  var body: some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(label).font(.system(size: 11, weight: .medium)).foregroundColor(Palette.text2).lineLimit(1)
      HStack(alignment: .firstTextBaseline, spacing: 2) {
        Text(value ?? "—").font(.system(size: 17, weight: .semibold).monospacedDigit()).foregroundColor(value == nil ? Palette.text3 : Palette.text)
          .lineLimit(1).minimumScaleFactor(0.6)
        if value != nil { Text(unit).font(Typo.unit).foregroundColor(Palette.text2).lineLimit(1) }
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .accessibilityElement(children: .combine)
  }
}

/// One planned exercise: name + restriction, prescription; opened, its sets (reps · kg · done) with add / remove.
struct ExerciseCard: View {
  let e: PlannedExercise
  let rows: [SetRow]
  let canLog: Bool
  let open: Bool
  let toggle: () -> Void
  let edit: ((inout [SetRow]) -> Void) -> Void
  /// Swap a name the library does not know for one of its candidates (nil: not offered, e.g. a plan that is not editable here).
  var swapTo: ((SwapCandidate) -> Void)? = nil
  /// For the demo picture; nil draws none.
  var baseURL: URL? = nil
  var sex = "male"
  /// 动作指导 / 看替代动作 (nil: a name the library does not know has no page).
  var guide: (() -> Void)? = nil
  /// × on the card: this day only, or from the session template (nil: the plan is not editable here).
  var remove: (() -> Void)? = nil

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack(spacing: 0) {
        Button(action: toggle) {
          HStack(spacing: 10) {
            if let baseURL {
              PlanExerciseThumb(thumb: e.thumb, primary: e.primaryMuscles, secondary: e.secondaryMuscles, base: baseURL, sex: sex)
            }
            VStack(alignment: .leading, spacing: 3) {
              HStack(spacing: 6) {
                Text(e.name).font(Typo.bodyStrong).foregroundColor(e.banned ? Palette.text2 : Palette.text)
                  .strikethrough(e.banned, color: Palette.text3).lineLimit(2).multilineTextAlignment(.leading)
                if e.banned { Badge(label: tr("禁用", "Banned"), tone: .bad) } else if e.caution { Badge(label: tr("注意", "Caution"), tone: .mid) }
                if e.unresolved { Badge(label: tr("认不出", "Not recognised"), tone: .mid) }
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
        if let remove {
          Button(action: remove) {
            Icon(.x, size: 16).foregroundColor(Palette.text3).frame(width: 40, height: 44)
          }
          .padding(.trailing, 4)
          .accessibilityLabel(tr("移除这个动作", "Remove this exercise"))
        }
      }
      if e.unresolved, let swapTo, !e.candidates.isEmpty {
        HStack(spacing: 8) {
          Text(tr("换成", "Swap to")).font(Typo.label).foregroundColor(Palette.text3)
          ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
              ForEach(e.candidates) { c in
                Button { swapTo(c) } label: {
                  Text(c.name).lineLimit(1)
                    .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text)
                    .padding(.vertical, 6).padding(.horizontal, 10)
                    .background(Capsule().fill(Palette.bg))
                }
                .buttonStyle(PressScale())
                .accessibilityLabel(tr("换成 \(c.name)", "Swap to \(c.name)"))
              }
            }
          }
        }
        .padding(.horizontal, 14).padding(.bottom, 12)
      }
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
          }
          HStack(spacing: 8) {
            if canLog, !e.banned {
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
            if let guide {
              Button(action: guide) {
                HStack(spacing: 6) {
                  Icon(.bookOpen, size: 16)
                  Text(e.banned ? tr("看替代动作", "See alternatives") : tr("动作指导", "How to"))
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
      Button { edit { if i < $0.count { $0[i].tick(target: e.target, timed: e.timed) } } } label: {
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
