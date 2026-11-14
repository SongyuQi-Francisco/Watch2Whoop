import HealthKit
import SwiftUI

/// 「缺 N 天」 as a list (FB-24): the days of the last 30 the server has nothing of sleep / heart rate / HRV / steps
/// (GET /v1/users/me/sync/missing-days, server sync/missing_days.py: the data status's kinds and labels), newest first, each
/// with its kinds, a re-sync of that day (`HealthSyncService.startResync(day:)`) and one for all of them. Whether the watch
/// recorded the day at all only the phone knows: a day Health has nothing of either is 「健康里也没有」, not a sync problem,
/// and so is a day still missing after its re-sync (no repeated prompt). Dates and kind codes only, no health values.
struct MissingDays: Equatable {
  struct Kind: Equatable {
    let key: String
    let label: String
  }

  struct Day: Equatable, Identifiable {
    let date: String
    let kinds: [String]
    var id: String { date }
  }

  var kinds: [Kind] = []
  var days: [Day] = []
  var windowDays = 30
  /// Last day of the window (the server's yesterday) and the first day with any data (nil = none yet); the 2.0.1 sync
  /// dial draws the window from these. Older servers leave them out.
  var end: String?
  var since: String?

  init(kinds: [Kind] = [], days: [Day] = [], windowDays: Int = 30) {
    self.kinds = kinds
    self.days = days
    self.windowDays = windowDays
  }

  init(_ j: [String: Any]) {
    kinds = (j["kinds"] as? [Any] ?? []).compactMap { k in
      guard let k = k as? [String: Any], let key = k["key"] as? String else { return nil }
      return Kind(key: key, label: k["label"] as? String ?? key)
    }
    days = (j["days"] as? [Any] ?? []).compactMap { d in
      guard let d = d as? [String: Any], let date = d["date"] as? String, date.count == 10 else { return nil }
      return Day(date: date, kinds: (d["kinds"] as? [Any] ?? []).compactMap { $0 as? String })
    }
    windowDays = (j["window_days"] as? NSNumber)?.intValue ?? 30
    end = (j["end"] as? String).map { String($0.prefix(10)) }
    since = (j["since"] as? String).map { String($0.prefix(10)) }
  }

  func label(_ key: String) -> String { kinds.first { $0.key == key }?.label ?? key }

  /// The HealthKit type the phone checks for each kind (what the engine uploads for it).
  static let healthTypes: [String: String] = [
    "sleep": "HKCategoryTypeIdentifierSleepAnalysis",
    "heart_rate": "HKQuantityTypeIdentifierHeartRate",
    "hrv": "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "steps": "HKQuantityTypeIdentifierStepCount",
  ]

  /// "yyyy-MM-dd" -> the start of that local day.
  static func dayStart(_ s: String, _ calendar: Calendar = .current) -> Date? {
    let p = s.split(separator: "-").compactMap { Int($0) }
    guard p.count == 3 else { return nil }
    return calendar.date(from: DateComponents(year: p[0], month: p[1], day: p[2]))
  }

  /// Where the phone looks for a kind on a day: a night belongs to the day it ends on after 04:00 (the server's
  /// sessionize: wake − 4 h), so sleep samples ending in [day 04:00, next day 04:00); the others start in the day.
  static func window(kind: String, day: Date, calendar: Calendar = .current) -> (start: Date, end: Date, byEnd: Bool) {
    let next = calendar.date(byAdding: .day, value: 1, to: day) ?? day.addingTimeInterval(86_400)
    if kind == "sleep" { return (day.addingTimeInterval(4 * 3600), next.addingTimeInterval(4 * 3600), true) }
    return (day, next, false)
  }

  /// 11月9日 周日 / Sun, Nov 9.
  static func dayLabel(_ s: String, calendar: Calendar = .current) -> String {
    guard let d = dayStart(s, calendar) else { return s }
    let f = DateFormatter()
    f.calendar = calendar
    f.timeZone = calendar.timeZone
    f.locale = Locale(identifier: Lang.isZh ? "zh_Hans" : "en_US_POSIX")
    f.dateFormat = tr("M月d日 EEE", "EEE, MMM d")
    return f.string(from: d)
  }

  /// Days from [day] to today, both counted: the range 「全部重新同步」 re-reads for the oldest day.
  static func daysBack(to day: String, now: Date = Date(), calendar: Calendar = .current) -> Int? {
    guard let d = dayStart(day, calendar) else { return nil }
    let n = calendar.dateComponents([.day], from: d, to: calendar.startOfDay(for: now)).day ?? 0
    return max(1, n + 1)
  }
}

/// One missing day as the list shows it. Pure (unit-tested).
enum MissingDayState: Equatable {
  /// The phone has data of a missing kind (or could not tell): 「重新同步」.
  case notSynced
  /// Re-synced already and still missing although the phone has data: a short label, no new prompt.
  case stillMissing
  /// Health has nothing of the missing kinds (the watch was not worn), or it could not tell and a re-sync did not help.
  case notInHealth
  /// This day's re-sync runs now.
  case resyncing
  /// Its re-sync just ended: the server derives and scores it (a few minutes), so no verdict yet.
  case computing

  /// After a day's re-sync the server needs this long before "still missing" means anything.
  static let settle: TimeInterval = 10 * 60

  /// - phone: kind -> whether Health has a sample of it that day (missing key: the phone could not tell);
  /// - resyncedAt: when this day's re-sync finished (nil: never).
  static func of(missing: [String], phone: [String: Bool], resyncedAt: Date?, running: Bool,
                 now: Date = Date()) -> MissingDayState {
    if running { return .resyncing }
    if let at = resyncedAt, now.timeIntervalSince(at) < settle { return .computing }
    let resynced = resyncedAt != nil
    let known = missing.compactMap { phone[$0] }
    if known.contains(true) { return resynced ? .stillMissing : .notSynced }
    if !missing.isEmpty && known.count == missing.count { return .notInHealth }   // Health has none of them
    return resynced ? .notInHealth : .notSynced
  }

  var canResync: Bool { self == .notSynced }
}

/// Whether Health has a sample of a kind on a day (true / false), nil when it cannot tell (locked phone, no type).
protocol HealthDayChecking {
  func has(kind: String, day: String) async -> Bool?
}

/// HealthKit: one sample is enough (limit 1). iOS answers "none" for a type it was never allowed to read, too.
struct HealthKitDayCheck: HealthDayChecking {
  private static let store = HKHealthStore()

  func has(kind: String, day: String) async -> Bool? {
    guard HKHealthStore.isHealthDataAvailable(), let id = MissingDays.healthTypes[kind], let start = MissingDays.dayStart(day)
    else { return nil }
    let type: HKSampleType? = id.hasPrefix("HKCategory")
      ? HKCategoryType.categoryType(forIdentifier: HKCategoryTypeIdentifier(rawValue: id))
      : HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: id))
    guard let type else { return nil }
    let w = MissingDays.window(kind: kind, day: start)
    let pred = HKQuery.predicateForSamples(withStart: w.start, end: w.end, options: w.byEnd ? .strictEndDate : .strictStartDate)
    return await withCheckedContinuation { c in
      let q = HKSampleQuery(sampleType: type, predicate: pred, limit: 1, sortDescriptors: nil) { _, samples, error in
        c.resume(returning: error != nil ? nil : !(samples ?? []).isEmpty)
      }
      Self.store.execute(q)
    }
  }
}

/// The sync center's way in: 「缺 N 天」 → the list (or 「没有缺的天」).
struct MissingDaysEntry: View {
  let count: Int?
  let windowDays: Int

  var body: some View {
    HStack(spacing: 10) {
      Icon(.calendarDays, size: 18).foregroundColor(Palette.text2)
      Text(tr("最近 \(windowDays) 天", "Last \(windowDays) days")).font(Typo.body).foregroundColor(Palette.text)
      Spacer(minLength: 6)
      if let count {
        Text(count == 0 ? tr("没有缺的天", "Nothing missing") : tr("缺 \(count) 天", "\(count) days missing"))
          .font(Typo.body.monospacedDigit()).foregroundColor(count == 0 ? Palette.text2 : Palette.mid)
      } else {
        Text("—").font(Typo.body).foregroundColor(Palette.text3)
      }
      if (count ?? 0) > 0 { Icon(.chevronRight, size: 16).foregroundColor(Palette.text3) }
    }
    .padding(.vertical, 12)
    .contentShape(Rectangle())
  }
}

/// One row: the day, its missing kinds, then the state or the day's 「重新同步」.
struct MissingDayRow: View {
  let day: MissingDays.Day
  let labels: (String) -> String
  let state: MissingDayState
  var percent: Int?
  var disabled = false
  let resync: () -> Void

  var body: some View {
    HStack(alignment: .center, spacing: 8) {
      VStack(alignment: .leading, spacing: 6) {
        Text(MissingDays.dayLabel(day.date)).font(Typo.body).foregroundColor(Palette.text)
        HStack(spacing: 6) {
          ForEach(day.kinds, id: \.self) { k in Badge(label: labels(k), tone: state == .notInHealth ? .text : .mid) }
        }
      }
      Spacer(minLength: 6)
      switch state {
      case .notSynced:
        Button(tr("重新同步", "Re-sync"), action: resync)
          .font(.system(size: 13, weight: .semibold)).foregroundColor(Palette.text)
          .padding(.vertical, 7).padding(.horizontal, 12)
          .background(Capsule().fill(Palette.surface2))
          .buttonStyle(PressScale())
          .disabled(disabled)
      case .resyncing:
        HStack(spacing: 6) {
          ProgressView().scaleEffect(0.7).tint(Palette.text2)
          if let percent { Text("\(percent)%").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2) }
        }
      case .computing:
        Text(tr("计算中", "Computing")).font(Typo.caption).foregroundColor(Palette.text2)
      case .stillMissing:
        Text(tr("重传后仍缺", "Still missing")).font(Typo.caption).foregroundColor(Palette.mid)
      case .notInHealth:
        Text(tr("健康里也没有", "Not in Health")).font(Typo.caption).foregroundColor(Palette.text3)
      }
    }
    .padding(.vertical, 10)
  }
}

/// The list itself (rows + 「全部重新同步」), from plain data: the page and the Debug screenshots use it.
struct MissingDaysList: View {
  let model: MissingDays
  let state: (MissingDays.Day) -> MissingDayState
  var percent: Int?
  var busy = false
  let resyncDay: (String) -> Void
  let resyncAll: (Int) -> Void

  private struct Item: Identifiable {
    let day: MissingDays.Day
    let state: MissingDayState
    var id: String { day.date }
  }

  var body: some View {
    let items = model.days.map { Item(day: $0, state: state($0)) }
    let open = items.filter { $0.state.canResync }
    VStack(alignment: .leading, spacing: 14) {
      if model.days.isEmpty {
        Text(tr("最近 \(model.windowDays) 天没有缺的天", "Nothing missing in the last \(model.windowDays) days"))
          .font(Typo.body).foregroundColor(Palette.text2)
      } else {
        HStack(spacing: 8) {
          Chip(label: tr("缺 \(model.days.count) 天", "\(model.days.count) days"), tone: .mid)
          let none = items.filter { $0.state == .notInHealth }.count
          if none > 0 { Chip(label: tr("健康里也没有 \(none)", "Not in Health \(none)"), tone: .text) }
        }
        RowGroup {
          ForEach(Array(items.enumerated()), id: \.element.id) { i, item in
            if i > 0 { RowDivider(inset: 0) }
            MissingDayRow(day: item.day, labels: model.label, state: item.state,
                          percent: item.state == .resyncing ? percent : nil, disabled: busy) { resyncDay(item.day.date) }
          }
        }
        if let oldest = open.last?.day.date, let n = MissingDays.daysBack(to: oldest) {
          SecondaryButton(icon: .refreshCw, title: tr("全部重新同步（\(open.count) 天）", "Re-sync all (\(open.count) days)"),
                          background: Palette.surface2) { resyncAll(n) }
            .disabled(busy)
        }
      }
    }
  }
}

/// 同步中心 → 最近 30 天 → the list. Re-syncs one day or all of them; reloads when a re-sync ends.
struct MissingDaysView: View {
  var checker: HealthDayChecking = HealthKitDayCheck()
  @EnvironmentObject private var session: AccountSession
  @ObservedObject private var sync = HealthSyncService.shared
  @State private var model: MissingDays?
  /// day -> kind -> whether Health has it.
  @State private var phone: [String: [String: Bool]] = [:]
  @State private var message: String?
  @State private var busy = false
  @State private var confirmAll: Int?
  @State private var resynced: [String: Date] = [:]

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 18) {
        if let message { Banner(text: message, tone: .text) }
        if let model {
          MissingDaysList(model: model, state: state, percent: sync.jobPercent, busy: busy || sync.job != nil,
                          resyncDay: { d in Task { await resync(day: d) } }, resyncAll: { confirmAll = $0 })
        } else {
          ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.top, 24)
        }
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.top, 8)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(tr("缺的日子", "Missing days"))
    .navigationBarTitleDisplayMode(.inline)
    .task { await load() }
    .refreshable { await load() }
    .onChange(of: sync.job == nil) { done in if done { Task { await load() } } }
    .confirmationDialog(tr("重新同步近 \(confirmAll ?? 0) 天？", "Re-sync the last \(confirmAll ?? 0) days?"),
                        isPresented: Binding(get: { confirmAll != nil }, set: { if !$0 { confirmAll = nil } }),
                        titleVisibility: .visible) {
      Button(tr("开始", "Start")) {
        let n = confirmAll
        confirmAll = nil
        Task { busy = true; message = await sync.startResync(days: n) ?? tr("已开始重新同步", "Re-sync started"); busy = false }
      }
      Button(tr("取消", "Cancel"), role: .cancel) { confirmAll = nil }
    } message: {
      Text(tr("把这段时间手机上的数据重新上传一遍，服务器再重新导入并重算分数。", "Uploads this period from the phone again; the server then re-imports it and recomputes the scores."))
    }
  }

  private func state(_ d: MissingDays.Day) -> MissingDayState {
    MissingDayState.of(missing: d.kinds, phone: phone[d.date] ?? [:], resyncedAt: resynced[d.date],
                       running: sync.job?.day == d.date)
  }

  private func load() async {
    do {
      let m = MissingDays(try await session.api.json(.get(Endpoint.syncMissingDays)))
      model = m
      var out: [String: [String: Bool]] = [:]
      for d in m.days {
        for k in d.kinds { if let has = await checker.has(kind: k, day: d.date) { out[d.date, default: [:]][k] = has } }
      }
      phone = out
      resynced = sync.settings.resyncedDays
    } catch APIError.notFound {
      message = tr("这台服务器还不支持缺失日期", "This server cannot list missing days yet")
      model = MissingDays()
    } catch let e as APIError {
      message = e.userMessage
    } catch {}
  }

  private func resync(day: String) async {
    busy = true
    message = await sync.startResync(day: day)
    busy = false
  }
}
