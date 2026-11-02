import SwiftUI
import UIKit
import UserNotifications

/// Notification settings (notify/api.py, flag `notifications`): this phone's permission, the categories and quiet hours.
/// The permission is asked here, never at launch.
struct NotificationSettingsView: View {
  @EnvironmentObject private var session: AccountSession
  @ObservedObject private var push = PushRegistration.shared
  @State private var s: NotificationSettings?
  @State private var off = false
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      RowGroup {
        HStack {
          VStack(alignment: .leading, spacing: 2) {
            Text(tr("这台手机", "This iPhone")).font(Typo.body).foregroundColor(Palette.text)
            Text(pushState).font(Typo.caption).foregroundColor(Palette.text2)
          }
          Spacer()
          switch push.state {
          case .notDetermined:
            Button(tr("允许通知", "Allow")) { Task { await push.enable(api: session.api) } }
              .font(Typo.bodyStrong).foregroundColor(Palette.text)
          case .denied:
            Button(tr("打开设置", "Open Settings")) {
              if let u = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(u) }
            }
            .font(Typo.bodyStrong).foregroundColor(Palette.text)
          default:
            EmptyView()
          }
        }
        .padding(.vertical, 12)
      }
      if off {
        Text(tr("通知还没对你开放", "Notifications are not available to you yet")).font(Typo.caption).foregroundColor(Palette.text2)
      } else if let s {
        RowGroup(title: tr("通知哪些", "What to notify")) {
          ForEach(Array(s.ordered.enumerated()), id: \.element.key) { i, c in
            if i > 0 { RowDivider(inset: 0) }
            ToggleRow(title: c.label, isOn: Binding(get: { s.categories[c.key] ?? false },
                                                     set: { v in Task { await save(categories: [c.key: v]) } }))
          }
        }
        RowGroup(title: tr("免打扰", "Quiet hours")) {
          ToggleRow(title: tr("夜间不推送", "No pushes at night"), subtitle: "\(s.quiet.start)–\(s.quiet.end)",
                    isOn: Binding(get: { s.quiet.enabled }, set: { v in Task { await save(quiet: v) } }))
        }
      } else {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity)
      }
      if let message { Banner(text: message, tone: .bad) }
    }
    .task {
      await push.refresh()
      await load()
    }
  }

  private var pushState: String {
    switch push.state {
    case .authorized, .provisional, .ephemeral: return tr("已允许", "Allowed")
    case .denied: return tr("已关闭：在 iPhone 设置里打开", "Off: turn it on in iPhone Settings")
    default: return tr("还没允许", "Not allowed yet")
    }
  }

  private func load() async {
    do {
      s = try await session.api.send(.get(Endpoint.notificationSettings), as: NotificationSettings.self)
    } catch APIError.notFound {
      off = true
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func save(categories: [String: Bool]? = nil, quiet: Bool? = nil) async {
    var body: [String: AnyEncodable] = [:]
    if let categories { body["categories"] = AnyEncodable(categories) }
    if let quiet { body["quiet"] = AnyEncodable(["enabled": quiet]) }
    do {
      s = try await session.api.send(try .json(.put, Endpoint.notificationSettings, body), as: NotificationSettings.self)
      message = nil
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }
}

/// This phone's push registration (#175): permission on demand, the APNs device token to
/// `POST /v1/users/me/push-devices` (again on every start: it can change), removed on sign-out.
@MainActor
final class PushRegistration: ObservableObject {
  static let shared = PushRegistration()
  @Published private(set) var state: UNAuthorizationStatus = .notDetermined
  private var api: APIClient?

  func refresh() async {
    state = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
  }

  /// Asks for the permission (once; iOS shows its dialog only the first time), then registers.
  func enable(api: APIClient) async {
    self.api = api
    _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
    await refresh()
    if state == .authorized || state == .provisional { UIApplication.shared.registerForRemoteNotifications() }
  }

  /// Every start while signed in: register again when allowed.
  func renew(api: APIClient) async {
    self.api = api
    await refresh()
    if state == .authorized || state == .provisional { UIApplication.shared.registerForRemoteNotifications() }
  }

  func received(_ deviceToken: Data) {
    guard let api else { return }
    let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
    #if DEBUG
    let env = "sandbox"
    #else
    let env = "production"
    #endif
    let body: [String: String] = ["token": hex, "environment": env, "bundle_id": Bundle.main.bundleIdentifier ?? "",
                                  "app_version": APIClient.appVersion, "lang": Lang.code]
    Task { _ = try? await api.data(try .json(.post, Endpoint.pushDevices, body, idempotent: false)) }
  }

  func failed() {}
}
