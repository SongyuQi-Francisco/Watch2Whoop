import GRDB
import SwiftUI

/// 「记体重」 (weight.js of the page): the weight, a day (default today), what the measurement was like (chips, several
/// allowed; clothed / unclothed exclude each other) and one line of note; the latest weigh-ins below.
/// Written the local-first way (docs/ios-native-app.md section 5.4): a `records` row (kind `weight`, pending) and an
/// offline-queue entry `POST /v1/users/me/weight` with a `client_id`; a 409 "another value that day" offers to overwrite.
enum WeightLog {
  static let minKg = 30.0, maxKg = 250.0   // the server's CHECKIN_RANGES["weight_kg"]
  /// Keys are the server's (weightlog/params.CONTEXT_TAGS); order = chip order.
  static let tags: [(String, () -> String)] = [
    ("fasting", { tr("早上空腹", "Fasting (morning)") }), ("after_toilet", { tr("如厕后", "After the toilet") }),
    ("after_meal", { tr("饭后", "After a meal") }), ("after_workout", { tr("运动后", "After a workout") }),
    ("before_bed", { tr("睡前", "Before bed") }), ("clothed", { tr("穿衣", "Clothed") }), ("unclothed", { tr("未穿衣", "Unclothed") }),
  ]
  static let exclusive: [[String]] = [["clothed", "unclothed"]]

  static func label(_ key: String) -> String { tags.first { $0.0 == key }?.1() ?? key }

  /// Toggling a chip: on adds it and drops the other of an exclusive pair; off removes it.
  static func toggle(_ chosen: Set<String>, _ key: String) -> Set<String> {
    var out = chosen
    if out.contains(key) { out.remove(key); return out }
    out.insert(key)
    for g in exclusive where g.contains(key) { for o in g where o != key { out.remove(o) } }
    return out
  }

  /// Parses what was typed ("81,4" too); nil when outside 30–250 kg.
  static func kg(_ text: String) -> Double? {
    guard let v = Double(text.replacingOccurrences(of: ",", with: ".").trimmingCharacters(in: .whitespaces)),
          v >= minKg, v <= maxKg else { return nil }
    return (v * 10).rounded() / 10
  }

  /// The request body; `measured_at` only for today's weigh-in.
  static func body(day: String, kg: Double, tags: Set<String>, note: String, clientId: String, isToday: Bool,
                   overwrite: Bool = false, now: Date = Date()) -> [String: Any] {
    var b: [String: Any] = ["date": day, "weight_kg": kg, "client_id": clientId,
                            "context_tags": WeightLog.tags.map(\.0).filter { tags.contains($0) },
                            "note": note.trimmingCharacters(in: .whitespacesAndNewlines)]
    if isToday { b["measured_at"] = ISO8601DateFormatter().string(from: now) }
    if overwrite { b["overwrite"] = true }
    return b
  }

  /// Local row first, then the queue. Returns the client id (the outbox entry's too, so the sheet can follow it).
  @discardableResult
  static func save(_ db: AppDatabase, queue: OfflineQueue, body: [String: Any]) throws -> String {
    let clientId = body["client_id"] as? String ?? UUID().uuidString.lowercased()
    let data = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
    try db.writer.write { db in
      try db.execute(sql: """
        INSERT INTO records (kind, id, date, client_id, payload, updated_at, pending) VALUES ('weight', ?, ?, ?, ?, ?, 1)
        ON CONFLICT(kind, id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at, pending = 1
        """, arguments: ["c:\(clientId)", body["date"] as? String, clientId, data, Date().timeIntervalSince1970])
    }
    try queue.enqueue(kind: "weight", group: "weight", method: .post, path: Endpoint.weight, body: data, clientId: clientId)
    return clientId
  }

  /// After the queue answered: sent → the row is no longer pending.
  static func markSent(_ db: AppDatabase, clientId: String) {
    try? db.writer.write { db in
      try db.execute(sql: "UPDATE records SET pending = 0 WHERE kind = 'weight' AND id = ?", arguments: ["c:\(clientId)"])
    }
  }
}

struct WeightSheet: View {
  let today: String?
  let done: () -> Void
  @EnvironmentObject private var session: AccountSession
  @State private var text = ""
  @State private var day = Date()
  @State private var chosen: Set<String> = []
  @State private var note = ""
  @State private var busy = false
  @State private var message: String?
  @State private var conflict: [String: Any]?
  @State private var recent: [[String: Any]] = []
  @FocusState private var focused: Bool

  private var zone: TimeZone { .current }
  private var todayStr: String { today ?? LocalDay.string(Date(), zone) }

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack(spacing: 10) {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
          TextField("0.0", text: $text)
            .keyboardType(.decimalPad)
            .font(.system(size: 32, weight: .semibold).monospacedDigit())
            .focused($focused)
            .accessibilityLabel(tr("体重", "Weight"))
          Text("kg").font(Typo.body).foregroundColor(Palette.text2)
        }
        .padding(.horizontal, 14).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.inSheet))
        DatePicker("", selection: $day, in: ...Date(), displayedComponents: .date)
          .labelsHidden()
          .accessibilityLabel(tr("日期", "Date"))
      }
      VStack(alignment: .leading, spacing: 8) {
        Text(tr("测量情况", "Measured")).font(Typo.label).foregroundColor(Palette.text2)
        FlowChips(items: WeightLog.tags.map { ($0.0, $0.1()) }, chosen: chosen) { chosen = WeightLog.toggle(chosen, $0) }
      }
      TextField(tr("备注（选填）", "Note (optional)"), text: $note)
        .font(Typo.body)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.inSheet))
      PrimaryButton(title: tr("记下", "Save"), busy: busy) { save(overwrite: false) }
      if let message {
        HStack {
          Text(message).font(Typo.caption).foregroundColor(Palette.text2)
          Spacer()
          if conflict != nil {
            Button(tr("用这次的覆盖", "Overwrite")) { save(overwrite: true) }.font(Typo.bodyStrong).foregroundColor(Palette.text)
          }
        }
      }
      if !recent.isEmpty {
        VStack(spacing: 0) {
          ForEach(Array(recent.enumerated()), id: \.offset) { _, r in
            HStack(alignment: .firstTextBaseline, spacing: 10) {
              Text(TodayModel.shortDay(r["date"] as? String)).font(Typo.caption).foregroundColor(Palette.text2).frame(width: 48, alignment: .leading)
              Text("\(TodayModel.fmt(TodayModel.num(r["weight_kg"]) ?? 0, digits: 1)) kg").font(Typo.bodyStrong.monospacedDigit())
              Text(((r["tags"] as? [String]) ?? []).map(WeightLog.label).joined(separator: " · "))
                .font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1)
              Spacer()
            }
            .padding(.vertical, 9)
            .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
          }
        }
      }
    }
    .padding(.top, 4)
    .onAppear {
      focused = true
      loadRecent()
    }
  }

  private func loadRecent() {
    if let json = try? session.db.doc(TodayDocs.weightRecent)?.json { recent = json["items"] as? [[String: Any]] ?? [] }
    Task {
      _ = await DocPuller(api: session.api, db: session.db).pull([DocFetch(TodayDocs.weightRecent, Endpoint.weightRecent())])
      if let json = try? session.db.doc(TodayDocs.weightRecent)?.json { recent = json["items"] as? [[String: Any]] ?? [] }
    }
  }

  private func save(overwrite: Bool) {
    guard let kg = WeightLog.kg(text) else { message = tr("体重写 30–250 kg", "Weight must be 30–250 kg"); return }
    let dayStr = LocalDay.string(day, zone)
    guard dayStr <= todayStr else { message = tr("不能记未来的日子", "Can't log a future day"); return }
    let queue = session.sync.queue
    if overwrite, let old = conflict?["client_id"] as? String,
       let item = (try? queue.all())?.first(where: { $0.clientId == old }), let id = item.id {
      try? queue.discard(id)
    }
    let body = WeightLog.body(day: dayStr, kg: kg, tags: chosen, note: note, clientId: UUID().uuidString.lowercased(),
                              isToday: dayStr == todayStr, overwrite: overwrite)
    busy = true
    conflict = nil
    Task {
      defer { busy = false }
      guard let cid = try? WeightLog.save(session.db, queue: queue, body: body) else {
        message = tr("没存上，再试一次", "Not saved, try again"); return
      }
      _ = try? await queue.drain(send: OfflineQueue.apiSender(session.api))
      let item = (try? queue.all())?.first { $0.clientId == cid }
      switch item?.state {
      case nil:
        WeightLog.markSent(session.db, clientId: cid)
        message = tr("已记录 \(TodayModel.fmt(kg, digits: 1)) kg", "Logged \(TodayModel.fmt(kg, digits: 1)) kg")
        text = ""
        note = ""
        loadRecent()
        _ = await DocPuller(api: session.api, db: session.db).pull([DocFetch(TodayDocs.memory, Endpoint.memory())])
      case .conflict:
        conflict = body
        message = tr("这天已经有另一个体重", "That day already has another weight")
      case .failed:
        message = item?.lastError ?? tr("出错了", "Something went wrong")
      default:
        message = tr("离线，已存在本机，联网后自动提交", "Offline: saved on this phone, sent when you're back online")
      }
    }
  }
}

/// Wrapping chips that toggle (the measurement tags).
struct FlowChips: View {
  let items: [(String, String)]
  let chosen: Set<String>
  let toggle: (String) -> Void

  var body: some View {
    FlowLayout(spacing: 8) {
      ForEach(items, id: \.0) { item in
        let key = item.0, label = item.1, on = chosen.contains(item.0)
        Button { toggle(key) } label: {
          Text(label).font(.system(size: 14, weight: .medium))
            .foregroundColor(on ? Palette.bg : Palette.text)
            .padding(.vertical, 8).padding(.horizontal, 12)
            .background(Capsule().fill(on ? Palette.text : Palette.inSheet))
        }
        .buttonStyle(PressScale())
        .accessibilityAddTraits(on ? .isSelected : [])
      }
    }
  }
}

/// Left-to-right wrapping layout (iOS 16 Layout).
struct FlowLayout: Layout {
  var spacing: CGFloat = 8

  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    let width = proposal.width ?? .infinity
    var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, maxX: CGFloat = 0
    for s in subviews {
      let size = s.sizeThatFits(.unspecified)
      if x > 0, x + size.width > width { x = 0; y += row + spacing; row = 0 }
      x += size.width + spacing
      maxX = max(maxX, x - spacing)
      row = max(row, size.height)
    }
    return CGSize(width: min(width, maxX), height: y + row)
  }

  func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
    var x = bounds.minX, y = bounds.minY, row: CGFloat = 0
    for s in subviews {
      let size = s.sizeThatFits(.unspecified)
      if x > bounds.minX, x + size.width > bounds.maxX { x = bounds.minX; y += row + spacing; row = 0 }
      s.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
      x += size.width + spacing
      row = max(row, size.height)
    }
  }
}
