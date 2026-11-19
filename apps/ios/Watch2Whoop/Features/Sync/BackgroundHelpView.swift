import SwiftUI
import UIKit

/// The help sheet of a background-sync hint (BackgroundCheck.hint): what iOS says, the switches with their paths in
/// Settings, when the background last ran, and one button to this app's page in Settings when there is a switch there
/// (an app may open no other page of Settings). It follows the live verdict: once the person fixes the setting and comes
/// back, the sheet says so instead of the old steps.
struct BackgroundHelpView: View {
  let verdict: BackgroundCheck.Verdict
  let lastWake: Date?
  var now = Date()

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      if let h = BackgroundCheck.hint(verdict, now: now) {
        Text(h.intro).font(Typo.body).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
        VStack(alignment: .leading, spacing: 12) {
          ForEach(Array(h.steps.enumerated()), id: \.offset) { i, step in
            HStack(alignment: .firstTextBaseline, spacing: 10) {
              Text("\(i + 1)").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text2)
              Text(step).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
            }
          }
        }
        lastRun
        if h.opensSettings {
          PrimaryButton(title: tr("打开设置", "Open Settings")) {
            if let u = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(u) }
          }
        }
      } else {
        HStack(spacing: 10) {
          Icon(.circleCheck, size: 22).foregroundColor(Palette.good)
          Text(tr("后台同步在运行", "Background sync is running")).font(Typo.bodyStrong).foregroundColor(Palette.text)
        }
        lastRun
      }
    }
  }

  private var lastRun: some View {
    HStack {
      Text(tr("上次后台同步", "Last background sync")).font(Typo.caption).foregroundColor(Palette.text2)
      Spacer()
      Text(lastWake == nil ? tr("还没有记录", "None recorded yet") : SyncDiagnosis.ago(lastWake, now: now))
        .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text)
    }
    .padding(.vertical, 10).padding(.horizontal, 12)
    .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface2))
  }
}
