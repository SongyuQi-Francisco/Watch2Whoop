import SwiftUI

/// Debug-only launch switches for simulator screenshots of the N5 pages (docs/ios-native-app.md section 9.3):
/// - `SIMCTL_CHILD_W2W_DEV_TAB=food|record|…` opens that tab;
/// - `SIMCTL_CHILD_W2W_DEV_RECORD=training|journal|food` picks the 记录 segment;
/// - `SIMCTL_CHILD_W2W_DEV_HEALTH=supplements|labs|events[:test_key]` opens a health record sheet over 记录 (with a test
///   key: that test's trend);
/// - `SIMCTL_CHILD_W2W_DEV_SCROLL=<points>` scrolls the 记录 / 饮食 page down that far.
enum RecordDevShot {
  static func value(_ key: String) -> String? {
    #if DEBUG
    let v = ProcessInfo.processInfo.environment[key]
    return v?.isEmpty == false ? v : nil
    #else
    return nil
    #endif
  }
}

extension View {
  /// `W2W_DEV_TAB`: the tab to open at launch (Debug builds only).
  @ViewBuilder func devTab(_ selection: Binding<AppTab>) -> some View {
    #if DEBUG
    onAppear {
      if let t = RecordDevShot.value("W2W_DEV_TAB").flatMap(AppTab.init(rawValue:)) { selection.wrappedValue = t }
    }
    #else
    self
    #endif
  }
}
