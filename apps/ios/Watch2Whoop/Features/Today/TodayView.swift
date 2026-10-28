import SwiftUI

/// N0's Today: proves the whole chain (Keychain token → API client → local database → page). The cached document is
/// on screen at once; a refresh pulls `/v1/users/me/today` into `docs` and the observation redraws. N3 builds the full
/// page on this.
@MainActor
final class TodayViewModel: ObservableObject {
  @Published private(set) var model: TodayModel = .empty
  @Published private(set) var hasData = false
  private var observing = false

  func observe(_ session: AccountSession) async {
    guard !observing else { return }
    observing = true
    do {
      for try await doc in session.db.observeDoc(DocKey.todayCurrent) {
        if let json = doc?.json {
          model = TodayModel(json: json)
          hasData = true
        }
      }
    } catch {
      observing = false
    }
  }
}

struct TodayView: View {
  @EnvironmentObject private var session: AccountSession
  @StateObject private var vm = TodayViewModel()

  var body: some View {
    GeometryReader { geo in
      PageScaffold(title: tr("今天", "Today"), overline: overline) {
        rings(width: geo.size.width - Metrics.gutter * 2).padding(.top, 12)
        if let w = vm.model.word {
          Text(w).font(Typo.heroWord).foregroundColor(Palette.text)
            .frame(maxWidth: .infinity).padding(.top, 12)
        }
        if let s = vm.model.sub {
          Text(s).font(Typo.caption).foregroundColor(Palette.text2).frame(maxWidth: .infinity).padding(.top, 3)
        }
        if !vm.model.chips.isEmpty {
          HStack(spacing: 8) { ForEach(vm.model.chips, id: \.self) { Chip(label: $0) } }
            .frame(maxWidth: .infinity).padding(.top, 14)
        }
        VStack(alignment: .leading, spacing: 10) {
          SectionHeader(title: tr("恢复", "Recovery"))
          HStack(spacing: 8) {
            MetricTile(label: "HRV", value: vm.model.hrv, unit: "ms")
            MetricTile(label: tr("静息心率", "Resting HR"), value: vm.model.rhr, unit: "bpm")
            MetricTile(label: tr("睡眠", "Sleep"), value: vm.model.sleep)
          }
        }
        .padding(.top, 22)
        SyncStatusLine(sync: session.sync, hasData: vm.hasData).padding(.top, 18)
      }
      .refreshable { await session.sync.refresh() }
    }
    .task { await vm.observe(session) }
    .task { await session.sync.refresh() }
  }

  private var overline: String? {
    guard let d = vm.model.date else { return nil }
    return TodayModel.shortDay(d)
  }

  /// Three rings side by side, recovery in the middle and 1.4× bigger (NativeToday.swift of 1.0.9).
  private func rings(width: CGFloat) -> some View {
    // 1 + 1.4 + 1 ring sides, plus each ring's stroke padding (≈ 0.24 side in all) and two 12 pt gaps
    let side = min(100, (max(width, 200) - 24) / 3.7), big = side * 1.4
    return HStack(alignment: .center, spacing: 12) {
      ForEach(vm.model.rings, id: \.key) { r in
        let isBig = r.key == "recovery"
        RingView(value: r.value, label: r.label, fraction: r.fraction, tone: r.tone, side: isBig ? big : side, big: isBig)
      }
    }
    .frame(maxWidth: .infinity)
  }
}

/// Small line under the page: updating / offline / error, so cached data is never mistaken for fresh.
struct SyncStatusLine: View {
  @ObservedObject var sync: SyncCoordinator
  let hasData: Bool

  var body: some View {
    HStack(spacing: 6) {
      switch sync.status {
      case .syncing:
        ProgressView().scaleEffect(0.7).tint(Palette.text3)
        Text(tr("更新中", "Updating"))
      case .offline:
        Text(hasData ? tr("离线，显示上次的数据", "Offline, showing the last data") : tr("离线", "Offline"))
      case .failed(let m):
        Text(m).lineLimit(2)
      case .idle:
        EmptyView()
      }
    }
    .font(Typo.label)
    .foregroundColor(Palette.text3)
    .frame(maxWidth: .infinity)
  }
}
