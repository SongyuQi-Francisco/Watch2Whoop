import SwiftUI

/// 每日一句 (flag `slim_mode`, #126): one post of a real person, word for word, with a link to the post. The words come
/// from the server (`/v1/users/me/appearance/quotes`, the same data as the Today page's slim-quotes.js), so changing a
/// quote needs no app release; the last list is kept for the first frame and for offline.
struct DailyQuote: Codable, Equatable, Identifiable {
  let id: String
  let person: String
  let text: String
  let lang: String
  let sourceURL: String

  enum CodingKeys: String, CodingKey { case id, person, text, lang, sourceURL = "source_url" }

  /// The post, only as a web link.
  var url: URL? {
    guard let u = URL(string: sourceURL), let s = u.scheme?.lowercased(), s == "https" || s == "http" else { return nil }
    return u
  }

  /// slim.js `dayNumber`: whole days since 1970-01-01 of an ISO date (the user's own date: changes at their midnight).
  static func dayNumber(_ iso: String) -> Int {
    let p = iso.prefix(10).split(separator: "-").compactMap { Int($0) }
    guard p.count == 3, iso.count >= 10 else { return 0 }
    var c = Calendar(identifier: .gregorian)
    c.timeZone = TimeZone(secondsFromGMT: 0)!
    guard let d = c.date(from: DateComponents(year: p[0], month: p[1], day: p[2])) else { return 0 }
    return Int(floor(d.timeIntervalSince1970 / 86400))
  }

  /// slim.js `at`: the quote of a day out of the server's ordered list, `taps` further on.
  static func at(_ list: [DailyQuote], day: String, taps: Int = 0) -> DailyQuote? {
    guard !list.isEmpty else { return nil }
    let n = (dayNumber(day) + taps) % list.count
    return list[n < 0 ? n + list.count : n]
  }
}

struct DailyQuoteList: Decodable, Equatable {
  var items: [DailyQuote]
}

/// The list and today's taps, both on this device (like the page's `be.slimq`).
@MainActor
final class DailyQuoteStore: ObservableObject {
  private static let listKey = "w2w.slim.quotes.v1"
  private static let tapsKey = "w2w.slim.taps.v1"
  private struct Taps: Codable { var day: String; var n: Int }

  @Published private(set) var items: [DailyQuote] = []
  @Published private var taps: Taps?
  private let defaults: UserDefaults

  init(defaults: UserDefaults = .standard) {
    self.defaults = defaults
    items = defaults.data(forKey: Self.listKey).flatMap { try? JSONDecoder().decode([DailyQuote].self, from: $0) } ?? []
    taps = defaults.data(forKey: Self.tapsKey).flatMap { try? JSONDecoder().decode(Taps.self, from: $0) }
  }

  func quote(on day: String) -> DailyQuote? {
    DailyQuote.at(items, day: day, taps: taps?.day == day ? taps?.n ?? 0 : 0)
  }

  func next(on day: String) {
    let t = Taps(day: day, n: (taps?.day == day ? taps?.n ?? 0 : 0) + 1)
    taps = t
    defaults.set(try? JSONEncoder().encode(t), forKey: Self.tapsKey)
  }

  /// Keeps what it has when the server cannot be reached.
  func refresh(_ api: APIClient) async {
    guard let list = try? await api.send(.get(Endpoint.appearanceQuotes), as: DailyQuoteList.self) else { return }
    set(list.items)
  }

  func set(_ list: [DailyQuote]) {
    guard list != items else { return }
    items = list
    defaults.set(try? JSONEncoder().encode(list), forKey: Self.listKey)
  }

  /// Sign-out: the next person starts clean.
  static func forget(defaults: UserDefaults = .standard) {
    defaults.removeObject(forKey: listKey)
    defaults.removeObject(forKey: tapsKey)
  }
}

/// The card at the top of the Today page (slim.js `.slimq`): a tap on the words shows the next one, the link icon opens
/// the post, ✕ turns the quote off.
struct DailyQuoteCard: View {
  let quote: DailyQuote
  let next: () -> Void
  let open: (URL) -> Void
  let close: () -> Void
  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  @State private var faded = false

  var body: some View {
    HStack(alignment: .top, spacing: 10) {
      Icon(.quote, size: 15).foregroundColor(Palette.accent).padding(.top, 3)
      VStack(alignment: .leading, spacing: 6) {
        Button(action: tapped) {
          Text(quote.text)
            .font(.system(size: 15, weight: .semibold)).lineSpacing(5)
            .foregroundColor(Palette.text)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .environment(\.locale, Locale(identifier: quote.lang == "en" ? "en" : "zh-Hans"))
            .opacity(faded ? 0 : 1)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint(tr("换一句", "Next quote"))
        HStack(spacing: 6) {
          Text(quote.person).font(.system(size: 12)).foregroundColor(Palette.text2)
          if let url = quote.url {
            Button { open(url) } label: {
              Icon(.externalLink, size: 15).foregroundColor(Palette.text2)
                .padding(8).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(-8)
            .accessibilityLabel(tr("原帖", "Original post"))
          }
        }
      }
      Button(action: close) {
        Icon(.x, size: 16).foregroundColor(Palette.text3).frame(width: 32, height: 32).contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .accessibilityLabel(tr("关闭语录", "Turn off quotes"))
    }
    .padding(.vertical, 12).padding(.leading, 14).padding(.trailing, 8)
    .background(Palette.surface)
    .overlay(alignment: .leading) { Palette.accent.frame(width: 3) }
    .clipShape(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous))
    .padding(.bottom, 12)
    #if DEBUG
    .onAppear(perform: devDrive)
    #endif
  }

  #if DEBUG
  /// `SIMCTL_CHILD_W2W_DEV_QUOTE=next|open|close` (Debug, simulator screenshots): does that 3 seconds after the card appears.
  private func devDrive() {
    guard let what = RecordDevShot.value("W2W_DEV_QUOTE") else { return }
    DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
      switch what {
      case "next": tapped()
      case "open": if let u = quote.url { open(u) }
      case "close": close()
      default: break
      }
    }
  }
  #endif

  private func tapped() {
    guard !reduceMotion else { return next() }
    withAnimation(.easeInOut(duration: 0.18)) { faded = true }
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.18) {
      next()
      withAnimation(.easeInOut(duration: 0.18)) { faded = false }
    }
  }
}

/// A post opened from the card, in Safari's own view inside the app.
struct QuoteLink: Identifiable {
  let url: URL
  var id: String { url.absoluteString }
}
