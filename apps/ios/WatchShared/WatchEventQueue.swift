import Foundation

/// Events waiting on the watch until the phone has them (plan 4.6): a session can be logged with the phone in a locker
/// and handed over when the two meet again. Pure value + a storage closure, so the rules are unit-tested on the phone.
///
/// Life of an event: `enqueue` (pending) → `markSending` when handed to WatchConnectivity → `markDelivered` when the
/// transfer finished, or `markFailed` (the S6 「重试」 mark; `retryFailed` sends them again). On start the watch calls
/// `restoreAfterLaunch`: transfers the system still holds stay `sending`, the rest go back to pending.
struct WatchEventQueue: Codable, Equatable {
  enum State: String, Codable { case pending, sending, failed }

  struct Item: Codable, Equatable {
    var event: WatchSetEvent
    var state: State
    var attempts: Int
  }

  private(set) var items: [Item] = []
  /// Delivered since the queue was last empty: the 「补交 4/6」 progress.
  private(set) var deliveredInBatch = 0
  /// Last time the phone took something from us or sent us a context (the 「2分前」 of S1).
  private(set) var lastContact: Date?

  var pending: Int { items.filter { $0.state == .pending }.count }
  var sending: Int { items.filter { $0.state == .sending }.count }
  var failed: Int { items.filter { $0.state == .failed }.count }
  /// Not yet with the phone (「待传 N」).
  var waiting: Int { items.count }
  var isEmpty: Bool { items.isEmpty }

  mutating func enqueue(_ e: WatchSetEvent) {
    guard !items.contains(where: { $0.event.id == e.id }) else { return }
    if items.isEmpty { deliveredInBatch = 0 }
    items.append(Item(event: e, state: .pending, attempts: 0))
  }

  /// The events to hand to WatchConnectivity now, oldest first; they become `sending`.
  mutating func takePending() -> [WatchSetEvent] {
    var out: [WatchSetEvent] = []
    for i in items.indices where items[i].state == .pending {
      items[i].state = .sending
      items[i].attempts += 1
      out.append(items[i].event)
    }
    return out
  }

  mutating func markDelivered(_ id: String, at now: Date) {
    guard let i = items.firstIndex(where: { $0.event.id == id }) else { return }
    items.remove(at: i)
    deliveredInBatch += 1
    lastContact = now
  }

  mutating func markFailed(_ id: String) {
    guard let i = items.firstIndex(where: { $0.event.id == id }) else { return }
    items[i].state = .failed
  }

  /// S6's 「重试」: the failed ones go back to pending.
  mutating func retryFailed() {
    for i in items.indices where items[i].state == .failed { items[i].state = .pending }
  }

  /// After a relaunch: events whose transfer the system still holds (`inFlight` ids) stay sending, the others are sent
  /// again (the phone drops duplicates by id).
  mutating func restoreAfterLaunch(inFlight: Set<String>) {
    for i in items.indices where items[i].state == .sending && !inFlight.contains(items[i].event.id) {
      items[i].state = .pending
    }
  }

  mutating func heard(at now: Date) { lastContact = now }

  // MARK: - storage

  func data() -> Data? { WatchCodec.encode(self) }
  static func load(_ data: Data?) -> WatchEventQueue { WatchCodec.decode(WatchEventQueue.self, data) ?? WatchEventQueue() }
}

/// The sync mark (plan 2.6, mockups S1–S9): the icon's shape is the state, the text is an age or a count.
enum WatchSyncState: Equatable {
  /// S8: the phone has never told us who is signed in, or it signed out.
  case signedOut
  /// S9: no plan for today on the watch.
  case noPlan
  /// S6: a transfer failed; tap = retry.
  case failed(Int)
  /// S2 / S5: handing over; `done` of `total` in this batch.
  case catchingUp(done: Int, total: Int)
  /// S4: the phone is not reachable; `n` sets are kept on the watch.
  case away(Int)
  /// S1: all handed over; minutes since we last heard from the phone (nil = never).
  case linked(minutesAgo: Int?)

  /// The state to show. `sessionRunning`: during a session the plan on the watch is used even if it became yesterday's.
  static func derive(context: WatchContext?, queue: WatchEventQueue, reachable: Bool, today: String,
                     sessionRunning: Bool, now: Date) -> WatchSyncState {
    guard let context, context.signedIn else { return .signedOut }
    if !sessionRunning && (context.plan == nil || context.plan?.day != today) && queue.isEmpty { return .noPlan }
    if queue.failed > 0 { return .failed(queue.failed) }
    if !queue.isEmpty {
      if !reachable { return .away(queue.waiting) }
      return .catchingUp(done: queue.deliveredInBatch, total: queue.deliveredInBatch + queue.waiting)
    }
    let ago = queue.lastContact.map { max(0, Int(now.timeIntervalSince($0) / 60)) }
    return .linked(minutesAgo: ago)
  }
}
