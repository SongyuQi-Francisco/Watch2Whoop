import Foundation

/// Everything that belongs to the signed-in account: its server, API client, local database and sync coordinator.
/// Built once per sign-in; signing out drops it (and its database directory, section 4.1).
@MainActor
final class AccountSession: ObservableObject {
  let config: AccountConfig
  let api: APIClient
  let db: AppDatabase
  let sync: SyncCoordinator
  /// Feature flags on for this user (`/v1/web/me` → `flags`), e.g. `friends` decides the tab bar.
  @Published var flags: Set<String> {
    didSet {
      let on = flags.contains(Self.nativeSyncFlag)
      if on, !sync.deltaAvailable {   // just learned the server has it (first start): pull and score now
        sync.deltaAvailable = true
        Task { await sync.refresh(.changes) }
      }
      sync.deltaAvailable = on
    }
  }
  @Published var displayName: String?
  /// N2's sync (`POST /v1/sync/pull`, `PUT /scores/app`) is on for this user: the phone scores itself (#340 N2b).
  static let nativeSyncFlag = "native_sync"

  init(config: AccountConfig, api: APIClient, db: AppDatabase, flags: Set<String>, runtime: AccountRuntime? = nil) {
    self.config = config
    self.api = api
    self.db = db
    self.flags = flags
    self.displayName = config.displayName
    // the long jobs (scoring, history download) are the runtime's, shared with the background task (local history)
    self.sync = SyncCoordinator(db: db, api: api, scoring: runtime?.scoring, history: runtime?.history)
    sync.deltaAvailable = flags.contains(Self.nativeSyncFlag)
  }

  /// Circles replace 饮食 in the tab bar while the `friends` flag is on (same rule as the Today page's applyTabs()).
  var circlesTab: Bool { flags.contains("friends") }
}

/// App-wide state: starting → signed out / signed in.
@MainActor
final class AppModel: ObservableObject {
  enum Phase: Equatable {
    case starting
    case signedOut(message: String?)
    /// Signed in but the server has not told us the user id yet (first launch after the upgrade, offline).
    case connecting(message: String?)
    case signedIn
  }

  @Published private(set) var phase: Phase = .starting
  @Published private(set) var session: AccountSession?
  /// Bumped when the UI language changes: the root view is rebuilt so every `tr()` is read again.
  @Published private(set) var langRevision = 0
  /// A setup link (camera QR scan, `watch2whoop://setup?…`) waiting for the sign-in screen, or for a confirmation while
  /// someone is signed in.
  @Published var pendingSetup: SetupInfo?
  /// Where a `watch2whoop://` link asked to go (sync center, history backfill).
  @Published var route: Route?

  enum Route: Equatable {
    case syncCenter
    /// The Today page's 「补全历史」: 90 days, or 0 = all history.
    case history(days: Int)
    /// The Friends tab (N7); `post` = a feed post to open with its comments (a like / comment notification).
    case circles(post: Int?)
    /// The join sheet with an invite code (`watch2whoop://circle?code=…`, a shared invite link).
    case circleJoin(String)
  }

  let store: CredentialStore
  private let flagsKey = "w2w.native.flags.v1"

  init(store: CredentialStore = CredentialStore()) {
    self.store = store
  }

  func start() async {
    NotificationTaps.shared.open = { [weak self] link in self?.handle(link: link) }   // a tapped notification
    FlutterMigration.run(store: store)
    #if DEBUG
    DevSignIn.applyIfRequested(store: store)
    // simulator runs: `SIMCTL_CHILD_W2W_DEV_SETUP=watch2whoop://setup?…` goes through the real setup-link sign-in
    if let link = ProcessInfo.processInfo.environment["W2W_DEV_SETUP"], let u = URL(string: link) { handle(url: u) }
    // `SIMCTL_CHILD_W2W_DEV_LINK=/app/?tab=friends&feed=12` behaves like a tapped notification (N7 screenshots)
    if let link = ProcessInfo.processInfo.environment["W2W_DEV_LINK"], !link.isEmpty { handle(link: link) }
    #endif
    #if DEBUG && targetEnvironment(simulator)
    DebugHealthSeeder.runIfRequested()   // `SIMCTL_CHILD_W2W_DEV_SEED_HEALTH=21`: synthetic Health data (N9 regression)
    #endif
    await connect()
  }

  /// Opens the account: straight away when the user id is known (cached data first), else after `/v1/web/me`.
  func connect() async {
    guard let cfg = store.config, store.accountToken != nil, let base = URL(string: cfg.server) else {
      phase = .signedOut(message: nil)
      return
    }
    let api = makeClient(base)
    if let uid = cfg.userId {
      do {
        try open(cfg, userId: uid, api: api)
      } catch {
        phase = .connecting(message: String(describing: error))
        return
      }
      Task { await refreshMe(api) }
      return
    }
    phase = .connecting(message: nil)
    await refreshMe(api)
  }

  /// Signs in with a server address and an account token (the token form; N8 adds setup link / e-mail / Apple).
  func signIn(server: String, token: String) async -> String? {
    guard let origin = FlutterMigration.serverOrigin(server.contains("://") ? server : "https://\(server)") else {
      return tr("服务器地址不对", "That server address is not valid")
    }
    store.accountToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
    store.config = AccountConfig(server: origin)
    await connect()
    if case .signedOut(let m) = phase { return m ?? tr("登录失败", "Sign-in failed") }
    return nil
  }

  /// Signs out on this phone: this account's local database, its Keychain items (native and Flutter's), the sync state
  /// (engine anchors, backfill, RawSync), this phone's push registration; [revoke] also revokes the token on the server
  /// (not needed after the account was deleted). Health data on the server is not touched.
  func signOut(revoke: Bool = true) async {
    let api = session?.api
    if let api, revoke {
      _ = try? await api.data(APIRequest(method: .delete, path: Endpoint.pushDevices, timeout: 10))
      _ = try? await api.data(APIRequest(method: .post, path: Endpoint.accountLogout, idempotent: false, timeout: 10))
    }
    await HealthSyncService.shared.detach()
    AccountRuntime.close()
    if let cfg = store.config, let uid = cfg.userId { try? AppDatabase.deleteAccount(server: cfg.server, userId: uid) }
    let server = store.config?.server
    store.signOut()
    if let server { UserDefaults.standard.set(server, forKey: Self.lastServerKey) }
    UserDefaults.standard.removeObject(forKey: flagsKey)
    Lang.override = nil   // the next person starts in the system language
    langRevision += 1
    session = nil
    phase = .signedOut(message: nil)
  }

  /// The server of the last sign-out: the sign-in screen offers it again.
  static let lastServerKey = "w2w.native.lastServer.v1"

  /// DELETE /v1/account (everything on the server), then the local sign-out. nil = done, else the server's reason.
  func deleteAccount() async -> String? {
    guard let api = session?.api else { return tr("还没有登录", "Not signed in") }
    do {
      _ = try await api.data(try .json(.delete, Endpoint.account, ["confirm": "DELETE"]))
    } catch let e as APIError {
      return e.userMessage
    } catch {
      return String(describing: error)
    }
    await signOut(revoke: false)
    return nil
  }

  /// "zh" / "en", or nil = follow the system.
  func setLanguage(_ code: String?) {
    Lang.override = code
    langRevision += 1
  }

  /// `watch2whoop://setup?…` (sign in), `watch2whoop://sync` (the sync center), `watch2whoop://backfill?days=90|all`.
  func handle(url: URL) {
    guard url.scheme?.lowercased() == SetupInfo.scheme else { return }
    switch url.host?.lowercased() {
    case "setup":
      let info = SetupInfo.parse(url.absoluteString)
      if !info.isEmpty { pendingSetup = info }
    case "sync":
      route = .syncCenter
    case "backfill":
      let days = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "days" }?.value
      route = .history(days: days == "all" ? 0 : Int(days ?? "") ?? SyncSettings.backfillDays)
    case "circle", "feed":                                                                    // N7
      handle(link: url.absoluteString)
    default:
      break
    }
  }

  /// A notification's `url` (`/app/?tab=friends&feed=12`) or a circle link: where it points (N7: circles only so far).
  func handle(link: String) {
    switch CircleLink.parse(link) {
    case .post(let id)?: route = .circles(post: id)
    case .join(let code)?: route = .circleJoin(code)
    case nil: if link.contains("tab=friends") { route = .circles(post: nil) }
    }
  }

  // MARK: - private

  private func makeClient(_ base: URL) -> APIClient {
    APIClient(baseURL: base, token: { [store] in store.accountToken }, onUnauthorized: { [weak self] in
      Task { @MainActor in self?.unauthorized() }
    })
  }

  private func unauthorized() {
    guard phase == .signedIn || phase == .starting || phaseIsConnecting else { return }
    store.accountToken = nil
    session = nil
    phase = .signedOut(message: tr("登录已失效，请重新登录", "You were signed out, please sign in again"))
  }

  private var phaseIsConnecting: Bool {
    if case .connecting = phase { return true }
    return false
  }

  private func open(_ cfg: AccountConfig, userId: Int, api: APIClient) throws {
    if session?.config.userId == userId, session?.config.server == cfg.server { phase = .signedIn; return }
    let runtime = try AccountRuntime.open(server: cfg.server, userId: userId, api: api)
    let db = runtime.db
    let cached = Set(UserDefaults.standard.stringArray(forKey: flagsKey) ?? [])
    let s = AccountSession(config: cfg, api: api, db: db, flags: cached, runtime: runtime)
    session = s
    phase = .signedIn
    // HealthKit sync for this account: the engine's anchors carry on (same keys as 1.0.8), rounds on open and in the
    // background; after a round with uploads the account's data is pulled again.
    let sync = HealthSyncService.shared
    sync.onRoundDone = { [weak s] in await s?.sync.refresh(.changes) }
    // #340 N2b: the scoring types go into the local tables as HealthKit has them, the beat-to-beat readings as they upload
    let reader = HealthKitScoringReader(db: db)
    Task {
      await s.sync.scoring.setHealthReader { store in
        let since = await MainActor.run { HealthSyncService.shared.uploadedSince }
        return await reader.read(into: store, uploadedSince: since)
      }
      await s.sync.scoring.setUploadGate { await MainActor.run { HealthSyncService.shared.uploadedEverything } }
    }
    sync.onHeartbeats = { [weak s] items, deleted in
      guard let s else { return }
      _ = try? LocalRawStore(db: s.db, zone: ScoringSync.zone(s.db)).ingestBeats(items, deleted: deleted)
    }
    if let token = store.accountToken {
      Task {
        await sync.attach(api: api, server: cfg.server, token: token,
                          accountKey: AppDatabase.accountKey(server: cfg.server, userId: userId))
        await sync.syncOnOpen()
      }
    }
    Task { await PushRegistration.shared.renew(api: api) }   // the APNs token can change between starts
  }

  /// `/v1/web/me`: user id (names the database), display name, flags.
  private func refreshMe(_ api: APIClient) async {
    do {
      let me = try await api.json(.get(Endpoint.webMe))
      let user = me["me"] as? [String: Any]
      guard let uid = (user?["id"] as? NSNumber)?.intValue, var cfg = store.config else {
        phase = .connecting(message: tr("服务器的回答看不懂", "Unexpected answer from the server"))
        return
      }
      cfg.userId = uid
      cfg.displayName = user?["display_name"] as? String
      store.config = cfg
      let flags = (me["flags"] as? [String]) ?? []
      UserDefaults.standard.set(flags, forKey: flagsKey)
      try open(cfg, userId: uid, api: api)
      session?.flags = Set(flags)
      session?.displayName = cfg.displayName
    } catch APIError.unauthorized {
      // handled by onUnauthorized
    } catch let e as APIError {
      if session == nil { phase = .connecting(message: e.userMessage) }
    } catch {
      if session == nil { phase = .connecting(message: String(describing: error)) }
    }
  }
}

#if DEBUG
/// Debug builds only: sign in from the launch environment (simulator runs, screenshots):
/// `SIMCTL_CHILD_W2W_DEV_SERVER=http://127.0.0.1:18100 SIMCTL_CHILD_W2W_DEV_TOKEN=… xcrun simctl launch …`;
/// `SIMCTL_CHILD_W2W_DEV_LANG=zh|en` sets the UI language.
enum DevSignIn {
  static func applyIfRequested(store: CredentialStore) {
    let env = ProcessInfo.processInfo.environment
    if let lang = env["W2W_DEV_LANG"], lang == "zh" || lang == "en" { Lang.override = lang }
    guard let server = env["W2W_DEV_SERVER"], let token = env["W2W_DEV_TOKEN"], !token.isEmpty,
          let origin = FlutterMigration.serverOrigin(server) else { return }
    if store.config?.server != origin || store.accountToken != token {
      store.accountToken = token
      store.config = AccountConfig(server: origin)
    }
  }
}
#endif
