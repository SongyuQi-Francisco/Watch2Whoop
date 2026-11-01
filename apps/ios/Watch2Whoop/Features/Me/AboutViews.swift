import SwiftUI

/// The privacy policy and terms of use are public pages of the server (`GET /legal/privacy|terms?lang=`, docs/legal/,
/// body_engine legal/). The base is the build's `W2WLegalBaseURL` (Info.plist, from the git-ignored local xcconfig) when
/// set — the store's fixed address —, else the server the person signs in to.
enum LegalLinks {
  enum Doc: String { case privacy, terms }

  static var buildBase: String? {
    guard let s = Bundle.main.object(forInfoDictionaryKey: "W2WLegalBaseURL") as? String,
          !s.isEmpty, !s.hasPrefix("$(") else { return nil }
    return s.contains("://") ? s : "https://\(s)"
  }

  static func url(_ doc: Doc, server: String?, lang: String = Lang.code) -> URL? {
    guard let base = buildBase ?? server.flatMap({ $0.isEmpty ? nil : $0 }),
          var c = URLComponents(string: base.trimmingCharacters(in: CharacterSet(charactersIn: "/"))),
          c.scheme == "https" || c.scheme == "http", c.host != nil else { return nil }
    c.path = "/legal/\(doc.rawValue)"
    c.queryItems = [URLQueryItem(name: "lang", value: lang)]
    return c.url
  }

  static func title(_ doc: Doc) -> String {
    switch doc {
    case .privacy: return tr("隐私政策", "Privacy Policy")
    case .terms: return tr("使用条款", "Terms of Use")
    }
  }
}

/// 「关于」 in 我: privacy policy, terms, open-source licences, version.
struct AboutRows: View {
  let server: String
  let licences: () -> Void
  @Environment(\.openURL) private var openURL

  var body: some View {
    RowGroup(title: tr("关于", "About")) {
      if let u = LegalLinks.url(.privacy, server: server) {
        ListRow(icon: .lock, title: LegalLinks.title(.privacy)) { openURL(u) }
        RowDivider()
      }
      if let u = LegalLinks.url(.terms, server: server) {
        ListRow(icon: .fileText, title: LegalLinks.title(.terms)) { openURL(u) }
        RowDivider()
      }
      ListRow(icon: .bookOpen, title: tr("开源许可", "Open-source licences"), action: licences)
      RowDivider()
      ListRow(icon: .info, title: tr("版本", "Version"), value: APIClient.appVersion)
    }
  }
}

/// Small links under the sign-in buttons: by signing in you accept the terms; both texts one tap away.
struct LegalFooter: View {
  let server: String?
  @Environment(\.openURL) private var openURL

  var body: some View {
    let privacy = LegalLinks.url(.privacy, server: server), terms = LegalLinks.url(.terms, server: server)
    if privacy != nil || terms != nil {
      VStack(spacing: 6) {
        Text(tr("登录即表示同意", "By signing in you agree to the")).font(Typo.label).foregroundColor(Palette.text3)
        HStack(spacing: 14) {
          if let terms { link(LegalLinks.title(.terms), terms) }
          if let privacy { link(LegalLinks.title(.privacy), privacy) }
        }
      }
      .frame(maxWidth: .infinity)
      .padding(.top, 12)
    }
  }

  private func link(_ title: String, _ url: URL) -> some View {
    Button(title) { openURL(url) }
      .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
      .underline()
  }
}

/// One open-source component the app ships (Resources/licenses.json; THIRD_PARTY_NOTICES.md is the repository's list).
struct LicenceEntry: Decodable, Identifiable, Equatable {
  var id: String { name }
  let name: String
  let version: String
  let license: String
  let url: String
  let text: String

  private struct File: Decodable { let components: [LicenceEntry] }

  static func load(_ bundle: Bundle = .main) -> [LicenceEntry] {
    guard let u = bundle.url(forResource: "licenses", withExtension: "json"), let data = try? Data(contentsOf: u),
          let f = try? JSONDecoder().decode(File.self, from: data) else { return [] }
    return f.components
  }
}

/// 开源许可: each component with its licence; tap for the full text.
struct LicensesView: View {
  @State private var entries = LicenceEntry.load()
  @State private var open: LicenceEntry?

  var body: some View {
    RowGroup {
      ForEach(Array(entries.enumerated()), id: \.element.id) { i, e in
        if i > 0 { RowDivider(inset: 0) }
        ListRow(title: e.name, subtitle: e.version.isEmpty ? e.license : "\(e.version) · \(e.license)") { open = e }
      }
    }
    .sheet(item: $open) { e in
      SheetContainer(title: e.name, close: { open = nil }) {
        VStack(alignment: .leading, spacing: 10) {
          Text(e.license).font(Typo.section).foregroundColor(Palette.text2)
          if let u = URL(string: e.url) { Link(e.url, destination: u).font(Typo.caption).foregroundColor(Palette.text2) }
          Text(e.text).font(.system(size: 13)).foregroundColor(Palette.text).textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
      .presentationDetents([.large])
      .presentationDragIndicator(.visible)
    }
  }
}
