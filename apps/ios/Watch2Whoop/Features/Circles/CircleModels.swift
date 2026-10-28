import Foundation

// Server answers of the circles endpoints (body_engine/friends/api.py, feed_api.py, avatars/api.py), decoded leniently:
// a field the server leaves out (a flag that is off, an older server) falls back to its default instead of failing the
// whole page. Everything here is only what the other person chose to share with the signed-in user (the server decides
// per read); nothing of another person is ever computed on the phone. Encodable too: the feed and the overview are kept
// in the `docs` table as the offline cache (docs/ios-native-app.md section 4.2 「只存圈子动态的缓存文档」).

extension KeyedDecodingContainer {
  /// The value, or nil when it is missing, null or of another type.
  func opt<T: Decodable>(_ key: Key) -> T? { (try? decodeIfPresent(T.self, forKey: key)) ?? nil }
}

/// What a person may share with a circle (friends/params.py CIRCLE_SCOPES). ECGs never.
enum CircleScope {
  static let all = ["scores", "sleep", "training", "nutrition_summary", "nutrition", "weight", "metrics", "labs",
                    "supplements", "journal", "events", "memory"]
  static let defaults = ["scores", "sleep", "training", "nutrition_summary"]

  static func label(_ s: String) -> String {
    switch s {
    case "scores": return tr("评分", "Scores")
    case "sleep": return tr("睡眠", "Sleep")
    case "training": return tr("训练", "Training")
    case "nutrition_summary": return tr("饮食达标率", "Diet on target")
    case "nutrition": return tr("具体饮食", "Food details")
    case "weight": return tr("体重", "Weight")
    case "metrics": return tr("每日指标", "Daily metrics")
    case "labs": return tr("化验", "Lab results")
    case "supplements": return tr("补剂", "Supplements")
    case "journal": return tr("日记", "Journal")
    case "events": return tr("身体事件", "Body events")
    case "memory": return tr("长期记忆", "AI memory")
    default: return s
    }
  }

  static func list(_ scopes: [String]) -> String {
    scopes.isEmpty ? tr("什么都不共享", "Nothing shared") : scopes.map(label).joined(separator: " · ")
  }
}

// MARK: - GET /v1/friends (my circles, invites addressed to me)

struct CirclesList: Codable, Equatable {
  var circles: [FriendCircle] = []
  var invites: [InboxInvite] = []
  var prefsOn = false
  var scopes: [String] = CircleScope.all
  var defaultScopes: [String] = CircleScope.defaults
  var consentOk = true
  var limits = Limits()
  var searchFrom = 6

  struct Limits: Codable, Equatable {
    var circles = 10
    var members = 30
    var inviteDays: [Int] = [1, 3, 7, 30]
    var inviteDefaultDays = 3
    enum CodingKeys: String, CodingKey {
      case circles, members, inviteDays = "invite_days", inviteDefaultDays = "invite_default_days"
    }
    init() {}
    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      circles = c.opt(.circles) ?? 10
      members = c.opt(.members) ?? 30
      inviteDays = c.opt(.inviteDays) ?? [1, 3, 7, 30]
      inviteDefaultDays = c.opt(.inviteDefaultDays) ?? 3
    }
  }

  enum CodingKeys: String, CodingKey {
    case circles, invites, scopes, limits
    case prefsOn = "prefs_on", defaultScopes = "default_scopes", consentOk = "consent_ok", searchFrom = "search_from"
  }

  init() {}
  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    circles = c.opt(.circles) ?? []
    invites = c.opt(.invites) ?? []
    prefsOn = c.opt(.prefsOn) ?? false
    scopes = c.opt(.scopes) ?? CircleScope.all
    defaultScopes = c.opt(.defaultScopes) ?? CircleScope.defaults
    consentOk = c.opt(.consentOk) ?? true
    limits = c.opt(.limits) ?? Limits()
    searchFrom = c.opt(.searchFrom) ?? 6
  }

  /// Pinned first, then the most recent activity; archived ones apart (the circles sheet of the Today page).
  var ordered: (shown: [FriendCircle], archived: [FriendCircle]) {
    let sorted = prefsOn
      ? circles.sorted { a, b in
          if a.prefs.pinned != b.prefs.pinned { return a.prefs.pinned }
          return (a.recentAt ?? "") > (b.recentAt ?? "")
        }
      : circles
    return (sorted.filter { !(prefsOn && $0.prefs.archived) }, sorted.filter { prefsOn && $0.prefs.archived })
  }
}

struct FriendCircle: Codable, Equatable, Identifiable {
  let id: Int
  var name: String
  var ownerId: Int?
  var isOwner = false
  var personal = false
  var avatar: String?
  var maxMembers: Int?
  var myScopes: [String] = []
  var members: [Member] = []
  var prefs = Prefs()
  var recentAt: String?
  var invites: LiveInvites?

  struct Member: Codable, Equatable, Identifiable {
    let id: Int
    var displayName: String
    var joinedAt: String?
    var owner = false
    var me = false
    var avatar: String?
    var remark: String?
    var join: Join?

    /// How a member came in (owner only, flag circle_join_trail).
    struct Join: Codable, Equatable {
      var via: String?
      var code: String?
      var by: By?
      struct By: Codable, Equatable {
        var displayName: String?
        var remark: String?
        var me: Bool?
        enum CodingKeys: String, CodingKey { case displayName = "display_name", remark, me }
      }
    }

    enum CodingKeys: String, CodingKey {
      case id, owner, me, avatar, remark, join, displayName = "display_name", joinedAt = "joined_at"
    }

    init(id: Int, displayName: String, owner: Bool = false, me: Bool = false) {
      self.id = id
      self.displayName = displayName
      self.owner = owner
      self.me = me
    }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      id = try c.decode(Int.self, forKey: .id)
      displayName = c.opt(.displayName) ?? ""
      joinedAt = c.opt(.joinedAt)
      owner = c.opt(.owner) ?? false
      me = c.opt(.me) ?? false
      avatar = c.opt(.avatar)
      remark = c.opt(.remark)
      join = c.opt(.join)
    }

    /// What I call them (my private remark), else their name.
    var shownName: String { remark?.isEmpty == false ? remark! : displayName }
  }

  struct Prefs: Codable, Equatable {
    var pinned = false
    var muted = false
    var archived = false
  }

  /// The live invite codes of a circle I own (no plaintext: only when they end and how often they were used).
  struct LiveInvites: Codable, Equatable {
    var live = 0
    var codes: [Code] = []
    struct Code: Codable, Equatable, Identifiable {
      let id: Int
      var expiresAt: String?
      var usedCount = 0
      enum CodingKeys: String, CodingKey { case id, expiresAt = "expires_at", usedCount = "used_count" }
      init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        expiresAt = c.opt(.expiresAt)
        usedCount = c.opt(.usedCount) ?? 0
      }
    }
  }

  enum CodingKeys: String, CodingKey {
    case id, name, personal, avatar, members, prefs, invites
    case ownerId = "owner_id", isOwner = "is_owner", maxMembers = "max_members", myScopes = "my_scopes"
    case recentAt = "recent_at"
  }

  init(id: Int, name: String) {
    self.id = id
    self.name = name
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(Int.self, forKey: .id)
    name = c.opt(.name) ?? ""
    ownerId = c.opt(.ownerId)
    isOwner = c.opt(.isOwner) ?? false
    personal = c.opt(.personal) ?? false
    avatar = c.opt(.avatar)
    maxMembers = c.opt(.maxMembers)
    myScopes = c.opt(.myScopes) ?? []
    members = c.opt(.members) ?? []
    prefs = c.opt(.prefs) ?? Prefs()
    recentAt = c.opt(.recentAt)
    invites = c.opt(.invites)
  }
}

/// An invite addressed to me (someone invited me by account, e.g. after I signed up with their referral code).
struct InboxInvite: Codable, Equatable, Identifiable {
  let id: Int
  var circleId: Int?
  var name: String
  var fromName: String?
  var members = 0
  var defaultScopes: [String] = CircleScope.defaults
  var expiresAt: String?

  enum CodingKeys: String, CodingKey {
    case id, name, members, circleId = "circle_id", fromName = "from_name", defaultScopes = "default_scopes",
         expiresAt = "expires_at"
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(Int.self, forKey: .id)
    circleId = c.opt(.circleId)
    name = c.opt(.name) ?? ""
    fromName = c.opt(.fromName)
    members = c.opt(.members) ?? 0
    defaultScopes = c.opt(.defaultScopes) ?? CircleScope.defaults
    expiresAt = c.opt(.expiresAt)
  }
}

/// GET /v1/friends/invite?code=: what a code opens, before choosing what to share.
struct InvitePreview: Codable, Equatable {
  var id: Int?
  var name: String
  var ownerName: String?
  var members = 0
  var defaultScopes: [String]?
  var circleCode = false

  enum CodingKeys: String, CodingKey {
    case id, name, members, ownerName = "owner_name", defaultScopes = "default_scopes", circleCode = "circle_code"
  }

  init(name: String) { self.name = name }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = c.opt(.id)
    name = c.opt(.name) ?? ""
    ownerName = c.opt(.ownerName)
    members = c.opt(.members) ?? 0
    defaultScopes = c.opt(.defaultScopes)
    circleCode = c.opt(.circleCode) ?? false
  }
}

/// POST /v1/friends/circles/{id}/invite (and the `invite` of a new circle): the plaintext code exists only in this answer.
struct MintedInvite: Codable, Equatable {
  var id: Int?
  var code: String
  var expiresAt: String?
  enum CodingKeys: String, CodingKey { case id, code, expiresAt = "expires_at" }
}

/// POST /v1/friends/circles → {circle, invite}.
struct CreatedCircle: Decodable {
  var circle: FriendCircle
  var invite: MintedInvite?
}

// MARK: - GET /v1/friends/overview (member cards)

struct MemberOverview: Codable, Equatable {
  var me: MemberCard?
  var members: [MemberCard] = []
  var nudgeKinds: [String] = ["clap", "sleep", "train"]
  var hasCircles = false
  var circles: [CircleChip]?
  var remarkMax = 20

  enum CodingKeys: String, CodingKey {
    case me, members, circles, nudgeKinds = "nudge_kinds", hasCircles = "has_circles", remarkMax = "remark_max"
  }

  init() {}
  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    me = c.opt(.me)
    members = c.opt(.members) ?? []
    nudgeKinds = c.opt(.nudgeKinds) ?? ["clap", "sleep", "train"]
    hasCircles = c.opt(.hasCircles) ?? false
    circles = c.opt(.circles)
    remarkMax = c.opt(.remarkMax) ?? 20
  }

  var everyone: [MemberCard] { (me.map { [$0] } ?? []) + members }
}

/// One circle in the switcher (flag circles_by_circle).
struct CircleChip: Codable, Equatable, Identifiable {
  let id: Int
  var name: String
  var members = 0
  var avatar: String?
  var personal = false
  var pinned = false
  var muted = false
  var archived = false

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(Int.self, forKey: .id)
    name = c.opt(.name) ?? ""
    members = c.opt(.members) ?? 0
    avatar = c.opt(.avatar)
    personal = c.opt(.personal) ?? false
    pinned = c.opt(.pinned) ?? false
    muted = c.opt(.muted) ?? false
    archived = c.opt(.archived) ?? false
  }
  enum CodingKeys: String, CodingKey { case id, name, members, avatar, personal, pinned, muted, archived }
}

struct MemberCard: Codable, Equatable, Identifiable {
  let id: Int
  var displayName: String
  var isSelf = false
  var date: String?
  /// null for my own card; else the scopes this person shares with me.
  var shared: [String]?
  var hidden: [String] = []
  var scores: Scores?
  var trained: Bool?
  var avatar: String?
  var remark: String?
  var sent: [String] = []
  /// My own card: cheers I got in the last 24 h, by kind → who.
  var received: [String: [String]]?
  var seen: Seen?

  struct Scores: Codable, Equatable {
    var recovery: Double?
    var zone: String?
    var label: String?
    var sleepScore: Double?
    var sleepMin: Double?
    var strain: Double?
    var state: String?
    var lastSyncMin: Int?
    var lastSyncDate: String?
    var lastDate: String?
    var week: [Day] = []

    struct Day: Codable, Equatable {
      var date: String
      var today = false
      var recovery: Double?
      var zone: String?
      var sleepScore: Double?
      var sleepMin: Double?
      var strain: Double?
      enum CodingKeys: String, CodingKey {
        case date, today, recovery, zone, strain, sleepScore = "sleep_score", sleepMin = "sleep_min"
      }
      init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        date = c.opt(.date) ?? ""
        today = c.opt(.today) ?? false
        recovery = c.opt(.recovery)
        zone = c.opt(.zone)
        sleepScore = c.opt(.sleepScore)
        sleepMin = c.opt(.sleepMin)
        strain = c.opt(.strain)
      }
    }

    enum CodingKeys: String, CodingKey {
      case recovery, zone, label, strain, state, week
      case sleepScore = "sleep_score", sleepMin = "sleep_min", lastSyncMin = "last_sync_min"
      case lastSyncDate = "last_sync_date", lastDate = "last_date", syncedToday = "synced_today"
    }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      recovery = c.opt(.recovery)
      zone = c.opt(.zone)
      label = c.opt(.label)
      sleepScore = c.opt(.sleepScore)
      sleepMin = c.opt(.sleepMin)
      strain = c.opt(.strain)
      let synced: Bool? = c.opt(.syncedToday)
      state = c.opt(.state) ?? (synced == true ? "scored" : "stale")   // older servers only send synced_today
      lastSyncMin = c.opt(.lastSyncMin)
      lastSyncDate = c.opt(.lastSyncDate)
      lastDate = c.opt(.lastDate)
      week = c.opt(.week) ?? []
    }

    func encode(to e: Encoder) throws {
      var c = e.container(keyedBy: CodingKeys.self)
      try c.encodeIfPresent(recovery, forKey: .recovery)
      try c.encodeIfPresent(zone, forKey: .zone)
      try c.encodeIfPresent(label, forKey: .label)
      try c.encodeIfPresent(sleepScore, forKey: .sleepScore)
      try c.encodeIfPresent(sleepMin, forKey: .sleepMin)
      try c.encodeIfPresent(strain, forKey: .strain)
      try c.encodeIfPresent(state, forKey: .state)
      try c.encodeIfPresent(lastSyncMin, forKey: .lastSyncMin)
      try c.encodeIfPresent(lastSyncDate, forKey: .lastSyncDate)
      try c.encodeIfPresent(lastDate, forKey: .lastDate)
      try c.encode(week, forKey: .week)
    }
  }

  /// My own card: who sees what of me (flag friend_card_v2).
  struct Seen: Codable, Equatable {
    var count = 0
    var scopes: [String] = []
    var varies = false
    var hidden: [String] = []
  }

  enum CodingKeys: String, CodingKey {
    case id, date, shared, hidden, scores, trained, avatar, remark, sent, received, seen
    case displayName = "display_name", isSelf = "self"
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(Int.self, forKey: .id)
    displayName = c.opt(.displayName) ?? ""
    isSelf = c.opt(.isSelf) ?? false
    date = c.opt(.date)
    shared = c.opt(.shared)
    hidden = c.opt(.hidden) ?? []
    scores = c.opt(.scores)
    trained = c.opt(.trained)
    avatar = c.opt(.avatar)
    remark = c.opt(.remark)
    sent = c.opt(.sent) ?? []
    received = c.opt(.received)
    seen = c.opt(.seen)
  }

  var shownName: String { remark?.isEmpty == false ? remark! : displayName }
  /// What my friends do not see of me (my card) / what this person does not share with me.
  func isHidden(_ scope: String) -> Bool { (isSelf ? seen?.hidden : hidden)?.contains(scope) ?? false }
}

// MARK: - GET /v1/friends/leaderboard

struct Leaderboard: Codable, Equatable {
  var board: String
  var windowDays = 7
  var boards: [String] = ["body", "activity", "lifestyle", "recomp", "longevity"]
  var rows: [Row] = []

  struct Row: Codable, Equatable, Identifiable {
    let id: Int
    var displayName: String
    var isSelf = false
    var avatar: String?
    var remark: String?
    var status: String
    var score: Double?
    var rank: Int?
    var sub: [String: Double] = [:]

    enum CodingKeys: String, CodingKey {
      case id, avatar, remark, status, score, rank, sub, displayName = "display_name", isSelf = "self"
    }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      id = try c.decode(Int.self, forKey: .id)
      displayName = c.opt(.displayName) ?? ""
      isSelf = c.opt(.isSelf) ?? false
      avatar = c.opt(.avatar)
      remark = c.opt(.remark)
      status = c.opt(.status) ?? "insufficient"
      score = c.opt(.score)
      rank = c.opt(.rank)
      // numbers only (the board's sub line); anything else is left out
      if let raw = try? c.decodeIfPresent([String: LooseNumber].self, forKey: .sub) {
        sub = raw.compactMapValues { $0.value }
      }
    }

    var shownName: String { remark?.isEmpty == false ? remark! : displayName }
  }

  enum CodingKeys: String, CodingKey { case board, boards, rows, windowDays = "window_days" }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    board = c.opt(.board) ?? "body"
    windowDays = c.opt(.windowDays) ?? 7
    boards = c.opt(.boards) ?? ["body", "activity", "lifestyle", "recomp", "longevity"]
    rows = c.opt(.rows) ?? []
  }
}

/// A JSON value that may or may not be a number.
struct LooseNumber: Decodable {
  let value: Double?
  init(from d: Decoder) throws {
    let c = try d.singleValueContainer()
    value = try? c.decode(Double.self)
  }
}

// MARK: - GET /v1/friends/feed (training posts)

struct FeedPage: Decodable {
  var posts: [Post] = []
  var next: String?
  var hasCircles = true
  var previewExercises = 3
  var commentPreview = 2
  var commentMax = 300

  enum CodingKeys: String, CodingKey {
    case posts, next, hasCircles = "has_circles", previewExercises = "preview_exercises"
    case commentPreview = "comment_preview", commentMax = "comment_max"
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    posts = c.opt(.posts) ?? []
    next = c.opt(.next)
    hasCircles = c.opt(.hasCircles) ?? true
    previewExercises = c.opt(.previewExercises) ?? 3
    commentPreview = c.opt(.commentPreview) ?? 2
    commentMax = c.opt(.commentMax) ?? 300
  }
}

struct PostUser: Codable, Equatable {
  let id: Int
  var name: String
  var isSelf = false
  var avatar: String?
  var remark: String?

  enum CodingKeys: String, CodingKey { case id, name, avatar, remark, isSelf = "self" }

  init(id: Int, name: String, isSelf: Bool = false, avatar: String? = nil) {
    self.id = id
    self.name = name
    self.isSelf = isSelf
    self.avatar = avatar
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(Int.self, forKey: .id)
    name = c.opt(.name) ?? ""
    isSelf = c.opt(.isSelf) ?? false
    avatar = c.opt(.avatar)
    remark = c.opt(.remark)
  }

  var shownName: String { remark?.isEmpty == false ? remark! : name }
}

struct Post: Codable, Equatable, Identifiable {
  let id: Int
  var date: String
  var user: PostUser
  var circleRefs: [CircleRef] = []
  var title: String = ""
  var durationMin: Double?
  var startedAt: String?
  var exerciseCount = 0
  var setCount = 0
  var volumeKg: Double = 0
  var prCount = 0
  var exercises: [Exercise] = []
  var activeKcal: Double?
  var avgHr: Double?
  var maxHr: Double?
  var activities: [Activity] = []
  var noExercises = false
  var likes = Likes()
  var commentCount = 0
  var previewComments: [Comment] = []

  struct CircleRef: Codable, Equatable {
    var id: Int?
    var name: String
    var avatar: String?
  }

  struct Likes: Codable, Equatable {
    var count = 0
    var mine = false
  }

  struct Exercise: Codable, Equatable {
    var slug: String?
    var name: String
    var thumb: String?
    var muscles: [String] = []
    var sets: [WorkSet] = []
    var volumeKg: Double = 0
    var pr = false

    struct WorkSet: Codable, Equatable {
      var reps: Double?
      var weightKg: Double?
      var durationS: Double?
      enum CodingKeys: String, CodingKey { case reps, weightKg = "weight_kg", durationS = "duration_s" }
    }

    enum CodingKeys: String, CodingKey { case slug, name, thumb, muscles, sets, pr, volumeKg = "volume_kg" }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      slug = c.opt(.slug)
      name = c.opt(.name) ?? ""
      thumb = c.opt(.thumb)
      muscles = c.opt(.muscles) ?? []
      sets = c.opt(.sets) ?? []
      volumeKg = c.opt(.volumeKg) ?? 0
      pr = c.opt(.pr) ?? false
    }
  }

  /// One watch workout of the day: its summary numbers only (never a heart-rate series).
  struct Activity: Codable, Equatable {
    var type: String?
    var kind: String?
    var name: String
    var durationMin: Double?
    var activeKcal: Double?
    var avgHr: Double?
    var maxHr: Double?
    var distanceKm: Double?
    var hrZones: [Double]?
    var load: Double?

    enum CodingKeys: String, CodingKey {
      case type, kind, name, load, durationMin = "duration_min", activeKcal = "active_kcal", avgHr = "avg_hr"
      case maxHr = "max_hr", distanceKm = "distance_km", hrZones = "hr_zones"
    }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      type = c.opt(.type)
      kind = c.opt(.kind)
      name = c.opt(.name) ?? ""
      durationMin = c.opt(.durationMin)
      activeKcal = c.opt(.activeKcal)
      avgHr = c.opt(.avgHr)
      maxHr = c.opt(.maxHr)
      distanceKm = c.opt(.distanceKm)
      hrZones = c.opt(.hrZones)
      load = c.opt(.load)
    }
  }

  enum CodingKeys: String, CodingKey {
    case id, date, user, title, exercises, activities, likes
    case circleRefs = "circle_refs", circles, durationMin = "duration_min", startedAt = "started_at"
    case exerciseCount = "exercise_count", setCount = "set_count", volumeKg = "volume_kg", prCount = "pr_count"
    case activeKcal = "active_kcal", avgHr = "avg_hr", maxHr = "max_hr", noExercises = "no_exercises"
    case commentCount = "comment_count", previewComments = "preview_comments"
  }

  init(id: Int, date: String, user: PostUser, title: String) {
    self.id = id
    self.date = date
    self.user = user
    self.title = title
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(Int.self, forKey: .id)
    date = c.opt(.date) ?? ""
    user = try c.decode(PostUser.self, forKey: .user)
    if let refs: [CircleRef] = c.opt(.circleRefs) {
      circleRefs = refs
    } else if let names: [String] = c.opt(.circles) {
      circleRefs = names.map { CircleRef(id: nil, name: $0, avatar: nil) }
    }
    title = c.opt(.title) ?? ""
    durationMin = c.opt(.durationMin)
    startedAt = c.opt(.startedAt)
    exerciseCount = c.opt(.exerciseCount) ?? 0
    setCount = c.opt(.setCount) ?? 0
    volumeKg = c.opt(.volumeKg) ?? 0
    prCount = c.opt(.prCount) ?? 0
    exercises = c.opt(.exercises) ?? []
    activeKcal = c.opt(.activeKcal)
    avgHr = c.opt(.avgHr)
    maxHr = c.opt(.maxHr)
    activities = c.opt(.activities) ?? []
    noExercises = c.opt(.noExercises) ?? false
    likes = c.opt(.likes) ?? Likes()
    commentCount = c.opt(.commentCount) ?? 0
    previewComments = c.opt(.previewComments) ?? []
  }

  func encode(to e: Encoder) throws {
    var c = e.container(keyedBy: CodingKeys.self)
    try c.encode(id, forKey: .id)
    try c.encode(date, forKey: .date)
    try c.encode(user, forKey: .user)
    try c.encode(circleRefs, forKey: .circleRefs)
    try c.encode(title, forKey: .title)
    try c.encodeIfPresent(durationMin, forKey: .durationMin)
    try c.encodeIfPresent(startedAt, forKey: .startedAt)
    try c.encode(exerciseCount, forKey: .exerciseCount)
    try c.encode(setCount, forKey: .setCount)
    try c.encode(volumeKg, forKey: .volumeKg)
    try c.encode(prCount, forKey: .prCount)
    try c.encode(exercises, forKey: .exercises)
    try c.encodeIfPresent(activeKcal, forKey: .activeKcal)
    try c.encodeIfPresent(avgHr, forKey: .avgHr)
    try c.encodeIfPresent(maxHr, forKey: .maxHr)
    try c.encode(activities, forKey: .activities)
    try c.encode(noExercises, forKey: .noExercises)
    try c.encode(likes, forKey: .likes)
    try c.encode(commentCount, forKey: .commentCount)
    try c.encode(previewComments, forKey: .previewComments)
  }
}

struct Comment: Codable, Equatable, Identifiable {
  /// The server's id; a comment still in the offline queue has a negative one (minus its outbox id).
  let id: Int
  var user: PostUser
  var body: String
  var createdAt: String?
  /// Not on the server yet: waiting in the offline queue (`pending`) or refused there (`failed`).
  var local: LocalState?

  enum LocalState: String, Codable { case pending, failed }

  enum CodingKeys: String, CodingKey { case id, user, body, local, createdAt = "created_at" }

  init(id: Int, user: PostUser, body: String, createdAt: String? = nil, local: LocalState? = nil) {
    self.id = id
    self.user = user
    self.body = body
    self.createdAt = createdAt
    self.local = local
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(Int.self, forKey: .id)
    user = try c.decode(PostUser.self, forKey: .user)
    body = c.opt(.body) ?? ""
    createdAt = c.opt(.createdAt)
    local = c.opt(.local)
  }
}

/// The feed as kept in `docs` (`circles:feed:<all|circle id>`): the posts seen so far, newest first, and where the next
/// page starts.
struct FeedCache: Codable, Equatable {
  var posts: [Post] = []
  var next: String?
  var previewExercises = 3
  var commentPreview = 2
  var commentMax = 300
  var hasCircles = true
}
