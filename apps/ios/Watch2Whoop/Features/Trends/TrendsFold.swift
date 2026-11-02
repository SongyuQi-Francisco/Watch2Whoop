import SwiftUI

/// Flag `trends_v2` (the page's foldable categories, #195): a category's header keeps its item count and, when it has one,
/// its status; which ones are folded is kept on this phone.
enum TrendsFold {
  static let flag = "trends_v2"
  static let storageKey = "w2w.trends.folded"

  /// The status card a category's header shows (app.js groupSummary).
  static let summaryKeys = ["recovery": "hrv_status", "strain": "training_status", "stress": "resilience", "sleep": "chronotype"]

  static func summary(_ group: String, _ status: TrendStatus) -> (text: String, tone: Tone)? {
    guard let key = summaryKeys[group], let t = status.tiles.first(where: { $0.key == key }), t.value != "—" else { return nil }
    return (t.value, t.tone)
  }

  // The folded categories as one stored string, comma separated.
  static func contains(_ raw: String, _ group: String) -> Bool { raw.split(separator: ",").contains(Substring(group)) }

  static func toggled(_ raw: String, _ group: String) -> String {
    var set = raw.split(separator: ",").map(String.init)
    if let i = set.firstIndex(of: group) { set.remove(at: i) } else { set.append(group) }
    return set.joined(separator: ",")
  }
}

/// A foldable category's header: title, item count, its status, the chevron.
struct TrendGroupHeader: View {
  let title: String
  let count: Int
  let summary: (text: String, tone: Tone)?
  let folded: Bool
  let toggle: () -> Void

  var body: some View {
    Button(action: toggle) {
      HStack(spacing: 8) {
        Text(title).font(Typo.section).foregroundColor(Palette.text2)
        Text(tr("\(count) 项", "\(count) items")).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3)
        if let s = summary {
          Text(s.text).font(Typo.label).foregroundColor(s.tone == .text ? Palette.text2 : s.tone.color).lineLimit(1)
        }
        Spacer(minLength: 6)
        Icon(.chevronDown, size: 16).foregroundColor(Palette.text3).rotationEffect(.degrees(folded ? 0 : 180))
      }
      .padding(.vertical, 8)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityAddTraits(.isHeader)
    .accessibilityValue(folded ? tr("已收起", "Collapsed") : tr("已展开", "Expanded"))
  }
}
