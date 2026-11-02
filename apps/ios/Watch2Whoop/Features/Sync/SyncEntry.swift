import SwiftUI

// The way to the sync center from the Today tab (sync is P0): a button in the header, the sync card, a banner when sync is
// stuck, and the reminder to fill in history. The Today page's counterparts: sync-status.js (status line and banner → the
// panel; the 「补全历史数据」 card).

/// `coverage` of GET …/sync/overview, same as the answer of GET …/sync/history (sync/backfill.py): how much watch history the
/// server has and the reminder's state, which the server keeps (1 / 3 / 7 days after the first sync, then weekly).
struct BackfillCoverage: Equatable {
  var days = 0
  var targetDays = SyncSettings.backfillDays
  /// complete / building / partial.
  var status = "partial"
  var impactLabel: String?
  /// A top-up is running, as the server sees it or (`withPhone`) as this phone does.
  var active = false
  var percent: Int?
  /// progress / error / due = shown; later / muted / complete / exhausted / none / consent = not.
  var state = "later"
  var show = false
  var muted = false
  /// What the card's button asks for.
  var askDays = SyncSettings.backfillDays

  init(days: Int = 0, targetDays: Int = SyncSettings.backfillDays, state: String = "later", show: Bool = false) {
    self.days = days
    self.targetDays = targetDays
    self.state = state
    self.show = show
  }

  init?(_ j: [String: Any]?) {
    guard let j, let remind = j["remind"] as? [String: Any] else { return nil }
    days = (j["days"] as? NSNumber)?.intValue ?? 0
    targetDays = (j["target_days"] as? NSNumber)?.intValue ?? SyncSettings.backfillDays
    status = j["status"] as? String ?? "partial"
    impactLabel = j["impact_label"] as? String
    let progress = j["progress"] as? [String: Any]
    active = progress?["active"] as? Bool ?? false
    percent = (progress?["percent"] as? NSNumber)?.intValue
    state = remind["state"] as? String ?? "later"
    show = remind["show"] as? Bool ?? false
    muted = remind["muted"] as? Bool ?? false
    askDays = ((j["action"] as? [String: Any])?["days"] as? NSNumber)?.intValue ?? targetDays
  }

  var complete: Bool { status == "complete" }
  var due: Bool { state == "due" }
  var failed: Bool { state == "error" }

  /// This phone's own history upload counts as running even before the server's next answer.
  func withPhone(_ b: BackfillState) -> BackfillCoverage {
    guard b.active else { return self }
    var c = self
    c.active = true
    c.percent = b.percent
    return c
  }

  /// The card shows on a reminder day, a running top-up or a failed one, and only while the sync itself is not stuck
  /// (sync-status.js wantsBackfill: `overviewState` = the overview's `state`).
  func showsCard(overviewState: String?) -> Bool {
    active || (show && (overviewState == "ok" || overviewState == "syncing"))
  }

  /// 「补全中 40%」, 「没能补全」 or 「14 / 90 天 · 恢复分基线还在建立」.
  var subtitle: String {
    if active { return tr("补全中 \(percent ?? 0)%", "Filling in \(percent ?? 0)%") }
    if failed { return tr("没能补全", "Could not fill in") }
    let d = tr("\(days) / \(targetDays) 天", "\(days) / \(targetDays) days")
    return [d, impactLabel].compactMap { $0 }.joined(separator: " · ")
  }

  /// The running top-up's percent, else how much of the target there is.
  var fraction: Double {
    let p = active ? Double(percent ?? 0) / 100 : Double(days) / Double(max(targetDays, 1))
    return min(1, max(0, p))
  }
}

/// What the Today tab says when sync is stuck, or nil. Pure (unit-tested): the phone's own diagnosis first, then what the
/// server sees (the overview's `issue`, same codes as sync-status.js BANNER_CODES).
struct SyncStuck: Equatable {
  let text: String
  /// The button retries right here; else it opens the sync center, where the self-help buttons are.
  let retry: Bool

  private static let serverCodes: Set<String> = ["server", "background_off", "low_power", "locked", "lagging"]
  private static let serverRetry: Set<String> = ["server", "locked", "lagging"]

  static func of(_ d: SyncDiagnosis, card: TodayModel.SyncCard?) -> SyncStuck? {
    switch d.overall {
    case .syncing, .off:
      return nil
    case .attention:
      if let i = d.issues.first(where: { $0.action != nil }) {
        return SyncStuck(text: i.text, retry: i.action == .syncNow || i.action == .retry)
      }
      return SyncStuck(text: d.headline, retry: false)
    case .ok:
      guard let c = card, c.state == "issue", let code = c.issueCode, serverCodes.contains(code), let label = c.issueLabel else { return nil }
      return SyncStuck(text: label, retry: serverRetry.contains(code))
    }
  }
}

/// The header's way to the sync center: one tap from the top of Today, in the state's colour.
struct SyncHeaderButton: View {
  @EnvironmentObject private var app: AppModel
  @ObservedObject private var sync = HealthSyncService.shared

  var body: some View {
    let d = sync.diagnosis
    Button { app.route = .syncCenter } label: {
      ZStack {
        Circle().fill(Palette.surface)
        if d.overall == .syncing {
          ProgressView().scaleEffect(0.7).tint(Palette.text2)
        } else {
          Icon(.refreshCw, size: 17).foregroundColor(d.overall == .attention ? Palette.mid : Palette.text2)
        }
      }
      .frame(width: 36, height: 36)
    }
    .buttonStyle(PressScale())
    .accessibilityLabel("\(tr("同步中心", "Sync center")) \(d.headline)")
  }
}

/// Under the Today header, only for today's own page: sync is stuck (a banner: tap = the sync center, its button retries),
/// else the reminder to fill in history. The reminder's rhythm is the server's; a reminder day is reported once
/// (`/sync/history/seen`, one row per local day on the server).
struct SyncNudges: View {
  let card: TodayModel.SyncCard?
  let coverage: BackfillCoverage?
  let refresh: () async -> Void
  @EnvironmentObject private var app: AppModel
  @EnvironmentObject private var session: AccountSession
  @ObservedObject private var sync = HealthSyncService.shared
  @State private var hidden = false
  @State private var seenDay: String?

  var body: some View {
    if let stuck = SyncStuck.of(sync.diagnosis, card: card) {
      Banner(text: stuck.text, tone: .mid, actionTitle: stuck.retry ? tr("重试", "Retry") : tr("处理", "Fix")) {
        if stuck.retry { Task { _ = await sync.syncNow(); await refresh() } } else { app.route = .syncCenter }
      }
      .contentShape(Rectangle())
      .onTapGesture { app.route = .syncCenter }
      .padding(.top, 2).padding(.bottom, 6)
    } else if !hidden, let c = coverage?.withPhone(sync.backfill), c.showsCard(overviewState: card?.state) {
      backfillCard(c)
        .padding(.top, 2).padding(.bottom, 6)
        .task(id: c.state) { await reportSeen(c) }
    }
  }

  private func backfillCard(_ c: BackfillCoverage) -> some View {
    Card {
      VStack(alignment: .leading, spacing: 12) {
        HStack(spacing: 12) {
          Icon(.calendarClock, size: 22).foregroundColor(Palette.text2)
          VStack(alignment: .leading, spacing: 2) {
            Text(tr("补全历史数据", "Fill in your history")).font(Typo.bodyStrong).foregroundColor(Palette.text)
            Text(c.subtitle).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
          }
          Spacer(minLength: 0)
        }
        ProgressBar(fraction: max(0.04, c.fraction))
        if !c.active {
          SecondaryButton(icon: .calendarClock, title: c.failed ? tr("再试一次", "Try again") : tr("补全历史", "Fill in history"),
                          background: Palette.surface2) { app.route = .history(days: c.askDays) }
          if c.due {
            Button(tr("不再提醒", "Don't remind me")) { Task { await mute() } }
              .font(Typo.caption).foregroundColor(Palette.text2).frame(maxWidth: .infinity)
          }
        }
      }
    }
  }

  private func reportSeen(_ c: BackfillCoverage) async {
    guard c.due else { return }
    let today = TypeVerdict.dayString(Date())
    guard seenDay != today else { return }
    seenDay = today
    _ = try? await session.api.data(APIRequest(method: .post, path: Endpoint.syncHistorySeen, idempotent: false))
  }

  private func mute() async {
    hidden = true
    _ = try? await session.api.data(try .json(.put, Endpoint.syncHistoryMute, ["muted": true]))
    await refresh()
  }
}
