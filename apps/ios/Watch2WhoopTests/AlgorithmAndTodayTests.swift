import CryptoKit
import XCTest
@testable import Watch2Whoop

final class AlgorithmRuntimeTests: XCTestCase {
  /// The one calling convention (N2b): the bundle's own entry points through OpenBodyScoreRuntime.
  func testEntryPointsAndErrors() throws {
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertThrowsError(try rt.invoke("nope", [])) { e in
      XCTAssertEqual(e as? AlgorithmError, .function(code: "exception", message: "no function nope"))
    }
    XCTAssertThrowsError(try OpenBodyScoreRuntime(source: "globalThis.OBS = {apiVersion: 1}")) { e in
      XCTAssertEqual(e as? AlgorithmError, .load("no OpenBodyScore object"))
    }
    XCTAssertThrowsError(try OpenBodyScoreRuntime(source: "this is not javascript"))
    XCTAssertEqual(AlgoBundles.version(of: "/*! openbodyscore-js 0.12.3 | AGPL */"), "0.12.3")
    XCTAssertTrue(OpenBodyScoreRuntime.isNewer("0.10.0", than: "0.9.3"))
    XCTAssertFalse(OpenBodyScoreRuntime.isNewer("0.2.0", than: "0.2.0"))
  }

  func testVerifierChecksHashAndSignature() throws {
    let key = Curve25519.Signing.PrivateKey()
    let bundle = Data("globalThis.OBS = {apiVersion: 1};".utf8)
    let sha = SHA256.hash(data: bundle).map { String(format: "%02x", $0) }.joined()
    var m = AlgorithmManifest(version: "1.0.0", api: 1, sha256: sha,
                              signature: try key.signature(for: bundle).base64EncodedString(), minApp: "1.0.9")
    XCTAssertNoThrow(try BundleVerifier.verify(bundle, manifest: m, publicKey: key.publicKey))
    XCTAssertThrowsError(try BundleVerifier.verify(Data("tampered".utf8), manifest: m, publicKey: key.publicKey))
    XCTAssertThrowsError(try BundleVerifier.verify(bundle, manifest: m, publicKey: Curve25519.Signing.PrivateKey().publicKey))
    m.api = 2
    XCTAssertThrowsError(try BundleVerifier.verify(bundle, manifest: m, publicKey: key.publicKey))
  }

  func testRotationAndUnsignedBundles() throws {
    let current = Curve25519.Signing.PrivateKey(), next = Curve25519.Signing.PrivateKey()
    let bundle = Data("globalThis.OBS = {apiVersion: 1};".utf8)
    let sha = SHA256.hash(data: bundle).map { String(format: "%02x", $0) }.joined()
    let keys = [current.publicKey, next.publicKey]
    // signed with the next key: accepted while the app carries both
    let signed = AlgorithmManifest(version: "1.0.1", api: 1, sha256: sha,
                                   signature: try next.signature(for: bundle).base64EncodedString(), minApp: nil)
    XCTAssertNoThrow(try BundleVerifier.verify(bundle, manifest: signed, publicKeys: keys, requireSignature: true))
    XCTAssertThrowsError(try BundleVerifier.verify(bundle, manifest: signed, publicKeys: [current.publicKey],
                                                   requireSignature: true))
    // unsigned: a Debug build takes it, a Release build does not
    let unsigned = AlgorithmManifest(version: "1.0.1", api: 1, sha256: sha, signature: nil, minApp: nil)
    XCTAssertNoThrow(try BundleVerifier.verify(bundle, manifest: unsigned, publicKeys: keys, requireSignature: false))
    XCTAssertThrowsError(try BundleVerifier.verify(bundle, manifest: unsigned, publicKeys: keys, requireSignature: true)) { e in
      XCTAssertEqual(e as? AlgorithmError, .verification("unsigned"))
    }
    let decoded = try JSONDecoder().decode(AlgorithmManifest.self, from: Data(
      #"{"version": "1.0.1", "api": 1, "sha256": "\#(sha)", "signature": null, "key_id": null}"#.utf8))
    XCTAssertNil(decoded.signature)
  }
}

final class TodayModelTests: XCTestCase {
  /// Fictional example values (no real person's data).
  private let example: [String: Any] = [
    "date": "2026-01-02",
    "scores": [
      "today": ["recovery": 67, "sleep_score": 81, "hrv_night_ms": 52.4, "rhr_night": 55, "sleep_actual_min": 452],
      "yesterday": ["strain": 12.34],
      "recovery_zone": "yellow",
      "recovery_word": "Train as planned",
      "load_status": ["key": "balanced", "label": "Balanced"],
    ] as [String: Any],
    "reminders": [["id": 1, "title": "Example reminder", "level": "info", "status": "new"]],
  ]

  func testRingsFollowThePage() {
    let m = TodayModel(json: example)
    XCTAssertEqual(m.rings.map(\.key), ["sleep", "recovery", "strain"])
    XCTAssertEqual(m.rings.map(\.value), ["81", "67", "12.3"])
    XCTAssertEqual(m.rings[1].tone, .mid)
    XCTAssertEqual(m.rings[2].fraction, 12.34 / 21, accuracy: 1e-9)
    XCTAssertEqual(m.word, "Train as planned")
    XCTAssertEqual(m.loadChip, "Balanced")
    XCTAssertEqual(m.reminders.count, 1)
    XCTAssertEqual(m.sections[0].metrics[0].value, "52")
    XCTAssertEqual(m.sections[1].metrics[0].value, "7:32")
  }

  func testEmptyDayShowsDashesAndLastRecovery() {
    let m = TodayModel(json: ["scores": ["today": NSNull(), "last_recovery": ["recovery": 70, "date": "2026-01-01"],
                                         "recovery_empty": ["label": "Waiting for sleep"]] as [String: Any]])
    XCTAssertEqual(m.rings.map(\.value), [nil, nil, nil])
    XCTAssertEqual(m.word, "Waiting for sleep")
    XCTAssertNotNil(m.sub)
  }

  func testNumberFormat() {
    XCTAssertEqual(TodayModel.fmt(12345.678, digits: 1), "12,345.7")
    XCTAssertEqual(TodayModel.fmt(7.0, digits: 1), "7")
    XCTAssertEqual(TodayModel.hm(59.6), "1:00")
  }
}
