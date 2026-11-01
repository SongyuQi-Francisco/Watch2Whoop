import SwiftUI
import UIKit

/// AI settings: which AI Jarvis uses (`/v1/users/me/ai-choice`, takes effect at once) and tokens for your own AI clients
/// over remote MCP (`/v1/users/me/ai-tokens`; the token is shown once). Adding an API key stays on the server's page for
/// now (the key never needs to be on the phone).
struct AISettingsView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var choice: AIChoice?
  @State private var tokens: AITokens?
  @State private var message: String?
  @State private var busy = false
  @State private var newClient = "claude-code"
  @State private var readOnly = false
  @State private var created: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      RowGroup(title: tr("Jarvis 用哪个 AI", "Which AI Jarvis uses")) {
        if let c = choice {
          if c.options.isEmpty {
            Text(tr("这台服务器还没有可选的 AI", "No AI to choose on this server yet")).font(Typo.caption).foregroundColor(Palette.text2).padding(.vertical, 12)
          }
          ForEach(Array(c.options.enumerated()), id: \.element.id) { i, o in
            if i > 0 { RowDivider(inset: 0) }
            Button { Task { await choose(o) } } label: {
              HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                  Text(o.label).font(Typo.body).foregroundColor(o.available ? Palette.text : Palette.text3)
                  if let n = o.note, !n.isEmpty { Text(n).font(Typo.caption).foregroundColor(Palette.text2) }
                }
                Spacer()
                if o.current { Icon(.circleCheck, size: 20).foregroundColor(Palette.good) }
              }
              .padding(.vertical, 12)
              .contentShape(Rectangle())
            }
            .buttonStyle(PressScale())
            .disabled(busy || o.current)
          }
        } else {
          ProgressView().tint(Palette.text2).padding(.vertical, 12)
        }
      }
      RowGroup(title: tr("给你自己的 AI 用的 token", "Tokens for your own AI")) {
        VStack(alignment: .leading, spacing: 10) {
          Text(tr("Claude Code、Codex 等 AI 客户端用它通过 MCP 读写你的数据。每个客户端一个，不用了就吊销。", "AI clients such as Claude Code or Codex use it to read and write your data over MCP. One per client; revoke it when unused."))
            .font(Typo.caption).foregroundColor(Palette.text2)
          if let u = tokens?.mcpURL { Text("MCP: \(u)").font(.system(size: 12, design: .monospaced)).foregroundColor(Palette.text2).textSelection(.enabled) }
          ForEach(tokens?.tokens.filter(\.active) ?? []) { t in
            HStack {
              VStack(alignment: .leading, spacing: 2) {
                Text(t.client).font(Typo.body).foregroundColor(Palette.text)
                Text(t.readOnly ? tr("只读", "Read only") : tr("读写", "Read and write")).font(Typo.caption).foregroundColor(Palette.text2)
              }
              Spacer()
              Button(tr("吊销", "Revoke")) { Task { await revoke(t) } }.font(Typo.caption).foregroundColor(Palette.bad)
            }
            .padding(.vertical, 6)
          }
          if let created {
            VStack(alignment: .leading, spacing: 6) {
              Text(tr("新 token（只显示这一次）", "New token (shown only once)")).font(Typo.label).foregroundColor(Palette.text2)
              Text(created).font(.system(size: 12, design: .monospaced)).foregroundColor(Palette.text).textSelection(.enabled)
              Button(tr("复制", "Copy")) { UIPasteboard.general.string = created }.font(Typo.caption).foregroundColor(Palette.text)
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface2))
          }
          FormField(label: tr("客户端名", "Client name"), text: $newClient, placeholder: "claude-code", background: Palette.surface2)
          ToggleRow(title: tr("只读", "Read only"), isOn: $readOnly)
          SecondaryButton(icon: .keyRound, title: tr("生成 token", "Create token"), background: Palette.surface2) { Task { await create() } }
            .disabled(busy || newClient.isEmpty)
        }
        .padding(.vertical, 12)
      }
      if let message { Banner(text: message, tone: .bad) }
    }
    .task { await load() }
  }

  private func load() async {
    do {
      choice = try await session.api.send(.get(Endpoint.aiChoice), as: AIChoice.self)
    } catch APIError.notFound {
      choice = AIChoice()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
    tokens = try? await session.api.send(.get(Endpoint.aiTokens), as: AITokens.self)
  }

  private func choose(_ o: AIChoice.Option) async {
    busy = true
    defer { busy = false }
    do {
      choice = try await session.api.send(try .json(.put, Endpoint.aiChoice, ["key": o.key]), as: AIChoice.self)
      message = nil
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func create() async {
    busy = true
    defer { busy = false }
    struct Body: Encodable { let client: String; let read_only: Bool }   // swiftlint:disable:this identifier_name
    do {
      let j = try await session.api.json(try .json(.post, Endpoint.aiTokens,
                                                    Body(client: newClient.lowercased().trimmingCharacters(in: .whitespaces), read_only: readOnly),
                                                    idempotent: false))
      created = j["token"] as? String
      message = nil
      tokens = try? await session.api.send(.get(Endpoint.aiTokens), as: AITokens.self)
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func revoke(_ t: AITokens.Token) async {
    _ = try? await session.api.data(APIRequest(method: .delete, path: Endpoint.aiToken(t.id)))
    tokens = try? await session.api.send(.get(Endpoint.aiTokens), as: AITokens.self)
  }
}
