import SwiftUI

// 长期记忆 (assistant/memory_api.py) and 反馈记录 (feedback/api.py): what the user's AI keeps and what it reported to the
// developers; the user can always see and remove them. Online only.

/// GET /v1/users/me/memory: the profile card and the notes (each entry knows its path on the server).
struct MemoryDoc: Decodable, Equatable {
  var entries: [MemoryEntry] = []
  var archived: [MemoryEntry] = []

  enum CodingKeys: String, CodingKey { case profileCard = "profile_card", observations, archived }
  private struct Archived: Decodable {
    var profile_card: [MemoryEntry]?   // swiftlint:disable:this identifier_name
    var observations: [MemoryEntry]?
  }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    let cards = try c.decodeIfPresent([MemoryEntry].self, forKey: .profileCard) ?? []
    let notes = try c.decodeIfPresent([MemoryEntry].self, forKey: .observations) ?? []
    // a note about the last few days is not part of the long-term memory (web: memEntries, flag memory_recent)
    entries = cards + notes.filter { $0.kind != "recent" }
    if let a = try c.decodeIfPresent(Archived.self, forKey: .archived) {
      archived = (a.profile_card ?? []) + (a.observations ?? [])
    }
  }

  /// The groups in the AI memory's order (how to talk first, then what keeps advice safe); unknown topics last.
  var groups: [(title: String, entries: [MemoryEntry])] {
    var out: [(String, [MemoryEntry])] = []
    for t in MemoryEntry.topicOrder {
      let e = entries.filter { $0.topicKey == t }
      if !e.isEmpty { out.append((MemoryEntry.topicLabel(t) ?? t, e)) }
    }
    let rest = entries.filter { $0.topicKey == nil }
    if !rest.isEmpty { out.append((tr("其他", "Other"), rest)) }
    return out
  }
}

struct MemoryEntry: Decodable, Equatable, Identifiable {
  /// Profile card items have a key, notes an id.
  var key: String?
  var noteId: Int?
  var text: String
  var label: String?
  var topic: String?
  var kind: String?
  var confirmedDays: Int?
  var stale = false
  var archivedReason: String?

  enum CodingKeys: String, CodingKey {
    case key, id, value, text, label, topic, kind, stale
    case confirmedDays = "confirmed_days", archivedReason = "archived_reason"
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    key = try c.decodeIfPresent(String.self, forKey: .key)
    noteId = try c.decodeIfPresent(Int.self, forKey: .id)
    text = try c.decodeIfPresent(String.self, forKey: .value) ?? c.decodeIfPresent(String.self, forKey: .text) ?? ""
    label = try c.decodeIfPresent(String.self, forKey: .label)
    topic = try c.decodeIfPresent(String.self, forKey: .topic)
    kind = try c.decodeIfPresent(String.self, forKey: .kind)
    confirmedDays = try c.decodeIfPresent(Int.self, forKey: .confirmedDays)
    stale = try c.decodeIfPresent(Bool.self, forKey: .stale) ?? false
    archivedReason = try c.decodeIfPresent(String.self, forKey: .archivedReason)
  }

  var id: String { key.map { "p:\($0)" } ?? "o:\(noteId ?? 0)" }
  var isCard: Bool { key != nil }
  /// `profile/<key>` or `observations/<id>` under /memory/.
  var path: String { key.map { "profile/\($0)" } ?? "observations/\(noteId ?? 0)" }
  var topicKey: String? { topic.flatMap { Self.topicLabel($0) == nil ? nil : $0 } }
  /// A card item of its own name; a standard one is named by its group already.
  var title: String? { isCard && Self.topicLabel(key ?? "") == nil ? label : nil }

  var age: String {
    guard let n = confirmedDays, n > 0 else { return tr("今天确认", "Confirmed today") }
    return tr("\(n) 天前确认", "Confirmed \(n) d ago")
  }

  static let topicOrder = ["communication", "health_constraints", "injuries", "goals", "current_plan", "schedule", "training_preferences",
                           "diet_preferences", "supplements", "follow_ups", "what_works", "what_doesnt", "basics"]

  static func topicLabel(_ t: String) -> String? {
    switch t {
    case "communication": return tr("沟通方式", "How to talk")
    case "health_constraints": return tr("健康约束", "Health constraints")
    case "injuries": return tr("伤病和禁用动作", "Injuries and banned exercises")
    case "goals": return tr("目标和进度", "Goals and progress")
    case "current_plan": return tr("当前训练和饮食计划", "Current training and diet plan")
    case "schedule": return tr("作息规律", "Daily schedule")
    case "training_preferences": return tr("训练偏好和条件", "Training preferences")
    case "diet_preferences": return tr("饮食偏好", "Diet preferences")
    case "supplements": return tr("补剂", "Supplements")
    case "follow_ups": return tr("复查和待办", "Follow-ups")
    case "what_works": return tr("对你有效的做法", "What works for you")
    case "what_doesnt": return tr("无效 / 要避免的做法", "What doesn't work")
    case "basics": return tr("基本情况", "Basics")
    default: return nil
    }
  }
}

/// 长期记忆: what the AI remembers about you. 「还准确」 confirms an entry, 「删除」 removes it for good, an archived one can come
/// back. Without the flag memory_hygiene the server offers only deleting notes.
struct MemoryView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var doc: MemoryDoc?
  @State private var message: String?
  @State private var deleting: MemoryEntry?
  @State private var showArchived = false

  private var hygiene: Bool { session.flags.contains("memory_hygiene") }

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if let doc {
        if doc.entries.isEmpty {
          Text(tr("暂无", "Nothing yet")).font(Typo.body).foregroundColor(Palette.text2)
        }
        ForEach(doc.groups, id: \.title) { g in
          RowGroup(title: g.title) {
            ForEach(Array(g.entries.enumerated()), id: \.element.id) { i, e in
              if i > 0 { RowDivider(inset: 0) }
              row(e)
            }
          }
        }
        if !doc.archived.isEmpty {
          Button { withAnimation { showArchived.toggle() } } label: {
            HStack {
              Text(tr("已归档 \(doc.archived.count) 条", "\(doc.archived.count) archived")).font(Typo.section).foregroundColor(Palette.text2)
              Spacer()
              Icon(showArchived ? .chevronDown : .chevronRight, size: 16).foregroundColor(Palette.text3)
            }
            .contentShape(Rectangle())
          }
          .buttonStyle(PressScale())
          if showArchived {
            RowGroup {
              ForEach(Array(doc.archived.enumerated()), id: \.element.id) { i, e in
                if i > 0 { RowDivider(inset: 0) }
                archivedRow(e)
              }
            }
          }
        }
      } else if message == nil {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity)
      }
      if let message { Banner(text: message, tone: .bad) }
    }
    .task { await load() }
    .confirmationDialog(tr("删除这一条？", "Delete this entry?"), isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }), titleVisibility: .visible) {
      Button(tr("删除", "Delete"), role: .destructive) { if let e = deleting { Task { await act(e, method: .delete, suffix: nil) } } }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    }
  }

  private func row(_ e: MemoryEntry) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      entryText(e)
      HStack(spacing: 14) {
        Text(e.age).font(Typo.label).foregroundColor(e.stale ? Palette.mid : Palette.text3)
        Spacer()
        if hygiene {
          Button(tr("还准确", "Still right")) { Task { await act(e, method: .post, suffix: "confirm") } }
            .font(Typo.caption).foregroundColor(Palette.text)
        }
        if hygiene || !e.isCard {
          Button(tr("删除", "Delete")) { deleting = e }.font(Typo.caption).foregroundColor(Palette.bad)
        }
      }
    }
    .padding(.vertical, 10)
  }

  private func archivedRow(_ e: MemoryEntry) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      entryText(e)
      HStack {
        Text(e.archivedReason ?? tr("已归档", "Archived")).font(Typo.label).foregroundColor(Palette.text3)
        Spacer()
        if hygiene {
          Button(tr("恢复", "Restore")) { Task { await act(e, method: .post, suffix: "restore") } }
            .font(Typo.caption).foregroundColor(Palette.text)
        }
      }
    }
    .padding(.vertical, 10)
  }

  private func entryText(_ e: MemoryEntry) -> some View {
    Text(e.title.map { "\($0) · \(e.text)" } ?? e.text).font(Typo.body).foregroundColor(Palette.text)
      .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
  }

  private func load() async {
    do {
      doc = try await session.api.send(Endpoint.memory(), as: MemoryDoc.self)
      message = nil
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func act(_ e: MemoryEntry, method: APIRequest.Method, suffix: String?) async {
    let path = Endpoint.memoryEntry(e.path + (suffix.map { "/\($0)" } ?? ""))
    do {
      _ = try await session.api.data(APIRequest(method: method, path: path, idempotent: method == .delete))
      await load()
    } catch APIError.notFound {
      await load()   // it is gone already
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }
}

/// GET /v1/users/me/feedback.
struct FeedbackList: Decodable, Equatable {
  var chosenMode: String?
  var available = false
  var reports: [Report] = []

  enum CodingKeys: String, CodingKey { case available, reports, chosenMode = "chosen_mode" }

  struct Report: Decodable, Equatable, Identifiable {
    let id: String
    var kind: String?
    var title: String
    var statusLabel: String?
    var status: String?
    var note: String?
    var createdAt: String?
    var votes = 0
    var voted = false
    var publicURL: String?
    var withdrawable = false

    enum CodingKeys: String, CodingKey {
      case id, kind, title, status, note, votes, voted, withdrawable
      case statusLabel = "status_label", createdAt = "created_at", publicURL = "public_url"
    }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      id = try c.decode(String.self, forKey: .id)
      kind = try c.decodeIfPresent(String.self, forKey: .kind)
      title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
      status = try c.decodeIfPresent(String.self, forKey: .status)
      statusLabel = try c.decodeIfPresent(String.self, forKey: .statusLabel)
      note = try c.decodeIfPresent(String.self, forKey: .note)
      createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
      votes = try c.decodeIfPresent(Int.self, forKey: .votes) ?? 0
      voted = try c.decodeIfPresent(Bool.self, forKey: .voted) ?? false
      publicURL = try c.decodeIfPresent(String.self, forKey: .publicURL)
      withdrawable = try c.decodeIfPresent(Bool.self, forKey: .withdrawable) ?? false
    }

    var done: Bool { status == "fixed" || status == "public" }
    var kindLabel: String { kind == "bug" ? tr("问题", "Bug") : tr("需求", "Request") }
    var day: String { String((createdAt ?? "").dropFirst(5).prefix(5)) }
    /// Only https links open (they come from the maintainers' side of the server).
    var link: URL? { publicURL.flatMap { $0.hasPrefix("https://") ? URL(string: $0) : nil } }
  }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    chosenMode = try c.decodeIfPresent(String.self, forKey: .chosenMode)
    available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
    reports = try c.decodeIfPresent([Report].self, forKey: .reports) ?? []
  }
}

/// 反馈记录: bugs and requests the user's AI reported, their status, whether the AI may report by itself, withdrawing a
/// report nobody has looked at yet. No input form on purpose: people report by telling their AI (docs/ai-feedback-plan.md).
struct FeedbackView: View {
  @EnvironmentObject private var session: AccountSession
  @Environment(\.openURL) private var openURL
  @State private var d: FeedbackList?
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if let d {
        if d.available {
          Picker("", selection: Binding(get: { d.chosenMode ?? "ask" }, set: { v in Task { await setMode(v) } })) {
            Text(tr("自动报告", "Automatic")).tag("auto")
            Text(tr("先问我", "Ask me first")).tag("ask")
            Text(tr("关闭", "Off")).tag("off")
          }
          .pickerStyle(.segmented)
        } else {
          Text(tr("反馈暂停", "Feedback is paused")).font(Typo.caption).foregroundColor(Palette.text2)
        }
        if d.reports.isEmpty {
          Text(tr("还没有反馈", "No feedback yet")).font(Typo.body).foregroundColor(Palette.text2)
        } else {
          RowGroup {
            ForEach(Array(d.reports.enumerated()), id: \.element.id) { i, r in
              if i > 0 { RowDivider(inset: 0) }
              row(r)
            }
          }
        }
      } else if message == nil {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity)
      }
      if let message { Banner(text: message, tone: .bad) }
    }
    .task { await load() }
  }

  private func row(_ r: FeedbackList.Report) -> some View {
    VStack(alignment: .leading, spacing: 4) {
      HStack(spacing: 8) {
        Text("\(r.id) · \(r.kindLabel) · \(r.day)").font(Typo.label).foregroundColor(Palette.text2)
        if r.votes > 0 { Text("+\(r.votes)").font(Typo.label).foregroundColor(Palette.text2) }
        Spacer()
        Text(r.statusLabel ?? r.status ?? "").font(Typo.label).foregroundColor(r.done ? Palette.good : Palette.text)
      }
      Text(r.title).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
      if let n = r.note, !n.isEmpty {
        Text(n).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
      }
      if r.link != nil || r.withdrawable {
        HStack(spacing: 14) {
          if let u = r.link { Button(tr("公开链接", "Public link")) { openURL(u) }.font(Typo.caption).foregroundColor(Palette.text) }
          if r.withdrawable { Button(tr("撤回", "Withdraw")) { Task { await withdraw(r) } }.font(Typo.caption).foregroundColor(Palette.bad) }
        }
        .padding(.top, 2)
      }
    }
    .padding(.vertical, 10)
  }

  private func load() async {
    do {
      d = try await session.api.send(.get(Endpoint.feedback), as: FeedbackList.self)
      message = nil
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func setMode(_ m: String) async {
    do {
      _ = try await session.api.data(try .json(.put, Endpoint.feedbackMode, ["mode": m]))
      await load()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func withdraw(_ r: FeedbackList.Report) async {
    do {
      _ = try await session.api.data(APIRequest(method: .delete, path: Endpoint.feedbackReport(r.id)))
      await load()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }
}

/// GET / PUT /v1/users/me/training/goals (flag muscle_load).
struct TrainingSettings: Decodable, Equatable {
  var focusMuscles: [String] = []
  var physiqueGoal: String?
  var muscles: [Muscle] = []
  var maxFocus = 0

  struct Muscle: Decodable, Equatable { let key: String; let name: String }

  enum CodingKeys: String, CodingKey {
    case muscles, focusMuscles = "focus_muscles", physiqueGoal = "physique_goal", maxFocus = "max_focus"
  }

  init() {}

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    focusMuscles = try c.decodeIfPresent([String].self, forKey: .focusMuscles) ?? []
    physiqueGoal = try c.decodeIfPresent(String.self, forKey: .physiqueGoal)
    muscles = try c.decodeIfPresent([Muscle].self, forKey: .muscles) ?? []
    maxFocus = try c.decodeIfPresent(Int.self, forKey: .maxFocus) ?? 0
  }
}

/// 训练设置: focus muscles and the physique goal the AI plans with.
struct TrainingSettingsView: View {
  @EnvironmentObject private var session: AccountSession
  let done: () -> Void
  @State private var g: TrainingSettings?
  @State private var chosen = Set<String>()
  @State private var goal = ""
  @State private var busy = false
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      if let g {
        VStack(alignment: .leading, spacing: 8) {
          HStack(spacing: 6) {
            Text(tr("重点肌群", "Focus muscles")).font(Typo.section).foregroundColor(Palette.text2)
            Text(tr("最多 \(g.maxFocus) 个", "at most \(g.maxFocus)")).font(Typo.label).foregroundColor(Palette.text3)
          }
          FlowChips(items: g.muscles.map { ($0.key, $0.name) }, chosen: chosen) { k in
            if chosen.contains(k) { chosen.remove(k) } else if chosen.count < g.maxFocus { chosen.insert(k) }
          }
        }
        VStack(alignment: .leading, spacing: 6) {
          Text(tr("体态 / 形体目标", "Physique goal")).font(Typo.section).foregroundColor(Palette.text2)
          TextField(tr("例如：改善圆肩，背更厚，腿更有线条", "e.g. fix rounded shoulders, a thicker back"), text: $goal, axis: .vertical)
            .lineLimit(2...5)
            .font(Typo.body).foregroundColor(Palette.text)
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
        }
        if let message { Banner(text: message, tone: .bad) }
        PrimaryButton(title: tr("保存", "Save"), busy: busy) { Task { await save() } }
      } else if let message {
        Banner(text: message, tone: .bad)
      } else {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity)
      }
    }
    .task { await load() }
  }

  private func load() async {
    do {
      let v = try await session.api.send(.get(Endpoint.trainingGoals), as: TrainingSettings.self)
      g = v
      chosen = Set(v.focusMuscles)
      goal = v.physiqueGoal ?? ""
    } catch APIError.notFound {
      message = tr("这个功能还没有对你开放", "This feature is not available to you yet")
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func save() async {
    struct Body: Encodable {
      let focus_muscles: [String]   // swiftlint:disable:this identifier_name
      let physique_goal: String?    // swiftlint:disable:this identifier_name
    }
    busy = true
    message = nil
    defer { busy = false }
    let text = goal.trimmingCharacters(in: .whitespacesAndNewlines)
    let order = (g?.muscles ?? []).map(\.key).filter(chosen.contains)
    do {
      _ = try await session.api.data(try .json(.put, Endpoint.trainingGoals, Body(focus_muscles: order, physique_goal: text.isEmpty ? nil : text)))
      done()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }
}
