import SwiftUI

/// Lucide icons from the asset catalog (scripts/lucide_assets.py; licence in THIRD_PARTY_NOTICES.md). Template images:
/// they take the foreground colour. Never draw icons by hand and never mix in SF Symbols for app chrome.
struct Icon: View {
  enum Name: String, CaseIterable {
    case circleGauge = "circle-gauge"
    case chartLine = "chart-line"
    case users
    case soup
    case squarePlus = "square-plus"
    case user
    case chevronRight = "chevron-right"
    case x
    case refreshCw = "refresh-cw"
    case chevronLeft = "chevron-left"
    case calendar
    case sparkles
    // N8: sign-in, Me / settings, sync center
    case qrCode = "qr-code"
    case mail
    case keyRound = "key-round"
    case link
    case clipboardPaste = "clipboard-paste"
    case heartPulse = "heart-pulse"
    case cloudUpload = "cloud-upload"
    case calendarClock = "calendar-clock"
    case circleCheck = "circle-check"
    case circleAlert = "circle-alert"
    case smartphone
    case bell
    case languages
    case palette
    case megaphone
    case database
    case inbox
    case fileText = "file-text"
    case pill
    case flaskConical = "flask-conical"
    case calendarDays = "calendar-days"
    case logOut = "log-out"
    case userX = "user-x"
    // N6: Jarvis
    case orbit
    case botMessageSquare = "bot-message-square"
    case audioWaveform = "audio-waveform"
    case scanEye = "scan-eye"
    case zap
    case chevronDown = "chevron-down"
    case mic
    case lock
    case arrowUp = "arrow-up"
    case plus
    case messagesSquare = "messages-square"
    case camera
    case image
    case video
    case copy
    case externalLink = "external-link"
    case check
    case audioLines = "audio-lines"
    case trash

    var assetName: String { "lucide.\(rawValue)" }
  }

  let name: Name
  var size: CGFloat = 22

  init(_ name: Name, size: CGFloat = 22) {
    self.name = name
    self.size = size
  }

  var body: some View {
    Image(name.assetName)
      .renderingMode(.template)
      .resizable()
      .scaledToFit()
      .frame(width: size, height: size)
      .accessibilityHidden(true)
  }
}
