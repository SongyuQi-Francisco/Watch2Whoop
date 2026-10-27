import SwiftUI
import UIKit

// The native 今天 tab (#247 phase 2, flag native_today; NativeUI.swift hosts it over the page's content area). Three rings side
// by side with recovery in the middle (sleep score 0–100, recovery 0–100 in its zone colour, yesterday's strain 0–21), the
// recovery word, the load / reminder chips and the core numbers in three groups. Labels, numbers and formatting come ready
// from Dart (native_ui.dart todayModel, the same numbers as the page), colours from the page's tokens (Theme). A ring or a
// number opens the page's own sheet for it (the page's data-card buttons); pull to refresh reloads the page's data; 「完整今天页」
// shows the web 今天 until the user leaves the tab.

struct TodayRing: Equatable {
  var key = "", card = "", label = "", value = "—", tone = "text"
  var frac: Double = 0
}

struct TodayItem: Equatable {
  var label = "", value = "—"
  var unit: String?, card: String?, tone: String?
}

struct TodaySection: Equatable {
  var title = ""
  var items: [TodayItem] = []
}

struct TodayChip: Equatable {
  var label = ""
  var card: String?
}

struct TodayModel: Equatable {
  var day = ""
  var rings: [TodayRing] = []
  var word: String?, sub: String?, error: String?
  var muted = false
  var sections: [TodaySection] = []
  var chips: [TodayChip] = []
  var labels: [String: String] = [:]

  init() {}
  init(_ m: [String: Any]) {
    day = m["day"] as? String ?? ""
    rings = (m["rings"] as? [[String: Any]] ?? []).map {
      TodayRing(key: $0["key"] as? String ?? "", card: $0["card"] as? String ?? "", label: $0["label"] as? String ?? "",
                value: $0["value"] as? String ?? "—", tone: $0["tone"] as? String ?? "text",
                frac: min(1, max(0, ($0["frac"] as? NSNumber)?.doubleValue ?? 0)))
    }
    word = m["word"] as? String
    sub = m["sub"] as? String
    error = m["error"] as? String
    muted = m["muted"] as? Bool ?? false
    sections = (m["sections"] as? [[String: Any]] ?? []).map { s in
      TodaySection(title: s["title"] as? String ?? "", items: (s["items"] as? [[String: Any]] ?? []).map {
        TodayItem(label: $0["label"] as? String ?? "", value: $0["value"] as? String ?? "—", unit: $0["unit"] as? String, card: $0["card"] as? String,
                  tone: $0["tone"] as? String)
      })
    }
    chips = (m["chips"] as? [[String: Any]] ?? []).map { TodayChip(label: $0["label"] as? String ?? "", card: $0["card"] as? String) }
    labels = m["labels"] as? [String: String] ?? [:]
  }
}

struct NativeTodayView: View {
  let state: TodayState
  let send: (String, [String: Any]) -> Void
  let meter: FrameMeter
  var demo = false
  @State private var shown = false
  @State private var demoRunning = false
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  @Environment(\.colorScheme) private var scheme
  /// The page's colours while they are for the current appearance; a copy kept from a dark start in light mode (or the other way
  /// round) falls back to the built-in tokens of the current one until the page reports again.
  private var th: Theme { state.dark == (scheme == .dark) ? state.theme : Theme(nil, dark: scheme == .dark) }
  private var m: TodayModel { state.model }

  var body: some View {
    ScrollViewReader { proxy in
      scroller.onAppear { if demo { runDemo(proxy) } }
    }
  }

  private var scroller: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(spacing: 0) {
        Color.clear.frame(height: 0).id("top")
        ringsRow.padding(.top, 12)
        if let w = m.word {
          Text(w).font(.system(size: 17, weight: .semibold)).foregroundColor(m.muted ? th.text2 : th.text)
            .multilineTextAlignment(.center).padding(.top, 12)
        }
        if let s = m.sub { Text(s).font(.system(size: 13)).foregroundColor(th.text2).padding(.top, 3) }
        if let e = m.error { Text(e).font(.system(size: 13)).foregroundColor(th.text2).padding(.top, 6) }
        if !m.chips.isEmpty { chipsRow.padding(.top, 14) }
        ForEach(Array(m.sections.enumerated()), id: \.offset) { _, s in section(s).padding(.top, 14) }
        Button { open("web") } label: {
          Text(m.labels["web"] ?? "完整今天页").font(.system(size: 13, weight: .medium)).foregroundColor(th.text2)
            .padding(.vertical, 10).padding(.horizontal, 16)
        }
        .buttonStyle(PressScale())
        .padding(.top, 18)
        Color.clear.frame(height: 0).id("end")
      }
      .padding(.horizontal, 20)
      .padding(.bottom, 32)
      .frame(maxWidth: 640)
      .frame(maxWidth: .infinity)
      .modifier(ScrollMeter(meter: meter))
    }
    .coordinateSpace(name: "today-scroll")
    .modifier(Refresh { open("refresh") })
    .background(th.bg)
    .onAppear {
      if reduceMotion { shown = true } else { withAnimation(.spring(response: 0.9, dampingFraction: 0.86).delay(0.05)) { shown = true } }
    }
  }

  private func open(_ card: String) { send("action", ["kind": "today", "card": card]) }

  /// Debug builds only (`W2W_NATIVE_UI=…,demo`): after the Jarvis demo (about 35 s), scroll to the end and back four times.
  private func runDemo(_ proxy: ScrollViewProxy) {
    guard !demoRunning else { return }
    demoRunning = true
    Task { @MainActor in
      try? await Task.sleep(nanoseconds: 40_000_000_000)
      for k in 0..<8 {
        meter.mark("today-scroll", seconds: 1.3)
        withAnimation(.easeInOut(duration: 0.9)) { proxy.scrollTo(k % 2 == 0 ? "end" : "top", anchor: k % 2 == 0 ? .bottom : .top) }
        try? await Task.sleep(nanoseconds: 1_600_000_000)
      }
      demoRunning = false
    }
  }

  // ------------------------------------------------------------ rings: sleep | recovery | strain
  private var ringsRow: some View {
    let w = max(state.rect.width - 40, 200)
    let side = min(100, (w - 24) / 3.4), big = side * 1.4
    return HStack(alignment: .center, spacing: 12) {
      ForEach(Array(m.rings.enumerated()), id: \.offset) { _, r in
        let isBig = r.key == "recovery"
        Button { open(r.card) } label: { ring(r, size: isBig ? big : side, big: isBig) }
          .buttonStyle(PressScale())
          .accessibilityLabel("\(r.label) \(r.value)")
      }
    }
    .frame(maxWidth: .infinity)
  }

  private func ring(_ r: TodayRing, size: CGFloat, big: Bool) -> some View {
    let line = size * 12 / 196 * (big ? 1 : 1.25)   // the page's ring: r 84 of 196, stroke 12
    return ZStack {
      Circle().stroke(th.surface2, lineWidth: line)
      Circle()
        .trim(from: 0, to: shown ? r.frac : 0)
        .stroke(th.tone(r.tone), style: StrokeStyle(lineWidth: line, lineCap: .round))
        .rotationEffect(.degrees(-90))
        .animation(reduceMotion ? nil : .spring(response: 0.8, dampingFraction: 0.86), value: r.frac)
      VStack(spacing: big ? 2 : 1) {
        Text(r.value).font(.system(size: big ? size * 0.3 : size * 0.26, weight: .bold)).monospacedDigit()
          .foregroundColor(r.value == "—" ? th.text3 : th.text)
          .minimumScaleFactor(0.6).lineLimit(1)
        Text(r.label).font(.system(size: big ? 13 : 11, weight: .medium)).foregroundColor(th.text2).lineLimit(1).minimumScaleFactor(0.7)
      }
      .padding(.horizontal, line + 4)
    }
    .frame(width: size, height: size)
    .padding(line / 2)
  }

  // ------------------------------------------------------------ chips and numbers
  private var chipsRow: some View {
    HStack(spacing: 8) {
      ForEach(Array(m.chips.enumerated()), id: \.offset) { _, c in
        Button { if let k = c.card { open(k) } } label: {
          Text(c.label).font(.system(size: 13, weight: .medium)).foregroundColor(th.text)
            .padding(.vertical, 7).padding(.horizontal, 12)
            .background(Capsule().fill(th.surface))
        }
        .buttonStyle(PressScale())
        .disabled(c.card == nil)
      }
    }
  }

  private func section(_ s: TodaySection) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      Text(s.title).font(.system(size: 13, weight: .semibold)).foregroundColor(th.text2)
      LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8, alignment: .top), count: 3), alignment: .leading, spacing: 8) {
        ForEach(Array(s.items.enumerated()), id: \.offset) { _, it in tile(it) }
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  private func tile(_ it: TodayItem) -> some View {
    Button { if let k = it.card { open(k) } } label: {
      VStack(alignment: .leading, spacing: 4) {
        Text(it.label).font(.system(size: 12, weight: .medium)).foregroundColor(th.text2).lineLimit(1).minimumScaleFactor(0.8)
        HStack(alignment: .firstTextBaseline, spacing: 2) {
          Text(it.value).font(.system(size: 21, weight: .semibold)).monospacedDigit()
            .foregroundColor(it.value == "—" ? th.text3 : it.tone.map(th.tone) ?? th.text).lineLimit(1).minimumScaleFactor(0.6)
          if let u = it.unit { Text(u).font(.system(size: 11, weight: .medium)).foregroundColor(th.text2).lineLimit(1) }
        }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(.vertical, 12).padding(.horizontal, 12)
      .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(th.surface))
    }
    .buttonStyle(PressScale())
    .disabled(it.card == nil)
    .accessibilityElement(children: .combine)
  }
}

/// Pull to refresh (ScrollView.refreshable is iOS 16+): asks the page to reload its data.
private struct Refresh: ViewModifier {
  let action: () -> Void
  @ViewBuilder func body(content: Content) -> some View {
    if #available(iOS 16.0, *) {
      content.refreshable {
        action()
        try? await Task.sleep(nanoseconds: 700_000_000)
      }
    } else {
      content
    }
  }
}

private struct ScrollOffset: PreferenceKey {
  static let defaultValue: CGFloat = 0
  static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}

/// Frame timing while 今天 scrolls (FrameMeter; only when the meter is on, so normal builds do no extra work).
private struct ScrollMeter: ViewModifier {
  let meter: FrameMeter
  @ViewBuilder func body(content: Content) -> some View {
    if meter.enabled {
      content
        .background(GeometryReader { g in Color.clear.preference(key: ScrollOffset.self, value: g.frame(in: .named("today-scroll")).minY) })
        .onPreferenceChange(ScrollOffset.self) { _ in meter.mark("today-scroll", seconds: 0.5) }
    } else {
      content
    }
  }
}
