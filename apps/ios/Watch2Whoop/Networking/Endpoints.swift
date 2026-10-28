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

  // N3: the views around the today document, sleep detail, explanations, look-back calendar, weigh-ins
  static func dayActivity(_ day: String, user: String = "me") -> APIRequest { .get("/v1/users/\(user)/training/day/\(day)") }
  static func strainCoach(_ day: String, user: String = "me") -> APIRequest { .get("/v1/users/\(user)/strain-coach", ["date": day]) }
  static func healthMonitor(_ day: String, user: String = "me") -> APIRequest { .get("/v1/users/\(user)/health-monitor", ["date": day]) }
  static func memory(user: String = "me") -> APIRequest { .get("/v1/users/\(user)/memory") }
  static func syncOverview(user: String = "me") -> APIRequest { .get("/v1/users/\(user)/sync/overview", ["in_app": "1"]) }
  static func monthDays(_ month: String, user: String = "me") -> APIRequest { .get("/v1/users/\(user)/days", ["month": month]) }
  static func sleepNight(_ day: String?, user: String = "me") -> APIRequest {
    .get("/v1/users/\(user)/sleep/night", day.map { ["date": $0] } ?? [:])
  }
  static func explain(_ kind: String, _ key: String, day: String?, user: String = "me") -> APIRequest {
    let k = key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key
    return .get("/v1/users/\(user)/explain/\(kind)/\(k)", day.map { ["date": $0] } ?? [:])
  }
  static func reminder(_ id: Int) -> String { "/v1/users/me/reminders/\(id)" }
  static let weight = "/v1/users/me/weight"
  static func weightRecent(limit: Int = 8) -> APIRequest { .get("/v1/users/me/weight/recent", ["limit": String(limit)]) }
  static let friendsOverview = APIRequest.get("/v1/friends/overview")

  // N2 (new; not on the server yet): incremental sync, app-computed scores, algorithm bundles
  static let syncPull = "/v1/sync/pull"
  static let syncPush = "/v1/sync/push"
  static let scoresUpload = "/v1/users/me/scores/app"
  static let algoManifest = "/v1/algo/manifest"
}
