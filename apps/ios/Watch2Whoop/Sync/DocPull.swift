import Foundation

/// One server view a page keeps as a document: the GET request and the `docs` key it is stored under
/// (docs/ios-native-app.md section 5.2, "N0: pages add their own scopes and store the answer in `docs`").
struct DocFetch: Equatable {
  let key: String
  let request: APIRequest

  init(_ key: String, _ request: APIRequest) {
    self.key = key
    self.request = request
  }

  static func == (a: DocFetch, b: DocFetch) -> Bool {
    a.key == b.key && a.request.path == b.request.path && a.request.query == b.request.query
  }
}

/// Pulls several documents at once (in parallel) into the local database, for the views a page shows next to its main
/// document (N3: the strain target, health monitor, status cards … beside the today document). Until N2's
/// `/v1/sync/pull` carries them, each is the existing GET endpoint stored as it came. A failed fetch keeps the cached
/// copy; 403 / 404 (no share, a feature that is off for this user) remove it, so the page hides that block.
struct DocPuller {
  let api: APIClient
  let db: AppDatabase

  struct Outcome: Equatable {
    var saved: [String] = []
    var removed: [String] = []
    /// No connection: the cached copies stay on screen.
    var offline = false
  }

  func pull(_ fetches: [DocFetch]) async -> Outcome {
    let results = await withTaskGroup(of: (String, Result<Data, APIError>).self) { group in
      for f in fetches {
        group.addTask {
          do { return (f.key, .success(try await api.data(f.request))) }
          catch let e as APIError { return (f.key, .failure(e)) }
          catch { return (f.key, .failure(.network(-1))) }
        }
      }
      var out: [(String, Result<Data, APIError>)] = []
      for await r in group { out.append(r) }
      return out
    }
    var outcome = Outcome()
    for (key, result) in results {
      switch result {
      case .success(let data):
        if (try? db.saveDoc(key, json: data)) != nil { outcome.saved.append(key) }
      case .failure(.notFound), .failure(.forbidden):
        if (try? db.deleteDoc(key)) != nil { outcome.removed.append(key) }
      case .failure(.network):
        outcome.offline = true
      case .failure:
        break
      }
    }
    return outcome
  }
}

extension AppDatabase {
  func deleteDoc(_ key: String) throws {
    _ = try writer.write { db in try DocRecord.deleteOne(db, key: key) }
  }
}
