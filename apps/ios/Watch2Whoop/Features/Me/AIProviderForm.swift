import SwiftUI

// Adding and editing one of the user's own AIs (`/v1/users/me/ai-providers`, the same fields as `apiFields` in ai.js).
//
// How an API key is handled:
// - typed into a SecureField (`.oneTimeCode`: no password autofill and no offer to save it as a password; `.privacySensitive`),
//   held only in this view's state;
// - sent once (POST / PATCH with `idempotent` false: never retried, never put in the offline queue, nothing written to the
//   local database, UserDefaults or the Keychain), and emptied from the state before the request leaves, so a failed
//   request means typing it again;
// - shown afterwards only as the last 4 characters the server answers (`secret_last4`).

/// The fields of an API-type AI.
private struct AIAPIFields: View {
  @Binding var draft: AIProviderDraft
  let kind: AIKind
  var last4: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      modelField
      if kind.needsBaseURL {
        FormField(label: tr("接口地址 base_url", "API address (base_url)"), text: $draft.baseURL,
                  placeholder: kind.baseURLExamples.first ?? "https://…/v1", keyboard: .URL)
      }
      FormField(label: keyLabel, text: $draft.apiKey, placeholder: kind.keyHint ?? "", secure: true, contentType: .oneTimeCode)
        .privacySensitive()
      FormField(label: tr("每月预算（美元，超过就停）", "Monthly budget (USD; stops when reached)"), text: $draft.budget, keyboard: .decimalPad)
      ToggleRow(title: tr("免费额度（按 $0 计）", "Free tier (counted as $0)"), isOn: $draft.free)
      if kind.offersWebTools {
        ToggleRow(title: tr("允许联网", "Allow web access"), subtitle: tr("费用算在你的 key 上", "Billed on your key"), isOn: $draft.webTools)
      }
    }
  }

  private var keyLabel: String {
    guard let last4, !last4.isEmpty else { return "API key" }
    return tr("API key（现在：…\(last4)，留空不改）", "API key (now …\(last4); leave empty to keep)")
  }

  private var modelField: some View {
    VStack(alignment: .leading, spacing: 6) {
      Text(tr("模型", "Model")).font(Typo.label).foregroundColor(Palette.text2)
      HStack(spacing: 8) {
        TextField(kind.needsBaseURL ? "deepseek-chat" : "", text: $draft.model)
          .textInputAutocapitalization(.never)
          .autocorrectionDisabled()
          .font(Typo.body).foregroundColor(Palette.text)
        if !kind.models.isEmpty {
          Menu {
            ForEach(kind.models, id: \.self) { m in Button(m) { draft.model = m } }
          } label: {
            Icon(.chevronDown, size: 18).foregroundColor(Palette.text2).frame(width: 32, height: 32)
          }
          .accessibilityLabel(tr("选模型", "Choose a model"))
        }
      }
      .padding(.horizontal, 14).frame(minHeight: 46)
      .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
    }
  }
}

/// A subscription kind: it is used in the vendor's own client over MCP, so the server stores nothing for it.
private struct AISubscriptionNote: View {
  let note: String?
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 8) {
        Chip(label: tr("订阅", "Subscription"))
        Chip(label: "MCP")
      }
      if let note, !note.isEmpty {
        Text(note).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
      }
    }
  }
}

/// 添加 AI. `done`: nil = saved an API AI (or nothing to do next); a client = saved a subscription, go on to connecting it.
struct AIProviderAddView: View {
  @EnvironmentObject private var session: AccountSession
  let kinds: [AIKind]
  let done: (AIClient?) -> Void

  @State private var kindName = ""
  @State private var draft: AIProviderDraft?
  @State private var busy = false
  @State private var message: String?

  private var kind: AIKind? { kinds.first { $0.kind == kindName } }

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      VStack(alignment: .leading, spacing: 6) {
        Text(tr("来源", "Source")).font(Typo.label).foregroundColor(Palette.text2)
        Menu {
          ForEach(kinds) { k in Button(k.label) { select(k) } }
        } label: {
          HStack {
            Text(kind?.label ?? "").font(Typo.body).foregroundColor(Palette.text).multilineTextAlignment(.leading)
            Spacer()
            Icon(.chevronDown, size: 18).foregroundColor(Palette.text2)
          }
          .padding(.horizontal, 14).frame(minHeight: 46)
          .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
        }
      }
      if let k = kind, let d = Binding($draft) {
        if k.isAPI { AIAPIFields(draft: d, kind: k) } else { AISubscriptionNote(note: k.note) }
        ToggleRow(title: tr("设为默认（Jarvis 用它）", "Make it the default (Jarvis uses it)"), isOn: d.makeDefault)
      }
      if let message { Banner(text: message, tone: .bad) }
      PrimaryButton(title: tr("保存", "Save"), busy: busy) { Task { await save() } }
    }
    .onAppear { if kindName.isEmpty, let first = kinds.first { select(first) } }
    .onDisappear { draft?.apiKey = "" }
  }

  private func select(_ k: AIKind) {
    kindName = k.kind
    draft = AIProviderDraft(kind: k)
    message = nil
  }

  private func save() async {
    guard let k = kind, let d = draft else { return }
    if let p = d.problem(kind: k, editing: false) { message = p; return }
    let sentKey = !d.key.isEmpty
    let body = d.body(kind: k, editing: false)
    draft?.apiKey = ""   // gone from the form before the request leaves
    busy = true
    defer { busy = false }
    do {
      _ = try await session.api.data(try .json(.post, Endpoint.aiProviders, body, idempotent: false))
      done(k.isAPI ? nil : AIClient(catalog: k.client))
    } catch let e as APIError {
      message = failure(e, sentKey: sentKey)
    } catch {
      message = tr("出错了", "Something went wrong")
    }
  }
}

/// One of the user's AIs: its fields, use it for Jarvis, turn it off or on, delete it. A retired kind can only be deleted.
struct AIProviderEditView: View {
  @EnvironmentObject private var session: AccountSession
  let provider: AIProvider
  let kind: AIKind?
  let current: Bool
  let connect: (AIClient) -> Void
  let done: () -> Void

  @State private var draft: AIProviderDraft
  @State private var busy = false
  @State private var message: String?
  @State private var confirmDelete = false

  init(provider: AIProvider, kind: AIKind?, current: Bool, connect: @escaping (AIClient) -> Void, done: @escaping () -> Void) {
    self.provider = provider
    self.kind = kind
    self.current = current
    self.connect = connect
    self.done = done
    _draft = State(initialValue: AIProviderDraft(provider: provider))
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      if provider.retired {
        Text(tr("这个 AI 提供商已下线，不能再用，只能删除。", "This AI provider was retired; it can only be deleted."))
          .font(Typo.body).foregroundColor(Palette.text2)
      } else if provider.isAPI, let kind {
        AIAPIFields(draft: $draft, kind: kind, last4: provider.secretLast4)
      } else {
        AISubscriptionNote(note: kind?.note)
      }
      if let message { Banner(text: message, tone: .bad) }
      if !provider.retired {
        if provider.isAPI, kind != nil {
          PrimaryButton(title: tr("保存", "Save"), busy: busy) { Task { await save() } }
        } else {
          SecondaryButton(icon: .link, title: tr("连接说明", "How to connect")) { connect(AIClient(catalog: kind?.client)) }
        }
        if !current, provider.isActive {
          SecondaryButton(icon: .circleCheck, title: tr("用这个", "Use this one")) { Task { await useForJarvis() } }
        }
        SecondaryButton(title: provider.isActive ? tr("停用", "Turn off") : tr("启用", "Turn on")) { Task { await toggle() } }
      }
      SecondaryButton(icon: .trash, title: tr("删除", "Delete"), tone: .bad) { confirmDelete = true }
    }
    .disabled(busy)
    .onDisappear { draft.apiKey = "" }
    .confirmationDialog(tr("删除「\(provider.displayName)」？", "Delete \"\(provider.displayName)\"?"), isPresented: $confirmDelete, titleVisibility: .visible) {
      Button(tr("删除", "Delete"), role: .destructive) { Task { await delete() } }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      Text(tr("保存的 key 也会删掉。", "The saved key is deleted too."))
    }
  }

  private func save() async {
    guard let kind else { return }
    if let p = draft.problem(kind: kind, editing: true) { message = p; return }
    let sentKey = !draft.key.isEmpty
    let body = draft.body(kind: kind, editing: true)
    draft.apiKey = ""   // gone from the form before the request leaves
    await run(sentKey: sentKey) {
      _ = try await session.api.data(try .json(.patch, Endpoint.aiProvider(provider.id), body, idempotent: false))
    }
  }

  private func useForJarvis() async {
    await run {
      _ = try await session.api.data(try .json(.put, Endpoint.aiChoice, ["key": "p:\(provider.id)"]))
    }
  }

  private func toggle() async {
    await run {
      _ = try await session.api.data(try .json(.patch, Endpoint.aiProvider(provider.id), ["status": provider.isActive ? "disabled" : "active"], idempotent: false))
    }
  }

  private func delete() async {
    await run {
      _ = try await session.api.data(APIRequest(method: .delete, path: Endpoint.aiProvider(provider.id)))
    }
  }

  private func run(sentKey: Bool = false, _ work: () async throws -> Void) async {
    busy = true
    defer { busy = false }
    do {
      try await work()
      done()
    } catch let e as APIError {
      message = failure(e, sentKey: sentKey)
    } catch {
      message = tr("出错了", "Something went wrong")
    }
  }
}

/// The server's message; a key that went out with the failed request is not kept, so say to enter it again.
private func failure(_ e: APIError, sentKey: Bool) -> String {
  guard sentKey else { return e.userMessage }
  return e.userMessage + " " + tr("key 没有保留，要重新填。", "The key was not kept; enter it again.")
}
