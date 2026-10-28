import SwiftUI

/// The report mode of the Trends tab: week / month, the period against the previous one, findings and behaviour.
struct ReportView: View {
  /// Last day of the report (nil = the user's today).
  let end: String?
  let explain: (String, String) -> Void
  @EnvironmentObject private var session: AccountSession
  @StateObject private var store = ReportStore()

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      SegControl(items: ReportModel.Period.allCases.map { ($0, $0.label) }, selected: store.period) { store.setPeriod($0, session: session) }
        .padding(.top, 4)
      if let r = store.report {
        content(r)
      } else if store.loaded {
        Text(tr("暂无", "Nothing yet")).font(Typo.body).foregroundColor(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 40)
      } else {
        ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 40)
      }
      behavior.padding(.top, 22)
    }
    .onAppear { store.observe(session, end: end) }
    .onChange(of: end) { e in
      store.observe(session, end: e)
      Task { await store.refresh(session) }
    }
    .task { await store.refresh(session) }
  }

  @ViewBuilder private func content(_ r: ReportModel) -> some View {
    HStack(spacing: 4) {
      Text(r.range).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
      if let p = r.previous { Text("vs \(p)").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2) }
    }
    .padding(.top, 14)
    if !r.findings.isEmpty {
      Card(padding: 12) {
        VStack(spacing: 0) {
          ForEach(r.findings) { f in
            HStack {
              Text(f.label).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
              Spacer()
              Text("\(tr("次日恢复", "Next-day recovery")) \(TrendsFormat.signed(f.diff, digits: 0))").font(Typo.caption.monospacedDigit())
                .foregroundColor(f.diff > 0 ? Palette.good : f.diff < 0 ? Palette.bad : Palette.text2)
            }
            .padding(.vertical, 8)
          }
        }
      }
      .padding(.top, 10)
    }
    LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
      ForEach(r.tiles) { t in
        VStack(alignment: .leading, spacing: 4) {
          Text(t.label).font(Typo.label).foregroundColor(Palette.text2)
          HStack(alignment: .firstTextBaseline, spacing: 2) {
            Text(t.value).font(Typo.tileValue).foregroundColor(Palette.text).lineLimit(1).minimumScaleFactor(0.6)
            if let u = t.unit { Text(u).font(Typo.unit).foregroundColor(Palette.text2) }
          }
          Text(t.delta ?? " ").font(Typo.label.monospacedDigit()).foregroundColor(t.tone == .text ? Palette.text3 : t.tone.color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
        .accessibilityElement(children: .combine)
      }
    }
    .padding(.top, 10)
  }

  @ViewBuilder private var behavior: some View {
    let b = store.behavior
    if !b.rows.isEmpty || b.lacking > 0 {
      VStack(alignment: .leading, spacing: 10) {
        HStack {
          Button { explain("behavior", tr("行为影响 · 次日恢复", "Behaviour · next-day recovery")) } label: {
            HStack(spacing: 4) {
              Text(tr("行为影响 · 次日恢复", "Behaviour · next-day recovery")).font(Typo.section).foregroundColor(Palette.text2)
              Icon(.info, size: 12).foregroundColor(Palette.text3)
            }
          }
          .buttonStyle(PressScale())
          Spacer()
          Text(tr("90 天", "90 days")).font(Typo.label).foregroundColor(Palette.text3)
        }
        Card(padding: 14) {
          VStack(alignment: .leading, spacing: 12) {
            let top = max(5, b.rows.map { abs($0.diff) }.max() ?? 5)
            ForEach(b.rows) { r in
              VStack(alignment: .leading, spacing: 5) {
                HStack {
                  Text(r.label).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
                  Spacer()
                  Text(TrendsFormat.signed(r.diff, digits: 0) + (r.reliable ? "" : " ?")).font(Typo.bodyStrong.monospacedDigit())
                    .foregroundColor(r.tone == .text ? Palette.text2 : r.tone.color)
                }
                DiffBar(fraction: r.diff / top, tone: r.tone)
              }
            }
            if b.rows.isEmpty { Text("—").foregroundColor(Palette.text3) }
            if b.lacking > 0 { ToneBadge(text: tr("\(b.lacking) 项样本不足", "\(b.lacking) items with too few days")) }
          }
        }
      }
    }
  }
}

/// A bar from the centre: right = positive, left = negative (the page's .dbar).
struct DiffBar: View {
  let fraction: Double
  let tone: Tone

  var body: some View {
    GeometryReader { g in
      let half = g.size.width / 2, w = min(half, abs(fraction) * half)
      ZStack(alignment: .leading) {
        Capsule().fill(Palette.surface2)
        Capsule().fill(tone == .text ? Palette.text3 : tone.color)
          .frame(width: max(2, w))
          .offset(x: fraction >= 0 ? half : half - w)
      }
    }
    .frame(height: 5)
    .accessibilityHidden(true)
  }
}
