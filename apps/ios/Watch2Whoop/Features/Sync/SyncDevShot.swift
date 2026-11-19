import SwiftUI

#if DEBUG
/// Debug builds only, for simulator screenshots of FB-23 / FB-24 with made-up data (no server, no Health):
/// `SIMCTL_CHILD_W2W_DEV_SYNC=processing|ok|failed|background|missing` shows the sync center's top (the status card from
/// `SyncDiagnosis.of` on a synthetic input, the issues, the hint row, the 「最近 30 天」 row) or the missing-days list;
/// `bg-off|bg-lowpower|bg-restricted|bg-idle|bg-running|bg-flagoff-running` show one case of the background check
/// (BackgroundCheck on synthetic wakes): the Today capsule, the status card, the hint row and the help sheet's content;
/// `SIMCTL_CHILD_W2W_DEV_LANG=zh|en` picks the language.
enum SyncDevShot {
  static var state: String? {
    guard let s = RecordDevShot.value("W2W_DEV_SYNC") else { return nil }
    if let l = RecordDevShot.value("W2W_DEV_LANG"), l == "zh" || l == "en" { Lang.override = l }
    return s
  }

  /// The background check's synthetic input of a `bg-…` state (the flag, Low Power Mode, the wakes, the newest data).
  static func background(_ state: String, now: Date = Date()) -> BackgroundCheck.Verdict {
    var b = BackgroundCheck.Input()
    b.now = now
    b.newestData = now.addingTimeInterval(-300)
    b.wakes.since = now.addingTimeInterval(-10 * 86_400)
    b.wakes.last = now.addingTimeInterval(-3 * 86_400 - 4 * 3600)
    switch state {
    case "bg-off", "background": b.status = .denied
    case "bg-lowpower": b.status = .denied; b.lowPower = true
    case "bg-restricted": b.status = .restricted
    case "bg-running": b.wakes.last = now.addingTimeInterval(-25 * 60)
    case "bg-flagoff-running": b.status = .denied; b.wakes.last = now.addingTimeInterval(-25 * 60)
    default: break   // bg-idle: iOS says on, the last wake 3 days ago, new data since
    }
    return BackgroundCheck.of(b)
  }

  static func diagnosis(_ state: String, now: Date = Date()) -> SyncDiagnosis {
    var i = SyncDiagnosis.Input()
    i.permission = .asked
    i.info = IngestInfo(["engine": ["enabled": true, "mode": "app", "accepts": ["phase", "samples"]]])
    i.engine = EngineSnapshot(["mode": "app", "active": true, "firstSync": ["types": 10, "done": 10]])
    i.lastOk = now.addingTimeInterval(-120)
    i.server = ServerSyncStatus(["mode": "app", "pipeline": ["up_to_date": true]])
    switch state {
    case "processing":
      i.server = ServerSyncStatus(["mode": "shadow", "pipeline": ["computing": true, "switching": true]])
    case "failed":
      i.engine = EngineSnapshot(["mode": "app", "active": true, "lastStop": "failed", "firstSync": ["types": 10, "done": 10]])
    case let s where s == "background" || s.hasPrefix("bg-"):
      i.background = background(s, now: now)
    default:
      break
    }
    return SyncDiagnosis.of(i, now: now)
  }

  static func missing(today: Date = Date(), calendar: Calendar = .current) -> MissingDays {
    let day = { (n: Int) in TypeVerdict.dayString(calendar.date(byAdding: .day, value: -n, to: today) ?? today, calendar) }
    return MissingDays(kinds: [.init(key: "sleep", label: tr("睡眠", "Sleep")), .init(key: "heart_rate", label: tr("心率", "Heart rate")),
                               .init(key: "hrv", label: "HRV"), .init(key: "steps", label: tr("步数", "Steps"))],
                       days: [.init(date: day(1), kinds: ["sleep", "hrv"]), .init(date: day(3), kinds: ["sleep", "heart_rate", "hrv", "steps"]),
                              .init(date: day(6), kinds: ["steps"]), .init(date: day(9), kinds: ["sleep", "hrv"]),
                              .init(date: day(17), kinds: ["sleep", "heart_rate", "hrv", "steps"])])
  }

  /// The made-up state of each day: one re-syncing, one Health has nothing of, one still missing after its re-sync.
  static func missingState(_ d: MissingDays.Day, _ m: MissingDays) -> MissingDayState {
    switch m.days.firstIndex(of: d) {
    case 0: return .resyncing
    case 1, 4: return MissingDayState.of(missing: d.kinds, phone: Dictionary(uniqueKeysWithValues: d.kinds.map { ($0, false) }),
                                         resyncedAt: nil, running: false)
    case 3: return MissingDayState.of(missing: d.kinds, phone: ["sleep": true], resyncedAt: Date().addingTimeInterval(-3600),
                                      running: false)
    default: return MissingDayState.of(missing: d.kinds, phone: [:], resyncedAt: nil, running: false)
    }
  }
}

struct SyncDevShotView: View {
  let state: String

  var body: some View {
    NavigationStack {
      ScrollView(.vertical, showsIndicators: false) {
        VStack(alignment: .leading, spacing: 18) {
          if state == "missing" {
            let m = SyncDevShot.missing()
            MissingDaysList(model: m, state: { SyncDevShot.missingState($0, m) }, percent: 40, busy: false,
                            resyncDay: { _ in }, resyncAll: { _ in })
          } else {
            let d = SyncDevShot.diagnosis(state)
            let bg = state.hasPrefix("bg-")
            let lastWake: Date? = {
              switch d.background {
              case .running(let at): return at
              case .notRunning(let last): return last
              default: return bg ? Date().addingTimeInterval(-3 * 86_400 - 4 * 3600) : Date().addingTimeInterval(-25 * 60)
              }
            }()
            if bg {
              HStack {
                Text(tr("今天", "Today")).font(Typo.heroWord).foregroundColor(Palette.text)
                Spacer()
                SyncCapsule(state: SyncCapsuleState.of(overall: d.overall, page: .idle, card: nil, background: d.background))
              }
              if let stuck = SyncStuck.of(d, card: nil) {   // the Today banner
                Banner(text: stuck.text, tone: .mid, actionTitle: tr("处理", "Fix")) {}
              }
            }
            Card {
              VStack(alignment: .leading, spacing: 14) {
                SyncStatusHeader(diagnosis: d, lastBackground: lastWake)
                PrimaryButton(title: tr("立即同步", "Sync now")) {}
              }
            }
            ForEach(d.issues) { i in Banner(text: i.text, tone: .mid, actionTitle: i.actionTitle, action: {}) }
            if !d.hints.isEmpty {
              RowGroup { ForEach(d.hints) { h in SyncHintRow(hint: h) {} } }
            }
            if bg {
              Card { BackgroundHelpView(verdict: d.background, lastWake: lastWake) }
            } else {
              RowGroup { MissingDaysEntry(count: state == "failed" ? nil : 5, windowDays: 30) }
            }
          }
        }
        .padding(.horizontal, Metrics.gutter)
        .padding(.top, 8)
        .padding(.bottom, 32)
      }
      .background(Palette.bg.ignoresSafeArea())
      .navigationTitle(state == "missing" ? tr("缺的日子", "Missing days") : tr("同步中心", "Sync center"))
      .navigationBarTitleDisplayMode(.inline)
    }
  }
}
#endif
