import Foundation

/// One row of the HealthKit type registry (`Resources/healthkit_types.json` = the Flutter app's
/// `assets/healthkit_types.json` = the server's `rawdata/hk_types.engine_json()`, copied by scripts/sync_flutter_sources.sh).
struct HealthType: Equatable, Decodable {
  let hk: String
  let key: String
  let kind: String
  var unit: String = ""
  var upload: String = "raw"
  var bg: String = "none"
  var order: Int = 50
  var minIos: Int = 0
  var page: [Int] = [2000, 200]
  /// The data-status key the sync center groups it under (nil: not shown there).
  var status: String?

  enum CodingKeys: String, CodingKey {
    case hk, key, kind, unit, upload, bg, order, status, page
    case minIos = "min_ios"
  }

  init(hk: String, key: String, kind: String, unit: String = "", upload: String = "raw", status: String? = nil) {
    self.hk = hk
    self.key = key
    self.kind = kind
    self.unit = unit
    self.upload = upload
    self.status = status
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    hk = try c.decode(String.self, forKey: .hk)
    key = try c.decode(String.self, forKey: .key)
    kind = try c.decode(String.self, forKey: .kind)
    unit = try c.decodeIfPresent(String.self, forKey: .unit) ?? ""
    upload = try c.decodeIfPresent(String.self, forKey: .upload) ?? "raw"
    bg = try c.decodeIfPresent(String.self, forKey: .bg) ?? "none"
    order = try c.decodeIfPresent(Int.self, forKey: .order) ?? 50
    minIos = try c.decodeIfPresent(Int.self, forKey: .minIos) ?? 0
    page = try c.decodeIfPresent([Int].self, forKey: .page) ?? [2000, 200]
    status = try c.decodeIfPresent(String.self, forKey: .status)
  }

  /// Read by the engine (quantities, categories, workouts). Heartbeat series, ECGs and water stay with RawSync.
  var isEngine: Bool { kind == "quantity" || kind == "category" || kind == "workout" }

  /// What SyncEngine.swift's `configure` takes per type (EngineTypeSpec).
  var engineMap: [String: Any] {
    ["hk": hk, "kind": kind, "unit": unit, "upload": upload, "bg": bg, "order": order, "min_ios": minIos, "page": page]
  }

  /// What Backfill.swift's `start` takes per type.
  var backfillMap: [String: String] { ["hk": hk, "kind": kind, "unit": unit, "upload": upload] }

  /// A label for people: the iOS Health name when known, else the server key in words.
  var label: String { HealthRegistry.label(key: key, status: status) }
}

enum HealthRegistry {
  private struct File: Decodable { let types: [HealthType] }

  static func parse(_ data: Data) throws -> [HealthType] { try JSONDecoder().decode(File.self, from: data).types }

  /// The bundled registry in reading order (the server's order: sleep and the overnight readings first).
  static let bundled: [HealthType] = {
    guard let url = Bundle.main.url(forResource: "healthkit_types", withExtension: "json"),
          let data = try? Data(contentsOf: url), let types = try? parse(data) else { return [] }
    return types
  }()

  /// Types only the Open Wearables SDK asked for in 1.0.8 (not in the registry): still asked in the one sheet, so a
  /// phone that upgrades sees nothing new (#214).
  static let owOnlyTypes = ["HKQuantityTypeIdentifierWaistCircumference", "HKQuantityTypeIdentifierInsulinDelivery"]

  /// Every HealthKit type the app reads, for the one permission sheet (RawSync adds the heartbeat series' parent).
  static func permissionTypes(_ types: [HealthType] = bundled) -> [String] { types.map(\.hk) + owOnlyTypes }

  // MARK: - names (as Apple's Health app calls them; the Flutter app's data_status.dart iosName + sync_engine.dart)

  static func label(key: String, status: String?) -> String {
    if let n = name(key) { return n }
    if let s = status, let n = statusName(s) { return n }
    let words = key.replacingOccurrences(of: "_", with: " ")
    return words.prefix(1).uppercased() + words.dropFirst()
  }

  static func statusName(_ key: String) -> String? {
    switch key {
    case "heart_rate": return tr("心率", "Heart Rate")
    case "hrv": return tr("心率变异性", "Heart Rate Variability")
    case "resting_heart_rate": return tr("静息心率", "Resting Heart Rate")
    case "sleep": return tr("睡眠", "Sleep")
    case "steps": return tr("步数", "Steps")
    case "active_energy": return tr("活动能量", "Active Energy")
    case "workouts": return tr("体能训练", "Workouts")
    case "weight": return tr("体重", "Weight")
    case "blood_oxygen": return tr("血氧", "Blood Oxygen")
    case "respiratory_rate": return tr("呼吸速率", "Respiratory Rate")
    case "wrist_temperature": return tr("睡眠时手腕温度", "Sleeping Wrist Temperature")
    case "vo2max": return tr("心肺适能", "Cardio Fitness")
    case "heartbeats": return tr("心跳序列", "Heartbeat Series")
    case "ecg": return tr("心电图", "Electrocardiograms (ECG)")
    case "water": return tr("水", "Water")
    default: return nil
    }
  }

  private static func name(_ key: String) -> String? {
    switch key {
    case "sleep_analysis": return tr("睡眠", "Sleep")
    case "workout": return tr("体能训练", "Workouts")
    case "heart_rate": return tr("心率", "Heart Rate")
    case "heart_rate_variability_sdnn": return tr("心率变异性", "Heart Rate Variability")
    case "resting_heart_rate": return tr("静息心率", "Resting Heart Rate")
    case "step_count": return tr("步数", "Steps")
    case "active_energy_burned": return tr("活动能量", "Active Energy")
    case "body_mass": return tr("体重", "Weight")
    case "oxygen_saturation": return tr("血氧", "Blood Oxygen")
    case "vo2_max": return tr("心肺适能", "Cardio Fitness")
    case "time_in_daylight": return tr("日照时间", "Time in daylight")
    case "physical_effort": return tr("体能消耗", "Physical effort")
    case "stair_ascent_speed": return tr("上楼速度", "Stair ascent speed")
    case "stair_descent_speed": return tr("下楼速度", "Stair descent speed")
    case "environmental_audio_exposure": return tr("环境音量", "Environmental audio exposure")
    case "headphone_audio_exposure": return tr("耳机音量", "Headphone audio exposure")
    case "environmental_sound_reduction": return tr("环境降噪", "Environmental sound reduction")
    case "apple_stand_time": return tr("站立时间", "Stand time")
    case "apple_exercise_time": return tr("锻炼分钟数", "Exercise minutes")
    case "distance_walking_running": return tr("步行+跑步距离", "Walking + running distance")
    case "distance_cycling": return tr("骑行距离", "Cycling distance")
    case "distance_swimming": return tr("游泳距离", "Swimming distance")
    case "swimming_stroke_count": return tr("划水次数", "Swimming strokes")
    case "flights_climbed": return tr("已爬楼层", "Flights climbed")
    case "basal_energy_burned": return tr("静息能量", "Resting energy")
    case "walking_heart_rate_average": return tr("步行平均心率", "Walking heart rate average")
    case "apple_walking_steadiness": return tr("步行稳定性", "Walking steadiness")
    case "heart_rate_variability_rmssd": return tr("心率变异性（RMSSD）", "Heart rate variability (RMSSD)")
    case "body_fat_percentage": return tr("体脂率", "Body fat percentage")
    case "lean_body_mass": return tr("去脂体重", "Lean body mass")
    case "body_mass_index": return tr("身体质量指数", "Body mass index")
    case "height": return tr("身高", "Height")
    case "body_temperature": return tr("体温", "Body temperature")
    case "blood_pressure_systolic": return tr("收缩压", "Blood pressure (systolic)")
    case "blood_pressure_diastolic": return tr("舒张压", "Blood pressure (diastolic)")
    case "blood_glucose": return tr("血糖", "Blood glucose")
    case "mindful_session": return tr("正念", "Mindful minutes")
    case "walking_speed": return tr("步行速度", "Walking speed")
    case "walking_step_length": return tr("步长", "Walking step length")
    case "walking_double_support_percentage": return tr("双支撑时间", "Double support time")
    case "walking_asymmetry_percentage": return tr("步伐不对称", "Walking asymmetry")
    case "six_minute_walk_test_distance": return tr("六分钟步行距离", "Six-minute walk")
    case "running_speed": return tr("跑步速度", "Running speed")
    case "running_stride_length": return tr("跑步步长", "Running stride length")
    case "running_power": return tr("跑步功率", "Running power")
    case "running_vertical_oscillation": return tr("垂直振幅", "Vertical oscillation")
    case "running_ground_contact_time": return tr("触地时间", "Ground contact time")
    case "cycling_speed": return tr("骑行速度", "Cycling speed")
    case "cycling_power": return tr("骑行功率", "Cycling power")
    case "cycling_cadence": return tr("骑行踏频", "Cycling cadence")
    case "cycling_functional_threshold_power": return tr("功能性阈值功率", "Functional threshold power")
    case "water_temperature": return tr("水温", "Water temperature")
    case "workout_effort_score": return tr("体能训练强度", "Workout effort")
    case "estimated_workout_effort_score": return tr("估算体能训练强度", "Estimated workout effort")
    case "dietary_water": return tr("水", "Water")
    default: return nil
    }
  }
}
