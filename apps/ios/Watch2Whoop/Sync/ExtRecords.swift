import Foundation
import GRDB

/// The training / weight / food log as raw rows, one `docs` entry per day (`ext_records:<date>`; docs/ios-native-2.0.md
/// section 2.2, decision 2): the strength sets, weigh-ins and nutrition day totals the phone's packages compute from
/// (muscle load, body-age strength minutes, reports). Pull only: the phone never writes these documents itself.
///
/// A pull asks for the day fingerprints of a range first (`?fingerprints=1`) and fetches only the days whose fingerprint
/// changed; a day the server no longer lists is removed. When the log cannot be read right now (`available` false, or no
/// connection) the cached days stay.
extension DocPuller {
  static func extRecordsKey(_ day: String) -> String { "ext_records:\(day)" }

  struct ExtOutcome: Equatable {
    var fetched: [String] = []
    var removed: [String] = []
    var unchanged = 0
    /// No connection: nothing changed here.
    var offline = false
    /// The server cannot read the log now (or has no such endpoint): nothing changed here.
    var unavailable = false
  }

  func pullExtRecords(from: String, to: String) async -> ExtOutcome {
    var out = ExtOutcome()
    let listed: [String: String]
    do {
      let o = try await api.json(Endpoint.extRecords(from: from, to: to, fingerprintsOnly: true))
      guard o["available"] as? Bool != false, let fp = o["fingerprints"] as? [String: String] else {
        out.unavailable = true
        return out
      }
      listed = fp
    } catch APIError.network {
      out.offline = true
      return out
    } catch {
      out.unavailable = true
      return out
    }
    let local = (try? db.extFingerprints(from: from, to: to)) ?? [:]
    let changed = listed.filter { local[$0.key] != $0.value }.keys.sorted()
    out.unchanged = listed.count - changed.count
    if let lo = changed.first, let hi = changed.last {
      do {
        let o = try await api.json(Endpoint.extRecords(from: lo, to: hi))
        let want = Set(changed)
        for day in (o["days"] as? [[String: Any]]) ?? [] {
          guard let d = day["date"] as? String, want.contains(d),
                let data = try? JSONSerialization.data(withJSONObject: day, options: [.sortedKeys]) else { continue }
          if (try? db.saveDoc(Self.extRecordsKey(d), json: data)) != nil { out.fetched.append(d) }
        }
      } catch APIError.network {
        out.offline = true
      } catch {
        out.unavailable = true
      }
    }
    for d in local.keys.sorted() where listed[d] == nil {
      if (try? db.deleteDoc(Self.extRecordsKey(d))) != nil { out.removed.append(d) }
    }
    return out
  }
}

extension AppDatabase {
  /// {date: fingerprint} of the cached `ext_records` days in [from, to].
  func extFingerprints(from: String, to: String) throws -> [String: String] {
    try writer.read { db in
      let rows = try DocRecord.fetchAll(db, sql: "SELECT * FROM docs WHERE key >= ? AND key <= ?",
                                        arguments: [DocPuller.extRecordsKey(from), DocPuller.extRecordsKey(to)])
      var out: [String: String] = [:]
      for r in rows {
        let day = String(r.key.dropFirst("ext_records:".count))
        out[day] = (r.json?["fingerprint"] as? String) ?? ""
      }
      return out
    }
  }

  /// One cached day of the log (nil = no rows that day, or not pulled yet).
  func extRecords(_ day: String) -> [String: Any]? {
    (try? doc(DocPuller.extRecordsKey(day)))?.json
  }
}
