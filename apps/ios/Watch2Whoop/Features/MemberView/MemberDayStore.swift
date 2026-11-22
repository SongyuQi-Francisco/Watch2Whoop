import SwiftUI

/// Someone else's day for the levels of their view (the first glance and 今日详情): the same `TodayModel` my own 今天 draws,
/// built from their documents only (`TodayModel.member`), which stay in `MemberViewStore`'s memory. The day looked at can
/// change (the month calendar, a swipe); nothing is computed on the phone and nothing is written.
@MainActor
final class MemberDayStore: ObservableObject, TodayDaySource {
  @Published private(set) var model: TodayModel = .empty
  @Published private(set) var hasData = false
  /// The day looked at (nil = their today).
  @Published private(set) var date: String?
  /// What they ate on the day (the 饮食 face; nil = not shared, nothing logged, or not loaded yet).
  @Published private(set) var nutrition: NutritionDay?
  /// Their training month around the day (the 训练 face; nil = not shared).
  @Published private(set) var month: TrainingMonth?

  let member: MemberViewStore

  init(member: MemberViewStore) {
    self.member = member
  }

  /// The day shown (their today when none is picked).
  var day: String? { date ?? member.today }

  /// Look back at `day` (nil or their today = today).
  func select(_ day: String?) {
    let d = day == member.today ? nil : day
    guard d != date else { return }
    date = d
    hasData = false
    Task { await load() }
  }

  /// One day back or forward, never past their today (the swipe, the dial's VoiceOver actions).
  func step(_ delta: Int) {
    if let to = TodayStore.stepTarget(from: date, today: member.today, delta: delta) { select(to) }
  }

  /// Pull to refresh: everything of the view again (a share taken back disappears), then this day.
  func refresh() async {
    await member.start(force: true)
    await load()
  }

  /// The day's documents (what is loaded already is not asked again), then the model.
  func load() async {
    let main = member.todayRequest(date)
    await member.load([main])
    rebuild()
    guard let d = day else { return }
    await member.load(Self.requests(member, day: d, date: date))
    rebuild()
  }

  /// The day's other documents: the same ones my own 今天 reads for a day (`TodayDocs.fetches`), under their path.
  static func requests(_ m: MemberViewStore, day d: String, date: String?) -> [APIRequest] {
    let u = m.user
    return [Endpoint.dayActivity(d, user: u), Endpoint.strainCoach(d, user: u), Endpoint.healthMonitor(d, user: u),
            Endpoint.trends(days: TodayParams.trendsDays, end: date, user: u), m.memoryRequest, m.calendarRequest(String(d.prefix(7)))]
  }

  private func rebuild() {
    let u = member.user, d = day ?? "", main = member.todayRequest(date)
    let doc = member.json(main)
    let m = TodayModel.member(today: doc, activity: member.json(Endpoint.dayActivity(d, user: u)),
                              coach: member.json(Endpoint.strainCoach(d, user: u)), monitor: member.json(Endpoint.healthMonitor(d, user: u)),
                              memory: member.json(member.memoryRequest),
                              trends: member.json(Endpoint.trends(days: TodayParams.trendsDays, end: date, user: u)), theirToday: member.today)
    if m != model { model = m }
    let n = d.isEmpty ? nil : NutritionDay.build(today: MemberViewStore.withoutRefused(doc), day: d, pending: [])
    if n != nutrition { nutrition = n }
    let t = d.isEmpty ? nil : TrainingMonth.build(member.json(member.calendarRequest(String(d.prefix(7)))), month: String(d.prefix(7)))
    if t != month { month = t }
    if doc != nil && !hasData { hasData = true }
  }
}
