import Foundation
import WatchConnectivity

/// WatchConnectivity on the watch (plan 4.4): the phone's context in, set events out with `transferUserInfo` (queued by
/// the system, delivered even when the phone app is not running, in order). The model owns the retry rules
/// (`WatchEventQueue`); this class only talks to the system.
final class WatchLinkClient: NSObject {
  var onContext: ((WatchContext) -> Void)?
  var onDelivered: ((String) -> Void)?
  var onFailed: ((String) -> Void)?
  var onReachable: ((Bool) -> Void)?
  var onActivated: (() -> Void)?

  private var wc: WCSession? { WCSession.isSupported() ? WCSession.default : nil }

  func activate() {
    guard let wc else { return }
    wc.delegate = self
    wc.activate()
  }

  var isActive: Bool { wc?.activationState == .activated }
  var isReachable: Bool { wc?.isReachable ?? false }

  /// Ids of the events whose transfer the system still holds (after a relaunch they are not sent again).
  var inFlight: Set<String> {
    Set((wc?.outstandingUserInfoTransfers ?? []).compactMap { WatchCodec.event($0.userInfo)?.id })
  }

  func send(_ e: WatchSetEvent) {
    guard let wc, wc.activationState == .activated else { return }
    wc.transferUserInfo(WatchCodec.message(e))
  }

  /// 「Send me the plan again」 (S9); only while the phone is reachable, else the next context brings it.
  func requestPlan() {
    guard let wc, wc.activationState == .activated, wc.isReachable else { return }
    wc.sendMessage([WatchCodec.requestKey: true], replyHandler: nil, errorHandler: nil)
  }
}

extension WatchLinkClient: WCSessionDelegate {
  func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
    let ctx = WatchCodec.context(session.receivedApplicationContext)
    let reachable = session.isReachable
    DispatchQueue.main.async {
      if let ctx { self.onContext?(ctx) }
      self.onReachable?(reachable)
      self.onActivated?()
    }
  }

  func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
    guard let ctx = WatchCodec.context(applicationContext) else { return }
    DispatchQueue.main.async { self.onContext?(ctx) }
  }

  func session(_ session: WCSession, didFinish userInfoTransfer: WCSessionUserInfoTransfer, error: Error?) {
    guard let id = WatchCodec.event(userInfoTransfer.userInfo)?.id else { return }
    DispatchQueue.main.async {
      if error == nil { self.onDelivered?(id) } else { self.onFailed?(id) }
    }
  }

  func sessionReachabilityDidChange(_ session: WCSession) {
    let r = session.isReachable
    DispatchQueue.main.async { self.onReachable?(r) }
  }
}
