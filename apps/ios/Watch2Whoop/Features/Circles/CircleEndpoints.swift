import Foundation

/// Server paths of the circles (docs/ios-native-app.md section 7.1, row 圈子). Everything acts for the signed-in user:
/// there is no user in these paths. Behind the `friends` flag (404 while it is off), the feed also behind `circle_feed`.
extension Endpoint {
  enum Circles {
    static let list = "/v1/friends"                                   // my circles + invites addressed to me
    static let overview = "/v1/friends/overview"                      // member cards (?circle=)
    static let leaderboard = "/v1/friends/leaderboard"                // ?board=&circle=
    static let create = "/v1/friends/circles"
    static let preview = "/v1/friends/invite"                         // ?code=
    static let join = "/v1/friends/join"
    static let nudges = "/v1/friends/nudges"
    static let invites = "/v1/friends/invites"                        // DELETE: decline all
    static func circle(_ id: Int) -> String { "/v1/friends/circles/\(id)" }
    static func invite(_ id: Int) -> String { "/v1/friends/circles/\(id)/invite" }
    static func revokeInvite(_ circle: Int, _ invite: Int) -> String { "/v1/friends/circles/\(circle)/invites/\(invite)" }
    static func me(_ id: Int) -> String { "/v1/friends/circles/\(id)/me" }                 // PATCH scopes, DELETE leave
    static func member(_ circle: Int, _ user: Int) -> String { "/v1/friends/circles/\(circle)/members/\(user)" }
    static func prefs(_ id: Int) -> String { "/v1/friends/circles/\(id)/prefs" }
    static func decline(_ invite: Int) -> String { "/v1/friends/invites/\(invite)" }
    static func remark(_ user: Int) -> String { "/v1/friends/remarks/\(user)" }

    // the training feed (friends/feed_api.py)
    static let feed = "/v1/friends/feed"                               // ?before=&limit=&circle=
    static let unread = "/v1/friends/feed/unread"
    static let seen = "/v1/friends/feed/seen"
    static func like(_ post: Int) -> String { "/v1/friends/feed/posts/\(post)/like" }
    static func comments(_ post: Int) -> String { "/v1/friends/feed/posts/\(post)/comments" }
    static func comment(_ id: Int) -> String { "/v1/friends/feed/comments/\(id)" }
    static func report(_ id: Int) -> String { "/v1/friends/feed/comments/\(id)/report" }

    // moderation (friends/moderation_api.py): block, report, community rules
    static let blocks = "/v1/friends/blocks"
    static func block(_ user: Int) -> String { "/v1/friends/blocks/\(user)" }   // PUT block, DELETE unblock
    static let reports = "/v1/friends/reports"
    static let rules = "/v1/friends/rules"                             // ?lang=
    static let rulesAccept = "/v1/friends/rules/accept"

    // avatars (avatars/api.py): the body of an upload is the picture itself
    static let myAvatar = "/v1/avatars/me"
    static func circleAvatar(_ id: Int) -> String { "/v1/avatars/circles/\(id)" }
  }
}

/// A server-relative URL as the API sends it (`/v1/avatars/users/7?v=3`) split into path and query, for `APIRequest`.
func apiRequest(relative url: String) -> APIRequest {
  guard let q = url.firstIndex(of: "?") else { return .get(url) }
  var r = APIRequest(path: String(url[..<q]))
  r.query = URLComponents(string: "http://x" + url)?.queryItems ?? []
  return r
}
