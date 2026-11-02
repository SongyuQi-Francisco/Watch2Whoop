import Combine
import Foundation
import GRDB

/// The AI-first Today (flag `ui_ai_first`, #279, docs/ui-ai-first-redesign.md v0.4; the web page ui-ai-first.js is the accepted
/// behaviour). Structure and interaction only: same look, same numbers, same documents as the classic Today.
///
/// Everything for it lives in this file and TodayHomeViews.swift. With the flag off none of it runs: TodayStore, TodayDocs and
/// the classic `content` of TodayView do not know about it, so turning the flag off is a one-switch rollback.
struct TodayHome: Equatable {
  static let flag = "ui_ai_first"
  static func isOn(_ flags: Set<String>) -> Bool { flags.contains(flag) }

  /// `docs` key of `GET /v1/users/me/ui/home`: the account's own document, in the account's own database.
  static let docKey = "ui:home"
  /// Kind and group of its writes in the offline queue.
  static let queueKind = "ui.home"
  /// The cards that can be on my Today, in the classic page's order (server: ui/params.py MODULES).
  static let defaultOrder = ["health_monitor", "strain_coach", "sleep", "sleep_goal", "training", "muscle_load", "journal"]
  static let defaultFocusOptions = ["fat", "muscle", "sleep", "recover", "markers"]
  static let defaultMax = 6   // ui/params.py MAX_MODULES (design D5)

  /// Why a card is on my Today (a short label next to its title).
  enum Reason: String {
    case pinned, focus, finding, used

    var label: String {
      switch self {
      case .pinned: return tr("固定", "Pinned")
      case .focus: return tr("关注", "Focus")
      case .finding: return tr("发现", "Found")
      case .used: return tr("用过", "Used")
      }
    }

    /// What the user pinned or an AI used goes on the page at once (design 4.2 signals 3 and 4, D5); the pace is for the rest.
    var immediate: Bool { self == .pinned || self == .used }
  }

  struct Module: Equatable, Identifiable {
    var key: String
    var reason: Reason?
    var id: String { key }
  }

  struct Finding: Equatable, Identifiable {
    var key: String
    var area: String
    var value: String
    var label: String
    var tone: Tone
    var id: String { key }
  }

  /// The first screen: what the history covers and the most important findings.
  struct Overview: Equatable {
    var findings: [Finding]
    var days: Int
    var nights: Int
    var workouts: Int
  }

  /// The new-user pace, worked out by the server from the account's dates (ui/home.py `pace`).
  struct Pace: Equatable {
    var day: Int
    var limit: Int
    var veteran: Bool
  }

  var focus: [String] = []
  var modules: [Module]
  var hidden: [String] = []
  var overview: Overview?
  var focusOptions = TodayHome.defaultFocusOptions
  var maxModules = TodayHome.defaultMax
  var pace: Pace?

  /// No document yet (first start offline, the server not reached): the classic page's order, no labels, no pace.
  static let fallback = TodayHome(modules: defaultOrder.map { Module(key: $0, reason: nil) })

  init(modules: [Module]) { self.modules = modules }

  init?(json: [String: Any]?) {
    guard let json, let list = json["modules"] as? [[String: Any]] else { return nil }
    modules = list.compactMap { m in
      guard let k = m["key"] as? String else { return nil }
      return Module(key: k, reason: (m["reason"] as? String).flatMap(Reason.init(rawValue:)))
    }
    focus = json["focus"] as? [String] ?? []
    hidden = json["hidden"] as? [String] ?? []
    if let o = json["focus_options"] as? [String], !o.isEmpty { focusOptions = o }
    if let n = (json["max_modules"] as? NSNumber)?.intValue, n > 0 { maxModules = n }
    if let p = json["pace"] as? [String: Any], let limit = (p["limit"] as? NSNumber)?.intValue {
      pace = Pace(day: (p["day"] as? NSNumber)?.intValue ?? 0, limit: limit, veteran: p["veteran"] as? Bool ?? false)
    }
    if let o = json["overview"] as? [String: Any] {
      let st = o["stats"] as? [String: Any] ?? [:]
      let found = (o["findings"] as? [[String: Any]] ?? []).compactMap { f -> Finding? in
        guard let k = f["key"] as? String, let v = f["value"] as? String, let l = f["label"] as? String else { return nil }
        return Finding(key: k, area: f["area"] as? String ?? "", value: v, label: l, tone: f["tone"] as? String == "good" ? .good : .mid)
      }
      overview = Overview(findings: found, days: (st["days"] as? NSNumber)?.intValue ?? 0,
                          nights: (st["nights"] as? NSNumber)?.intValue ?? 0, workouts: (st["workouts"] as? NSNumber)?.intValue ?? 0)
    }
  }

  /// The cards on the page, in the server's order: only those with something to show (no empty cards, design 4.4), at most
  /// `maxModules`, and of those the user did not ask for no more than the pace allows so far (nothing is locked: 全部功能 has them).
  func shown(has: (String) -> Bool) -> [Module] {
    let limit = min(maxModules, pace?.limit ?? maxModules)
    var out: [Module] = []
    for m in modules where has(m.key) {
      if out.count >= maxModules { break }
      if m.reason?.immediate == true || out.count < limit { out.append(m) }
    }
    return out
  }

  /// The cards not on the page, for 全部功能: every other candidate, then those the user took off.
  func notShown(_ shown: [Module]) -> [String] {
    let on = Set(shown.map(\.key))
    return modules.map(\.key).filter { !on.contains($0) } + hidden
  }

  static func focusName(_ k: String) -> String {
    switch k {
    case "fat": return tr("减脂", "Lose fat")
    case "muscle": return tr("增肌", "Build muscle")
    case "sleep": return tr("睡得更好", "Sleep better")
    case "recover": return tr("训练和恢复", "Training and recovery")
    case "markers": return tr("健康指标", "Health markers")
    default: return k
    }
  }

  static func moduleName(_ k: String) -> String {
    switch k {
    case "health_monitor": return tr("健康监测", "Health monitor")
    case "strain_coach": return tr("负荷目标", "Strain target")
    case "sleep": return tr("睡眠", "Sleep")
    case "sleep_goal": return tr("入睡目标", "Bedtime goal")
    case "training": return tr("训练与饮食", "Training & food")
    case "muscle_load": return tr("肌肉负荷", "Muscle load")
    case "journal": return tr("日记", "Journal")
    default: return k
    }
  }

  // MARK: - which cards have something to show

  static func has(_ key: String, model m: TodayModel, flags: Set<String>) -> Bool {
    func values(_ section: String) -> Bool { m.sections.first { $0.id == section }?.metrics.contains { $0.value != nil } ?? false }
    switch key {
    case "health_monitor": return !m.vitals.isEmpty
    case "strain_coach": return coachCard(m) != nil
    case "sleep": return values("sleep")
    case "training": return m.training != nil || !m.isPast
    // these open today's page, so they make no sense on a day looked back at
    case "sleep_goal", "journal": return !m.isPast
    case "muscle_load": return !m.isPast && flags.contains("muscle_load")
    default: return false
    }
  }

  static func coachCard(_ m: TodayModel) -> TodayCoreCard? { m.core.first { $0.id == "coach" } }

  /// These cards draw their own title (the module row above them then only says why they are there).
  static func cardHasHeading(_ key: String, model m: TodayModel) -> Bool {
    (key == "training" && m.training != nil) || (key == "muscle_load" && m.muscle != nil)
  }

  // MARK: - the top of the page

  /// Three rings side by side, recovery in the middle. As on the web page the sleep ring is the night's hours against its need
  /// (design section 6: L1 is recovery, sleep hours, strain); the recovery and strain rings are the classic ones.
  static func rings(_ m: TodayModel) -> [TodayModel.Ring] {
    m.rings.map { r in
      guard r.key == "sleep" else { return r }
      var s = r
      s.label = tr("睡眠", "Sleep")
      s.value = m.detail.sleepMin.map(TodayModel.hm)
      if let a = m.detail.sleepMin, let n = m.detail.sleepNeedMin, n > 0 { s.fraction = min(1, max(0, a / n)) } else { s.fraction = 0 }
      return s
    }
  }

  /// 重点: what is off on the day (需要注意), then the reminders; 1–3 things, with an end (design 4.4).
  static func focusItems(_ m: TodayModel) -> (attention: [TodayAttention], reminders: [TodayModel.Reminder]) {
    let a = Array(m.attention.prefix(TodayHomeParams.maxFocusItems))
    return (a, Array(m.reminders.prefix(max(0, TodayHomeParams.maxFocusItems - a.count))))
  }
}

/// Numbers of the AI-first Today that are not in the server's document.
enum TodayHomeParams {
  /// Design 4.4: 「重点 1–3 件」.
  static let maxFocusItems = 3
  /// A question about short sleep from 30 minutes under the night's need: the threshold of the 「sleep_short」 finding
  /// (body_engine ui/params.py SLEEP_SHORT_MIN) and of the web page's question.
  static let sleepShortMin = 30.0
  /// Design 1.0: 1–3 concrete questions in each place.
  static let maxQuestions = 3
}

// MARK: - questions: concrete ones from the user's own numbers, never an abstract "try the AI"

struct TodayHomeQuestion: Equatable, Identifiable {
  var label: String
  /// What is sent when it differs from the label.
  var send: String?
  var photo = false
  var id: String { label }

  /// What goes to the AI.
  var text: String { send ?? label }
  /// A real question (not a shortcut), good as the search field's example.
  var isExample: Bool { send == nil && !photo }
}

enum TodayHomeAsk {
  static func today(_ m: TodayModel, photo: Bool) -> [TodayHomeQuestion] {
    var qs: [TodayHomeQuestion] = []
    if let r = m.detail.recovery {
      let v = TodayModel.fmt(r)
      qs.append(TodayHomeQuestion(label: tr("为什么恢复 \(v)？", "Why is recovery \(v)?")))
    }
    if let a = m.detail.sleepMin, let n = m.detail.sleepNeedMin, n - a >= TodayHomeParams.sleepShortMin {
      qs.append(TodayHomeQuestion(label: tr("为什么睡得比需要的少？", "Why did I sleep less than I need?")))
    }
    qs.append(TodayHomeQuestion(label: tr("记一顿饭", "Log a meal"), send: tr("帮我记一顿饭", "Log a meal for me")))
    if photo { qs.append(TodayHomeQuestion(label: tr("拍照记饭", "Photo a meal"), photo: true)) }
    return Array(qs.prefix(TodayHomeParams.maxQuestions))
  }

  static var defaultExample: String { tr("今天练什么？", "What should I train today?") }

  /// The first row of a card's long press: a question about exactly that card (web: ui-ai-first.js QUESTION).
  static func question(card key: String, label: String, value: String?) -> String {
    switch key {
    case "recovery":
      let v = value ?? "—"
      return tr("为什么我今天恢复 \(v)？", "Why is my recovery \(v) today?")
    case "sleep": return tr("我昨晚睡得怎么样？", "How did I sleep last night?")
    case "strain":
      let v = value ?? "—"
      return tr("负荷 \(v) 算高吗？", "Is a strain of \(v) high?")
    case "sleep_goal": return tr("今晚几点睡最好？", "When should I go to bed tonight?")
    case "health_monitor": return tr("我的健康监测有什么要注意的？", "Anything to watch in my health monitor?")
    case "strain_coach": return tr("今天还该练多少？", "How much more should I train today?")
    case "training": return tr("今天该练什么？", "What should I train today?")
    case "reminders": return tr("先做哪件事？", "What should I do first?")
    case "circles": return tr("我这周在圈里排第几？", "Where do I rank in my circle this week?")
    default:
      if let value { return tr("「\(label)」\(value)，这说明什么？", "\(label) \(value): what does it say?") }
      return tr("「\(label)」是什么意思？", "What does \(label) mean?")
    }
  }
}

// MARK: - 全部功能: every feature, nothing locked; also what the search field finds

/// Where a row of 全部功能, a search hit or a card's 「看详情」 goes.
enum TodayHomeDest: Equatable {
  case sheet(TodaySheet)
  case sleepDetail
  case trends(TrendsView.Mode)
  case record(RecordTab.Segment)
  case food
  case circles
  case me
  case bodyStatus
  case bedtimeGoal
  case focus
  case allFeatures
}

struct TodayHomeFeature: Equatable, Identifiable {
  var area: String
  var name: String
  var dest: TodayHomeDest
  var id: String { "\(area).\(name)" }
}

enum TodayHomeFeatures {
  /// By area, as on the web page (ui-ai-first.js FEATURES). 身体状态 is here because the native Today had the status cards.
  static func all(circles: Bool) -> [(area: String, items: [TodayHomeFeature])] {
    func area(_ a: String, _ items: [(String, TodayHomeDest)]) -> (area: String, items: [TodayHomeFeature]) {
      (a, items.map { TodayHomeFeature(area: a, name: $0.0, dest: $0.1) })
    }
    var out = [
      area(tr("恢复", "Recovery"), [
        (tr("恢复与 HRV", "Recovery and HRV"), .trends(.metrics)),
        (tr("健康监测", "Health monitor"), .sheet(.monitor)),
        (tr("压力", "Stress"), .sheet(.detail(.stress))),
        (tr("训练负荷", "Training load"), .sheet(.load)),
      ]),
      area(tr("睡眠", "Sleep"), [
        (tr("昨晚的睡眠", "Last night's sleep"), .sleepDetail),
        (tr("睡眠趋势", "Sleep trends"), .trends(.sleep)),
        (tr("入睡目标", "Bedtime goal"), .bedtimeGoal),
      ]),
      area(tr("训练", "Training"), [
        (tr("训练计划和记录", "Training plan and log"), .record(.training)),
        (tr("肌肉负荷", "Muscle load"), .record(.training)),
      ]),
      area(tr("饮食", "Food"), [
        (tr("今天吃了", "Eaten today"), .food),
        (tr("喝水", "Water"), .food),
        (tr("食谱", "Recipes"), .food),
        (tr("采购", "Shopping"), .food),
      ]),
      area(tr("身体", "Body"), [
        (tr("身体状态", "Body status"), .bodyStatus),
        (tr("体重", "Weight"), .sheet(.weight)),
        (tr("身体年龄", "Body age"), .sheet(.detail(.bodyAge))),
        (tr("周报 / 月报", "Weekly / monthly report"), .trends(.report)),
      ]),
      area(tr("健康档案", "Health records"), [
        (tr("化验", "Lab results"), .me),
        (tr("补剂", "Supplements"), .me),
        (tr("日记", "Journal"), .record(.journal)),
      ]),
    ]
    if circles {
      out.append(area(tr("圈子", "Circles"), [
        (tr("圈子动态", "Circle feed"), .circles),
        (tr("邀请好友", "Invite friends"), .circles),
      ]))
    }
    out.append(area(tr("设置", "Settings"), [
      (tr("AI 设置", "AI settings"), .me),
      (tr("数据状态", "Data status"), .me),
      (tr("设置", "Settings"), .me),
    ]))
    return out
  }

  static func normalized(_ s: String) -> String {
    s.lowercased().filter { !$0.isWhitespace && $0 != "·" && $0 != "/" }
  }

  /// What the search field lists: a part of the name, or the whole area; at most 5.
  static func matches(_ q: String, circles: Bool) -> [TodayHomeFeature] {
    let n = normalized(q)
    guard !n.isEmpty else { return [] }
    return Array(all(circles: circles).flatMap(\.items)
      .filter { normalized($0.name).contains(n) || normalized($0.area) == n }.prefix(5))
  }

  /// Enter in the search field: the exact or only match opens, anything else is a question for Jarvis.
  static func direct(_ q: String, circles: Bool) -> TodayHomeFeature? {
    let list = matches(q, circles: circles), n = normalized(q)
    return list.first { normalized($0.name) == n } ?? (list.count == 1 ? list[0] : nil)
  }
}

// MARK: - edits of the local copy, before the server has them

extension TodayHome {
  /// The document with one card's new state (`pinned` / `hidden` / `auto`), until the server's answer replaces it.
  static func patched(_ json: [String: Any], module key: String, state: String) -> [String: Any] {
    var out = json
    var list = (json["modules"] as? [[String: Any]] ?? []).filter { $0["key"] as? String != key }
    var hidden = (json["hidden"] as? [String] ?? []).filter { $0 != key }
    switch state {
    case "hidden": hidden.append(key)
    case "pinned":
      let at = list.prefix { $0["reason"] as? String == Reason.pinned.rawValue }.count
      list.insert(["key": key, "reason": Reason.pinned.rawValue], at: at)
    default: list.append(["key": key, "reason": NSNull()])   // back to the rules: the server puts it in its place
    }
    out["modules"] = list
    out["hidden"] = hidden.sorted()
    return out
  }
}

// MARK: - the store

/// Keeps `ui:home` of the signed-in account: read from the local database (first value synchronously), refreshed from the server,
/// edited locally first and through the offline queue. The account's choices are stored by the server (`ui_state`, `ui_modules`),
/// so they survive a reinstall and are the same on every device. Does nothing until `start`, and `start` is only called with the
/// flag on.
@MainActor
final class TodayHomeStore: ObservableObject {


  @Published private(set) var home: TodayHome?
  /// The first screen is opened once per launch at most, whatever the document says afterwards.
  var overviewOpened = false

  private var cancellable: AnyDatabaseCancellable?
  private weak var session: AccountSession?

  var isStarted: Bool { cancellable != nil }

  func start(_ session: AccountSession) {
    guard cancellable == nil else { return }
    self.session = session
    cancellable = ValueObservation.tracking { db in try DocRecord.fetchOne(db, key: TodayHome.docKey) }
      .removeDuplicates()
      .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] rec in
        let h = TodayHome(json: rec?.json)
        if h != self?.home { self?.home = h }
      })
  }

  /// The flag went off: forget it all (the cached document stays unread; the server answers 404 and the next pull removes it).
  func stop() {
    cancellable?.cancel()
    cancellable = nil
    session = nil
    home = nil
    overviewOpened = false
  }

  /// Unless a change of ours is still on its way (the server's older copy would flip the page back).
  func refresh() async {
    guard let session else { return }
    if pendingWrite(session) { _ = try? await session.sync.queue.drain(send: OfflineQueue.apiSender(session.api)) }
    if pendingWrite(session) { return }
    _ = await DocPuller(api: session.api, db: session.db).pull([DocFetch(TodayHome.docKey, Endpoint.uiHome())])
  }

  func setModule(_ key: String, state: String) {
    write(.put, Endpoint.uiModule(key), ["state": state]) { TodayHome.patched($0, module: key, state: state) }
  }

  func setFocus(_ focus: [String]) {
    write(.put, Endpoint.uiFocus, ["focus": focus]) { var j = $0; j["focus"] = focus; return j }
  }

  func overviewSeen() {
    write(.post, Endpoint.uiOverviewSeen, nil) { var j = $0; j["overview"] = NSNull(); return j }
  }

  private func pendingWrite(_ session: AccountSession) -> Bool {
    (try? session.db.writer.read { db in
      try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM outbox WHERE group_key = ? AND state IN ('pending', 'sending'))",
                        arguments: [TodayHome.queueKind])
    }) ?? false
  }

  /// The local copy first, then the queue (in order, one group); once it has gone out, the server's own order.
  private func write(_ method: APIRequest.Method, _ path: String, _ body: [String: Any]?, patch: ([String: Any]) -> [String: Any]) {
    guard let session else { return }
    if let rec = try? session.db.doc(TodayHome.docKey), let json = rec.json,
       let data = try? JSONSerialization.data(withJSONObject: patch(json)) {
      try? session.db.saveDoc(TodayHome.docKey, json: data, version: rec.version)
    }
    let data = body.flatMap { try? JSONSerialization.data(withJSONObject: $0) }
    _ = try? session.sync.queue.enqueue(kind: TodayHome.queueKind, group: TodayHome.queueKind, method: method, path: path, body: data)
    Task { await refresh() }
  }
}

/// A row of 全部功能 asked for from another tab (我): Today shows it.
@MainActor
final class TodayHomeLink: ObservableObject {
  static let shared = TodayHomeLink()
  @Published var pending: TodayHomeDest?
}
