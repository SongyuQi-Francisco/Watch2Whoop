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
