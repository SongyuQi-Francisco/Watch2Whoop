import SwiftUI

/// Pages a card's button opens over the chat, as the page's sheets (app.js goPage): the conversation stays underneath.
enum JarvisDetail: Identifiable, Equatable {
  case lab(key: String?, name: String?)
  case supplements, dataStatus, profile, bodyAge
  case sleep(date: String?)
  case explain(key: String, title: String)

  var id: String {
    switch self {
    case .lab(let k, _): return "lab:\(k ?? "")"
    case .supplements: return "supplements"
    case .dataStatus: return "data_status"
    case .profile: return "profile"
    case .bodyAge: return "body_age"
    case .sleep(let d): return "sleep:\(d ?? "")"
    case .explain(let k, _): return "explain:\(k)"
    }
  }

  var title: String {
    switch self {
    case .lab(_, let name): return name ?? tr("化验", "Lab results")
    case .supplements: return tr("补剂", "Supplements")
    case .dataStatus: return tr("数据状态", "Data status")
    case .profile: return tr("个人资料", "Profile")
    case .bodyAge: return tr("身体年龄", "Body age")
    case .sleep: return tr("睡眠详情", "Sleep detail")
    case .explain(_, let t): return t
    }
  }
}

/// The page itself: the same views as 我 (health record, data status, profile), the sleep page and the explain sheets.
struct JarvisDetailSheet: View {
  let detail: JarvisDetail
  let close: () -> Void
  @State private var explain: JarvisDetail?

  var body: some View {
    Group {
      if case .sleep(let date) = detail {
        // the sleep page scrolls by itself, so it goes without the sheet's scroll view
        VStack(spacing: 0) {
          HStack {
            Text(detail.title).font(.system(size: 17, weight: .semibold)).foregroundColor(Palette.text)
            Spacer()
            Button(action: close) {
              Icon(.x, size: 18).foregroundColor(Palette.text2)
                .frame(width: 32, height: 32)
                .background(Circle().fill(Palette.inSheet))
            }
            .buttonStyle(PressScale())
            .accessibilityLabel(tr("关闭", "Close"))
          }
          .padding(.horizontal, Metrics.gutter).padding(.top, 18).padding(.bottom, 4)
          NavigationStack {
            SleepDetailView(initialDate: date) { key, title in explain = .explain(key: key, title: title) }
              .toolbar(.hidden, for: .navigationBar)
          }
        }
        .background(Palette.bg.ignoresSafeArea())
      } else {
        SheetContainer(title: detail.title, close: close) { content(detail) }
      }
    }
    .presentationDetents([.large])
    .presentationDragIndicator(.visible)
    .sheet(item: $explain) { d in
      SheetContainer(title: d.title, close: { explain = nil }) { content(d) }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
  }

  @ViewBuilder private func content(_ d: JarvisDetail) -> some View {
    switch d {
    case .lab(let key, _):
      if let key { LabDetail(key: key) } else { HealthRecordView(kind: .labs) }
    case .supplements: HealthRecordView(kind: .supplements)
    case .dataStatus: DataStatusView()
    case .profile: ProfileSetupView(first: false) { close() }
    case .bodyAge: BodyAgeView()
    case .explain(let key, _):
      ExplainBody(kind: "metric", key: key, day: nil)
      if TrendStatus.medicalKeys.contains(key) { DisclaimerTag(kind: .medical).padding(.top, 10) }
    case .sleep: EmptyView()
    }
  }
}

/// One test's trend, straight from its card (app.js goPage: `labSheet`) or a Today status card with `lab_key`.
struct LabDetail: View {
  let key: String
  @EnvironmentObject private var session: AccountSession

  var body: some View { LabDetailContent(key: key, session: session) }
}

private struct LabDetailContent: View {
  let key: String
  @StateObject private var store: HealthStore

  init(key: String, session: AccountSession) {
    self.key = key
    _store = StateObject(wrappedValue: HealthStore(session: session))
  }

  var body: some View { LabTrendView(store: store, key: key) }
}

/// What a card's button asks of a page once its tab is up. Published and cleared by the page that takes it, so a tab
/// that is created by the tap still gets it (as `TrendsLink`).
@MainActor
final class JarvisPageLink: ObservableObject {
  static let shared = JarvisPageLink()

  enum FoodPage: Equatable {
    case diet, shopping, recipes
    case recipe(id: String)
  }

  enum Request: Equatable {
    case trends(TrendsView.Mode)
    case training
    case food(FoodPage)
  }

  /// The 记录 segment to show.
  @Published var segment: RecordTab.Segment?
  /// The food page to show.
  @Published var food: FoodPage?

  /// With the `friends` flag food is a segment of 记录; without it, it has its own tab.
  func ask(_ r: Request, circles: Bool) {
    switch r {
    case .trends(let mode): TrendsLink.shared.open(mode: mode)
    case .training: segment = .training
    case .food(let page):
      if circles { segment = .food }
      food = page
    }
  }
}
