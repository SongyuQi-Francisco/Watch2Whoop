import SwiftUI

// Form building blocks (sign-in, settings sheets), in the Today page's look: surface fields without borders, one
// primary button per screen, secondary actions as tinted text buttons.

/// A labelled text field (the page's `.field` + `.input`).
struct FormField: View {
  let label: String
  @Binding var text: String
  var placeholder = ""
  var secure = false
  var keyboard: UIKeyboardType = .default
  var contentType: UITextContentType?
  var background = Palette.surface

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      if !label.isEmpty { Text(label).font(Typo.label).foregroundColor(Palette.text2) }
      Group {
        if secure { SecureField(placeholder, text: $text) } else { TextField(placeholder, text: $text) }
      }
      .keyboardType(keyboard)
      .textContentType(contentType)
      .textInputAutocapitalization(.never)
      .autocorrectionDisabled()
      .font(Typo.body)
      .foregroundColor(Palette.text)
      .padding(.horizontal, 14).frame(minHeight: 46)
      .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(background))
    }
  }
}

/// A full-width secondary button: surface background, icon + title.
struct SecondaryButton: View {
  var icon: Icon.Name?
  let title: String
  var tone: Tone = .text
  var background = Palette.surface
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 8) {
        if let icon { Icon(icon, size: 18) }
        Text(title).font(Typo.bodyStrong)
      }
      .foregroundColor(tone.color)
      .frame(maxWidth: .infinity, minHeight: 48)
      .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(background))
    }
    .buttonStyle(PressScale())
  }
}

/// A short message with a state colour and an optional action (errors, hints, "where it is stuck").
struct Banner: View {
  let text: String
  var tone: Tone = .text
  var actionTitle: String?
  var action: (() -> Void)?

  var body: some View {
    HStack(alignment: .center, spacing: 10) {
      if tone != .text { Icon(tone == .good ? .circleCheck : .circleAlert, size: 18).foregroundColor(tone.color) }
      Text(text).font(Typo.caption).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
      Spacer(minLength: 4)
      if let actionTitle, let action {
        Button(actionTitle, action: action)
          .font(.system(size: 13, weight: .semibold)).foregroundColor(Palette.text)
          .padding(.vertical, 7).padding(.horizontal, 12)
          .background(Capsule().fill(Palette.surface2))
          .buttonStyle(PressScale())
      }
    }
    .padding(12)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(background))
  }

  private var background: Color {
    switch tone {
    case .good: return Palette.goodSoft
    case .mid: return Palette.midSoft
    case .bad: return Palette.badSoft
    default: return Palette.surface
    }
  }
}

/// Rows inside one card with hairlines between them (settings lists).
struct RowGroup<Content: View>: View {
  var title: String?
  @ViewBuilder var content: () -> Content

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      if let title { SectionHeader(title: title).padding(.leading, 4) }
      VStack(spacing: 0) { content() }
        .padding(.horizontal, 14)
        .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    }
  }
}

/// The hairline between rows of a RowGroup.
struct RowDivider: View {
  var inset: CGFloat = 32
  var body: some View { Divider().overlay(Palette.hair).padding(.leading, inset) }
}

/// A row with a switch.
struct ToggleRow: View {
  let title: String
  var subtitle: String?
  @Binding var isOn: Bool

  var body: some View {
    Toggle(isOn: $isOn) {
      VStack(alignment: .leading, spacing: 2) {
        Text(title).font(Typo.body).foregroundColor(Palette.text)
        if let subtitle { Text(subtitle).font(Typo.caption).foregroundColor(Palette.text2) }
      }
    }
    .tint(Palette.good)
    .padding(.vertical, 10)
  }
}

/// A thin progress bar (first sync, history, re-sync).
struct ProgressBar: View {
  let fraction: Double
  var tone: Tone = .good

  var body: some View {
    GeometryReader { g in
      ZStack(alignment: .leading) {
        Capsule().fill(Palette.surface2)
        Capsule().fill(tone.color).frame(width: max(4, g.size.width * min(1, max(0, fraction))))
      }
    }
    .frame(height: 6)
    .accessibilityElement()
    .accessibilityValue("\(Int(fraction * 100))%")
  }
}
