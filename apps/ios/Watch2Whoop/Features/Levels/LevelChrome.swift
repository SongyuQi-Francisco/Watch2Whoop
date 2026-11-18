import SwiftUI

/// The depth ladder of 今天 and 身体 (drilldown option C′): three short segments in the page header, as many lit as the
/// level shown. On a first glance it is a door (a tap opens level 2, also when the dial's own tap is taken); on levels 2
/// and 3 it only says where you are, and VoiceOver's decrement goes up one level.
struct LevelLadder: View {
  let level: Int
  var down: (() -> Void)?
  var up: (() -> Void)?

  var body: some View {
    Group {
      if let down {
        Button(action: down) { bars }.buttonStyle(PressScale())
      } else {
        bars
      }
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(tr("第 \(level) 层，共 \(LevelParams.levels) 层", "Level \(level) of \(LevelParams.levels)"))
    .accessibilityHint(down != nil ? tr("打开下一层", "Opens the next level") : "")
    .accessibilityAddTraits(down != nil ? .isButton : [])
    .accessibilityAdjustableAction { d in
      switch d {
      case .increment: down?()
      case .decrement: up?()
      @unknown default: break
      }
    }
  }

  private var bars: some View {
    HStack(spacing: LevelParams.ladderGap) {
      ForEach(1...LevelParams.levels, id: \.self) { i in
        RoundedRectangle(cornerRadius: LevelParams.ladderSegment.height / 2)
          .fill(i <= level ? Palette.text : Palette.track)
          .frame(width: LevelParams.ladderSegment.width, height: LevelParams.ladderSegment.height)
      }
    }
    .frame(minWidth: LevelParams.hitSide, minHeight: LevelParams.hitSide)
    .contentShape(Rectangle())
  }
}

/// The recovery dial, small: the way up in a level's header (the dial of the first glance has shrunk into it).
struct MiniDial: View {
  let value: Double?
  let tone: Tone

  var body: some View {
    let s = LevelParams.miniDial, w = LevelParams.miniDialLine
    ZStack {
      Circle().stroke(Palette.track, lineWidth: w)
      Circle()
        .trim(from: 0, to: TodayGlanceRules.dialFraction(value))
        .stroke(tone == .text ? Palette.text2 : tone.color, style: StrokeStyle(lineWidth: w, lineCap: .round))
        .rotationEffect(.degrees(-90))
      Text(value.map { TodayModel.fmt($0) } ?? "—").font(.system(size: 13, weight: .semibold).monospacedDigit())
        .foregroundColor(Palette.text).minimumScaleFactor(0.7).lineLimit(1)
    }
    .frame(width: s - w, height: s - w)
    .frame(width: s, height: s)
  }
}

/// The small chevron after the label of an element that opens the next level and has no card fill of its own (the dial,
/// the two readings, 身体's hero). Decorative: the element's own label says where it goes.
struct DoorMark: View {
  var size: CGFloat = 12

  var body: some View {
    Icon(.chevronRight, size: size).foregroundColor(Palette.text2).opacity(0.85).accessibilityHidden(true)
  }
}

/// The header of a pushed level: back · the way up (the crumb) · the title · an optional tag · the ladder.
struct LevelHeader<Crumb: View, Title: View, Trailing: View>: View {
  let level: Int
  let back: () -> Void
  let up: () -> Void
  /// VoiceOver label of the crumb (where up leads).
  let upLabel: String
  @ViewBuilder var crumb: () -> Crumb
  @ViewBuilder var title: () -> Title
  @ViewBuilder var trailing: () -> Trailing

  var body: some View {
    HStack(spacing: 6) {
      Button(action: back) {
        Icon(.chevronLeft, size: 22).foregroundColor(Palette.text).frame(width: 28, height: LevelParams.hitSide).contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("返回", "Back"))
      Button(action: up) { crumb().frame(minHeight: LevelParams.hitSide).contentShape(Rectangle()) }
        .buttonStyle(PressScale())
        .accessibilityLabel(upLabel)
      title()
      Spacer(minLength: 4)
      trailing()
      LevelLadder(level: level, up: up)
    }
    .frame(height: LevelParams.hitSide)
  }
}

/// A category's header on a level-2 page: icon, title, its count at the right (dd.css `.gh`).
struct LevelGroupHeader: View {
  let icon: Icon.Name
  let title: String
  let count: Int?

  var body: some View {
    HStack(spacing: 6) {
      Icon(icon, size: 15).foregroundColor(Palette.text2)
      Text(title).font(Typo.section).foregroundColor(Palette.text)
      Spacer(minLength: 4)
      if let count { Text("\(count)").font(.system(size: 12, weight: .medium).monospacedDigit()).foregroundColor(Palette.text2) }
    }
    .frame(height: 36)
    .overlay(alignment: .bottom) { Rectangle().fill(Palette.hair).frame(height: 1) }
    .accessibilityElement(children: .combine)
    .accessibilityAddTraits(.isHeader)
  }
}

/// The jump rail under 今日详情's chips: one icon per category with its count; a tap scrolls the list to it (dd.css `.jump`).
struct JumpRail: View {
  struct Item: Equatable, Identifiable {
    var id: String
    var title: String
    var count: Int
  }

  let items: [Item]
  let selected: String?
  let pick: (String) -> Void

  var body: some View {
    HStack(spacing: 0) {
      ForEach(items) { it in
        let on = it.id == (selected ?? items.first?.id)
        Button { pick(it.id) } label: {
          HStack(spacing: 4) {
            Icon(LevelGroup.icon(it.id), size: 15)
            Text("\(it.count)").font(.system(size: 12, weight: .medium).monospacedDigit())
          }
          .foregroundColor(on ? Palette.text : Palette.text2)
          .frame(maxWidth: .infinity, minHeight: LevelParams.jumpHeight - 6)
          .background(Capsule().fill(on ? Palette.surface2 : .clear))
          .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(it.title) \(it.count)")
        .accessibilityAddTraits(on ? [.isButton, .isSelected] : .isButton)
      }
    }
    .padding(3)
    .frame(height: LevelParams.jumpHeight)
    .background(Capsule().fill(Palette.surface))
  }
}

/// One of 今日详情's chips (训练 · 今晚 · 提醒): icon, number, a dot when something new waits (dd.css `.chips .chip`).
struct LevelChip: View {
  let icon: Icon.Name
  let number: String
  var unit: String?
  var dot: Tone?
  let label: String
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 6) {
        Icon(icon, size: 16).foregroundColor(Palette.text2)
        Text(number).font(.system(size: 15, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text).lineLimit(1)
        if let unit { Text(unit).font(Typo.unit).foregroundColor(Palette.text2).lineLimit(1) }
      }
      .minimumScaleFactor(0.8)
      .frame(maxWidth: .infinity, minHeight: LevelParams.chipHeight)
      .background(Capsule().fill(Palette.surface))
      .overlay(alignment: .topTrailing) {
        if let dot { Circle().fill(dot.color).frame(width: 6, height: 6).padding(.top, 8).padding(.trailing, 12) }
      }
      .contentShape(Capsule())
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(label) \(number)\(unit.map { " \($0)" } ?? "")")
    .accessibilityAddTraits(.isButton)
  }
}

/// The rows of a level (今日详情, 身体's list, 相关): label · line on the usual band · value · change · chevron.
struct LevelRowList: View {
  let rows: [TodayGlance.Row]
  let open: (TodayGlance.Row) -> Void
  /// A trailing action on one row (the ＋ of 体重 = 记体重): row id → (label, action).
  var extra: [String: (label: String, run: () -> Void)] = [:]

  var body: some View {
    VStack(spacing: 0) {
      ForEach(rows) { r in
        HStack(spacing: 0) {
          L1RowView(row: r, labelWidth: LevelParams.rowLabelWidth) { open(r) }
          if let x = extra[r.id] {
            Button(action: x.run) {
              Icon(.plus, size: 16).foregroundColor(Palette.text)
                .frame(width: 32, height: 32).background(Circle().fill(Palette.surface))
                .frame(width: LevelParams.hitSide, height: LevelParams.hitSide).contentShape(Rectangle())
            }
            .buttonStyle(PressScale())
            .accessibilityLabel(x.label)
            .overlay(alignment: .bottom) { Rectangle().fill(Palette.hair).frame(height: 1) }
          }
        }
      }
    }
  }
}
