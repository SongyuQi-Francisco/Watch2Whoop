import SwiftUI

/// Level 2 of 身体 (drilldown option C′: 「点柱子 = 全部趋势」): the 2.0.0 lists of the tab behind one visible switch, 指标 ·
/// 睡眠 · 报告. 指标 is every trend of the header's range as one row each (headline, change against the previous period, the
/// range's line; a tap opens the metric page), then 当前状态; 睡眠 the last 14 nights (a night opens the night page); 报告
/// the weekly / monthly report. The range is the first glance's (its header menu); the crumb goes back up to it.
struct BodyAllPage: View {
  @ObservedObject var store: TrendsStore
  @Binding var mode: TrendsView.Mode
  /// The next level and the door it grows out of (`.zoomDoor`).
  let push: (LevelRoute, String?) -> Void
  let up: () -> Void
  let sheet: (TrendsSheet) -> Void
  @Environment(\.dismiss) private var dismiss
  @State private var jumped: String?

  var body: some View {
    ScrollViewReader { proxy in
      ScrollView(.vertical, showsIndicators: false) {
        LazyVStack(alignment: .leading, spacing: 0) {
          LevelHeader(level: 2, back: { dismiss() }, up: up, upLabel: BodyGlancePage.rangeTitle(store.range)) {
            Text(BodyGlancePage.rangeTitle(store.range)).font(Typo.header).foregroundColor(Palette.text).lineLimit(1)
          } title: {
            if let end = store.end {
              Chip(label: tr("截至 \(TodayModel.shortDay(end))", "To \(TodayModel.shortDay(end))"))
            }
          } trailing: {
            EmptyView()
          }
          SegControl(items: TrendsView.Mode.allCases.map { ($0, $0.label) }, selected: mode) { mode = $0 }.padding(.top, 8)
          switch mode {
          case .metrics: metrics(proxy)
          case .sleep: TrendsSleepView(end: store.end) { push(.night($0), "nights.\($0)") }.padding(.top, 14)
          case .report: ReportView(end: store.end) { key, title in sheet(.explain(key: key, title: title)) }.padding(.top, 10)
          }
          Color.clear.frame(height: 24)
        }
        .padding(.horizontal, Metrics.gutter)
        .padding(.bottom, 32)
        .frame(maxWidth: Metrics.maxContentWidth)
        .frame(maxWidth: .infinity)
      }
      .background(Palette.bg.ignoresSafeArea())
      .statusBarCover()
      .clearOfJarvisKey()
      .refreshable { await store.refresh() }
      .toolbar(.hidden, for: .navigationBar)
    }
  }

  @ViewBuilder private func metrics(_ proxy: ScrollViewProxy) -> some View {
    if let p = store.page {
      let d = p.data
      let groups = LevelRows.range(p)
      let rail = groups.map { JumpRail.Item(id: $0.id, title: $0.title, count: $0.rows.count) }
        + (p.status.tiles.isEmpty ? [] : [JumpRail.Item(id: "status", title: tr("当前状态", "Current status"), count: p.status.tiles.count)])
      (Text("\(TrendsFormat.day(d.start, year: d.withYear)) – \(TrendsFormat.day(d.end, year: d.withYear))")
        .font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
        + Text("  vs \(TrendsFormat.day(d.prevStart, year: d.withYear)) – \(TrendsFormat.day(LocalDay.add(d.start, -1), year: d.withYear))")
        .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2))
        .lineLimit(1).minimumScaleFactor(0.8)
        .padding(.top, 14)
      JumpRail(items: rail, selected: jumped) { id in
        jumped = id
        withAnimation { proxy.scrollTo("group.\(id)", anchor: .top) }
      }
      .padding(.top, 12)
      ForEach(groups) { g in
        LevelGroupHeader(icon: LevelGroup.icon(g.id), title: g.title, count: g.rows.count).padding(.top, 8).id("group.\(g.id)")
        LevelRowList(rows: g.rows, door: "all.row") { push(.metric($0.id), "all.row.\($0.id)") }
      }
      if !p.status.tiles.isEmpty {
        HStack(spacing: 6) {
          LevelGroupHeader(icon: .circleGauge, title: tr("当前状态", "Current status"), count: nil)
          Text(TodayModel.shortDay(d.end)).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3)
        }
        .padding(.top, 8).id("group.status")
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
          ForEach(p.status.tiles) { t in StatusTileView(tile: t) { sheet(.status(t)) } }
        }
        .padding(.top, 10)
      }
    } else if store.loading {
      ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 60)
    } else {
      Text(store.offline ? tr("需要联网", "Needs a connection") : tr("暂无", "Nothing yet")).font(Typo.body).foregroundColor(Palette.text3)
        .frame(maxWidth: .infinity).padding(.vertical, 60)
    }
  }
}
