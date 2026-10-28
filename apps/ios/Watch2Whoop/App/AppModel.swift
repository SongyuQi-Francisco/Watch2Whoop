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
  @Published var flags: Set<String>
  @Published var displayName: String?

  init(config: AccountConfig, api: APIClient, db: AppDatabase, flags: Set<String>) {
    self.config = config
    self.api = api
    self.db = db
    self.flags = flags
    self.displayName = config.displayName
    self.sync = SyncCoordinator(db: db, api: api)
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

  let store: CredentialStore
  private let flagsKey = "w2w.native.flags.v1"

  init(store: CredentialStore = CredentialStore()) {
    self.store = store
  }

  func start() async {
    FlutterMigration.run(store: store)
    #if DEBUG
    DevSignIn.applyIfRequested(store: store)
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

  func signOut() {
    let api = session?.api
    if let cfg = store.config, let uid = cfg.userId { try? AppDatabase.deleteAccount(server: cfg.server, userId: uid) }
    store.signOut()
    UserDefaults.standard.removeObject(forKey: flagsKey)
    session = nil
    phase = .signedOut(message: nil)
    if let api { Task { _ = try? await api.data(APIRequest(method: .post, path: Endpoint.accountLogout, idempotent: false)) } }
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
    let db = try AppDatabase.open(server: cfg.server, userId: userId)
    let cached = Set(UserDefaults.standard.stringArray(forKey: flagsKey) ?? [])
    session = AccountSession(config: cfg, api: api, db: db, flags: cached)
    phase = .signedIn
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
