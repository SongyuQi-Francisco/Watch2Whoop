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

  // N8: sign-in (no token yet), account, consent, profile
  static let authMethods = "/v1/auth/methods"
  static let authEmailStart = "/v1/auth/email/start"
  static let authEmailVerify = "/v1/auth/email/verify"
  static let authApple = "/v1/auth/apple"
  static let webLoginCode = "/v1/web/login-code"      // client=app: the setup link's one-time code -> a device token
  static let webActivate = "/v1/web/activate"         // activation code -> setup link
  static let account = "/v1/account"
  static let redeemInvite = "/v1/account/redeem-invite"
  static let consent = "/v1/consent"
  static let myConsent = "/v1/users/me/consent"
  static let profile = "/v1/users/me/profile"

  // N8: Me / settings
  static let aiChoice = "/v1/users/me/ai-choice"
  static let aiTokens = "/v1/users/me/ai-tokens"
  static func aiToken(_ id: Int) -> String { "/v1/users/me/ai-tokens/\(id)" }
  static let notificationSettings = "/v1/users/me/notification-settings"
  static let pushDevices = "/v1/users/me/push-devices"
  static let whatsNew = "/v1/whats-new"
  static let dataStatus = "/v1/users/me/data-status"
  static let appearance = "/v1/users/me/appearance"

  // N8: sync center (docs/apple-sync-engine-app-contract.md)
  static let ingest = "/v1/ingest/healthkit"
  static let ingestSyncStatus = "/v1/ingest/sync-status"
  static let ingestResync = "/v1/ingest/resync"
  static let ingestSyncDevice = "/v1/ingest/sync-device"
  static let historyBackfill = "/v1/users/me/sync/backfill"

  // N2 (new; not on the server yet): incremental sync, app-computed scores, algorithm bundles
  static let syncPull = "/v1/sync/pull"
  static let syncPush = "/v1/sync/push"
  static let scoresUpload = "/v1/users/me/scores/app"
  static let algoManifest = "/v1/algo/manifest"
}
