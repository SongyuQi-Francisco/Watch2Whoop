import SwiftUI

/// Health access, one sheet for every type the app reads (#344: a second sheet right after the first never showed), then
/// the first sync (the newest 14 days; older history is offered in the sync center).
struct HealthAccessView: View {
  @ObservedObject private var sync = HealthSyncService.shared
  let done: () async -> Void
  @State private var busy = false
  @State private var note: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Spacer().frame(height: 40)
      Icon(.heartPulse, size: 44).foregroundColor(Palette.bad)
      Text(tr("连接 Apple 健康", "Connect Apple Health")).font(.system(size: 28, weight: .bold)).titleTracking()
        .foregroundColor(Palette.text)
      Text(tr("下一步 iOS 会问你要不要允许读取。点「全部打开」，同步才完整：睡眠、心率、HRV、训练、步数……只传到你的服务器。", "iOS asks next whether to allow reading. Tap Turn On All so the sync is complete: sleep, heart rate, HRV, workouts, steps… sent only to your server."))
        .font(Typo.body).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
      if let note { Banner(text: note, tone: .mid) }
      Spacer()
      PrimaryButton(title: tr("允许读取「健康」", "Allow Health access"), busy: busy) {
        Task {
          busy = true
          let asked = await sync.requestPermission()
          busy = false
          if asked || sync.permission == .asked { await done() }
          else { note = tr("「健康」的窗口没有弹出来，再点一次试试", "The Health sheet did not come up; tap again") }
        }
      }
      Button(tr("以后再说", "Later")) { Task { await done() } }
        .font(Typo.body).foregroundColor(Palette.text2).frame(maxWidth: .infinity).padding(.bottom, 20)
    }
    .padding(.horizontal, Metrics.gutter)
    .frame(maxWidth: Metrics.maxContentWidth)
    .frame(maxWidth: .infinity)
    .background(Palette.bg.ignoresSafeArea())
  }
}
