import Charts
import SwiftUI

/// 健康档案 from 「我」: supplements (with today's check-in), lab results (list → one test's trend), body events.
/// Reads the local copies first, then the server; check-ins are journal answers, local first through the offline
/// queue. Lab entry and supplement changes stay with the user's AI (section 5.5); a body event is sent online.
@MainActor
final class HealthStore: LocalPageStore {
  @Published private(set) var today: String
  @Published private(set) var supplements: [Supplement] = []
  @Published private(set) var labs: [LabSummary] = []
  @Published private(set) var events: [BodyEvent] = []

  init(session: AccountSession) {
    let t = session.db.userToday()
    today = t
    super.init(api: session.api, db: session.db, queue: session.sync.queue)
    observe(keys: [HealthDocs.supplements, HealthDocs.labs, HealthDocs.events, HealthDocs.journal(t)], kinds: ["journal"])
  }

  override func changed() {
    supplements = Supplement.build(view.list(HealthDocs.supplements), journal: view.list(HealthDocs.journal(today)), today: today,
                                   pending: view.pending)
    labs = LabSummary.parseList(view.list(HealthDocs.labs))
    events = BodyEvent.parseList(view.list(HealthDocs.events))
  }

  func refresh(_ kind: MeSheet.RecordKind) async {
    await send()
    switch kind {
    case .supplements:
      await pull([DocFetch(HealthDocs.supplements, HealthAPI.supplements), DocFetch(HealthDocs.journal(today), HealthAPI.journal(today))])
    case .labs: await pull([DocFetch(HealthDocs.labs, HealthAPI.labs)])
    case .events: await pull([DocFetch(HealthDocs.events, HealthAPI.events)])
    }
  }

  /// Today's check-in of a supplement (its journal item), last tap wins.
  func setTaken(_ s: Supplement, _ taken: Bool) async -> WriteOutcome {
    guard let key = s.journalKey else { return WriteOutcome(clientId: nil, item: nil, saved: false) }
    let path = "/v1/users/me/journal/\(today)/\(key)"
    let overlay: [String: Any] = ["key": key, "value": taken ? 1 : NSNull()]
    let out = taken
      ? await write(kind: "journal", date: today, group: "journal:\(today):\(key)", method: .put, path: path, body: ["value": 1],
                    overlay: overlay, supersede: true)
      : await write(kind: "journal", date: today, group: "journal:\(today):\(key)", method: .delete, path: path, body: nil,
                    overlay: overlay, clientIdInBody: false, supersede: true)
    if out.sent { await pull([DocFetch(HealthDocs.journal(today), HealthAPI.journal(today))]) }
    return out
  }

  func labHistory(_ key: String) -> LabHistory? { LabHistory.parse((try? db.doc(HealthDocs.lab(key)))?.json) }

  func loadLab(_ key: String) async -> LabHistory? {
    _ = await RecordsPuller(api: api, db: db).pull([DocFetch(HealthDocs.lab(key), HealthAPI.lab(key))], kinds: [])
    return labHistory(key)
  }

  /// A new body event (online: the server gives it its id and time zone).
  func addEvent(_ body: [String: Any]) async -> String? {
    guard let data = try? JSONSerialization.data(withJSONObject: body) else { return tr("出错了", "Something went wrong") }
    do {
      _ = try await api.data(APIRequest(method: .post, path: HealthAPI.eventsPath, body: data))
      await pull([DocFetch(HealthDocs.events, HealthAPI.events)])
      return nil
    } catch let e as APIError {
      return e.userMessage
    } catch {
      return tr("出错了", "Something went wrong")
    }
  }
}

extension MeSheet.RecordKind: Identifiable {
  var id: String { rawValue }
  var title: String {
    switch self {
    case .supplements: return tr("补剂", "Supplements")
    case .labs: return tr("化验", "Lab results")
    case .events: return tr("身体事件", "Body events")
    }
  }
}

/// The sheet behind 「我」 → 补剂 / 化验 / 身体事件. `openLab`: a test's trend to open at once.
struct HealthRecordView: View {
  let kind: MeSheet.RecordKind
  var openLab: String?
  @EnvironmentObject private var session: AccountSession

  init(kind: MeSheet.RecordKind, openLab: String? = nil) {
    self.kind = kind
    self.openLab = openLab
  }

  var body: some View {
    HealthRecordContent(kind: kind, openLab: openLab, session: session)
  }
}

private struct HealthRecordContent: View {
  let kind: MeSheet.RecordKind
  let openLab: String?
  @StateObject private var store: HealthStore
  @State private var toast: Toast?
  @State private var lab: LabSummary?
  @State private var addEvent = false

  init(kind: MeSheet.RecordKind, openLab: String?, session: AccountSession) {
    self.kind = kind
    self.openLab = openLab
    _store = StateObject(wrappedValue: HealthStore(session: session))
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      RecordStatusLine(offline: store.offline, refreshing: store.refreshing)
      switch kind {
      case .supplements: supplements
      case .labs: labs
      case .events: events
      }
    }
    .task {
      await store.refresh(kind)
      if let key = openLab { lab = store.labs.first { $0.key == key } }
    }
    .toast($toast)
    .sheet(item: $lab) { l in
      SheetContainer(title: l.name, close: { lab = nil }) { LabTrendView(store: store, key: l.key) }
        .presentationDetents([.large])
    }
    .sheet(isPresented: $addEvent) {
      SheetContainer(title: tr("身体事件", "Body event"), close: { addEvent = false }) {
        EventForm(store: store) { msg in
          addEvent = false
          if let msg { toast = Toast(msg) }
        }
      }
      .presentationDetents([.large])
    }
  }

  // MARK: supplements

  @ViewBuilder private var supplements: some View {
    let active = store.supplements.filter(\.active), other = store.supplements.filter { !$0.active }
    if store.supplements.isEmpty {
      EmptyNote(text: tr("还没有补剂。让你的 AI 记下你在吃的。", "No supplements yet. Ask your AI to add what you take."))
    }
    if !active.isEmpty {
      RowGroup(title: tr("今天打卡", "Today's check-in")) {
        ForEach(Array(active.enumerated()), id: \.element.id) { n, s in
          if n > 0 { RowDivider(inset: 34) }
          supplementRow(s)
        }
      }
    }
    if !other.isEmpty {
      RowGroup(title: tr("没在吃", "Not taking")) {
        ForEach(Array(other.enumerated()), id: \.element.id) { n, s in
          if n > 0 { RowDivider(inset: 0) }
          HStack {
            VStack(alignment: .leading, spacing: 2) {
              Text(s.name).font(Typo.body).foregroundColor(Palette.text2)
              if let d = s.dose { Text(d).font(Typo.caption).foregroundColor(Palette.text3) }
            }
            Spacer()
            Badge(label: s.statusTitle)
          }
          .padding(.vertical, 11)
        }
      }
    }
  }

  private func supplementRow(_ s: Supplement) -> some View {
    Button {
      guard s.journalKey != nil else { return }
      Task {
        let out = await store.setTaken(s, !s.takenToday)
        if !out.sent { toast = Toast(out.message) }
      }
    } label: {
      HStack(spacing: 12) {
        ZStack {
          Circle().stroke(s.takenToday ? Palette.good : Palette.text3, lineWidth: 1.5).frame(width: 22, height: 22)
          if s.takenToday { Icon(.check, size: 14).foregroundColor(Palette.good) }
        }
        .opacity(s.journalKey == nil ? 0.3 : 1)
        VStack(alignment: .leading, spacing: 2) {
          Text(s.name).font(Typo.body).foregroundColor(Palette.text)
          Text([s.dose, s.timing].compactMap { $0 }.joined(separator: " · ")).font(Typo.caption).foregroundColor(Palette.text2)
        }
        if let p = s.pending { PendingMark(needsAttention: p.needsAttention) }
        Spacer()
        if s.status == "as_needed" { Badge(label: s.statusTitle) }
      }
      .padding(.vertical, 11)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityAddTraits(s.takenToday ? .isSelected : [])
  }

  // MARK: labs

  @ViewBuilder private var labs: some View {
    if store.labs.isEmpty {
      EmptyNote(text: tr("还没有化验记录。把化验单拍给你的 AI。", "No lab results yet. Send your lab report to your AI."))
    } else {
      RowGroup {
        ForEach(Array(store.labs.enumerated()), id: \.element.id) { n, l in
          if n > 0 { RowDivider(inset: 0) }
          Button { lab = l } label: {
            HStack(spacing: 10) {
              VStack(alignment: .leading, spacing: 3) {
                Text(l.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
                Text(l.date.map(RecordDay.short) ?? "").font(Typo.caption).foregroundColor(Palette.text3)
              }
              Spacer(minLength: 6)
              if l.history.count >= 2 { TrendLine(values: l.history, tone: LabSummary.tone(l.status)).frame(width: 54, height: 22) }
              VStack(alignment: .trailing, spacing: 3) {
                Text("\(l.value.map { RecordFormat.num($0, 2) } ?? l.valueText ?? "—") \(l.unit ?? "")")
                  .font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
                if let st = LabSummary.statusTitle(l.status) { Badge(label: st, tone: LabSummary.tone(l.status)) }
              }
              Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
            }
            .padding(.vertical, 11)
            .contentShape(Rectangle())
          }
          .buttonStyle(PressScale())
        }
      }
    }
  }

  // MARK: events

  @ViewBuilder private var events: some View {
    SecondaryButton(icon: .plus, title: tr("记身体事件", "Log a body event")) { addEvent = true }
    if store.events.isEmpty {
      EmptyNote(text: tr("还没有身体事件", "No body events yet"))
    } else {
      RowGroup {
        ForEach(Array(store.events.enumerated()), id: \.element.id) { n, e in
          if n > 0 { RowDivider(inset: 0) }
          HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
              Text(e.title).font(Typo.body).foregroundColor(Palette.text)
              Text([RecordDay.short(e.date), BodyEvent.categoryTitle(e.category), e.bodyPart].compactMap { $0 }.joined(separator: " · "))
                .font(Typo.caption).foregroundColor(Palette.text2)
            }
            Spacer()
            if let s = e.severity { Badge(label: tr("程度 \(s)", "Level \(s)"), tone: s >= 4 ? .bad : s >= 3 ? .mid : .text) }
            if e.resolved != nil { Badge(label: tr("已好", "Resolved"), tone: .good) }
          }
          .padding(.vertical, 11)
        }
      }
    }
  }
}

/// A tiny line of the last readings (no axes).
struct TrendLine: View {
  let values: [Double]
  var tone: Tone = .text

  var body: some View {
    GeometryReader { g in
      let lo = values.min() ?? 0, hi = values.max() ?? 1, span = hi - lo == 0 ? 1 : hi - lo
      Path { p in
        for (i, v) in values.enumerated() {
          let pt = CGPoint(x: g.size.width * CGFloat(i) / CGFloat(max(1, values.count - 1)),
                           y: g.size.height - 2 - (g.size.height - 4) * CGFloat((v - lo) / span))
          if i == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
        }
      }
      .stroke(tone == .text ? Palette.text2 : tone.color, style: StrokeStyle(lineWidth: 1.6, lineCap: .round, lineJoin: .round))
    }
    .accessibilityHidden(true)
  }
}

/// One test over time: the readings as a line (lab = filled, home device = hollow), the reference range as a band,
/// then every reading with its change from the previous one.
struct LabTrendView: View {
  @ObservedObject var store: HealthStore
  let key: String
  @State private var history: LabHistory?
  @State private var loaded = false

  /// The y range: the readings and the reference range, with a little room.
  static func domain(_ h: LabHistory, _ values: [Double]) -> ClosedRange<Double> {
    let all = values + [band(h, values)?.0, band(h, values)?.1].compactMap { $0 }
    guard let lo = all.min(), let hi = all.max() else { return 0...1 }
    let pad = max((hi - lo) * 0.15, abs(hi) * 0.02, 0.01)
    return (lo - pad)...(hi + pad)
  }

  /// The reference range as a band; a one-sided range runs to just past the readings.
  static func band(_ h: LabHistory, _ values: [Double]) -> (Double, Double)? {
    guard let vmin = values.min(), let vmax = values.max() else { return nil }
    switch (h.refLow, h.refHigh) {
    case let (lo?, hi?): return (lo, hi)
    case let (nil, hi?): return (min(vmin, hi) * 0.9, hi)
    case let (lo?, nil): return (lo, max(vmax, lo) * 1.1)
    default: return nil
    }
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      if let h = history {
        let pts = h.rows.compactMap { r -> (Date, Double, Bool)? in
          guard let v = r.value, let d = RecordDay.date(r.date) else { return nil }
          return (d, v, r.homeDevice)
        }
        if pts.count >= 1 {
          Chart {
            if let band = Self.band(h, pts.map(\.1)), let first = pts.first?.0, let last = pts.last?.0 {
              RectangleMark(xStart: .value("from", first.addingTimeInterval(-86_400 * 10)), xEnd: .value("to", last.addingTimeInterval(86_400 * 10)),
                            yStart: .value("low", band.0), yEnd: .value("high", band.1))
                .foregroundStyle(Palette.good.opacity(0.12))
            }
            ForEach(Array(pts.enumerated()), id: \.offset) { _, p in
              LineMark(x: .value("date", p.0), y: .value("value", p.1)).foregroundStyle(Palette.text2)
              PointMark(x: .value("date", p.0), y: .value("value", p.1))
                .foregroundStyle(p.2 ? Palette.bg : Palette.text)
                .symbolSize(50)
                .annotation(position: .top) {
                  Text(RecordFormat.num(p.1, 2)).font(.system(size: 10).monospacedDigit()).foregroundColor(Palette.text2)
                }
            }
          }
          .chartYScale(domain: Self.domain(h, pts.map(\.1)))
          .frame(height: 200)
          HStack(spacing: 12) {
            let unit = h.unit ?? ""
            if let lo = h.refLow, let hi = h.refHigh {
              let r = "\(RecordFormat.num(lo, 2))–\(RecordFormat.num(hi, 2)) \(unit)"
              Text(tr("参考 \(r)", "Range \(r)"))
            } else if let hi = h.refHigh {
              let r = "< \(RecordFormat.num(hi, 2)) \(unit)"
              Text(tr("参考 \(r)", "Range \(r)"))
            }
          }
          .font(Typo.caption).foregroundColor(Palette.text2)
        }
        VStack(spacing: 0) {
          ForEach(h.rows.reversed()) { r in
            HStack(alignment: .firstTextBaseline, spacing: 8) {
              Text(RecordDay.short(r.date)).font(Typo.caption).foregroundColor(Palette.text2).frame(width: 52, alignment: .leading)
              Text("\(r.value.map { RecordFormat.num($0, 2) } ?? r.valueText ?? "—")").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
              if let d = r.delta {
                Text("\(d > 0 ? "+" : "")\(RecordFormat.num(d, 2))").font(Typo.caption.monospacedDigit()).foregroundColor(h.deltaTone(d).color)
              }
              Spacer()
              if r.homeDevice { Badge(label: tr("家测", "Home"), tone: .mid) }
              if let f = r.fasting { Badge(label: f ? tr("空腹", "Fasting") : tr("饭后", "After a meal")) }
              if let st = LabSummary.statusTitle(r.status) { Badge(label: st, tone: LabSummary.tone(r.status)) }
            }
            .padding(.vertical, 10)
            .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
          }
        }
      } else {
        EmptyNote(text: loaded ? tr("需要联网", "Needs a connection") : "…")
      }
    }
    .task {
      history = store.labHistory(key)
      if let h = await store.loadLab(key) { history = h }
      loaded = true
    }
  }
}

/// 记身体事件: what happened, category, body part, severity 1–5, when, note. Sent online.
struct EventForm: View {
  var store: HealthStore?
  let done: (String?) -> Void
  @EnvironmentObject private var session: AccountSession
  @State private var title = ""
  @State private var category = "pain"
  @State private var part = ""
  @State private var severity: Int?
  @State private var when = Date()
  @State private var note = ""
  @State private var busy = false
  @State private var error: String?

  init(store: HealthStore? = nil, done: @escaping (String?) -> Void) {
    self.store = store
    self.done = done
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      FormField(label: tr("发生了什么", "What happened"), text: $title, placeholder: tr("例：头晕", "e.g. dizzy"), background: Palette.inSheet)
      VStack(alignment: .leading, spacing: 6) {
        Text(tr("类别", "Category")).font(Typo.label).foregroundColor(Palette.text2)
        ScrollView(.horizontal, showsIndicators: false) {
          HStack(spacing: 6) {
            ForEach(BodyEvent.categories, id: \.self) { c in
              Button { category = c } label: {
                Text(BodyEvent.categoryTitle(c)).font(.system(size: 14, weight: .medium))
                  .foregroundColor(category == c ? Palette.bg : Palette.text)
                  .padding(.vertical, 8).padding(.horizontal, 12)
                  .background(Capsule().fill(category == c ? Palette.text : Palette.inSheet))
              }
              .buttonStyle(PressScale())
            }
          }
        }
      }
      FormField(label: tr("部位", "Body part"), text: $part, placeholder: tr("可不填", "Optional"), background: Palette.inSheet)
      VStack(alignment: .leading, spacing: 6) {
        Text(tr("程度", "Severity")).font(Typo.label).foregroundColor(Palette.text2)
        HStack(spacing: 8) {
          ForEach(1...5, id: \.self) { n in
            Button { severity = severity == n ? nil : n } label: {
              Text("\(n)").font(.system(size: 15, weight: .semibold))
                .foregroundColor(severity == n ? Palette.bg : Palette.text)
                .frame(maxWidth: .infinity, minHeight: 40)
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(severity == n ? Palette.text : Palette.inSheet))
            }
            .buttonStyle(PressScale())
          }
        }
      }
      DatePicker(tr("时间", "When"), selection: $when, in: ...Date())
        .font(Typo.body).foregroundColor(Palette.text2)
      FormField(label: tr("备注", "Note"), text: $note, placeholder: tr("可不填", "Optional"), background: Palette.inSheet)
      if let error { Text(error).font(Typo.caption).foregroundColor(Palette.bad) }
      PrimaryButton(title: tr("保存", "Save"), busy: busy) { save() }
        .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty)
    }
  }

  private func save() {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    var body: [String: Any] = ["title": title.trimmingCharacters(in: .whitespaces), "category": category, "time_precision": "minute",
                               "occurred_at": f.string(from: when)]
    if !part.trimmingCharacters(in: .whitespaces).isEmpty { body["body_part"] = part.trimmingCharacters(in: .whitespaces) }
    if !note.trimmingCharacters(in: .whitespaces).isEmpty { body["note"] = note.trimmingCharacters(in: .whitespaces) }
    if let severity { body["severity"] = severity }
    busy = true
    Task {
      let s = store ?? HealthStore(session: session)
      let err = await s.addEvent(body)
      busy = false
      let t = body["title"] as? String ?? ""
      if let err { error = err } else { done(tr("已记录：\(t)", "Logged: \(t)")) }
    }
  }
}
