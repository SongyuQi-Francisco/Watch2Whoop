import Foundation

/// Thresholds of the "is the background sync running?" check (BackgroundCheck, docs/ios-native-app.md "Background sync:
/// evidence, not the flag"). Operational, not health rules.
enum BackgroundSyncParams {
  /// No background wake for this long while the phone has newer data = the background sync is not running. Source: the
  /// server's phone-sync health check measured the delay from the watch to the server on healthy phones on 2026-10-26
  /// (median 1.8–3.2 h, p90 7–9 h, iOS's own background scheduling) and warns after 12 h without an upload; the phone uses
  /// the same 12 h so it and the administrator agree, and a healthy phone (HealthKit wakes within the hour for immediate
  /// types, BGAppRefresh at least every 30 min when iOS allows it, SyncEngine.swift) is far inside it.
  static let idleHours: Double = 12
  /// The window of the wake count sent with the device report (`background_wakes_7d`).
  static let countDays: Double = 7
  /// Wake times kept on the phone for that count (times only; a busy phone wakes a few dozen times a day).
  static let keptWakes = 500
}
