#if DEBUG
import SwiftUI
import WatchKit

/// Debug only: every complication state on one scrolling screen, drawn by the widget's own views from entries built by
/// `FaceTimeline` (the same rules as the face) on synthetic data (小林). `SIMCTL_CHILD_W2W_WATCH_FACES=1` (all) or
/// `=circle` / `=key` / `=rect` / `=rect2` / `=live` (one group, for screenshots). Sizes are the face's slots on this watch
/// (`FaceGallery.slot`); the background disc stands in for the system's.
struct FaceGallery: View {
  var group: String

  var body: some View {
    let s = FaceGallery.states(Date())
    ScrollView {
      VStack(alignment: .leading, spacing: 8) {
        if group == "1" || group == "circle" {
          row(tr("恢复", "Recovery"), s.circle.map { e in AnyView(FaceRecoveryCircle(entry: e.1).frame(width: slot.circle, height: slot.circle)) }, labels: s.circle.map(\.0))
        }
        if group == "1" || group == "key" {
          row(tr("开始训练", "Start"), s.key.map { e in AnyView(FaceStartKey(entry: e.1).frame(width: slot.circle, height: slot.circle)) }, labels: s.key.map(\.0))
        }
        if group == "1" || group == "rect" || group == "rect2" || group == "live" {
          let idle = s.rect.filter { $0.1.live == nil }
          let list = group == "live" ? s.rect.filter { $0.1.live != nil } : group == "rect" ? Array(idle.prefix(2))
            : group == "rect2" ? Array(idle.dropFirst(2)) : s.rect
          ForEach(Array(list.enumerated()), id: \.offset) { _, e in
            VStack(alignment: .leading, spacing: 2) {
              Text(e.0).font(.system(size: 9)).foregroundStyle(W.text3)
              FaceTodayRect(entry: e.1).frame(width: slot.rect.width, height: slot.rect.height)
            }
          }
        }
      }
      .padding(.horizontal, 4)
    }
    .background(W.bg)
  }

  private var slot: (circle: CGFloat, rect: CGSize) { FaceGallery.slot(width: Double(WKScreenWidth.value)) }

  private func row(_ title: String, _ views: [AnyView], labels: [String]) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(title).font(.system(size: 10, weight: .semibold)).foregroundStyle(W.text2)
      LazyVGrid(columns: [GridItem(.adaptive(minimum: slot.circle + 6), spacing: 4)], alignment: .leading, spacing: 4) {
        ForEach(Array(views.enumerated()), id: \.offset) { i, v in
          VStack(spacing: 1) {
            // outside a face the system backdrop is a square: clip it to the slot's circle
            v.frame(width: slot.circle, height: slot.circle).clipShape(Circle())
            Text(labels[i]).font(.system(size: 8)).foregroundStyle(W.text3).lineLimit(1)
          }
        }
      }
    }
  }

  /// The complication slots (Apple HIG, watchOS accessory sizes) by screen width; approximate for the newest models
  /// until measured on a real face (README 「Complications」).
  static func slot(width: Double) -> (circle: CGFloat, rect: CGSize) {
    switch Int(width.rounded()) {
    case ..<170: return (42, CGSize(width: 150, height: 47))      // 40 mm
    case ..<180: return (44.5, CGSize(width: 162, height: 50))    // 41 mm
    case ..<186: return (47, CGSize(width: 171, height: 54))      // 44 mm
    case ..<190: return (47, CGSize(width: 170, height: 51))      // 42 mm (Series 10–11)
    case ..<200: return (50, CGSize(width: 178.5, height: 54))    // 45 mm
    default: return (50, CGSize(width: 184, height: 56))          // 46 mm, 49 mm
    }
  }

  /// Every state, each built by `FaceTimeline` from the inputs it names.
  static func states(_ t: Date) -> (circle: [(String, FaceEntry)], key: [(String, FaceEntry)], rect: [(String, FaceEntry)]) {
    let day = WatchDay.string(t)
    func ctx(rest: Bool = false, planMissing: Bool = false, stale: Bool = false) -> WatchContext {
      WatchDemoData.context(day: day, sentAt: t, rest: rest, planMissing: planMissing, stale: stale, lang: Lang.code)
    }
    var queue = WatchEventQueue()
    queue.heard(at: t.addingTimeInterval(-120))
    func entry(_ c: WatchContext?, _ s: SavedSession? = nil, at: Date? = nil) -> FaceEntry {
      FaceTimeline.entry(FaceTimeline.Inputs(context: c, queue: queue, session: s), at: at ?? t)
    }
    var bad = ctx()
    bad.today?.recovery = 28
    bad.today?.tone = .bad
    var missing = ctx()
    missing.today?.recovery = nil
    missing.today?.reason = tr("昨晚没戴表", "No watch last night")
    var yesterday = ctx()
    yesterday.today?.day = WatchDay.string(t.addingTimeInterval(-86_400))
    let signedOut = WatchContext(signedIn: false, plan: nil, lang: Lang.code, sentAt: t)
    let circle = [("82", entry(ctx())), ("54", entry(ctx(rest: true))), ("28", entry(bad)),
                  (tr("旧了", "stale"), entry(ctx(stale: true))), (tr("没有", "missing"), entry(missing)),
                  (tr("昨天的", "yesterday"), entry(yesterday)), (tr("未登录", "signed out"), entry(signedOut)),
                  (tr("没手机", "no phone"), entry(nil))]
    // a session: three sets of bench logged, resting since 18 s ago (90 s planned); and one in a set
    var s = SetSession(plan: WatchDemoData.plan(day: day))
    _ = s.primary(at: t.addingTimeInterval(-400))
    for i in 0..<2 {
      _ = s.primary(at: t.addingTimeInterval(-300 + Double(i) * 120))
      _ = s.tick(at: t.addingTimeInterval(-298 + Double(i) * 120))
      _ = s.primary(at: t.addingTimeInterval(-240 + Double(i) * 120))
    }
    var inSet = s
    _ = s.primary(at: t.addingTimeInterval(-18))
    _ = inSet.tick(at: t)
    let resting = SavedSession(session: s, savedAt: t.addingTimeInterval(-18))
    let setting = SavedSession(session: inSet, savedAt: t.addingTimeInterval(-60))
    let key = [(tr("训练日", "plan"), entry(ctx())), (tr("休息日", "rest day"), entry(ctx(rest: true))),
               (tr("计划没同步", "no plan"), entry(ctx(planMissing: true))), (tr("训练中", "session"), entry(ctx(), resting))]
    let rect = [(tr("训练日 82", "plan 82"), entry(ctx())), (tr("旧了", "stale"), entry(ctx(stale: true))),
                (tr("没有恢复", "missing"), entry(missing)), (tr("未登录", "signed out"), entry(signedOut)),
                (tr("休息倒计时", "rest countdown"), entry(ctx(), resting)), (tr("做组中", "in a set"), entry(ctx(), setting))]
    return (circle, key, rect)
  }
}

/// The watch's screen width (WatchKit lives in the app, not in the shared views).
enum WKScreenWidth {
  static var value: CGFloat { WKInterfaceDevice.current().screenBounds.width }
}
#endif
