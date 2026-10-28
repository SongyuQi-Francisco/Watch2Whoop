import Foundation

/// Server paths the native app calls, in one place (docs/ios-native-app.md section 7 lists them by page). Always `me`
/// for the signed-in user; viewing someone else (shares / circles) passes their id.
enum Endpoint {
  // account / session
  static let webMe = "/v1/web/me"                       // me + feature flags + the user's local today
  static let accountLogout = "/v1/account/logout"

  // N0: the today document (until N2's /v1/sync/pull carries it)
  static func today(_ user: String = "me", date: String? = nil) -> APIRequest {
    .get("/v1/users/\(user)/today", date.map { ["date": $0] } ?? [:])
  }

  // N2 (new; not on the server yet): incremental sync, app-computed scores, algorithm bundles
  static let syncPull = "/v1/sync/pull"
  static let syncPush = "/v1/sync/push"
  static let scoresUpload = "/v1/users/me/scores/app"
  static let algoManifest = "/v1/algo/manifest"
}
