import SwiftUI

/// 更新记录: what shipped, by week (`GET /v1/whats-new`, the same list for everyone, in the app's language).
struct WhatsNewView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var w: WhatsNew?
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if let w {
        ForEach(w.weeks) { week in
          RowGroup(title: week.weekStart) {
            ForEach(Array(week.items.enumerated()), id: \.offset) { i, item in
              if i > 0 { RowDivider(inset: 0) }
              VStack(alignment: .leading, spacing: 2) {
                Text(item.text).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
                Text(item.date).font(Typo.caption).foregroundColor(Palette.text3)
              }
              .frame(maxWidth: .infinity, alignment: .leading)
              .padding(.vertical, 10)
            }
          }
        }
      } else if let message {
        Banner(text: message, tone: .bad)
      } else {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity)
      }
    }
    .task {
      do { w = try await session.api.send(.get(Endpoint.whatsNew), as: WhatsNew.self) } catch let e as APIError { message = e.userMessage } catch {}
    }
  }
}

/// 数据状态: per data type, when the server last got it (`GET /v1/users/me/data-status`).
struct DataStatusView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var s: DataStatus?
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if let s {
        let ok = s.summary["ok"] ?? 0, stale = s.summary["stale"] ?? 0, never = s.summary["never"] ?? 0
        HStack(spacing: 8) {
          Chip(label: tr("正常 \(ok)", "OK \(ok)"), tone: .good)
          Chip(label: tr("过期 \(stale)", "Stale \(stale)"), tone: .mid)
          Chip(label: tr("从没有 \(never)", "Never \(never)"), tone: .text)
        }
        RowGroup {
          ForEach(Array(s.types.enumerated()), id: \.element.id) { i, t in
            if i > 0 { RowDivider(inset: 0) }
            HStack {
              Text(t.label).font(Typo.body).foregroundColor(Palette.text)
              Spacer()
              Text(Self.since(t)).font(Typo.caption).foregroundColor(t.status == "ok" ? Palette.text2 : t.status == "stale" ? Palette.mid : Palette.text3)
            }
            .padding(.vertical, 11)
          }
        }
      } else if let message {
        Banner(text: message, tone: .bad)
      } else {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity)
      }
    }
    .task {
      do { s = try await session.api.send(.get(Endpoint.dataStatus), as: DataStatus.self) } catch let e as APIError { message = e.userMessage } catch {}
    }
  }

  static func since(_ t: DataStatus.Kind) -> String {
    guard let d = t.daysSince else { return tr("从没有", "Never") }
    return d == 0 ? tr("今天", "Today") : tr("\(d) 天前", "\(d) d ago")
  }
}

/// 外观 (testers, flag `slim_mode`, #126): the theme and the daily quote, saved on the server (`/v1/users/me/appearance`).
struct AppearanceView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var a: AppearanceChoice?
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if let a {
        RowGroup(title: tr("主题", "Theme")) {
          Picker("", selection: Binding(get: { a.theme }, set: { v in Task { await save(AppearanceChoice(theme: v, quotes: a.quotes)) } })) {
            Text(tr("默认", "Default")).tag("default")
            Text(tr("薄肌模式", "Slim mode")).tag("slim")
          }
          .pickerStyle(.segmented)
          .padding(.vertical, 12)
        }
        RowGroup {
          ToggleRow(title: tr("每日一句", "Daily quote"),
                    isOn: Binding(get: { a.quotes }, set: { v in Task { await save(AppearanceChoice(theme: a.theme, quotes: v)) } }))
        }
      } else if let message {
        Banner(text: message, tone: .bad)
      } else {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity)
      }
    }
    .task {
      do { a = try await session.api.send(.get(Endpoint.appearance), as: AppearanceChoice.self) } catch let e as APIError { message = e.userMessage } catch {}
    }
  }

  private func save(_ v: AppearanceChoice) async {
    do {
      a = try await session.api.send(try .json(.put, Endpoint.appearance, v), as: AppearanceChoice.self)
      message = nil
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }
}

/// 没提交的修改: writes still in the offline queue; failed / conflicting ones block their group until retried or dropped
/// (docs/ios-native-app.md 5.4).
struct UnsentChangesView: View {
  @EnvironmentObject private var session: AccountSession
  @State private var items: [OutboxItem] = []

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if items.isEmpty {
        Text(tr("全部已经提交", "Everything has been sent")).font(Typo.body).foregroundColor(Palette.text2)
      } else {
        RowGroup {
          ForEach(Array(items.enumerated()), id: \.element.clientId) { i, it in
            if i > 0 { RowDivider(inset: 0) }
            VStack(alignment: .leading, spacing: 6) {
              HStack {
                Text(Self.kindLabel(it.kind)).font(Typo.body).foregroundColor(Palette.text)
                Spacer()
                Text(Self.stateLabel(it.state)).font(Typo.caption).foregroundColor(it.state == .pending || it.state == .sending ? Palette.text2 : Palette.bad)
              }
              if let e = it.lastError, it.state == .failed || it.state == .conflict {
                Text(e).font(Typo.caption).foregroundColor(Palette.text2)
              }
              if it.state == .failed || it.state == .conflict, let id = it.id {
                HStack(spacing: 16) {
                  Button(tr("重试", "Retry")) { try? session.sync.queue.retry(id); reload(); Task { await session.sync.refresh() } }
                    .font(Typo.caption).foregroundColor(Palette.text)
                  Button(tr("丢弃", "Discard")) { try? session.sync.queue.discard(id); reload() }
                    .font(Typo.caption).foregroundColor(Palette.bad)
                }
              }
            }
            .padding(.vertical, 10)
          }
        }
      }
    }
    .onAppear(perform: reload)
  }

  private func reload() { items = (try? session.sync.queue.all()) ?? [] }

  static func kindLabel(_ k: String) -> String {
    switch k {
    case "water": return tr("喝水", "Water")
    case "weight": return tr("体重", "Weight")
    case "journal": return tr("日记", "Journal")
    case "meal", "food": return tr("饮食", "Food")
    case "training": return tr("训练", "Training")
    case "supplement": return tr("补剂", "Supplement")
    case "circle.like": return tr("圈子点赞", "Circle like")          // N7
    case "circle.comment": return tr("圈子评论", "Circle comment")    // N7
    default: return k
    }
  }

  static func stateLabel(_ s: OutboxItem.State) -> String {
    switch s {
    case .pending, .sending: return tr("等待发送", "Waiting")
    case .failed: return tr("失败", "Failed")
    case .conflict: return tr("有冲突", "Conflict")
    }
  }
}
