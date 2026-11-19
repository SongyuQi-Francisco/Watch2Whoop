import SwiftUI
import WidgetKit

/// The watch-face complications of 2.1.0 (docs/watch-app-plan.md 2.5): recovery (circular), today (rectangular: the
/// three numbers, or the rest countdown during a session) and the 「开始训练」 key (circular). They read the App Group
/// store the watch app writes (`WatchStore.shared`) and draw `FaceTimeline`'s entries; the app reloads them when the
/// phone pushes something that changes the face or a session step does (`WatchStore.changingFace`).
@main
struct Watch2WhoopFaceWidgets: WidgetBundle {
  var body: some Widget {
    RecoveryComplication()
    TodayComplication()
    StartComplication()
  }
}

struct FaceTimelineEntry: TimelineEntry {
  var entry: FaceEntry
  var date: Date { entry.date }
}

struct FaceProvider: TimelineProvider {
  /// The widget gallery's picture: a sample with synthetic numbers (never the person's: the gallery is shown before a
  /// face is chosen, on any watch).
  func placeholder(in context: Context) -> FaceTimelineEntry { FaceTimelineEntry(entry: FaceSamples.idle(Date())) }

  func getSnapshot(in context: Context, completion: @escaping (FaceTimelineEntry) -> Void) {
    if context.isPreview {
      completion(placeholder(in: context))
      return
    }
    let now = Date()
    completion(FaceTimelineEntry(entry: FaceTimeline.entry(load(), at: now)))
  }

  func getTimeline(in context: Context, completion: @escaping (Timeline<FaceTimelineEntry>) -> Void) {
    let now = Date()
    let inputs = load()
    let entries = FaceTimeline.entries(inputs, now: now).map(FaceTimelineEntry.init(entry:))
    completion(Timeline(entries: entries, policy: .after(FaceTimeline.horizon(now: now))))
  }

  /// The store, and the phone's language for the labels (the extension has no other source for it).
  private func load() -> FaceTimeline.Inputs {
    let inputs = WatchStore.shared(migrate: false).faceInputs()
    if let lang = inputs.context?.lang { Lang.override = lang }
    return inputs
  }
}

struct RecoveryComplication: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: FaceComplication.recovery.kind, provider: FaceProvider()) { e in
      FaceRecoveryCircle(entry: e.entry).containerBackground(.clear, for: .widget)
    }
    .configurationDisplayName(tr("恢复", "Recovery"))
    .description(tr("今天的恢复分", "Today's recovery"))
    .supportedFamilies([.accessoryCircular])
  }
}

struct TodayComplication: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: FaceComplication.today.kind, provider: FaceProvider()) { e in
      FaceTodayRect(entry: e.entry).containerBackground(.clear, for: .widget)
    }
    .configurationDisplayName(tr("今天", "Today"))
    .description(tr("恢复、睡眠、负荷；训练中是休息倒计时", "Recovery, sleep, strain; rest countdown while training"))
    .supportedFamilies([.accessoryRectangular])
  }
}

struct StartComplication: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: FaceComplication.start.kind, provider: FaceProvider()) { e in
      FaceStartKey(entry: e.entry).containerBackground(.clear, for: .widget)
    }
    .configurationDisplayName(tr("开始训练", "Start workout"))
    .description(tr("打开今天的训练", "Opens today's workout"))
    .supportedFamilies([.accessoryCircular])
  }
}
