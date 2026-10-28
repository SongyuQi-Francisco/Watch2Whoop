import Foundation

/// 健康档案 (「我」 → 补剂 / 化验 / 身体事件): server views (`/supplements`, `/labs/summary`, `/labs/{key}/history`,
/// `/events`) with today's supplement check-ins (journal answers) laid over them. Pure (HealthTests).

enum HealthDocs {
  static let supplements = "health:supplements"
  static let labs = "health:labs"
  static func lab(_ key: String) -> String { "health:lab:\(key)" }
  static let events = "health:events"
  static func journal(_ day: String) -> String { "journal:\(day)" }   // the same document as 记录 → 日记
}

enum HealthAPI {
  static let supplements = APIRequest.get("/v1/users/me/supplements")
  static let labs = APIRequest.get("/v1/users/me/labs/summary")
  static func lab(_ key: String) -> APIRequest {
    .get("/v1/users/me/labs/\(key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key)/history")
  }
  static let events = APIRequest.get("/v1/users/me/events")
  static let eventsPath = "/v1/users/me/events"
  static func journal(_ day: String) -> APIRequest { .get("/v1/users/me/journal", ["start": day, "end": day]) }
}

struct Supplement: Equatable, Identifiable {
  var id: Int
  var name: String
  var dose: String?
  var timing: String?
  var status: String
  var journalKey: String?
  /// Today's check-in (the journal item of this supplement).
  var takenToday: Bool
  var pending: PendingRecord?

  var active: Bool { status == "active" || status == "as_needed" }

  var statusTitle: String {
    switch status {
    case "active": return tr("在吃", "Taking")
    case "as_needed": return tr("按需", "As needed")
    case "paused": return tr("暂停", "Paused")
    case "stopped": return tr("已停", "Stopped")
    case "not_needed": return tr("不需要", "Not needed")
    default: return status
    }
  }

  static func build(_ list: [[String: Any]]?, journal: [[String: Any]]?, today: String, pending: [PendingRecord]) -> [Supplement] {
    var taken: [String: Bool] = [:]
    for e in journal ?? [] where (e["date"] as? String) == today {
      if let k = e["key"] as? String { taken[k] = RecordFormat.double(e["value"]) == 1 }
    }
    var marks: [String: PendingRecord] = [:]
    for p in pending where p.kind == "journal" && p.date == today {
      guard let k = p.json["key"] as? String else { continue }
      taken[k] = RecordFormat.double(p.json["value"]) == 1
      marks[k] = p
    }
    return (list ?? []).compactMap { s in
      guard let id = (s["id"] as? NSNumber)?.intValue, let name = s["name"] as? String else { return nil }
      let key = s["journal_key"] as? String
      return Supplement(id: id, name: name, dose: s["dose"] as? String, timing: s["timing"] as? String,
                        status: s["status"] as? String ?? "active", journalKey: key, takenToday: key.flatMap { taken[$0] } ?? false,
                        pending: key.flatMap { marks[$0] })
    }
  }
}

/// One test of `/labs/summary`: latest value, status, a few earlier readings.
struct LabSummary: Equatable, Identifiable {
  var key: String
  var name: String
  var value: Double?
  var valueText: String?
  var unit: String?
  var date: String?
  var status: String?
  var history: [Double]
  var delta: Double?
  var id: String { key }

  static func parseList(_ list: [[String: Any]]?) -> [LabSummary] {
    (list ?? []).compactMap { t in
      guard let key = t["test_key"] as? String else { return nil }
      let latest = t["latest"] as? [String: Any]
      return LabSummary(key: key, name: t["test_name"] as? String ?? key, value: RecordFormat.double(latest?["value"]),
                        valueText: latest?["value_text"] as? String, unit: latest?["unit"] as? String,
                        date: latest?["collected_on"] as? String, status: t["status"] as? String,
                        history: ((t["history"] as? [[String: Any]]) ?? []).compactMap { RecordFormat.double($0["value"]) },
                        delta: RecordFormat.double((t["change_from_previous"] as? [String: Any])?["delta"]))
    }
  }

  static func tone(_ status: String?) -> Tone {
    switch status {
    case "high", "abnormal": return .bad
    case "low": return .mid
    case "normal": return .good
    default: return .text
    }
  }

  static func statusTitle(_ status: String?) -> String? {
    switch status {
    case "high": return tr("偏高", "High")
    case "low": return tr("偏低", "Low")
    case "normal": return tr("正常", "Normal")
    case "abnormal": return tr("异常", "Abnormal")
    default: return nil
    }
  }
}

/// Every reading of one test (`/labs/{key}/history`), for the trend chart.
struct LabHistory: Equatable {
  struct Reading: Equatable, Identifiable {
    var id: Int
    var date: String
    var value: Double?
    var valueText: String?
    var status: String?
    var homeDevice: Bool
    var fasting: Bool?
    var delta: Double?
  }

  var key: String
  var name: String
  var unit: String?
  var refLow: Double?
  var refHigh: Double?
  /// "down" / "up": which way is better (colours the change from the previous reading).
  var goodDirection: String?
  var rows: [Reading]

  static func parse(_ j: [String: Any]?) -> LabHistory? {
    guard let j, let key = j["test_key"] as? String else { return nil }
    let rows = ((j["rows"] as? [[String: Any]]) ?? []).enumerated().compactMap { n, r -> Reading? in
      guard let d = r["collected_on"] as? String else { return nil }
      return Reading(id: (r["id"] as? NSNumber)?.intValue ?? n, date: d, value: RecordFormat.double(r["value"]),
                     valueText: r["value_text"] as? String, status: r["status"] as? String,
                     homeDevice: (r["source"] as? String) == "home_device", fasting: r["fasting"] as? Bool,
                     delta: RecordFormat.double(r["delta"]))
    }
    return LabHistory(key: key, name: j["test_name"] as? String ?? key, unit: j["unit"] as? String,
                      refLow: RecordFormat.double(j["ref_low"]), refHigh: RecordFormat.double(j["ref_high"]),
                      goodDirection: j["good_direction"] as? String, rows: rows)
  }

  /// The change's colour: towards the good direction = good.
  func deltaTone(_ d: Double?) -> Tone {
    guard let d, d != 0, let g = goodDirection else { return .text }
    return (d < 0) == (g == "down") ? .good : .bad
  }
}

struct BodyEvent: Equatable, Identifiable {
  var id: Int
  var title: String
  var date: String
  var category: String?
  var bodyPart: String?
  var severity: Int?
  var resolved: String?

  static func parseList(_ list: [[String: Any]]?) -> [BodyEvent] {
    (list ?? []).compactMap { e in
      guard let id = (e["id"] as? NSNumber)?.intValue, let title = e["title"] as? String else { return nil }
      return BodyEvent(id: id, title: title, date: (e["local_date"] as? String) ?? String((e["occurred_at"] as? String ?? "").prefix(10)),
                       category: e["category"] as? String, bodyPart: e["body_part"] as? String,
                       severity: (e["severity"] as? NSNumber)?.intValue, resolved: e["resolved_on"] as? String)
    }
    .sorted { $0.date > $1.date }
  }

  static let categories = ["injury", "pain", "illness", "symptom", "medical", "other"]

  static func categoryTitle(_ c: String?) -> String {
    switch c {
    case "injury": return tr("受伤", "Injury")
    case "pain": return tr("疼痛", "Pain")
    case "illness": return tr("生病", "Illness")
    case "symptom": return tr("症状", "Symptom")
    case "medical": return tr("看医生", "Medical")
    default: return tr("其他", "Other")
    }
  }
}
