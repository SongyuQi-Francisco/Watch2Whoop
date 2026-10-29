import Foundation
import SwiftUI

/// Forced upgrade (R2; server `client/policy.py`, docs/ios-native-2.0.md §4 R2). The server tells an old app to upgrade in
/// `/v1/web/me` → `upgrade` (`state` none / notice / block, `must_upgrade`) and, after the block date, answers its API calls
/// with 426 `upgrade_required` (`detail` = the same object). Notice: a banner the person can close, once a day. Block: a
/// full-screen page over everything. Title and text come from the server in the app language; the link (App Store /
/// TestFlight) is the server's config, https only. Uploads (`/v1/ingest/*`) are never refused, so no data is lost.
struct UpgradeInfo: Equatable {
  enum State: String { case none, notice, block }
  var state: State
  var title: String?
  var message: String?
  var url: URL?
  var minVersion: String?
  var blockFrom: String?

  static let none = UpgradeInfo(state: .none)

  /// `upgrade` of `/v1/web/me`, or a 426 answer's `detail`. nil when it is not an object.
  static func parse(_ o: [String: Any]?) -> UpgradeInfo? {
    guard let o else { return nil }
    var state = State(rawValue: o["state"] as? String ?? "") ?? .none
    if (o["must_upgrade"] as? Bool) == true || (o["code"] as? String) == "upgrade_required" { state = .block }
    let url = (o["url"] as? String).flatMap(URL.init(string:)).flatMap { $0.scheme?.lowercased() == "https" ? $0 : nil }
    return UpgradeInfo(state: state, title: o["title"] as? String, message: o["message"] as? String, url: url,
                       minVersion: o["min"] as? String, blockFrom: o["block_from"] as? String)
  }

  /// A 426 answer: always a block, with the server's texts when it sent them.
  static func blocked(by body: Data) -> UpgradeInfo {
    let o = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
    var info = parse(o?["detail"] as? [String: Any]) ?? UpgradeInfo(state: .block)
    info.state = .block
    return info
  }
}

/// What the app shows about the upgrade right now (one per app; every API client reports 426 here).
@MainActor
final class UpgradeGate: ObservableObject {
  static let shared = UpgradeGate()
  static let dayKey = "w2w.native.upgradeNotice.day.v1"

  @Published private(set) var info: UpgradeInfo = .none
  @Published private(set) var noticeClosedToday = false
  private let defaults: UserDefaults
  private let now: () -> Date

  init(defaults: UserDefaults = .standard, now: @escaping () -> Date = { Date() }) {
    self.defaults = defaults
    self.now = now
  }

  var showsBlock: Bool { info.state == .block }
  var showsNotice: Bool { info.state == .notice && !noticeClosedToday }

  /// The server's current answer (`/v1/web/me`): also lifts a block when the server no longer asks for one.
  func apply(_ new: UpgradeInfo?) {
    guard let new else { return }
    info = new
    noticeClosedToday = !Self.noticeDue(today: Self.day(now()), lastClosed: defaults.string(forKey: Self.dayKey))
  }

  /// An API call answered 426.
  func receivedUpgradeRequired(_ body: Data) {
    info = UpgradeInfo.blocked(by: body)
  }

  func closeNotice() {
    defaults.set(Self.day(now()), forKey: Self.dayKey)
    noticeClosedToday = true
  }

  /// The notice shows again on a new local day.
  nonisolated static func noticeDue(today: String, lastClosed: String?) -> Bool { today != lastClosed }

  nonisolated static func day(_ d: Date) -> String {
    let c = Calendar.current.dateComponents([.year, .month, .day], from: d)
    return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
  }
}

/// Full screen after the block date: no way around it except updating (or the server lifting it: 「重新检查」).
struct UpgradeScreen: View {
  let info: UpgradeInfo
  let recheck: () -> Void
  @Environment(\.openURL) private var openURL

  var body: some View {
    VStack(spacing: 16) {
      Spacer()
      Text(info.title ?? tr("请升级 Watch2Whoop", "Please update Watch2Whoop"))
        .font(Typo.title).foregroundColor(Palette.text).multilineTextAlignment(.center)
      Text(info.message ?? tr("这个版本已停用，请升级后继续使用。", "This version is no longer supported. Please update to continue."))
        .font(Typo.body).foregroundColor(Palette.text2).multilineTextAlignment(.center)
        .fixedSize(horizontal: false, vertical: true)
      if let url = info.url {
        PrimaryButton(title: tr("去升级", "Update")) { openURL(url) }.padding(.top, 8)
      }
      Button(tr("重新检查", "Check again"), action: recheck)
        .font(Typo.caption).foregroundColor(Palette.text2)
      Spacer()
    }
    .padding(Metrics.gutter)
    .frame(maxWidth: Metrics.maxContentWidth)
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(Palette.bg.ignoresSafeArea())
    .accessibilityAddTraits(.isModal)
  }
}

/// During the notice window: one line at the top, 「去升级」 or 「稍后」 (back tomorrow).
struct UpgradeNoticeBanner: View {
  let info: UpgradeInfo
  let close: () -> Void
  @Environment(\.openURL) private var openURL

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(info.title ?? tr("请升级 Watch2Whoop", "Please update Watch2Whoop"))
        .font(Typo.bodyStrong).foregroundColor(Palette.text)
      if let m = info.message {
        Text(m).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
      }
      HStack(spacing: 10) {
        if let url = info.url {
          Button(tr("去升级", "Update")) { openURL(url) }
            .font(.system(size: 13, weight: .semibold)).foregroundColor(Palette.bg)
            .padding(.vertical, 7).padding(.horizontal, 14)
            .background(Capsule().fill(Palette.text))
            .buttonStyle(PressScale())
        }
        Button(tr("稍后", "Later"), action: close)
          .font(.system(size: 13, weight: .semibold)).foregroundColor(Palette.text)
          .padding(.vertical, 7).padding(.horizontal, 14)
          .background(Capsule().fill(Palette.surface2))
          .buttonStyle(PressScale())
      }
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.midSoft))
    .padding(.horizontal, Metrics.gutter)
  }
}
