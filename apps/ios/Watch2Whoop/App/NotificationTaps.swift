import Foundation
import UserNotifications

/// Tapped notifications (#175 pushes, notify/core.py): the payload's `url` (`/app/?tab=friends&feed=<post>` for a like or
/// a comment, `/app/?whatsnew=1` for the weekly message, `/app/?tab=me` …) goes to `AppModel.handle(link:)`, which routes it
/// (AppLink). A tap that launches the app arrives before the model is ready: it waits here until `open` is set.
/// While the app is open, circle notifications (`circle.*`) still show as a banner, like any social app; other kinds stay
/// in Notification Center as before.
final class NotificationTaps: NSObject, UNUserNotificationCenterDelegate {
  static let shared = NotificationTaps()
  /// A push arrived while the app is open.
  static let pushArrived = Notification.Name("w2w.push.arrived")

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
    if let link = InboxLink.pushTarget(url: info["url"] as? String, kind: info["kind"] as? String, nid: (info["nid"] as? NSNumber)?.intValue) {
      Task { @MainActor in self.deliver(link) }
    }
    done()
  }

  func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                              withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
    let kind = notification.request.content.userInfo["kind"] as? String ?? ""
    NotificationCenter.default.post(name: Self.pushArrived, object: nil)   // the inbox's badge keeps up
    done(kind.hasPrefix("circle.") ? [.banner, .list, .sound] : [])
  }
}
