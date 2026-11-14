import Combine
import Foundation
import WatchConnectivity

// The iPhone's half of the watch link (docs/watch-app-plan.md 4.4 and 4.6, phase 1: logging sets). The phone stays the
// only writer of the training day: it pushes today's plan to the watch, merges the watch's set events into the day's
// draft and lets the existing local-first write and offline queue send it (`TrainingStore.saveDraft` / `submit`). No
// new endpoint, no backend change.

/// Today's plan as the watch needs it.
enum WatchPlanBuilder {
  static func plan(from s: TrainingSession?) -> WatchPlan? {
    guard let s, !s.restDay, s.canLog else { return nil }
    let exercises = s.loggable.map { e -> WatchExercise in
      let rows = s.rows[e.slug] ?? []
      return WatchExercise(slug: e.slug, name: e.name, sets: max(rows.count, e.targetSets, 1),
                           done: rows.prefix { $0.done }.count,
                           reps: e.timed ? nil : e.target.reps, weightKg: e.weighted ? e.target.weightKg : nil,
                           durationS: e.timed ? e.target.durationS : nil, weighted: e.weighted,
                           restSec: e.restSec ?? WatchParams.defaultRestSec, weightStepKg: WatchParams.defaultWeightStepKg,
                           last: e.lastSets)
    }
    guard !exercises.isEmpty else { return nil }
    return WatchPlan(day: s.day, title: s.title, exercises: exercises)
  }
}

/// Merging the watch's events into the day's rows (pure; `PhoneWatchBridge` feeds it, the tests call it directly).
enum WatchEventMerge {
  struct Result: Equatable {
    var rows: [String: [SetRow]]
    /// Events used (set or end), in the order applied.
    var applied: [String]
    /// An `end` event was among them: the phone submits the day.
    var ended: Bool
    /// Events for another day or an exercise not in today's plan: kept for later, not lost.
    var held: [WatchSetEvent]
  }

  /// `seen`: ids already applied (a transfer can arrive twice). Events are applied in the order they happened on the
  /// watch; a later event for the same exercise and set index replaces the earlier one.
  static func apply(_ events: [WatchSetEvent], to rows: [String: [SetRow]], day: String, exercises: [PlannedExercise],
                    seen: Set<String>) -> Result {
    var out = rows
    var applied: [String] = []
    var held: [WatchSetEvent] = []
    var ended = false
    var done = seen
    for e in events.sorted(by: { $0.at < $1.at }) where !done.contains(e.id) {
      guard e.day == day else { held.append(e); continue }
      switch e.kind {
      case .end:
        ended = true
      case .set:
        guard let slug = e.slug, let i = e.setIndex, i >= 0, let ex = exercises.first(where: { $0.slug == slug }), !ex.banned else {
          held.append(e)
          continue
        }
        var list = out[slug] ?? []
        while list.count <= i { list.append(SetRow(reps: ex.target.reps, weightKg: ex.target.weightKg, durationS: ex.target.durationS, done: false)) }
        list[i] = SetRow(reps: e.reps, weightKg: e.weightKg, durationS: e.durationS, done: true)
        out[slug] = list
      }
      applied.append(e.id)
      done.insert(e.id)
    }
    return Result(rows: out, applied: applied, ended: ended, held: held)
  }
}

/// Ids already merged, and events held for another day, kept across launches (UserDefaults; a few KB at most).
struct PhoneWatchInbox: Codable, Equatable {
  var seen: [String] = []
  var held: [WatchSetEvent] = []

  mutating func remember(_ ids: [String]) {
    seen.append(contentsOf: ids)
    if seen.count > WatchParams.phoneSeenIdsKept { seen.removeFirst(seen.count - WatchParams.phoneSeenIdsKept) }
  }

  static let key = "w2w.native.watchInbox.v1"
  static func load(_ d: UserDefaults = .standard) -> PhoneWatchInbox { WatchCodec.decode(PhoneWatchInbox.self, d.data(forKey: key)) ?? PhoneWatchInbox() }
  func save(_ d: UserDefaults = .standard) { d.set(WatchCodec.encode(self), forKey: Self.key) }
}

/// WatchConnectivity on the phone. Attached to the signed-in account; does nothing on a phone without a paired watch.
@MainActor
final class PhoneWatchBridge: NSObject, ObservableObject {
  static let shared = PhoneWatchBridge()

  private var store: TrainingStore?
  private var cancellable: AnyCancellable?
  private var inbox = PhoneWatchInbox.load()
  private var queued: [WatchSetEvent] = []
  private var working = false

  private var wc: WCSession? { WCSession.isSupported() ? WCSession.default : nil }

  /// Called when an account opens: watches today's training and pushes it to the watch.
  func attach(_ session: AccountSession) {
    guard let wc else { return }
    if wc.delegate == nil {
      wc.delegate = self
      wc.activate()
    }
    let s = TrainingStore(session: session)
    store = s
    cancellable = s.$session.removeDuplicates().sink { [weak self] t in self?.push(WatchPlanBuilder.plan(from: t)) }
    Task {
      await s.refresh()
      await drain()
    }
  }

  /// Sign-out: the watch shows 「在手机上登录」 and keeps its own queue until someone signs in again.
  func detach() {
    cancellable = nil
    store = nil
    pushContext(WatchContext(signedIn: false, plan: nil, lang: Lang.code, sentAt: Date()))
  }

  private func push(_ plan: WatchPlan?) {
    pushContext(WatchContext(signedIn: true, plan: plan, lang: Lang.code, sentAt: Date()))
  }

  private func pushContext(_ c: WatchContext) {
    guard let wc, wc.activationState == .activated, wc.isPaired, wc.isWatchAppInstalled else { return }
    try? wc.updateApplicationContext(WatchCodec.message(c))
  }

  fileprivate func received(_ e: WatchSetEvent) {
    queued.append(e)
    Task { await drain() }
  }

  /// Merges what arrived (and what was held) into today's draft, saves it the local-first way, submits on 「end」.
  private func drain() async {
    guard !working, let store, let session = store.session else { return }
    let events = inbox.held + queued
    guard !events.isEmpty else { return }
    working = true
    defer { working = false }
    queued.removeAll()
    let r = WatchEventMerge.apply(events, to: store.rows, day: session.day, exercises: session.exercises, seen: Set(inbox.seen))
    inbox.held = r.held
    inbox.remember(r.applied)
    inbox.save()
    if r.rows != store.rows {
      store.rows = r.rows
      await store.saveDraft()
    }
    if r.ended { _ = await store.submit() }
    if !queued.isEmpty { await drain() }
  }
}

extension PhoneWatchBridge: WCSessionDelegate {
  nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
    Task { @MainActor in self.push(WatchPlanBuilder.plan(from: self.store?.session)) }
  }

  nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}

  nonisolated func sessionDidDeactivate(_ session: WCSession) {
    session.activate()   // switched to another watch
  }

  nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
    guard let e = WatchCodec.event(userInfo) else { return }
    Task { @MainActor in self.received(e) }
  }

  nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
    guard message[WatchCodec.requestKey] != nil else { return }
    Task { @MainActor in
      if self.store == nil { self.detach(); return }
      await self.store?.refresh()
      self.push(WatchPlanBuilder.plan(from: self.store?.session))
    }
  }
}
