import SwiftUI

/// AI settings, the native form of the page's `ai.js` (the forced upgrade of 2.0.0 closes that page): which AI Jarvis uses
/// (`/v1/users/me/ai-choice`, takes effect at once), the user's own AIs (`/ai-providers`: add, edit, turn off, delete), the
/// fallback AI's allowance and terms (`/ai-fallback`), and connecting AI clients (`/ai-tokens`, `/ai-connections`).
/// Everything is online only: no cache, no offline queue. See `AIProviderForm` for how an API key is handled.
struct AISettingsView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var choice: AIChoice?
  @State private var list: AIProviderList?
  @State private var message: String?
  @State private var busy = false
  @State private var sheet: AISheet?
  /// Pick the fallback AI once its terms are agreed to.
  @State private var pickAfterConsent = false

  private enum AISheet: Identifiable {
    case add, edit(AIProvider), connect(AIClient), fallback, consent
    var id: String {
      switch self {
      case .add: return "add"
      case .edit(let p): return "edit.\(p.id)"
      case .connect: return "connect"
      case .fallback: return "fallback"
      case .consent: return "consent"
      }
    }
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      RowGroup(title: tr("Jarvis 用哪个 AI", "Which AI Jarvis uses")) {
        if let c = choice {
          if c.options.isEmpty {
            Text(tr("这台服务器还没有可选的 AI", "No AI to choose on this server yet")).font(Typo.caption).foregroundColor(Palette.text2).padding(.vertical, 12)
          }
          ForEach(Array(c.options.enumerated()), id: \.element.id) { i, o in
            if i > 0 { RowDivider(inset: 0) }
            Button { tap(o) } label: { optionRow(o) }
              .buttonStyle(PressScale())
              .disabled(busy || o.current)
          }
        } else {
          ProgressView().tint(Palette.text2).padding(.vertical, 12)
        }
      }
      if let l = list, !l.providers.isEmpty || l.fallback != nil {
        RowGroup(title: tr("管理", "Manage")) {
          if let f = l.fallback {
            ListRow(title: f.displayName, subtitle: f.quotaLine, value: f.badge?.text, tone: f.badge?.tone ?? .text) { sheet = .fallback }
          }
          ForEach(Array(l.providers.enumerated()), id: \.element.id) { i, p in
            if i > 0 || l.fallback != nil { RowDivider(inset: 0) }
            ListRow(title: p.displayName, subtitle: p.subtitle, value: p.isActive || p.retired ? nil : tr("已停用", "Off"), tone: .mid) { sheet = .edit(p) }
          }
        }
      }
      if list != nil {
        VStack(spacing: 10) {
          SecondaryButton(icon: .plus, title: tr("添加", "Add")) { sheet = .add }
          SecondaryButton(icon: .link, title: tr("连接客户端（MCP）", "Connect a client (MCP)")) { sheet = .connect(.claudeCode) }
        }
      }
      if let l = list, l.hasAPIProvider {
        RowGroup {
          ListRow(title: tr("本月", "This month"), value: AIProvider.money(l.monthCostUSD))
        }
      }
      if let message { Banner(text: message, tone: .bad) }
    }
    .task { await load() }
    .sheet(item: $sheet, onDismiss: { Task { await load(); await afterChange() } }) { s in
      content(s).environmentObject(session)
    }
  }

  @ViewBuilder private func content(_ s: AISheet) -> some View {
    switch s {
    case .add:
      SheetContainer(title: tr("添加 AI", "Add an AI"), close: { sheet = nil }, keyboardDone: true) {
        AIProviderAddView(kinds: list?.kinds ?? []) { added in
          sheet = added.map(AISheet.connect)
        }
      }
      .presentationDetents([.large])
    case .edit(let p):
      SheetContainer(title: p.displayName, close: { sheet = nil }, keyboardDone: true) {
        AIProviderEditView(provider: p, kind: list?.kind(p.kind), current: choice?.options.first { $0.key == "p:\(p.id)" }?.current ?? false,
                           connect: { sheet = .connect($0) }, done: { sheet = nil })
      }
      .presentationDetents([.large])
    case .connect(let client):
      SheetContainer(title: tr("连接客户端（MCP）", "Connect a client (MCP)"), close: { sheet = nil }) {
        AIConnectView(client: client)
      }
      .presentationDetents([.large])
    case .fallback:
      if let f = list?.fallback {
        SheetContainer(title: f.displayName, close: { sheet = nil }) {
          AIFallbackView(fallback: f, askConsent: {
            pickAfterConsent = !f.used
            sheet = .consent
          }, pick: {
            sheet = nil
            Task { await choose(key: "fallback") }
          }, done: { sheet = nil })
        }
        .presentationDetents([.medium, .large])
      }
    case .consent:
      if let f = list?.fallback {
        SheetContainer(title: tr("Jarvis · 使用前确认", "Jarvis · before you start"), close: { pickAfterConsent = false; sheet = nil }) {
          AIFallbackConsentView(fallback: f) {
            sheet = nil
            guard pickAfterConsent else { return }
            pickAfterConsent = false
            Task { await choose(key: "fallback") }
          }
        }
        .presentationDetents([.medium, .large])
      }
    }
  }

  private func optionRow(_ o: AIChoice.Option) -> some View {
    HStack(spacing: 10) {
      VStack(alignment: .leading, spacing: 2) {
        Text(o.label).font(Typo.body).foregroundColor(o.available ? Palette.text : Palette.text3)
        if let n = optionNote(o) { Text(n).font(Typo.caption).foregroundColor(Palette.text2) }
      }
      Spacer()
      if o.current { Icon(.circleCheck, size: 20).foregroundColor(Palette.good) }
    }
    .padding(.vertical, 12)
    .contentShape(Rectangle())
  }

  private func optionNote(_ o: AIChoice.Option) -> String? {
    var parts = [String]()
    if let n = o.note, !n.isEmpty { parts.append(n) }
    if o.status == "disabled" { parts.append(tr("已停用", "Off")) } else if !o.available { parts.append(tr("不可用", "Unavailable")) }
    return parts.isEmpty ? nil : parts.joined(separator: " · ")
  }

  private func tap(_ o: AIChoice.Option) {
    if o.status == "disabled" {
      message = tr("先在下面「管理」里启用", "Turn it on under Manage first")
    } else if o.key == "fallback" && !o.consent {
      pickAfterConsent = true
      sheet = .consent
    } else {
      Task { await choose(key: o.key) }
    }
  }

  private func load() async {
    do {
      choice = try await session.api.send(.get(Endpoint.aiChoice), as: AIChoice.self)
      list = try await session.api.send(.get(Endpoint.aiProviders), as: AIProviderList.self)
      message = nil
    } catch APIError.notFound {
      choice = choice ?? AIChoice()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func choose(key: String) async {
    busy = true
    defer { busy = false }
    do {
      choice = try await session.api.send(try .json(.put, Endpoint.aiChoice, ["key": key]), as: AIChoice.self)
      message = nil
      list = try? await session.api.send(.get(Endpoint.aiProviders), as: AIProviderList.self)
      await afterChange()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  /// The Jarvis button routes by the AI in use: tell it without waiting for the next launch.
  private func afterChange() async {
    await JarvisRouter.current?.chat?.loadChoices()
  }
}

/// The terms of the fallback AI, before its first use (the points come from the server, in the request's language). Same
/// points, same version and same call as the sheet in Jarvis.
struct AIFallbackConsentView: View {
  @EnvironmentObject private var session: AccountSession
  let fallback: AIFallback
  let done: () -> Void
  @State private var agreed = false
  @State private var busy = false
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      ForEach(Array(fallback.points.enumerated()), id: \.offset) { _, p in
        HStack(alignment: .firstTextBaseline, spacing: 8) {
          Circle().fill(Palette.text2).frame(width: 4, height: 4)
          Text(p).font(Typo.caption).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
        }
      }
      if fallback.pointsTranslated {
        Text(tr("以中文版为准", "The Chinese text is the one agreed to")).font(Typo.label).foregroundColor(Palette.text3)
      }
      Button { agreed.toggle() } label: {
        HStack(spacing: 10) {
          Group {
            if agreed {
              Icon(.check, size: 14).foregroundColor(Palette.bg).frame(width: 20, height: 20)
                .background(RoundedRectangle(cornerRadius: 5).fill(Palette.text))
            } else {
              RoundedRectangle(cornerRadius: 5).strokeBorder(Palette.text.opacity(0.5), lineWidth: 1).frame(width: 20, height: 20)
            }
          }
          Text(tr("我已阅读并同意", "I have read and agree")).font(Typo.body).foregroundColor(Palette.text)
        }
      }
      .accessibilityAddTraits(agreed ? .isSelected : [])
      if let message { Banner(text: message, tone: .bad) }
      PrimaryButton(title: tr("同意并开始", "Agree and start"), busy: busy) { Task { await accept() } }
        .disabled(!agreed)
        .opacity(agreed ? 1 : 0.4)
    }
  }

  private func accept() async {
    struct Body: Encodable { let version: String; let agree: Bool }
    busy = true
    defer { busy = false }
    do {
      _ = try await session.api.data(try .json(.post, Endpoint.aiFallbackConsent, Body(version: fallback.consentVersion, agree: true), idempotent: false))
      done()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }
}

/// The fallback AI: this month's allowance, when it resets, the terms (a percentage only, no amounts or model name).
struct AIFallbackView: View {
  @EnvironmentObject private var session: AccountSession
  let fallback: AIFallback
  let askConsent: () -> Void
  let pick: () -> Void
  let done: () -> Void
  @State private var confirmRevoke = false
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      RowGroup {
        ListRow(title: tr("本月剩余", "Left this month"), value: fallback.remainingPct.map { "\($0)%" } ?? "—")
        RowDivider(inset: 0)
        ListRow(title: tr("重置", "Resets"), value: AIFallback.monthDay(fallback.resetOn))
        RowDivider(inset: 0)
        ListRow(title: tr("已同意", "Agreed"), value: fallback.consent ? String((fallback.consentedAt ?? "✓").prefix(10)) : "—")
      }
      if let m = fallback.message, !m.isEmpty { Banner(text: m, tone: .mid) }
      if let message { Banner(text: message, tone: .bad) }
      if !fallback.used { PrimaryButton(title: tr("用这个", "Use this one")) { fallback.consent ? pick() : askConsent() } }
      if fallback.consent, fallback.consentedAt != nil {
        SecondaryButton(title: tr("撤回同意", "Withdraw consent"), tone: .bad) { confirmRevoke = true }
      } else if !fallback.consent, fallback.used {
        PrimaryButton(title: tr("查看并同意", "Read and agree")) { askConsent() }
      }
    }
    .confirmationDialog(tr("撤回同意？", "Withdraw consent?"), isPresented: $confirmRevoke, titleVisibility: .visible) {
      Button(tr("撤回同意", "Withdraw consent"), role: .destructive) { Task { await revoke() } }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      Text(tr("撤回后 Jarvis 不再发送你的数据，也就不能用了。", "Jarvis stops sending your data and can no longer be used."))
    }
  }

  private func revoke() async {
    do {
      _ = try await session.api.data(APIRequest(method: .delete, path: Endpoint.aiFallbackConsent))
      done()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }
}
