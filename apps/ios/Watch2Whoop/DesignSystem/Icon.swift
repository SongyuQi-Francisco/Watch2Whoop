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
    // N7: circles (feed, invites, circle management, avatars)
    case heart
    case heartFill = "heart.fill"   // the Lucide heart, filled (scripts/lucide_assets.py FILLED)
    case messageCircle = "message-circle"
    case send
    case trophy
    case dumbbell
    case flame
    case mapPin = "map-pin"
    case activity
    case timer
    case share
    case userPlus = "user-plus"
    case logIn = "log-in"
    case plus
    case copy
    case settings
    case ellipsis
    case camera
    case image
    case trash
    case pin
    case bellOff = "bell-off"
    case archive
    case chevronDown = "chevron-down"
    case flag
    case crown
    case clock

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
