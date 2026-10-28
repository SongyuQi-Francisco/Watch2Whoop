import GRDB
import SwiftUI

/// One explanation (`GET /v1/users/me/explain/{metric|status}/{key}[?date=]`, explain/ on the server): what the item
/// is, how to read it, advice from the user's own numbers (rules, no AI) and `ai_prompt` for 「问 AI」. The words live
/// only here, in the sheet (app.js explainHtml).
struct ExplainModel: Equatable {
  struct Read: Equatable { var range: String; var label: String; var tone: Tone }
  struct Advice: Equatable { var text: String; var tone: Tone }

  var title: String?
  var currentValue: String?
  var currentUnit: String?
  var currentBasis: String?
  var currentTone: Tone = .text
  var facts: [(String, String)] = []
  var missing: String?
  var advice: [Advice] = []
  var what: String?
  var read: [Read] = []
  var recovery: String?
  var recoveryHead: String
  var tips: [String] = []
  var avoid: [String] = []
  var caution: [String] = []
  var profile: [String] = []
  var aiPrompt: String?

  init(json: [String: Any]) {
    title = json["title"] as? String
    if let c = json["current"] as? [String: Any] {
      if let n = TodayModel.num(c["value"]) { currentValue = TodayModel.fmt(n, digits: 1) } else { currentValue = c["value"] as? String }
      currentUnit = c["unit"] as? String
      currentBasis = c["basis"] as? String
      currentTone = Self.tone(c["tone"] as? String)
    }
    facts = ((json["facts"] as? [[String: Any]]) ?? []).compactMap { f in
      guard let l = f["label"] as? String else { return nil }
      return (l, (f["value"] as? String) ?? TodayModel.num(f["value"]).map { TodayModel.fmt($0, digits: 1) } ?? "—")
    }
    missing = json["missing"] as? String
    advice = ((json["advice"] as? [[String: Any]]) ?? []).compactMap { a in
      (a["text"] as? String).map { Advice(text: $0, tone: Self.tone(a["tone"] as? String)) }
    }
    what = json["what"] as? String
    read = ((json["read"] as? [[String: Any]]) ?? []).compactMap { r in
      guard let range = r["range"] as? String, let label = r["label"] as? String else { return nil }
      return Read(range: range, label: label, tone: Self.tone(r["tone"] as? String))
    }
    recovery = json["recovery"] as? String
    recoveryHead = (json["key"] as? String) == "recovery" ? tr("怎么用", "How to use it") : tr("和恢复", "And recovery")
    tips = json["tips"] as? [String] ?? []
    let ex = json["exercises"] as? [String: Any]
    avoid = (json["avoid"] as? [String] ?? []) + ((ex?["banned"] as? [[String: Any]]) ?? []).compactMap { b in
      (b["name"] as? String).map { n in [n, b["reason"] as? String].compactMap { $0 }.joined(separator: " · ") }
    }
    caution = ((ex?["caution"] as? [[String: Any]]) ?? []).compactMap { b in
      (b["name"] as? String).map { n in [n, b["cues"] as? String].compactMap { $0 }.joined(separator: " · ") }
    }
    profile = json["profile"] as? [String] ?? []
    aiPrompt = json["ai_prompt"] as? String
  }

  static func tone(_ s: String?) -> Tone {
    switch s {
    case "good": return .good
    case "mid", "warn": return .mid
    case "bad": return .bad
    default: return .text
    }
  }

  static func == (a: ExplainModel, b: ExplainModel) -> Bool {
    a.title == b.title && a.currentValue == b.currentValue && a.what == b.what && a.advice == b.advice
      && a.facts.map(\.0) == b.facts.map(\.0) && a.facts.map(\.1) == b.facts.map(\.1) && a.aiPrompt == b.aiPrompt
  }
}

/// Loads an explanation: the cached copy at once, a fresh one from the server into `docs` (online-only content, kept
/// as a cache document so a second open works offline).
@MainActor
final class ExplainLoader: ObservableObject {
  @Published private(set) var model: ExplainModel?
  @Published private(set) var error: String?
  private var cancellable: AnyDatabaseCancellable?

  func start(_ session: AccountSession, kind: String, key: String, day: String?) {
    let docKey = TodayDocs.explain(kind, key, day)
    cancellable = ValueObservation.tracking { db in try DocRecord.fetchOne(db, key: docKey) }
      .removeDuplicates()
      .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] rec in
        if let json = rec?.json { self?.model = ExplainModel(json: json) }
      })
    Task {
      let out = await DocPuller(api: session.api, db: session.db).pull([DocFetch(docKey, Endpoint.explain(kind, key, day: day))])
      if model == nil {
        if out.removed.contains(docKey) { error = tr("无权限", "No access") }
        else if out.offline { error = tr("需要联网", "Needs a connection") }
      }
    }
  }
}

/// The explanation body; `head` shows the big current value (off when the sheet already shows the number).
struct ExplainBody: View {
  let kind: String
  let key: String
  var day: String?
  var head = true
  @EnvironmentObject private var session: AccountSession
  @StateObject private var loader = ExplainLoader()

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if let m = loader.model { content(m) }
      else if let e = loader.error { ToneBadge(text: e).padding(.top, 12) }
      else { ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 24) }
    }
    .task { loader.start(session, kind: kind, key: key, day: day) }
  }

  @ViewBuilder private func content(_ m: ExplainModel) -> some View {
    if head, let v = m.currentValue {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text(v).font(.system(size: 34, weight: .bold).monospacedDigit()).foregroundColor(Palette.text)
        if let u = m.currentUnit { Text(u).font(Typo.caption).foregroundColor(Palette.text2) }
        Spacer()
        if let b = m.currentBasis, !b.isEmpty { ToneBadge(text: b, tone: m.currentTone) }
      }
      .padding(.top, 4)
    }
    if !m.facts.isEmpty {
      VStack(spacing: 0) { ForEach(Array(m.facts.enumerated()), id: \.offset) { _, f in KVRow(label: f.0, value: f.1) } }
        .padding(.top, 6)
    }
    if let miss = m.missing, m.currentValue == nil { ToneBadge(text: miss).padding(.top, 10) }
    if !m.advice.isEmpty {
      heading(tr("建议", "Advice"))
      VStack(alignment: .leading, spacing: 8) {
        ForEach(Array(m.advice.enumerated()), id: \.offset) { _, a in bullet(a.text, tone: a.tone) }
      }
    }
    if let w = m.what { heading(tr("是什么", "What it is")); para(w) }
    if !m.read.isEmpty {
      heading(tr("怎么看", "How to read it"))
      VStack(spacing: 0) {
        ForEach(Array(m.read.enumerated()), id: \.offset) { _, r in KVRow(label: r.range, badge: (r.label, r.tone)) }
      }
    }
    if let r = m.recovery { heading(m.recoveryHead); para(r) }
    list(tr("注意", "Notes"), m.tips, tone: .text)
    list(tr("避免", "Avoid"), m.avoid, tone: .bad)
    list(tr("小心做", "With care"), m.caution, tone: .mid)
    list(tr("你的档案", "Your profile"), m.profile, tone: .sleep)
    if let p = m.aiPrompt { AskAIButton(prompt: p).padding(.top, 20) }
  }

  private func heading(_ t: String) -> some View {
    Text(t).font(Typo.section).foregroundColor(Palette.text2).padding(.top, 18).padding(.bottom, 6)
  }

  private func para(_ t: String) -> some View {
    Text(t).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true).lineSpacing(3)
  }

  private func bullet(_ t: String, tone: Tone) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 8) {
      Circle().fill(tone == .text ? Palette.text3 : tone.color).frame(width: 6, height: 6).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 1 }
      Text(t).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
    }
  }

  @ViewBuilder private func list(_ title: String, _ items: [String], tone: Tone) -> some View {
    if !items.isEmpty {
      heading(title)
      VStack(alignment: .leading, spacing: 8) { ForEach(Array(items.enumerated()), id: \.offset) { _, t in bullet(t, tone: tone) } }
    }
  }
}
