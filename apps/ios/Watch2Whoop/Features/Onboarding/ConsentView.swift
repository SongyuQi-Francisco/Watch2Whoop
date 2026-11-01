import SwiftUI

/// The consent (profile/consent.py): the summary, the full text on demand, one tick and one button. Text and version come
/// from the server (`GET /v1/consent`, in the app's language); `POST /v1/users/me/consent` records the version that was read.
/// Required = no way past it (health data is only stored, not processed, until then); reconfirm = a revised version.
struct ConsentView: View {
  @EnvironmentObject private var session: AccountSession
  @EnvironmentObject private var app: AppModel
  let reconfirm: Bool
  let done: () async -> Void

  @State private var text: ConsentText?
  @State private var showFull = false
  @State private var agreed = false
  @State private var busy = false
  @State private var message: String?
  @State private var declined = false

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 14) {
        HStack { Spacer(); LanguageChip() }.padding(.top, 6)
        Text(reconfirm ? tr("同意书更新了", "The consent was updated") : tr("使用前请确认", "Before you start"))
          .font(.system(size: 28, weight: .bold)).titleTracking().foregroundColor(Palette.text).padding(.top, 12)
        Text(reconfirm ? tr("《健康数据使用同意书》有了新版本，请再看一遍。", "There is a new version of the health data consent; please read it again.")
             : tr("Watch2Whoop 会处理你的健康数据。看完要点（或全文），同意后才开始处理。", "Watch2Whoop processes your health data. Read the points (or the full text); processing starts only after you agree."))
          .font(Typo.body).foregroundColor(Palette.text2)
        if let t = text {
          Card {
            VStack(alignment: .leading, spacing: 10) {
              ForEach(Array(t.summary.enumerated()), id: \.offset) { _, line in
                HStack(alignment: .top, spacing: 8) {
                  Circle().fill(Palette.text3).frame(width: 5, height: 5).padding(.top, 7)
                  Text(line).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
                }
              }
            }
          }
          Button(showFull ? tr("收起全文", "Hide the full text") : tr("阅读完整同意书（版本 \(t.version)）", "Read the full consent (version \(t.version))")) {
            showFull.toggle()
          }
          .font(Typo.caption).foregroundColor(Palette.text2)
          if showFull {
            VStack(alignment: .leading, spacing: 8) {
              ForEach(Array(Self.paragraphs(t.text).enumerated()), id: \.offset) { _, p in
                Text(p).font(Typo.caption).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
              }
            }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
          }
          Button { agreed.toggle() } label: {
            HStack(spacing: 10) {
              Group {
                if agreed { Icon(.circleCheck, size: 20).foregroundColor(Palette.good) }
                else { Circle().stroke(Palette.text3, lineWidth: 1.75).frame(width: 18, height: 18).padding(1) }
              }
              Text(tr("我已阅读并同意《健康数据使用同意书》", "I have read and agree to the health data consent"))
                .font(Typo.body).foregroundColor(Palette.text).multilineTextAlignment(.leading)
            }
          }
          .buttonStyle(PressScale())
          .accessibilityAddTraits(agreed ? .isSelected : [])
          PrimaryButton(title: tr("同意并继续", "Agree and continue"), busy: busy) { Task { await accept(t) } }
            .disabled(!agreed)
            .opacity(agreed ? 1 : 0.5)
          if !reconfirm {
            Button(tr("不同意", "I don't agree")) { declined = true }
              .font(Typo.caption).foregroundColor(Palette.text2).frame(maxWidth: .infinity)
          }
        } else if message == nil {
          ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.top, 40)
        }
        if declined {
          Banner(text: tr("不同意就不能使用 Watch2Whoop。你的健康数据目前只暂存、没有处理；可以退出登录，或请管理员删除账号和暂存的数据。", "Without your consent Watch2Whoop can't be used. Your health data is only stored, not processed; you can sign out, or ask the admin to delete your account and the stored data."),
                 tone: .mid, actionTitle: tr("退出登录", "Sign out")) { Task { await app.signOut() } }
        }
        if let message { Banner(text: message, tone: .bad, actionTitle: text == nil ? tr("重试", "Retry") : nil) { Task { await load() } } }
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .interactiveDismissDisabled()
    .task { await load() }
  }

  private func load() async {
    message = nil
    do {
      text = try await session.api.send(.get(Endpoint.consent), as: ConsentText.self)
    } catch let e as APIError {
      message = e.userMessage
    } catch {
      message = String(describing: error)
    }
  }

  private func accept(_ t: ConsentText) async {
    busy = true
    defer { busy = false }
    var body: [String: AnyEncodable] = ["version": AnyEncodable(t.version), "agree": AnyEncodable(true)]
    if let l = t.language { body["language"] = AnyEncodable(l) }
    do {
      _ = try await session.api.data(try .json(.post, Endpoint.myConsent, body, idempotent: false))
      await done()
    } catch let e as APIError {
      message = e.userMessage
    } catch {
      message = String(describing: error)
    }
  }

  /// The consent's Markdown as plain paragraphs (headings and list markers dropped, **bold** kept as AttributedString).
  static func paragraphs(_ md: String) -> [AttributedString] {
    md.components(separatedBy: "\n").compactMap { raw in
      var line = raw.trimmingCharacters(in: .whitespaces)
      if line.isEmpty || line.allSatisfy({ $0 == "-" }) { return nil }
      while line.hasPrefix("#") { line.removeFirst() }
      if line.hasPrefix("- ") || line.hasPrefix("* ") { line = "• " + line.dropFirst(2) }
      line = line.trimmingCharacters(in: .whitespaces)
      return (try? AttributedString(markdown: line)) ?? AttributedString(line)
    }
  }
}

/// A JSON value of any Encodable type (mixed request bodies).
struct AnyEncodable: Encodable {
  private let encode: (Encoder) throws -> Void
  init<T: Encodable>(_ v: T) { encode = v.encode }
  func encode(to encoder: Encoder) throws { try encode(encoder) }
}
