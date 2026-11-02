import SwiftUI
import UIKit

/// 连接客户端（MCP）: the MCP address, the apps connected with OAuth (`/ai-connections`, disconnect), the tokens for AI clients
/// (`/ai-tokens`, revoke) and a new token. A new token is shown once, with what to run in that client; it stays only in this
/// view's state, like an API key (AIProviderForm.swift).
struct AIConnectView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var client: AIClient
  @State private var readOnly = false
  @State private var d: AITokens?
  @State private var created: String?
  @State private var busy = false
  @State private var message: String?
  @State private var revoking: AITokens.Token?
  @State private var disconnecting: AITokens.Connection?

  init(client: AIClient = .claudeCode) { _client = State(initialValue: client) }

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if let u = d?.mcpURL {
        RowGroup(title: tr("地址", "Address")) {
          codeRow(u)
        }
      }
      if let note = d?.oauthNote, !note.isEmpty {
        Text(note).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
      }
      if let s = d?.webSearch {
        Text(s ? tr("外接 AI 也能用服务器联网：搜索、读网页（每天有上限）", "External AIs can use the server's web access too: search and read pages (daily limit)")
             : tr("外接 AI 也能用服务器读网页（每天有上限）", "External AIs can use the server to read pages too (daily limit)"))
          .font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
      }
      if let cs = d?.connections, !cs.isEmpty {
        RowGroup(title: tr("已连接的应用", "Connected apps")) {
          ForEach(Array(cs.enumerated()), id: \.element.id) { i, c in
            if i > 0 { RowDivider(inset: 0) }
            HStack {
              VStack(alignment: .leading, spacing: 2) {
                Text(c.clientName ?? tr("未命名的应用", "Unnamed app")).font(Typo.body).foregroundColor(Palette.text)
                Text(line(host: c.redirectHost, readOnly: c.readOnly, used: c.lastUsedAt)).font(Typo.caption).foregroundColor(Palette.text2)
              }
              Spacer()
              Button(tr("断开", "Disconnect")) { disconnecting = c }.font(Typo.caption).foregroundColor(Palette.bad)
            }
            .padding(.vertical, 10)
          }
        }
      }
      if let ts = d?.liveTokens, !ts.isEmpty {
        RowGroup(title: "Token") {
          ForEach(Array(ts.enumerated()), id: \.element.id) { i, t in
            if i > 0 { RowDivider(inset: 0) }
            HStack {
              VStack(alignment: .leading, spacing: 2) {
                Text(t.client).font(Typo.body).foregroundColor(Palette.text)
                Text(line(host: nil, readOnly: t.readOnly, used: t.lastUsedAt)).font(Typo.caption).foregroundColor(Palette.text2)
              }
              Spacer()
              Button(tr("吊销", "Revoke")) { revoking = t }.font(Typo.caption).foregroundColor(Palette.bad)
            }
            .padding(.vertical, 10)
          }
        }
      }
      RowGroup(title: tr("新 token", "New token")) {
        VStack(alignment: .leading, spacing: 4) {
          Menu {
            ForEach(AIClient.allCases) { c in Button(c.label) { client = c; created = nil } }
          } label: {
            HStack {
              Text(tr("客户端", "Client")).font(Typo.body).foregroundColor(Palette.text)
              Spacer()
              Text(client.label).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1)
              Icon(.chevronDown, size: 16).foregroundColor(Palette.text3)
            }
            .padding(.vertical, 12)
            .contentShape(Rectangle())
          }
          RowDivider(inset: 0)
          ToggleRow(title: tr("只读", "Read only"), isOn: $readOnly)
        }
      }
      SecondaryButton(icon: .keyRound, title: tr("生成 token", "Create token")) { Task { await create() } }
        .disabled(busy)
      if let created { createdSteps(created) }
      if let message { Banner(text: message, tone: .bad) }
    }
    .task { await load() }
    .confirmationDialog(tr("吊销这个 token？", "Revoke this token?"), isPresented: Binding(get: { revoking != nil }, set: { if !$0 { revoking = nil } }), titleVisibility: .visible) {
      Button(tr("吊销", "Revoke"), role: .destructive) { if let t = revoking { Task { await revoke(t) } } }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      Text(tr("这个客户端立即连不上。", "That client can't connect any more, at once."))
    }
    .confirmationDialog(tr("断开这个应用？", "Disconnect this app?"), isPresented: Binding(get: { disconnecting != nil }, set: { if !$0 { disconnecting = nil } }), titleVisibility: .visible) {
      Button(tr("断开", "Disconnect"), role: .destructive) { if let c = disconnecting { Task { await disconnect(c) } } }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      Text(tr("这个应用立即连不上。", "That app can't connect any more, at once."))
    }
  }

  private func line(host: String?, readOnly: Bool, used: String?) -> String {
    var parts = [String]()
    if let host, !host.isEmpty { parts.append(host) }
    parts.append(readOnly ? tr("只读", "Read only") : tr("读写", "Read and write"))
    if let used {
      let day = String(used.prefix(10))
      parts.append(tr("最近使用 \(day)", "Last used \(day)"))
    } else {
      parts.append(tr("还没用过", "Not used yet"))
    }
    return parts.joined(separator: " · ")
  }

  private func createdSteps(_ token: String) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      Banner(text: tr("只显示一次", "Shown only once"), tone: .mid)
      codeBlock(token).privacySensitive()
      if let k = d?.keychain {
        Text(tr("1. 钥匙串", "1. Keychain")).font(Typo.label).foregroundColor(Palette.text2)
        codeBlock(k)
      }
      if let s = d?.snippet(client) {
        Text(tr("2. 客户端", "2. Client")).font(Typo.label).foregroundColor(Palette.text2)
        codeBlock(s)
      }
      if let p = d?.prompt {
        Text("3. \(p)").font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
      }
    }
  }

  private func codeRow(_ text: String) -> some View {
    HStack(spacing: 10) {
      Text(text).font(.system(size: 13, design: .monospaced)).foregroundColor(Palette.text).lineLimit(2)
      Spacer(minLength: 6)
      copyButton(text)
    }
    .padding(.vertical, 12)
  }

  private func codeBlock(_ text: String) -> some View {
    HStack(alignment: .top, spacing: 8) {
      Text(text).font(.system(size: 12, design: .monospaced)).foregroundColor(Palette.text)
        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
      Spacer(minLength: 4)
      copyButton(text)
    }
    .padding(12)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
  }

  private func copyButton(_ text: String) -> some View {
    Button { UIPasteboard.general.string = text } label: {
      Icon(.copy, size: 16).foregroundColor(Palette.text2).frame(width: 28, height: 28)
    }
    .buttonStyle(PressScale())
    .accessibilityLabel(tr("复制", "Copy"))
  }

  private func load() async {
    do {
      d = try await session.api.send(.get(Endpoint.aiTokens), as: AITokens.self)
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func create() async {
    struct Body: Encodable { let client: String; let read_only: Bool }   // swiftlint:disable:this identifier_name
    busy = true
    defer { busy = false }
    do {
      let j = try await session.api.json(try .json(.post, Endpoint.aiTokens, Body(client: client.rawValue, read_only: readOnly), idempotent: false))
      created = j["token"] as? String
      message = nil
      await load()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func revoke(_ t: AITokens.Token) async {
    await remove(Endpoint.aiToken(t.id))
  }

  private func disconnect(_ c: AITokens.Connection) async {
    await remove(Endpoint.aiConnection(c.id))
  }

  private func remove(_ path: String) async {
    do {
      _ = try await session.api.data(APIRequest(method: .delete, path: path))
      message = nil
      await JarvisRouter.current?.chat?.loadChoices()   // a subscription with no way in left stops being usable
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
    await load()
  }
}
