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

  /// Takes back an event the phone has not got (undo of a quick log right after it): only while it is pending or its
  /// transfer failed. False once it was handed to WatchConnectivity; then the phone gets an `undo` event instead.
  mutating func cancel(_ id: String) -> Bool {
    guard let i = items.firstIndex(where: { $0.event.id == id }), items[i].state != .sending else { return false }
    items.remove(at: i)
    return true
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

/// The sync mark (plan 2.6, mockups S1–S7): the icon's shape is the state, the text is an age or a count, never a clock
/// time. It joins two things: the watch's own link to the phone (the event queue) and the phone's capsule about its data,
/// aged on the watch (a phone that has gone quiet cannot say so itself).
enum WatchSyncState: Equatable {
  /// S8: the phone has never told us who is signed in, or it signed out.
  case signedOut
  /// S6: a transfer failed; tap = retry.
  case failed(Int)
  /// S2 / S5: handing over; `done` of `total` in this batch.
  case catchingUp(done: Int, total: Int)
  /// S4: the phone is not reachable; `n` events are kept on the watch.
  case away(Int)
  /// S2: the phone is syncing its data.
  case phoneSyncing
  /// S7: the phone could not reach the server (its numbers are its last copy).
  case offline
  /// The phone needs the user (permission, sync off, missing days): the phone capsule's label, shown as it is.
  case behind(String)
  /// The phone's own sync failed.
  case phoneFailed
  /// S3: the numbers are older than `WatchParams.staleAfterMin` minutes: drawn grey, with their age.
  case stale(minutesAgo: Int)
  /// S1: fine; minutes since the newest data (nil = unknown).
  case linked(minutesAgo: Int?)

  /// The numbers are drawn grey (S3: 「不冒充最新」).
  var greysNumbers: Bool {
    if case .stale = self { return true }
    return false
  }

  static func derive(context: WatchContext?, queue: WatchEventQueue, reachable: Bool, now: Date,
                     staleAfter: Int = WatchParams.staleAfterMin) -> WatchSyncState {
    guard let context, context.signedIn else { return .signedOut }
    if queue.failed > 0 { return .failed(queue.failed) }
    if !queue.isEmpty {
      if !reachable { return .away(queue.waiting) }
      return .catchingUp(done: queue.deliveredInBatch, total: queue.deliveredInBatch + queue.waiting)
    }
    // the age of what is on screen: the newest data behind the numbers, else the last time we heard from the phone
    let since = context.today?.dataAt ?? queue.lastContact ?? context.today?.computedAt
    let ago = since.map { max(0, Int(now.timeIntervalSince($0) / 60)) }
    let heardAgo = now.timeIntervalSince(queue.lastContact ?? context.sentAt)
    if let phone = context.today?.phone {
      switch phone.kind {
      case .busy where heardAgo <= WatchParams.phoneBusyFreshSec: return .phoneSyncing
      case .failed: return .phoneFailed
      case .offline: return .offline
      case .behind, .off: return .behind(phone.label ?? tr("需处理", "Check"))
      case .stale: return .stale(minutesAgo: ago ?? staleAfter)
      default: break
      }
    }
    if let ago, ago >= staleAfter { return .stale(minutesAgo: ago) }
    return .linked(minutesAgo: ago)
  }

  /// 「2分前」 / 「6时前」 / 「3天前」: the age grammar of the phone's capsule.
  static func age(_ minutes: Int?) -> String {
    guard let m = minutes else { return "—" }
    if m < 1 { return tr("刚刚", "now") }
    if m < WatchParams.ageHoursAfterMin { return tr("\(m)分前", "\(m)m ago") }
    if m < 24 * 60 { return tr("\(m / 60)时前", "\(m / 60)h ago") }
    return tr("\(m / (24 * 60))天前", "\(m / (24 * 60))d ago")
  }
}
