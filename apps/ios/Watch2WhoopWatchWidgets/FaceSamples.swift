import Foundation

/// The widget gallery's sample (WidgetKit's placeholder and preview snapshot): synthetic numbers in the shape of the
/// mockups (C1), never anyone's data.
enum FaceSamples {
  static func idle(_ t: Date) -> FaceEntry {
    FaceEntry(date: t, lang: Lang.code, signedIn: true, recovery: 82, tone: .good, grey: false, sleep: 88, strain: 2.8,
              key: .train, live: nil)
  }
}
