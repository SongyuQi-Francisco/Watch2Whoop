import XCTest
@testable import Watch2Whoop

/// Section 8 of docs/ios-native-app.md: what the Flutter app stored is found and copied once.
final class FlutterMigrationTests: XCTestCase {
  private var defaults: UserDefaults!
  private var keychain: MemoryKeychain!
  private var store: CredentialStore!
  private let suite = "w2w.tests.migration"

  override func setUp() {
    defaults = UserDefaults(suiteName: suite)
    defaults.removePersistentDomain(forName: suite)
    keychain = MemoryKeychain()
    store = CredentialStore(keychain: keychain, defaults: defaults)
  }

  override func tearDown() {
    defaults.removePersistentDomain(forName: suite)
  }

  private func flutterInstall(token: String? = "example-account-token", ingest: String? = nil,
                              todayUrl: String? = "https://body.example.com/app/") {
    if let token { keychain.write(Data(token.utf8), service: "flutter_secure_storage_service", account: "w2w.account.token.v1") }
    if let ingest { keychain.write(Data(ingest.utf8), service: "flutter_secure_storage_service", account: "w2w.ingest.token.v1") }
    if let todayUrl {
      defaults.set(#"{"todayUrl":"\#(todayUrl)","owHost":"https://sync.example.com","onboardingDone":true}"#,
                   forKey: "flutter.w2w.config.v1")
    }
    defaults.set("en", forKey: "flutter.w2w.lang.v1")
  }

  func testCopiesTokenServerAndLanguage() {
    flutterInstall(ingest: "example-ingest-token")
    let out = FlutterMigration.run(store: store, keychain: keychain, defaults: defaults)
    XCTAssertEqual(out, .migrated(server: "https://body.example.com"))
    XCTAssertEqual(store.accountToken, "example-account-token")
    XCTAssertEqual(store.ingestToken, "example-ingest-token")
    XCTAssertEqual(store.config, AccountConfig(server: "https://body.example.com", owHost: "https://sync.example.com"))
    XCTAssertEqual(defaults.string(forKey: Lang.overrideKey), "en")
    // the Flutter items stay (TestFlight can go back to 1.0.8)
    XCTAssertNotNil(keychain.read(service: "flutter_secure_storage_service", account: "w2w.account.token.v1"))
  }

  func testRunsOnlyOnce() {
    flutterInstall()
    XCTAssertEqual(FlutterMigration.run(store: store, keychain: keychain, defaults: defaults), .migrated(server: "https://body.example.com"))
    store.accountToken = nil
    XCTAssertEqual(FlutterMigration.run(store: store, keychain: keychain, defaults: defaults), .alreadyDone)
    XCTAssertNil(store.accountToken)
  }

  func testFreshInstall() {
    XCTAssertEqual(FlutterMigration.run(store: store, keychain: keychain, defaults: defaults), .nothingToMigrate)
    XCTAssertFalse(store.isSignedIn)
  }

  func testTokenWithoutServerIsIncomplete() {
    flutterInstall(todayUrl: nil)
    XCTAssertEqual(FlutterMigration.run(store: store, keychain: keychain, defaults: defaults), .incomplete)
    XCTAssertFalse(store.isSignedIn)
  }

  func testSignOutRemovesFlutterItemsToo() {
    flutterInstall()
    FlutterMigration.run(store: store, keychain: keychain, defaults: defaults)
    store.signOut()
    XCTAssertNil(store.accountToken)
    XCTAssertNil(store.config)
    XCTAssertNil(keychain.read(service: "flutter_secure_storage_service", account: "w2w.account.token.v1"))
  }

  func testServerOrigin() {
    XCTAssertEqual(FlutterMigration.serverOrigin("https://body.example.com/app/"), "https://body.example.com")
    XCTAssertEqual(FlutterMigration.serverOrigin("https://example.com:8443"), "https://example.com:8443")
    XCTAssertEqual(FlutterMigration.serverOrigin("HTTP://127.0.0.1:18100/app/#login=x"), "http://127.0.0.1:18100")
    XCTAssertNil(FlutterMigration.serverOrigin("ftp://example.com"))
    XCTAssertNil(FlutterMigration.serverOrigin(""))
  }
}
