import Foundation

/// Bilingual UI, same rule as the Today page and the Flutter app (CONTRIBUTING.md "Bilingual UI"): every user-facing
/// string is written `tr("中文", "English")`. `scripts/check_i18n.py` (CI) fails on Chinese outside `tr()` and on Chinese
/// in the English half. The language follows the user's choice (migrated from the Flutter app's `w2w.lang.v1`), else
/// the system's first preferred language.
enum Lang {
  /// "zh" / "en" chosen in the app; nil = follow the system.
  static let overrideKey = "w2w.native.lang.v1"

  static var override: String? {
    get { UserDefaults.standard.string(forKey: overrideKey) }
    set { UserDefaults.standard.set(newValue, forKey: overrideKey) }
  }

  static var isZh: Bool {
    if let o = override { return o.hasPrefix("zh") }
    return (Locale.preferredLanguages.first ?? "zh").hasPrefix("zh")
  }

  /// Sent as `X-Body-Lang` so server labels (`L()` / `tl()`) come in the same language.
  static var code: String { isZh ? "zh" : "en" }
}

/// The one way to write user-facing text.
@inline(__always)
func tr(_ zh: String, _ en: String) -> String { Lang.isZh ? zh : en }
