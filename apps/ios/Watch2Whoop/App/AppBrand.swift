import Foundation

/// The product name users see (home screen, App Store, permission texts). It is one string in both languages. The open-source
/// project, the repository, the bundle ID, the target / scheme and the `watch2whoop://` URL scheme keep the name Watch2Whoop;
/// only text a person reads uses this constant. Keep it equal to `W2W_DISPLAY_NAME` (Config/Watch2Whoop.xcconfig) and to
/// `CFBundleDisplayName` in both InfoPlist.strings files: scripts/check_sources.py (CI) checks all of them, and it fails on
/// a string literal that still says "Watch2Whoop".
enum AppBrand {
  static let name = "Hale 息"
}
