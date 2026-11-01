import XCTest
@testable import Watch2Whoop

/// App Store package A: legal links, the open-source licences page, the AI / medical disclaimer tags, the privacy
/// manifest and the localized permission texts.
final class AboutAndLegalTests: XCTestCase {
  func testLegalLinksUseTheServerAndLanguage() throws {
    let u = try XCTUnwrap(LegalLinks.url(.privacy, server: "https://body.example.com", lang: "en"))
    XCTAssertEqual(u.absoluteString, "https://body.example.com/legal/privacy?lang=en")
    XCTAssertEqual(LegalLinks.url(.terms, server: "https://body.example.com/", lang: "zh")?.absoluteString,
                   "https://body.example.com/legal/terms?lang=zh")
    if LegalLinks.buildBase == nil {   // the test build has no fixed W2WLegalBaseURL
      XCTAssertNil(LegalLinks.url(.privacy, server: nil))
      XCTAssertNil(LegalLinks.url(.privacy, server: ""))
      XCTAssertNil(LegalLinks.url(.privacy, server: "not a url"))
    }
  }

  func testLicencesListGRDBAndEveryText() {
    let entries = LicenceEntry.load()
    XCTAssertFalse(entries.isEmpty)
    let grdb = entries.first { $0.name == "GRDB.swift" }
    XCTAssertEqual(grdb?.license, "MIT")
    XCTAssertEqual(grdb?.version, "7.11.1")
    XCTAssertTrue(entries.contains { $0.name == "Lucide" && $0.license == "ISC" })
    XCTAssertTrue(entries.allSatisfy { !$0.text.isEmpty && URL(string: $0.url) != nil })
  }

  func testDisclaimerPointsAreShortAndAIFirst() {
    let ai = DisclaimerBody.points(.ai), med = DisclaimerBody.points(.medical)
    XCTAssertEqual(ai.count, med.count + 1)
    XCTAssertEqual(Array(ai.dropFirst()), med)
    XCTAssertTrue(TrendStatus.medicalKeys.contains("illness"))
    XCTAssertLessThanOrEqual(DisclaimerTag(kind: .ai).label.count, 14)
    XCTAssertLessThanOrEqual(DisclaimerTag(kind: .medical).label.count, 16)
  }

  func testPrivacyManifestAndPermissionTexts() throws {
    let url = try XCTUnwrap(Bundle.main.url(forResource: "PrivacyInfo", withExtension: "xcprivacy"))
    let plist = try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: url), format: nil) as? [String: Any])
    XCTAssertEqual(plist["NSPrivacyTracking"] as? Bool, false)
    let apis = (plist["NSPrivacyAccessedAPITypes"] as? [[String: Any]] ?? []).compactMap { $0["NSPrivacyAccessedAPIType"] as? String }
    XCTAssertTrue(apis.contains("NSPrivacyAccessedAPICategoryUserDefaults"))
    let types = (plist["NSPrivacyCollectedDataTypes"] as? [[String: Any]] ?? []).compactMap { $0["NSPrivacyCollectedDataType"] as? String }
    XCTAssertTrue(types.contains("NSPrivacyCollectedDataTypeHealth"))
    XCTAssertTrue(types.contains("NSPrivacyCollectedDataTypeFitness"))
    for lang in ["zh-Hans", "en"] {
      let p = try XCTUnwrap(Bundle.main.path(forResource: "InfoPlist", ofType: "strings", inDirectory: nil, forLocalization: lang))
      let d = try XCTUnwrap(NSDictionary(contentsOfFile: p) as? [String: String])
      for key in ["NSHealthShareUsageDescription", "NSHealthUpdateUsageDescription", "NSCameraUsageDescription",
                  "NSMicrophoneUsageDescription", "NSSpeechRecognitionUsageDescription"] {
        XCTAssertFalse((d[key] ?? "").isEmpty, "\(lang) \(key)")
      }
      XCTAssertTrue(d["NSHealthShareUsageDescription"]?.contains("Jarvis") == true)
    }
  }
}
