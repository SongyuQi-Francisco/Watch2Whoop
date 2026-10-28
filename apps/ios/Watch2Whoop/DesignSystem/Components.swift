import SwiftUI

// Shared building blocks. Pages (N3–N8) compose these instead of styling views themselves, so the app keeps the Today
// page's look: no borders, cards told apart by background, one colour per state, tabular numbers.

/// A card: surface background, 18 pt corners, 16 pt padding (app.css `.card`).
struct Card<Content: View>: View {
  var padding: CGFloat = 16
  @ViewBuilder var content: () -> Content

  var body: some View {
    content()
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(padding)
      .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }
}

/// A section title above a group (13 semibold, secondary).
struct SectionHeader: View {
  let title: String
  var body: some View {
    Text(title).font(Typo.section).foregroundColor(Palette.text2)
      .frame(maxWidth: .infinity, alignment: .leading)
      .accessibilityAddTraits(.isHeader)
  }
}

/// A list row: optional leading icon, title, optional subtitle, optional trailing value, chevron when tappable.
struct ListRow: View {
  var icon: Icon.Name?
  let title: String
  var subtitle: String?
  var value: String?
  var tone: Tone = .text
  var action: (() -> Void)?

  var body: some View {
    let row = HStack(spacing: 12) {
      if let icon { Icon(icon, size: 20).foregroundColor(Palette.text2) }
      VStack(alignment: .leading, spacing: 2) {
        Text(title).font(Typo.body).foregroundColor(Palette.text)
        if let subtitle { Text(subtitle).font(Typo.caption).foregroundColor(Palette.text2) }
      }
      Spacer(minLength: 8)
      if let value {
        Text(value).font(Typo.body.monospacedDigit()).foregroundColor(tone == .text ? Palette.text2 : tone.color)
      }
      if action != nil { Icon(.chevronRight, size: 16).foregroundColor(Palette.text3) }
    }
    .padding(.vertical, 12)
    .contentShape(Rectangle())
    if let action {
      Button(action: action) { row }.buttonStyle(PressScale())
    } else {
      row
    }
  }
}

/// A number tile (label, value, unit), three per row on Today (NativeToday.swift of 1.0.9).
struct MetricTile: View {
  let label: String
  let value: String?
  var unit: String?
  var tone: Tone = .text

  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.8)
      HStack(alignment: .firstTextBaseline, spacing: 2) {
        Text(value ?? "—").font(Typo.tileValue)
          .foregroundColor(value == nil ? Palette.text3 : tone.color).lineLimit(1).minimumScaleFactor(0.6)
        if let unit, value != nil { Text(unit).font(Typo.unit).foregroundColor(Palette.text2).lineLimit(1) }
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(12)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
    .accessibilityElement(children: .combine)
  }
}

/// A small pill (load status, reminders count).
struct Chip: View {
  let label: String
  var tone: Tone = .text
  var body: some View {
    Text(label).font(.system(size: 13, weight: .medium)).foregroundColor(tone.color)
      .padding(.vertical, 7).padding(.horizontal, 12)
      .background(Capsule().fill(Palette.surface))
  }
}

/// Pressed = slightly smaller and dimmer (the page's `:active`).
struct PressScale: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .scaleEffect(configuration.isPressed ? 0.96 : 1)
      .opacity(configuration.isPressed ? 0.75 : 1)
      .animation(.spring(response: 0.25, dampingFraction: 0.8), value: configuration.isPressed)
  }
}

/// The primary button inside forms and sheets.
struct PrimaryButton: View {
  let title: String
  var busy = false
  let action: () -> Void
  var body: some View {
    Button(action: action) {
      HStack {
        if busy { ProgressView().tint(Palette.bg) }
        Text(title).font(Typo.bodyStrong)
      }
      .frame(maxWidth: .infinity, minHeight: 48)
      .foregroundColor(Palette.bg)
      .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.text))
    }
    .buttonStyle(PressScale())
    .disabled(busy)
  }
}

/// Page scaffold: large title + optional date line, content in a scroll view with the page's gutters.
struct PageScaffold<Content: View>: View {
  let title: String
  var overline: String?
  @ViewBuilder var content: () -> Content

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 0) {
        VStack(alignment: .leading, spacing: 1) {
          if let overline { Text(overline).font(Typo.overline).foregroundColor(Palette.text2) }
          Text(title).font(Typo.title).titleTracking().foregroundColor(Palette.text)
        }
        .padding(.top, 10).padding(.bottom, 8)
        content()
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
  }
}
