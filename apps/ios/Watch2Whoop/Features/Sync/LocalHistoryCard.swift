import SwiftUI

/// The sync center's row for the history on this iPhone (#340 local history): how far the download got, why it waits.
/// The download itself is `HistoryDownload` (foreground after each sync, then `BGProcessingTask`).
struct LocalHistoryCard: View {
  @ObservedObject var sync: SyncCoordinator
  @State private var tick = Timer.publish(every: 5, on: .main, in: .common).autoconnect()

  var body: some View {
    let s = sync.historyState
    if s.phase != "server_mode" && s.phase != "idle" || s.fetched > 0 {
      RowGroup(title: tr("本机历史数据", "History on this iPhone")) {
        VStack(alignment: .leading, spacing: 8) {
          HStack {
            Text(Self.phaseText(s)).font(Typo.body).foregroundColor(Palette.text)
            Spacer()
            Text(Self.countText(s)).font(Typo.body.monospacedDigit())
              .foregroundColor(s.phase == "done" ? Palette.good : Palette.text2)
          }
          if s.phase != "done", let f = s.fraction { ProgressBar(fraction: f) }
          if let oldest = s.oldest {
            Text(tr("已到 \(oldest)", "Back to \(oldest)")).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
          }
        }
        .padding(.vertical, 12)
      }
      .onReceive(tick) { _ in sync.reloadHistoryState() }
    }
  }

  static func countText(_ s: HistoryDownload.State) -> String {
    if s.phase == "done" { return tr("完成", "Done") }
    guard let total = s.total, total > 0 else { return tr("\(s.fetched) 天", "\(s.fetched) d") }
    return "\(min(s.fetched, total))/\(total)"
  }

  /// A few words: what the download is doing or waiting for.
  static func phaseText(_ s: HistoryDownload.State) -> String {
    switch s.phase {
    case "downloading": return tr("下载中", "Downloading")
    case "done": return tr("全部在本机", "All on this iPhone")
    case "throttled": return tr("服务器忙，稍后继续", "Server busy, continues later")
    case "failed": return tr("暂停，稍后重试", "Paused, retries later")
    case "waiting_restore": return tr("等首次同步完成", "After the first sync")
    case "waiting_network":
      switch s.detail {
      case "low_data": return tr("低数据模式下暂停", "Paused in Low Data Mode")
      case "power": return tr("等充电时继续", "Continues while charging")
      default: return tr("等连上 Wi-Fi 并充电", "Waits for Wi-Fi and power")
      }
    default: return tr("准备中", "Getting ready")
    }
  }
}
