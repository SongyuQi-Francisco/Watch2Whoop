import Foundation
import UserNotifications

/// Tapped notifications (#175 pushes, notify/core.py): the payload's `url` (`/app/?tab=friends&feed=<post>` for a like or
/// a comment) goes to `AppModel.handle(link:)`, which routes it (N7: to the post in the Friends tab). A tap that launches
/// the app arrives before the model is ready: it waits here until `open` is set.
/// While the app is open, circle notifications (`circle.*`) still show as a banner, like any social app; other kinds stay
/// in Notification Center as before.
final class NotificationTaps: NSObject, UNUserNotificationCenterDelegate {
  static let shared = NotificationTaps()

  @MainActor var open: (@MainActor (String) -> Void)? {
    didSet { if let open, let link = pending { pending = nil; open(link) } }
  }
  @MainActor private var pending: String?

  @MainActor private func deliver(_ link: String) {
    if let open { open(link) } else { pending = link }
  }

  func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                              withCompletionHandler done: @escaping () -> Void) {
    let info = response.notification.request.content.userInfo
    if let link = info["url"] as? String {
      Task { @MainActor in self.deliver(link) }
    }
    done()
  }

  func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                              withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
    let kind = notification.request.content.userInfo["kind"] as? String ?? ""
    done(kind.hasPrefix("circle.") ? [.banner, .list, .sound] : [])
  }
}
