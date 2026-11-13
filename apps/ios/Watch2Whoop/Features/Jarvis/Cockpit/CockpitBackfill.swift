import SwiftUI

/// Jarvis → 记训练 → 补记 (docs/design/2.0.1/18-backfill.html, without the "found on the watch" card: watch workouts are
/// imported by themselves). A cardio / ball-game session logged by hand: type, day (and a start time if known), minutes,
/// intensity, 记上. The types and the intensity levels are the server's (`/training/manual-workouts/options`); logging is
/// direct, 撤销 deletes that record. When the server says the watch already has this session (`duplicate_of`), the card says
/// so instead of pretending a new one was logged.
struct ManualWorkoutAPI {
  static let base = "/v1/users/me/training/manual-workouts"

  struct Options: Hashable {
    struct Kind: Equatable, Hashable {
      var type: String
      var name: String
    }

    struct Level: Equatable, Hashable {
      var key: String
      var label: String
    }

    var types: [Kind]
    var intensities: [Level]
    var maxMinutes: Double
    var maxPastDays: Int

    static func parse(_ j: [String: Any]) -> Options? {
      let types = ((j["types"] as? [[String: Any]]) ?? []).compactMap { t -> Kind? in
        guard let type = t["type"] as? String else { return nil }
        let zh = t["name"] as? String, en = t["name_en"] as? String
        return Kind(type: type, name: (Lang.isZh ? zh : (en ?? zh)) ?? type)
      }
      let levels = ((j["intensities"] as? [[String: Any]]) ?? []).compactMap { l -> Level? in
        guard let k = l["key"] as? String else { return nil }
        return Level(key: k, label: l["label"] as? String ?? k)
      }
      guard !types.isEmpty, !levels.isEmpty else { return nil }
      return Options(types: types, intensities: levels, maxMinutes: RecordFormat.double(j["max_duration_min"]) ?? 600,
                     maxPastDays: (j["max_past_days"] as? NSNumber)?.intValue ?? 7)
    }
  }

  /// What one 记上 sends. `client_id` is made once per form: a retry (the client's own, or the user tapping again after an
  /// error) is the same record on the server.
  struct Entry: Equatable {
    var type: String
    var minutes: Double
    var intensity: String
    var date: String
    /// `HH:mm` when the user set a start time; nil = untimed (the server places it at noon of that day).
    var time: String?
    var clientId: String

    var body: [String: Any] {
      var b: [String: Any] = ["type": type, "duration_min": minutes, "intensity": intensity, "client_id": clientId]
      if let time { b["start"] = "\(date)T\(time):00" } else { b["date"] = date }
      return b
    }
  }

  /// The answer of a 记上.
  struct Outcome: Equatable {
    var workoutId: Int
    var name: String
    var date: String
    /// Another record of this session already exists (its `source`: apple = the watch): this one is linked to it and counts
    /// nowhere.
    var duplicate: String?

    static func parse(_ j: [String: Any]) -> Outcome? {
      guard let w = j["workout"] as? [String: Any], let id = (w["id"] as? NSNumber)?.intValue else { return nil }
      let dup = j["duplicate_of"] as? [String: Any]
      return Outcome(workoutId: id, name: w["name"] as? String ?? "", date: w["date"] as? String ?? "",
                     duplicate: dup.map { ($0["source"] as? String) ?? "" })
    }
  }

  let api: APIClient

  /// nil when the server does not have the endpoint yet (404): the key keeps opening 记录 → 训练.
  func options() async throws -> Options? {
    do {
      return Options.parse(try await api.json(.get(Self.base + "/options")))
    } catch APIError.notFound {
      return nil
    }
  }

  func add(_ e: Entry) async throws -> Outcome {
    let data = try JSONSerialization.data(withJSONObject: e.body)
    // idempotent by client_id: the client may retry it after a dropped connection
    let j = try await api.json(APIRequest(method: .post, path: Self.base, body: data, idempotent: true))
    guard let out = Outcome.parse(j) else { throw APIError.http(200, nil) }
    return out
  }

  /// A 404 counts as undone: the record is gone (an earlier DELETE went through and only its answer was lost).
  func undo(_ id: Int) async throws {
    do {
      _ = try await api.data(APIRequest(method: .delete, path: "\(Self.base)/\(id)"))
    } catch APIError.notFound {
      return
    }
  }

  /// The card's line for a session another record already has (`duplicate_of.source`).
  static func duplicateLabel(_ source: String) -> String {
    switch source {
    case "apple", "apple_export", "healthkit", "ow", "shortcut":
      return tr("手表已有这次训练，以手表为准", "The watch has this session; the watch's counts")
    case "manual":
      return tr("已经补记过这次训练", "This session is already logged")
    default:
      return tr("已有这次训练，以已有记录为准", "This session is already recorded; that record counts")
    }
  }
}

/// 补记: opens at once and loads the server's types and levels inside (a server without the endpoint: 记录 → 训练).
struct CockpitBackfillSheet: View {
  let client: ManualWorkoutAPI
  let today: String
  let close: () -> Void
  /// The server does not have manual workouts yet (404): the key's old way.
  let fallback: () -> Void
  @State private var options: ManualWorkoutAPI.Options?
  @State private var failed: String?

  var body: some View {
    Group {
      if let options {
        CockpitBackfillForm(client: client, options: options, today: today, close: close)
      } else {
        SheetContainer(title: tr("补记训练", "Log a session"), close: close) {
          if let failed {
            RetryNote(text: failed) { Task { await load() } }
          } else {
            ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.vertical, 40)
          }
        }
      }
    }
    .task { await load() }
  }

  private func load() async {
    failed = nil
    do {
      if let o = try await client.options() { options = o } else { fallback() }
    } catch {
      failed = TrainingStore.message(error)
    }
  }
}

struct CockpitBackfillForm: View {
  let client: ManualWorkoutAPI
  let options: ManualWorkoutAPI.Options
  let today: String
  let close: () -> Void
  /// Made when the sheet opens: every 记上 of this form is the same record.
  @State private var clientId = UUID().uuidString
  @State private var type: String
  @State private var day: String
  @State private var timed = false
  @State private var start = Date()
  @State private var minutes: Double = 45
  @State private var intensity: String
  @State private var busy = false
  @State private var done: ManualWorkoutAPI.Outcome?
  /// The type the result card names (in the app's language: the server's name is Chinese).
  @State private var doneName = ""
  @State private var toast: Toast?

  init(client: ManualWorkoutAPI, options: ManualWorkoutAPI.Options, today: String, close: @escaping () -> Void) {
    self.client = client
    self.options = options
    self.today = today
    self.close = close
    _type = State(initialValue: options.types.first?.type ?? "")
    _day = State(initialValue: today)
    _intensity = State(initialValue: options.intensities.first { $0.key == "moderate" }?.key ?? options.intensities[0].key)
  }

  /// The days that can be picked: today and the ones before it, oldest first.
  static func days(today: String, back: Int) -> [String] {
    (0...max(0, min(back, 31))).reversed().map { RecordDay.add(today, -$0) }
  }

  static let step: Double = 5

  /// The latest start time that can be picked: now on today, the end of the day before.
  static func latestStart(day: String, today: String, now: Date = Date()) -> PartialRangeThrough<Date> {
    ...(day == today ? now : Date.distantFuture)
  }

  var entry: ManualWorkoutAPI.Entry {
    let f = DateFormatter()
    f.dateFormat = "HH:mm"
    f.locale = Locale(identifier: "en_US_POSIX")
    return ManualWorkoutAPI.Entry(type: type, minutes: minutes, intensity: intensity, date: day, time: timed ? f.string(from: start) : nil,
                                  clientId: clientId)
  }

  var body: some View {
    SheetContainer(title: tr("补记训练", "Log a session"), close: close) {
      if let done { result(done) } else { form }
    }
    .toast($toast)
    .interactiveDismissDisabled(busy)   // a record being written keeps its sheet (and its 撤销)
  }

  private var form: some View {
    VStack(alignment: .leading, spacing: 20) {
      ScrollViewReader { proxy in
      ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 6) {
          ForEach(Self.days(today: today, back: options.maxPastDays), id: \.self) { d in
            Button { day = d } label: {
              VStack(spacing: 4) {
                Text(ExerciseVocab.weekday(RecordDay.weekday(d))).font(Typo.label).foregroundColor(d == day ? Palette.onAccent : Palette.text2)
                Text(String(Int(d.suffix(2)) ?? 0)).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(d == day ? Palette.onAccent : Palette.text)
              }
              .frame(width: 46, height: 56)
              .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(d == day ? Palette.accent : Palette.inSheet))
            }
            .buttonStyle(PressScale())
            .accessibilityAddTraits(d == day ? .isSelected : [])
            .id(d)
          }
        }
      }
      .onAppear { proxy.scrollTo(day, anchor: .trailing) }   // today (the newest) in view
      }
      FlowRow(spacing: 8) {
        ForEach(options.types, id: \.self) { k in
          ToggleChip(title: k.name, on: k.type == type) { type = k.type }
        }
      }
      HStack {
        Toggle(tr("开始时间", "Start time"), isOn: $timed).font(Typo.body).tint(Palette.accent).fixedSize()
        Spacer()
        if timed {
          // today: no start time after now
          DatePicker("", selection: $start, in: Self.latestStart(day: day, today: today), displayedComponents: .hourAndMinute).labelsHidden()
        }
      }
      HStack {
        stepButton(.minus) { minutes = max(Self.step, minutes - Self.step) }
        Spacer()
        HStack(alignment: .firstTextBaseline, spacing: 4) {
          Text(RecordFormat.num(minutes)).font(.system(size: 44, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
          Text(tr("分", "min")).font(Typo.unit).foregroundColor(Palette.text2)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAdjustableAction { d in
          minutes = d == .increment ? min(options.maxMinutes, minutes + Self.step) : max(Self.step, minutes - Self.step)
        }
        Spacer()
        stepButton(.plus) { minutes = min(options.maxMinutes, minutes + Self.step) }
      }
      SegmentedControl(items: options.intensities.map { ($0.key, $0.label) }, selection: $intensity)
      PrimaryButton(title: tr("记上", "Log it"), busy: busy) { save() }
        .disabled(type.isEmpty || busy)
    }
  }

  private func stepButton(_ icon: Icon.Name, _ action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Icon(icon, size: 20).foregroundColor(Palette.text).frame(width: 48, height: 48).background(Circle().fill(Palette.inSheet))
    }
    .buttonStyle(PressScale())
    .accessibilityLabel(icon == .plus ? tr("加 5 分钟", "5 minutes more") : tr("减 5 分钟", "5 minutes less"))
  }

  private func result(_ o: ManualWorkoutAPI.Outcome) -> some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack(spacing: 6) {
        Icon(o.duplicate == nil ? .circleCheck : .circleAlert, size: 18).foregroundColor(o.duplicate == nil ? Palette.good : Palette.mid)
        Text(o.duplicate.map(ManualWorkoutAPI.duplicateLabel) ?? tr("已记到 \(RecordDay.short(o.date))", "Logged to \(RecordDay.short(o.date))"))
          .font(Typo.bodyStrong).foregroundColor(Palette.text)
      }
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text(doneName.isEmpty ? o.name : doneName).font(.system(size: 26, weight: .semibold)).foregroundColor(Palette.text)
        Spacer()
        Text(RecordFormat.num(minutes)).font(.system(size: 26, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
        Text(tr("分", "min")).font(Typo.unit).foregroundColor(Palette.text2)
      }
      HStack(spacing: 10) {
        Button { undo(o) } label: {
          HStack(spacing: 6) { Icon(.undo2, size: 15); Text(tr("撤销", "Undo")).font(Typo.bodyStrong) }
            .foregroundColor(Palette.text).frame(maxWidth: .infinity, minHeight: 44)
            .overlay(Capsule().stroke(Palette.text.opacity(0.3), lineWidth: 1))
        }
        .buttonStyle(PressScale())
        .disabled(busy)
        PrimaryButton(title: tr("完成", "Done"), busy: false) { close() }
      }
    }
  }

  private func save() {
    busy = true
    let e = entry
    Task {
      defer { busy = false }
      do {
        let out = try await client.add(e)
        doneName = options.types.first { $0.type == e.type }?.name ?? ""
        done = out
        JarvisHaptics.notice.notificationOccurred(.success)
        NotificationCenter.default.post(name: JarvisRouter.wroteNotification, object: nil)
      } catch {
        toast = Toast(TrainingStore.message(error))   // the same client_id goes out again on the next 记上
      }
    }
  }

  private func undo(_ o: ManualWorkoutAPI.Outcome) {
    busy = true
    Task {
      defer { busy = false }
      do {
        try await client.undo(o.workoutId)
        NotificationCenter.default.post(name: JarvisRouter.wroteNotification, object: nil)
        toast = Toast(tr("已撤销", "Undone"))
        done = nil
        clientId = UUID().uuidString   // a new form: the next 记上 is a new record
      } catch {
        toast = Toast(TrainingStore.message(error))
      }
    }
  }
}
