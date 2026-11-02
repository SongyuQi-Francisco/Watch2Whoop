import SwiftUI

/// The Today tab's two training cards (app.js dayCardHtml + trainingSheet, muscle-load.js card): what is planned or was
/// trained on the day, and this week's muscle map. Both only show and link; logging stays on 记录 → 训练.

/// The today document's `training` block (web/views.py training_today).
struct TodayTraining: Equatable {
  struct Tag: Equatable, Identifiable {
    var text: String
    var tone: Tone
    var id: String { text }
  }

  var title: String
  var tags: [Tag]
  var failed: Bool
  var restDay: Bool
  var planned: [PlanSession]
  var logged: [WeekExercise]
  /// The sessions the template had before this date was changed (`change.from`), nil when it was not changed.
  var changedFrom: [String]?
  var readiness: Tag?
  var readinessAdvice: String?
  var logUnreadable: Bool

  /// nil: no share of the training (`forbidden`), no card.
  static func parse(_ t: [String: Any]?) -> TodayTraining? {
    guard let t, (t["code"] as? String) != "forbidden" else { return nil }
    let failed = t["error"] != nil
    let planned = ((t["planned"] as? [[String: Any]]) ?? []).map(PlanSession.parse)
    let loggedRaw = (t["logged"] as? [[String: Any]]) ?? []
    let logged = loggedRaw.flatMap { ($0["exercises"] as? [[String: Any]]) ?? [] }.compactMap(WeekExercise.parse)
    let rest = (t["rest_day"] as? Bool) ?? false
    var title = tr("训练", "Training")
    var tags: [Tag] = []
    if failed {
      tags.append(Tag(text: "—", tone: .text))
    } else if planned.isEmpty, !loggedRaw.isEmpty {   // no plan but trained that day: the title says what was done
      let names = loggedRaw.compactMap { $0["name"] as? String }.filter { !$0.isEmpty }
      if !names.isEmpty { title = names.joined(separator: " · ") }
      tags.append(Tag(text: tr("\(logged.count) 动作", "\(logged.count) exercises"), tone: .text))
    } else if planned.isEmpty {
      title = rest ? tr("休息日", "Rest day") : tr("无计划", "No plan")
    } else {
      title = planned.map(\.name).joined(separator: " · ")
      let all = planned.flatMap(\.exercises)
      tags.append(Tag(text: tr("\(all.count) 动作", "\(all.count) exercises"), tone: .text))
      let caution = all.filter { $0.status != "active" }.count, up = all.filter(\.hasSuggestion).count
      if caution > 0 { tags.append(Tag(text: tr("注意 \(caution)", "Caution \(caution)"), tone: .mid)) }
      if up > 0 { tags.append(Tag(text: tr("加重 \(up)", "Heavier \(up)"), tone: .good)) }
    }
    let change = t["change"] as? [String: Any]
    if change != nil { tags.append(Tag(text: tr("已调整", "Changed"), tone: .mid)) }
    if !loggedRaw.isEmpty { tags.append(Tag(text: tr("已练", "Done"), tone: .good)) }
    var readiness: Tag?
    let r = t["readiness"] as? [String: Any]
    if let tier = r?["tier"] as? String, tier != "unknown", let advice = r?["advice"] as? String, !advice.isEmpty {
      readiness = Tag(text: (r?["label"] as? String) ?? tr("恢复自调节", "Recovery-based"), tone: tier == "green" ? .good : tier == "red" ? .bad : .mid)
    }
    return TodayTraining(title: title, tags: tags, failed: failed, restDay: rest, planned: planned, logged: logged,
                         changedFrom: change.map { ($0["from"] as? [String]) ?? [] }, readiness: readiness,
                         readinessAdvice: readiness == nil ? nil : r?["advice"] as? String, logUnreadable: t["sparky_error"] != nil && !planned.isEmpty)
  }
}

extension MuscleLoad {
  /// What the small card says (muscle-load.js card).
  var weekTotalSets: Double { muscles.reduce(0) { $0 + ($1.sets[.week] ?? 0) } }
  var weekSportMinutes: Double { (sports[.week] ?? []).reduce(0) { $0 + $1.1 } }

  var todayCardLine: String {
    let trained = muscles.filter(\.trainedToday).map(\.name)
    if !trained.isEmpty {
      let names = trained.prefix(4).joined(separator: tr("、", ", "))
      return "\(tr("今天练了", "Trained today"))\(tr("：", ": "))\(names)"
    }
    let low = muscles.filter { ($0.sets[.week] ?? 0) > 0 && $0.status[.week] == "low" }.count
    return low > 0 ? tr("\(low) 块肌群偏少", "\(low) muscle groups are low") : tr("本周还没有逐组记录", "No sets logged this week")
  }
}

/// A row of a day's plan: name, restriction, suggestion; sets and weight (app.js exerciseRow).
struct PlanExerciseLabel: View {
  let e: WeekExercise

  var body: some View {
    VStack(alignment: .leading, spacing: 3) {
      HStack(spacing: 6) {
        Text(e.name).font(Typo.body).foregroundColor(e.status == "banned" ? Palette.text2 : Palette.text)
          .strikethrough(e.status == "banned", color: Palette.text3).lineLimit(2).multilineTextAlignment(.leading)
        ExerciseStatusBadge(status: e.status)
        if let s = e.suggestion { Badge(label: s.text, tone: s.tone) }
      }
      HStack(spacing: 6) {
        if let t = e.setsText { Text(t).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1) }
        if let c = e.load?.chip { Badge(label: c.text, tone: c.tone) }
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}

/// 训练: the day's title and tags, 训练历史 next to the heading.
struct TodayTrainingCard: View {
  let training: TodayTraining
  let open: () -> Void
  let history: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: tr("训练", "Training"))
        Button(action: history) {
          HStack(spacing: 4) {
            Icon(.calendar, size: 13)
            Text(tr("训练历史", "Training history"))
          }
          .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2).fixedSize()
        }
        .buttonStyle(PressScale())
      }
      Button(action: open) {
        Card {
          VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
              Text(training.title).font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(2).multilineTextAlignment(.leading)
              Spacer(minLength: 4)
              Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
            }
            if !training.tags.isEmpty {
              HStack(spacing: 6) { ForEach(training.tags) { Badge(label: $0.text, tone: $0.tone) } }
            }
          }
        }
      }
      .buttonStyle(PressScale())
    }
  }
}

/// 肌肉负荷: this week's map, how many sets, what today trained; tap → 记录 → 训练. Not on a look-back day.
struct TodayMuscleCard: View {
  let muscle: MuscleLoad
  let open: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: tr("肌肉负荷", "Muscle load"))
        Text(tr("本周", "This week")).font(Typo.label).foregroundColor(Palette.text2).fixedSize()
      }
      Button(action: open) {
        Card {
          HStack(spacing: 12) {
            bodies
            VStack(alignment: .leading, spacing: 3) {
              if muscle.weekTotalSets > 0 {
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                  Text(RecordFormat.num(muscle.weekTotalSets)).font(.system(size: 22, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
                  Text(tr("组", "sets")).font(Typo.unit).foregroundColor(Palette.text2)
                }
              } else {
                Text("—").font(.system(size: 22, weight: .semibold)).foregroundColor(Palette.text3)
              }
              Text(muscle.todayCardLine).font(.system(size: 12)).foregroundColor(Palette.text2).lineLimit(2).multilineTextAlignment(.leading)
              if muscle.weekSportMinutes > 0 {
                let minutes = RecordFormat.num(muscle.weekSportMinutes)
                Text(tr("运动 \(minutes) 分钟", "Activity \(minutes) min")).font(.system(size: 12).monospacedDigit()).foregroundColor(Palette.text2)
              }
            }
            Spacer(minLength: 0)
            Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
          }
        }
      }
      .buttonStyle(PressScale())
    }
  }

  private var bodies: some View {
    let regions = muscle.regions(.week)
    return HStack(spacing: 2) {
      ForEach([true, false], id: \.self) { front in
        if let side = MuscleBodies.shared.side(sex: muscle.sex, front: front) {
          MuscleBodyView(side: side, regions: regions, lineScale: 0.6)
        }
      }
    }
    .frame(width: 112, height: 130)
    .allowsHitTesting(false)   // a tap anywhere on the card opens the page
  }
}

/// 训练 · 日期: readiness, the day's plan with each exercise's guide, what was logged, a way to the logging page.
struct TodayTrainingSheet: View {
  let training: TodayTraining
  let date: String?
  let canLog: Bool
  let close: () -> Void
  let openLog: () -> Void
  @StateObject private var store: TrainingStore
  @State private var toast: Toast?

  init(session: AccountSession, training: TodayTraining, date: String?, canLog: Bool, close: @escaping () -> Void, openLog: @escaping () -> Void) {
    self.training = training
    self.date = date
    self.canLog = canLog
    self.close = close
    self.openLog = openLog
    _store = StateObject(wrappedValue: TrainingStore(session: session))
  }

  var body: some View {
    let nav = TrainingNav(store: store, finish: { _ in close() }, say: { toast = $0 })
    NavigationStack {
      TrainingPage(nav: nav, title: date.map { "\(tr("训练", "Training")) · \(RecordDay.short($0))" } ?? tr("训练", "Training")) { content(nav) }
    }
    .tint(Palette.text)
    .toast($toast)
    .presentationDragIndicator(.visible)
  }

  private func content(_ nav: TrainingNav) -> some View {
    VStack(alignment: .leading, spacing: 14) {
      if let r = training.readiness {
        VStack(alignment: .leading, spacing: 6) {
          Badge(label: r.text, tone: r.tone)
          if let a = training.readinessAdvice { Text(a).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true) }
        }
      }
      if training.logUnreadable { Badge(label: tr("训练记录读不到 · 重量为估算", "Training log unreadable · weights are estimates"), tone: .mid) }
      if let from = training.changedFrom {
        KeyValueRow(label: tr("原计划", "Original plan"), value: from.isEmpty ? tr("休息", "Rest") : from.joined(separator: " · "))
      }
      if training.failed {
        EmptyNote(text: tr("暂无", "None"))
      } else if training.planned.isEmpty {
        EmptyNote(text: training.restDay ? tr("休息", "Rest day") : tr("无计划", "No plan"))
      }
      ForEach(training.planned) { s in
        VStack(alignment: .leading, spacing: 8) {
          if !(s.exercises.count == 1 && s.exercises[0].name == s.name) { Text(s.name).font(Typo.section).foregroundColor(Palette.text2) }
          rows(s.exercises, nav)
        }
      }
      if !training.logged.isEmpty {
        VStack(alignment: .leading, spacing: 8) {
          SectionHeader(title: tr("已记录", "Logged"))
          rows(training.logged, nav)
        }
      }
      if canLog, !training.planned.isEmpty {
        PrimaryButton(title: tr("逐组记录", "Log sets"), action: openLog)
      }
    }
  }

  private func rows(_ list: [WeekExercise], _ nav: TrainingNav) -> some View {
    SheetRows {
      ForEach(Array(list.enumerated()), id: \.offset) { i, e in
        if i > 0 { RowDivider(inset: 0) }
        NavigationLink { ExerciseDetailView(nav: nav, slug: e.slug ?? e.name) } label: {
          HStack(spacing: 8) {
            PlanExerciseLabel(e: e)
            Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
          }
          .padding(.vertical, 10)
          .contentShape(Rectangle())
        }
        .buttonStyle(PressScale())
      }
    }
  }
}
