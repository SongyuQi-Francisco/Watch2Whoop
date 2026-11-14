import SwiftUI

/// The weekly muscle load map (docs/design/2.0.1 `13-muscle-week`), pushed from the 肌肉 face: front and back coloured by
/// each muscle's sets against its weekly range (`train` dim = under, `good` = in range, `bad` = over, grey = not trained),
/// 「9/17 达标」 with the legend, one row per body area (sets as a bar on the summed range). 「全部肌群」 opens every muscle
/// (the 2.0.0 card: per muscle detail, sports, exercises not in the library, training settings); 「问 Jarvis」 asks for
/// an analysis.
struct MuscleWeekPage: View {
  @ObservedObject var store: TrainingStore
  @Binding var toast: Toast?
  let push: (RecordRoute) -> Void
  @State private var range: MuscleLoad.Range = .week
  @Environment(\.askAI) private var askAI

  /// The ranges offered: the server has today, this week and the last 7 days (no four-week range yet).
  static let ranges: [MuscleLoad.Range] = [.week, .last7]

  var body: some View {
    RecordPage(title: tr("肌肉负荷", "Muscle load"), trailing: { rangeSwitch }) {
      if let m = store.muscle {
        content(m)
      } else {
        EmptyNote(text: store.refreshing ? "…" : tr("暂无", "None")).padding(.top, 24)
      }
    }
    .task { await store.refresh() }
  }

  private var rangeSwitch: some View {
    HStack(spacing: 2) {
      ForEach(Self.ranges) { r in
        let on = r == range
        Button { withAnimation(.easeOut(duration: 0.15)) { range = r } } label: {
          Text(r == .week ? tr("本周", "Week") : tr("近7天", "7 days")).font(.system(size: 14, weight: on ? .semibold : .medium))
            .foregroundColor(on ? Palette.text : Palette.text2)
            .padding(.horizontal, 12).frame(height: 32)
            .background(Capsule().fill(on ? Palette.surface2 : .clear))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
      }
    }
    .padding(3)
    .background(Capsule().fill(Palette.surface))
  }

  /// A region's colour on the weekly map.
  static func color(_ status: String?) -> Color? {
    switch status {
    case "low": return Palette.train.opacity(0.45)
    case "ok": return Palette.good
    case "high": return Palette.bad
    default: return nil
    }
  }

  static func tone(_ status: String) -> Tone {
    switch status {
    case "ok": return .good
    case "high": return .bad
    default: return .train
    }
  }

  private func content(_ m: MuscleLoad) -> some View {
    let regions = m.regions(range)
    let t = RecordGlance.onTarget(m, range: range)
    let groups = RecordGlance.groups(m, range: range)
    let scale = RecordGlance.groupScale(groups)
    return VStack(alignment: .leading, spacing: 0) {
      BodyPair(sex: m.sex, spacing: 40, fill: { Self.color(regions[$0]?.status) })
        .frame(height: 330).frame(maxWidth: .infinity).padding(.top, 24)
      HStack(alignment: .firstTextBaseline) {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
          Text("\(t.ok)").font(.system(size: 34, weight: .light).monospacedDigit()).foregroundColor(Palette.text)
          Text("/\(t.total) \(tr("达标", "on target"))").font(.system(size: 15).monospacedDigit()).foregroundColor(Palette.text2)
        }
        Spacer(minLength: 8)
        legend
      }
      .padding(.top, 24).padding(.bottom, 10)
      ForEach(groups) { g in
        Rectangle().fill(Palette.hair).frame(height: 1)
        groupRow(g, scale: scale)
      }
      Rectangle().fill(Palette.hair).frame(height: 1)
      HStack(spacing: 10) {
        exit(icon: nil, title: tr("全部肌群", "All muscles"), chevron: true) { push(.allMuscles) }
        exit(icon: .orbit, title: tr("问 Jarvis", "Ask Jarvis"), chevron: false) { askAI(MusclePrompts.analyse) }
      }
      .padding(.top, 20)
    }
  }

  private var legend: some View {
    HStack(spacing: 12) {
      ForEach([("low", tr("不足", "Under")), ("ok", tr("达标", "In range")), ("high", tr("过量", "Over"))], id: \.0) { k, label in
        HStack(spacing: 5) {
          Circle().fill(Self.color(k) ?? Palette.surface2).frame(width: 8, height: 8)
          Text(label).font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
        }
      }
    }
    .lineLimit(1).minimumScaleFactor(0.8)
  }

  private func groupRow(_ g: RecordGlance.GroupRow, scale: Double) -> some View {
    Button { push(.allMuscles) } label: {
      HStack(spacing: 12) {
        Text(g.title).font(.system(size: 17, weight: .medium)).foregroundColor(Palette.text).frame(width: 52, alignment: .leading)
          .lineLimit(1).minimumScaleFactor(0.7)
        MuscleRangeBar(value: g.sets, low: g.low, high: g.high, max: scale, tone: Self.tone(g.status))
        HStack(alignment: .firstTextBaseline, spacing: 3) {
          Text(RecordFormat.num(g.sets, 1)).font(Typo.row).foregroundColor(Palette.text)
          Text(tr("组", "sets")).font(Typo.unit).foregroundColor(Palette.text2)
        }
        .frame(minWidth: 62, alignment: .trailing)
        Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
      }
      .frame(minHeight: 60)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(g.title) \(RecordFormat.num(g.sets, 1)) \(tr("组", "sets")) \(MuscleLoad.statusTitle(g.status))")
    .accessibilityAddTraits(.isButton)
  }

  private func exit(icon: Icon.Name?, title: String, chevron: Bool, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      HStack(spacing: 8) {
        if let icon { Icon(icon, size: 18) }
        Text(title).font(.system(size: 16, weight: .semibold)).lineLimit(1)
        if chevron { Icon(.chevronRight, size: 14).foregroundColor(Palette.text2) }
      }
      .foregroundColor(Palette.text)
      .frame(maxWidth: .infinity, minHeight: 52)
      .background(Capsule().fill(Palette.surface))
    }
    .buttonStyle(PressScale())
  }
}

/// 「全部肌群」: every muscle of the week as on 2.0.0 (map with ranges, list, sports, exercises not in the library, Jarvis,
/// training settings).
struct AllMusclesPage: View {
  @ObservedObject var store: TrainingStore
  @Binding var toast: Toast?
  @State private var sheet: TrainingSheet?

  var body: some View {
    RecordPage(title: tr("全部肌群", "All muscles")) {
      if let m = store.muscle {
        MuscleLoadCard(load: m, open: { sheet = .muscle($0, $1) }, goals: { sheet = .goals }).padding(.top, 12)
      } else {
        EmptyNote(text: tr("暂无", "None")).padding(.top, 24)
      }
    }
    .trainingSheets(store: store, sheet: $sheet, toast: $toast)
  }
}
