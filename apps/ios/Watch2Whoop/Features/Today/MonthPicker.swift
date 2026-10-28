import GRDB
import SwiftUI

/// One month of the look-back calendar (`GET /v1/users/me/days?month=`): which days have data, coloured by recovery.
struct MonthDays: Equatable {
  struct Day: Equatable, Identifiable {
    var id: String { date }
    var date: String
    var future: Bool
    var hasData: Bool
    var recovery: Double?
    var tone: Tone
  }

  var month: String
  var first: String?
  var days: [Day]

  init(json: [String: Any]) {
    month = json["month"] as? String ?? ""
    first = json["first"] as? String
    days = ((json["days"] as? [[String: Any]]) ?? []).compactMap { d in
      guard let date = d["date"] as? String else { return nil }
      return Day(date: date, future: d["future"] as? Bool ?? false, hasData: d["has_data"] as? Bool ?? false,
                 recovery: TodayModel.num(d["recovery"]), tone: Tone(zone: d["zone"] as? String))
    }
  }

  /// Monday-first offset of the month's first day (the page's calendar starts on Monday).
  static func leadingBlanks(_ month: String) -> Int {
    let utc = TimeZone(identifier: "UTC")!
    guard let d = LocalDay.parse("\(month)-01", utc) else { return 0 }
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = utc
    return (cal.component(.weekday, from: d) + 5) % 7
  }

  static func addMonths(_ month: String, _ n: Int) -> String {
    let p = month.split(separator: "-").compactMap { Int($0) }
    guard p.count == 2 else { return month }
    let total = p[0] * 12 + (p[1] - 1) + n
    return String(format: "%04d-%02d", total / 12, total % 12 + 1)
  }
}

/// The month calendar sheet: page between months, tap a day with data to look back at it.
struct MonthPicker: View {
  let selected: String?
  let userToday: String?
  let pick: (String?) -> Void
  @EnvironmentObject private var session: AccountSession
  @State private var month: String = ""
  @State private var data: MonthDays?
  @State private var cancellable: AnyDatabaseCancellable?

  private var thisMonth: String { String((userToday ?? LocalDay.string(Date(), .current)).prefix(7)) }

  var body: some View {
    VStack(spacing: 12) {
      HStack {
        step(.chevronLeft, enabled: data?.first.map { String($0.prefix(7)) < month } ?? true) { go(-1) }
        Spacer()
        Text(title).font(Typo.bodyStrong).foregroundColor(Palette.text)
        Spacer()
        step(.chevronRight, enabled: month < thisMonth) { go(1) }
      }
      let cols = Array(repeating: GridItem(.flexible(), spacing: 4), count: 7)
      LazyVGrid(columns: cols, spacing: 6) {
        ForEach(Array(weekdays.enumerated()), id: \.offset) { Text($0.element).font(Typo.label).foregroundColor(Palette.text3) }
        ForEach(0..<MonthDays.leadingBlanks(month), id: \.self) { _ in Color.clear.frame(height: 40) }
        ForEach(days) { d in cell(d) }
      }
      if selected != nil {
        Button { pick(nil) } label: {
          Text(tr("回到今天", "Back to today")).font(Typo.bodyStrong).foregroundColor(Palette.text)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.inSheet))
        }
        .buttonStyle(PressScale())
        .padding(.top, 8)
      }
    }
    .onAppear {
      if month.isEmpty { month = String((selected ?? thisMonth).prefix(7)) }
      load()
    }
  }

  private var title: String {
    let p = month.split(separator: "-").compactMap { Int($0) }
    guard p.count == 2 else { return month }
    let y = p[0], mo = p[1], name = TodayModel.monthName(mo)
    return tr("\(y) 年 \(mo) 月", "\(name) \(y)")
  }

  private var weekdays: [String] {
    [tr("一", "M"), tr("二", "T"), tr("三", "W"), tr("四", "T"), tr("五", "F"), tr("六", "S"), tr("日", "S")]
  }

  /// The month's days: the server's list, or plain dates while it loads.
  private var days: [MonthDays.Day] {
    if let data, data.month == month { return data.days }
    let p = month.split(separator: "-").compactMap { Int($0) }
    guard p.count == 2 else { return [] }
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "UTC")!
    let n = cal.range(of: .day, in: .month, for: cal.date(from: DateComponents(year: p[0], month: p[1], day: 1))!)?.count ?? 30
    return (1...n).map { d in
      let date = String(format: "%@-%02d", month, d)
      return MonthDays.Day(date: date, future: date > (userToday ?? date), hasData: false, recovery: nil, tone: .text)
    }
  }

  private func cell(_ d: MonthDays.Day) -> some View {
    let isSel = d.date == (selected ?? userToday)
    let isToday = d.date == userToday
    return Button { pick(d.date) } label: {
      VStack(spacing: 3) {
        Text(String(Int(d.date.suffix(2)) ?? 0)).font(.system(size: 15, weight: isToday ? .bold : .medium).monospacedDigit())
          .foregroundColor(d.future ? Palette.text3 : (isSel ? Palette.bg : Palette.text))
        Circle().fill(d.recovery != nil ? d.tone.color : (d.hasData ? Palette.text3 : Color.clear)).frame(width: 5, height: 5)
      }
      .frame(maxWidth: .infinity, minHeight: 40)
      .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(isSel ? Palette.text : Color.clear))
    }
    .buttonStyle(PressScale())
    .disabled(d.future)
    .accessibilityLabel("\(TodayModel.shortDay(d.date)) \(d.recovery.map { TodayModel.fmt($0) } ?? "")")
  }

  private func step(_ icon: Icon.Name, enabled: Bool, _ run: @escaping () -> Void) -> some View {
    Button(action: run) {
      Icon(icon, size: 18).foregroundColor(enabled ? Palette.text : Palette.text3)
        .frame(width: 36, height: 36).background(Circle().fill(Palette.inSheet))
    }
    .buttonStyle(PressScale())
    .disabled(!enabled)
  }

  private func go(_ n: Int) {
    month = MonthDays.addMonths(month, n)
    load()
  }

  private func load() {
    let key = TodayDocs.days(month)
    cancellable = ValueObservation.tracking { db in try DocRecord.fetchOne(db, key: key) }
      .removeDuplicates()
      .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { rec in
        if let json = rec?.json { data = MonthDays(json: json) }
      })
    let m = month
    Task { _ = await DocPuller(api: session.api, db: session.db).pull([DocFetch(key, Endpoint.monthDays(m))]) }
  }
}
