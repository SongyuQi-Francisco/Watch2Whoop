import SwiftUI

/// The profile (profile/store.py): what to call you, birthday, sex, height, goal weight, time zone. The scores need
/// birthday, sex and height (`missing`); the first-run step can be skipped ("later"). PATCH /v1/users/me/profile.
struct ProfileSetupView: View {
  @EnvironmentObject private var session: AccountSession
  let first: Bool
  let done: () async -> Void

  @State private var p = Profile()
  @State private var name = ""
  @State private var hasBirth = false
  @State private var birth = Calendar.current.date(byAdding: .year, value: -30, to: Date()) ?? Date()
  @State private var sex = ""
  @State private var height = ""
  @State private var goal = ""
  @State private var tz = TimeZone.current.identifier
  @State private var loaded = false
  @State private var busy = false
  @State private var message: String?

  private static let day: DateFormatter = {
    let f = DateFormatter()
    f.calendar = Calendar(identifier: .gregorian)
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd"
    return f
  }()

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 14) {
        if first {
          Text(tr("完善资料", "Complete your profile")).font(.system(size: 28, weight: .bold)).titleTracking()
            .foregroundColor(Palette.text).padding(.top, 28)
          Text(tr("生日、性别和身高让分数更准。", "Birthday, sex and height make the scores more accurate."))
            .font(Typo.body).foregroundColor(Palette.text2)
        }
        if !loaded {
          ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.top, 40)
        } else {
          FormField(label: tr("称呼", "Name"), text: $name, contentType: .nickname)
          VStack(alignment: .leading, spacing: 6) {
            Text(tr("生日", "Birthday")).font(Typo.label).foregroundColor(Palette.text2)
            HStack {
              if hasBirth {
                DatePicker("", selection: $birth, in: Self.birthRange, displayedComponents: .date).labelsHidden()
                Spacer()
              } else {
                Button(tr("填写生日", "Add birthday")) { hasBirth = true }.font(Typo.body).foregroundColor(Palette.text)
                Spacer()
              }
            }
            .padding(.horizontal, 14).frame(minHeight: 46)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
          }
          VStack(alignment: .leading, spacing: 6) {
            Text(tr("性别", "Sex")).font(Typo.label).foregroundColor(Palette.text2)
            Picker("", selection: $sex) {
              Text(tr("不填", "Not set")).tag("")
              Text(tr("男", "Male")).tag("male")
              Text(tr("女", "Female")).tag("female")
            }
            .pickerStyle(.segmented)
          }
          FormField(label: tr("身高（cm）", "Height (cm)"), text: $height, placeholder: "175", keyboard: .decimalPad)
          FormField(label: tr("目标体重（kg）", "Goal weight (kg)"), text: $goal, placeholder: "70", keyboard: .decimalPad)
          VStack(alignment: .leading, spacing: 6) {
            Text(tr("时区", "Time zone")).font(Typo.label).foregroundColor(Palette.text2)
            Picker("", selection: $tz) {
              ForEach(Self.zones(including: tz), id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.menu).tint(Palette.text)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 8).frame(minHeight: 46)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
          }
          if let message { Banner(text: message, tone: .bad) }
          PrimaryButton(title: tr("保存", "Save"), busy: busy) { Task { await save() } }
          if first {
            Button(tr("以后再说", "Later")) { Task { await done() } }
              .font(Typo.body).foregroundColor(Palette.text2).frame(maxWidth: .infinity).padding(.top, 4)
          }
        }
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.bottom, 32)
    }
    .background((first ? Palette.bg : Palette.sheet).ignoresSafeArea())
    .task { await load() }
  }

  static let birthRange: ClosedRange<Date> = {
    let c = Calendar(identifier: .gregorian)
    let lo = c.date(from: DateComponents(year: 1920, month: 1, day: 1)) ?? Date.distantPast
    let hi = c.date(byAdding: .year, value: -10, to: Date()) ?? Date()
    return lo...hi
  }()

  static func zones(including current: String) -> [String] {
    var z = TimeZone.knownTimeZoneIdentifiers
    if !z.contains(current) { z.insert(current, at: 0) }
    return z
  }

  private func load() async {
    do {
      p = try await session.api.send(.get(Endpoint.profile), as: Profile.self)
      name = p.displayName ?? ""
      if let b = p.birthDate, let d = Self.day.date(from: b) { birth = d; hasBirth = true }
      sex = p.sex ?? ""
      height = p.heightCm.map { Self.number($0) } ?? ""
      goal = p.goalWeightKg.map { Self.number($0) } ?? ""
      tz = p.timezone ?? TimeZone.current.identifier
      loaded = true
    } catch let e as APIError {
      message = e.userMessage
      loaded = true
    } catch {
      loaded = true
    }
  }

  static func number(_ v: Double) -> String { v == v.rounded() ? String(Int(v)) : String(format: "%.1f", v) }

  /// The PATCH body: only what changed (null clears a field).
  func patch() -> [String: AnyEncodable?] {
    var b: [String: AnyEncodable?] = [:]
    let n = name.trimmingCharacters(in: .whitespaces)
    if !n.isEmpty, n != p.displayName { b["display_name"] = AnyEncodable(n) }
    let bd = hasBirth ? Self.day.string(from: birth) : nil
    if bd != p.birthDate { b["birth_date"] = bd.map(AnyEncodable.init) ?? .some(nil) }
    let sx = sex.isEmpty ? nil : sex
    if sx != p.sex { b["sex"] = sx.map(AnyEncodable.init) ?? .some(nil) }
    let h = Double(height.replacingOccurrences(of: ",", with: "."))
    if h != p.heightCm { b["height_cm"] = h.map(AnyEncodable.init) ?? .some(nil) }
    let g = Double(goal.replacingOccurrences(of: ",", with: "."))
    if g != p.goalWeightKg { b["goal_weight_kg"] = g.map(AnyEncodable.init) ?? .some(nil) }
    if tz != p.timezone { b["timezone"] = AnyEncodable(tz) }
    return b
  }

  private func save() async {
    busy = true
    defer { busy = false }
    message = nil
    let body = patch()
    if !body.isEmpty {
      do {
        p = try await session.api.send(try .json(.patch, Endpoint.profile, body), as: Profile.self)
        if let n = p.displayName { session.displayName = n }
      } catch let e as APIError {
        message = e.userMessage
        return
      } catch {
        message = String(describing: error)
        return
      }
    }
    await done()
  }
}
