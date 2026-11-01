import Foundation
import GRDB

/// The state every record page (N5: 饮食, 记录, 健康档案) shares: it observes its documents and pending rows in the
/// local database (first value synchronously, so cached data is on screen in the first frame), refreshes them from the
/// server, and writes the local-first way (docs/ios-native-app.md section 9, "新页面的做法").
@MainActor
class LocalPageStore: ObservableObject {
  @Published private(set) var view = LocalView()
  /// The last refresh found no connection: the page says it shows the last data.
  @Published private(set) var offline = false
  @Published private(set) var refreshing = false

  let api: APIClient
  let db: AppDatabase
  let queue: OfflineQueue
  private var cancellable: AnyDatabaseCancellable?
  private(set) var keys: [String] = []
  private(set) var kinds: [String] = []

  init(api: APIClient, db: AppDatabase, queue: OfflineQueue) {
    self.api = api
    self.db = db
    self.queue = queue
  }

  /// (Re)starts the observation of these documents and pending kinds.
  func observe(keys: [String], kinds: [String]) {
    guard keys != self.keys || kinds != self.kinds || cancellable == nil else { return }
    self.keys = keys
    self.kinds = kinds
    cancellable?.cancel()
    cancellable = ValueObservation.tracking { db in try LocalView.read(db, keys: keys, kinds: kinds) }
      .removeDuplicates()
      .start(in: db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] v in
        self?.view = v
        self?.changed()
      })
  }

  /// Called after every new local view (subclasses derive their models here).
  func changed() {}

  func stop() {
    cancellable?.cancel()
    cancellable = nil
  }

  /// Fetches the documents and prunes rows the server has now.
  func pull(_ fetches: [DocFetch]) async {
    refreshing = true
    let out = await RecordsPuller(api: api, db: db).pull(fetches, kinds: kinds)
    offline = out.offline
    refreshing = false
  }

  /// Sends what the offline queue has now; errors stay in the queue (「没提交」).
  func send() async {
    _ = try? await queue.drain(send: OfflineQueue.apiSender(api))
  }

  /// Writes locally, sends at once, and says how it went for this write.
  @discardableResult
  func write(kind: String, date: String?, group: String, method: APIRequest.Method, path: String, body: [String: Any]?,
             overlay: [String: Any]? = nil, clientIdInBody: Bool = true, supersede: Bool = false) async -> WriteOutcome {
    do {
      if supersede { try LocalRecords.supersede(db, group: group) }
      let cid = try LocalRecords.write(db, kind: kind, date: date, group: group, method: method.rawValue, path: path,
                                       body: body, overlay: overlay, clientIdInBody: clientIdInBody)
      await send()
      return WriteOutcome(clientId: cid, item: LocalRecords.outboxState(db, clientId: cid))
    } catch {
      return WriteOutcome(clientId: nil, item: nil, saved: false)
    }
  }

  /// Undo of one write: still queued → dropped here; already sent → `online` runs (it needs the network).
  func undo(clientId: String?, online: () async throws -> Void) async -> Bool {
    if let clientId, (try? LocalRecords.cancel(db, clientId: clientId)) == true { return true }
    do {
      try await online()
      return true
    } catch {
      return false
    }
  }
}

/// How a local-first write went after the first send attempt.
struct WriteOutcome {
  var clientId: String?
  /// The queue entry after the attempt: nil = the server has it.
  var item: OutboxItem?
  var saved = true

  var sent: Bool { saved && item == nil }
  var conflict: Bool { item?.state == .conflict }
  var failed: Bool { item?.state == .failed }
  /// Kept on the phone (no connection, server busy): sent later.
  var queued: Bool { item?.state == .pending || item?.state == .sending }

  /// The toast after a write.
  var message: String {
    if !saved { return tr("没存上，再试一次", "Not saved, try again") }
    if queued { return tr("离线，已存在本机，联网后自动提交", "Offline: saved on this phone, sent when you're back online") }
    if failed { return item?.lastError ?? tr("没提交成功", "Not accepted by the server") }
    return tr("已记录", "Logged")
  }
}
