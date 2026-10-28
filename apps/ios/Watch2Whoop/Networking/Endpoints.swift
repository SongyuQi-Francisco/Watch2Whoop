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

  // N6: Jarvis (online only, section 5.5)
  static let assistantChat = "/v1/assistant/chat"                   // SSE
  static let assistantConfirm = "/v1/assistant/chat/confirm"        // SSE
  static func assistantEvents(_ conversation: Int) -> String { "/v1/assistant/chat/\(conversation)/events" }
  static let assistantTranscribe = "/v1/assistant/transcribe"
  static let assistantVoiceFix = "/v1/assistant/voice-fix"
  static let conversations = "/v1/assistant/conversations"
  static func conversation(_ id: Int) -> String { "/v1/assistant/conversations/\(id)" }
  static let aiFallback = "/v1/users/me/ai-fallback"
  static let aiFallbackConsent = "/v1/users/me/ai-fallback/consent"
  static let mealTemplates = "/v1/users/me/meal-templates"

  // N8: sync center (docs/apple-sync-engine-app-contract.md)
  static let ingest = "/v1/ingest/healthkit"
  static let ingestSyncStatus = "/v1/ingest/sync-status"
  static let ingestResync = "/v1/ingest/resync"
  static let ingestSyncDevice = "/v1/ingest/sync-device"
  static let historyBackfill = "/v1/users/me/sync/backfill"

  // N4: trends (series and status cards), VO₂max level, sleep regularity, nights, reports, journal behaviour
  static func trends(days: Int, end: String?, user: String = "me") -> APIRequest {
    var q = ["days": String(days)]
    if let end { q["end"] = end }
    return .get("/v1/users/\(user)/trends", q)
  }
  static func vo2max(end: String?, user: String = "me") -> APIRequest {
    var q = ["days": "365"]
    if let end { q["end"] = end }
    return .get("/v1/users/\(user)/vo2max", q)
  }
  static func sleepDetails(end: String?, user: String = "me") -> APIRequest {
    .get("/v1/users/\(user)/sleep/details", end.map { ["end": $0] } ?? [:])
  }
  static func sleepNights(days: Int = 14, end: String?, user: String = "me") -> APIRequest {
    var q = ["days": String(days)]
    if let end { q["end"] = end }
    return .get("/v1/users/\(user)/sleep/nights", q)
  }
  static func report(_ period: String, end: String?, user: String = "me") -> APIRequest {
    var q = ["period": period]
    if let end { q["end"] = end }
    return .get("/v1/users/\(user)/reports", q)
  }
  static func journalAnalysis(end: String?, days: Int = 90, user: String = "me") -> APIRequest {
    var q = ["days": String(days)]
    if let end { q["end"] = end }
    return .get("/v1/users/\(user)/journal/analysis", q)
  }

  // N2 / N2b: incremental sync, the phone's scoring context, app-computed scores, algorithm bundles
  static let syncPull = "/v1/sync/pull"
  static let syncScoring = "/v1/sync/scoring"
  static let syncPush = "/v1/sync/push"
  static let scoresUpload = "/v1/users/me/scores/app"
  static let algoManifest = "/v1/algo/manifest"
}
