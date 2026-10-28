import CryptoKit
import XCTest
@testable import Watch2Whoop

final class AlgorithmRuntimeTests: XCTestCase {
  func testBuiltinBundleFollowsTheCallingConvention() throws {
    let rt = try AlgorithmRuntime.builtin()
    XCTAssertEqual(rt.version, "0.0.0-n0")
    XCTAssertTrue(rt.functions.contains("echo"))
    struct Input: Codable, Equatable { var day: String; var hrv: [Double?] }
    let input = Input(day: "2026-01-02", hrv: [41.5, nil, 38])
    XCTAssertEqual(try rt.call("echo", input, as: Input.self), input)
  }

  func testErrorsComeBackAsAlgorithmError() throws {
    let rt = try AlgorithmRuntime.builtin()
    XCTAssertThrowsError(try rt.callJSON("nope", "{}")) { e in
      XCTAssertEqual(e as? AlgorithmError, .function(code: "unknown_function", message: "nope"))
    }
    XCTAssertThrowsError(try AlgorithmRuntime(source: "globalThis.OBS = {apiVersion: 2}")) { e in
      XCTAssertEqual(e as? AlgorithmError, .apiMismatch(2))
    }
    XCTAssertThrowsError(try AlgorithmRuntime(source: "this is not javascript"))
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
    "reminders": [["id": 1]],
  ]

  func testRingsFollowThePage() {
    let m = TodayModel(json: example)
    XCTAssertEqual(m.rings.map(\.key), ["sleep", "recovery", "strain"])
    XCTAssertEqual(m.rings.map(\.value), ["81", "67", "12.3"])
    XCTAssertEqual(m.rings[1].tone, .mid)
    XCTAssertEqual(m.rings[2].fraction, 12.34 / 21, accuracy: 1e-9)
    XCTAssertEqual(m.word, "Train as planned")
    XCTAssertEqual(m.chips.count, 2)
    XCTAssertEqual(m.hrv, "52")
    XCTAssertEqual(m.sleep, "7:32")
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
