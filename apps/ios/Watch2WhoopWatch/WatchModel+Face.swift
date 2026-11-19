import Foundation
import WatchConnectivity

extension WatchModel {
  /// A complication was tapped (`widgetURL`): go where its `WatchDeepLink` says for the app's state now.
  func open(_ url: URL) {
    guard let link = WatchDeepLink(url: url) else { return }
    path = link.route(home: home, running: running, signedIn: context?.signedIn == true)
  }

  /// The phone pushed a context while the app was not running: watchOS launches it in the background for a few seconds
  /// (`.backgroundTask(.watchConnectivity)`). Start the link so the context is received and stored, which reloads the
  /// complications (`WatchStore`), and finish once nothing is pending. Without this the context waits for the next time
  /// someone opens the app, and the face keeps yesterday's numbers.
  func receiveInBackground() async {
    start()
    let deadline = Date().addingTimeInterval(WatchParams.backgroundLinkSec)
    while Date() < deadline {
      if WCSession.isSupported(), WCSession.default.activationState == .activated, !WCSession.default.hasContentPending { break }
      try? await Task.sleep(nanoseconds: 200_000_000)
    }
  }
}
