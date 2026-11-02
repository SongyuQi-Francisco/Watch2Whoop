import SwiftUI

/// 我 → 数据状态 → 本机存储 (#340 local history): what this account's local copy takes on the iPhone, per area, the
/// history download's progress, and 「清除本机缓存」 (everything is downloaded again from the server; unsent records stay).
struct LocalStorageSection: View {
  @EnvironmentObject private var session: AccountSession
  @State private var f: AppDatabase.Footprint?
  @State private var confirm = false
  @State private var busy = false
  @State private var message: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      RowGroup(title: tr("本机存储", "On this iPhone")) {
        row(tr("合计", "Total"), f.map { $0.fileBytes }, strong: true)
        RowDivider(inset: 0)
        row(tr("近 60 天原始数据", "Last 60 days, raw"), f?.raw)
        RowDivider(inset: 0)
        row(tr("历史数据", "History"), f?.history, note: historyNote)
        RowDivider(inset: 0)
        row(tr("分数和每日汇总", "Scores and daily totals"), f?.scores)
        RowDivider(inset: 0)
        row(tr("其他", "Other"), f?.other)
      }
      if let message { Banner(text: message, tone: .text) }
      SecondaryButton(icon: .trash, title: tr("清除本机缓存", "Clear the local copy"), tone: .bad) { confirm = true }
        .disabled(busy)
      Text(tr("不进 iCloud 备份；清除后从服务器重新下载。", "Not in iCloud backups; cleared data downloads again from the server."))
        .font(Typo.caption).foregroundColor(Palette.text3).padding(.horizontal, 4)
    }
    .task { await load() }
    .confirmationDialog(tr("清除本机缓存？", "Clear the local copy?"), isPresented: $confirm, titleVisibility: .visible) {
      Button(tr("清除", "Clear"), role: .destructive) { Task { await clear() } }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      let n = session.db.unsentWrites()
      Text(n > 0
           ? tr("服务器上的数据不受影响，会重新下载。还没上传的 \(n) 条记录会保留。", "Nothing changes on the server; it all downloads again. The \(n) records not uploaded yet stay.")
           : tr("服务器上的数据不受影响，会重新下载。", "Nothing changes on the server; it all downloads again."))
    }
  }

  private var historyNote: String? {
    let s = session.sync.historyState
    guard s.phase != "server_mode", s.phase != "idle" || s.fetched > 0 else { return nil }
    return "\(LocalHistoryCard.phaseText(s)) · \(LocalHistoryCard.countText(s))"
  }

  private func row(_ title: String, _ bytes: Int64?, note: String? = nil, strong: Bool = false) -> some View {
    HStack {
      VStack(alignment: .leading, spacing: 2) {
        Text(title).font(strong ? Typo.bodyStrong : Typo.body).foregroundColor(Palette.text)
        if let note { Text(note).font(Typo.caption).foregroundColor(Palette.text2) }
      }
      Spacer()
      Text(bytes.map(ByteText.string) ?? "—").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
    }
    .padding(.vertical, 11)
  }

  private func load() async {
    let db = session.db
    f = await Task.detached(priority: .utility) { db.footprint() }.value
  }

  private func clear() async {
    busy = true
    defer { busy = false }
    do {
      try await session.sync.clearLocalCache()
      message = tr("已清除，正在从服务器重新下载", "Cleared; downloading again from the server")
    } catch {
      message = String(describing: error)
    }
    await load()
  }
}
