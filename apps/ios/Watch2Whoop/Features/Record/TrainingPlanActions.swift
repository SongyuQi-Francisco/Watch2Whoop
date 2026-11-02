import Foundation

/// The training page's online actions: the exercise library, plan edits, custom exercises and training settings. Unlike a
/// set or a journal answer these are not queued: they change the plan the server checks banned exercises against, so they
/// need its answer. Offline they say so, in the words the other pages use, and nothing changes.
extension TrainingStore {
  /// What to show for a failed action.
  static func message(_ e: Error) -> String {
    if case APIError.network = e { return tr("离线，连上网络后重试", "Offline, retry when connected") }
    return (e as? APIError)?.userMessage ?? tr("出错了", "Something went wrong")
  }

  private func call(_ method: APIRequest.Method, _ path: String, _ body: [String: Any]? = nil) async throws -> [String: Any] {
    let data = try body.map { try JSONSerialization.data(withJSONObject: $0) }
    // Sent once: the outcome of a plan edit decides what the page shows next, a retry belongs to the user.
    return try await api.json(APIRequest(method: method, path: path, body: data, idempotent: false))
  }

  /// A read that keeps its last answer for the next time there is no connection.
  private func cached(_ key: String, _ request: APIRequest) async -> (json: [String: Any]?, offline: Bool) {
    let out = await DocPuller(api: api, db: db).pull([DocFetch(key, request)])
    return ((try? db.doc(key))?.json, out.offline)
  }

  /// The plan changed: the day, the week and the muscle map follow.
  func reloadPlan() async {
    var f = [DocFetch(RecordDocs.session(today), RecordAPI.session(today)), DocFetch(RecordDocs.week(monday), RecordAPI.week(today))]
    if muscleOn { f.append(DocFetch(RecordDocs.muscle, RecordAPI.muscle)) }
    await pull(f)
  }

  // MARK: reads

  func searchLibrary(query: String, group: String, equipment: String) async throws -> [LibraryExercise] {
    LibraryExercise.list(try await api.json(ExerciseAPI.search(query: query, group: group, equipment: equipment)))
  }

  func exerciseDetail(_ slug: String) async -> (detail: ExerciseDetail?, offline: Bool) {
    let out = await cached(ExerciseDocs.exercise(slug), ExerciseAPI.exercise(slug))
    return (ExerciseDetail.parse(out.json), out.offline)
  }

  func muscleDetail(_ key: String) async -> (detail: MuscleDetail?, offline: Bool) {
    let out = await cached(ExerciseDocs.muscle(key), ExerciseAPI.muscle(key))
    return (MuscleDetail.parse(out.json), out.offline)
  }

  func addOptions(_ slug: String) async throws -> AddPlanOptions? {
    AddPlanOptions.parse(try await api.json(ExerciseAPI.addOptions(slug)))
  }

  func customExercises() async throws -> CustomExerciseList? {
    CustomExerciseList.parse(try await api.json(.get(ExerciseAPI.customExercises)))
  }

  func trainingGoals() async throws -> TrainingGoals? {
    TrainingGoals.parse(try await api.json(.get(ExerciseAPI.goals)))
  }

  // MARK: plan edits (nil = done, else what to show)

  func removeForDay(_ t: RemoveTarget) async -> String? {
    await perform(.post, ExerciseAPI.removeExercise, ["exercise": t.slug, "scope": "day", "day": t.date])
  }

  /// Out of the session template, every week (the page asked twice; the server never empties a session).
  func removeFromTemplate(_ t: RemoveTarget) async -> String? {
    var body: [String: Any] = ["exercise": t.slug, "scope": "template", "confirm": true]
    if let d = t.dayOfWeek { body["day_of_week"] = d }
    if let n = t.sessionName { body["session_name"] = n }
    return await perform(.post, ExerciseAPI.removeExercise, body)
  }

  func restore(_ slug: String, day: String) async -> String? {
    await perform(.post, ExerciseAPI.restoreExercise, ["exercise": slug, "day": day])
  }

  func addToPlan(_ body: [String: Any]) async -> String? {
    await perform(.post, ExerciseAPI.addExercise, body)
  }

  private func perform(_ method: APIRequest.Method, _ path: String, _ body: [String: Any]) async -> String? {
    await flushDraft()
    do {
      _ = try await call(method, path, body)
      await reloadPlan()
      return nil
    } catch {
      return Self.message(error)
    }
  }

  /// Activates a built-in split; the server swaps banned exercises out (`auto_swap_banned`). The message to show either way.
  func activate(_ split: SplitOption) async -> (ok: Bool, message: String) {
    await flushDraft()
    do {
      let res = try await call(.put, ExerciseAPI.plan, ["split_type": split.type, "auto_swap_banned": true, "sync_sparky": true])
      await reloadPlan()
      return (true, SplitOption.enabledMessage(res, shown: split.name))
    } catch {
      return (false, Self.message(error))
    }
  }

  // MARK: custom exercises

  func saveCustom(id: Int?, body: [String: Any]) async -> String? {
    do {
      _ = try await call(id == nil ? .post : .patch, id.map(ExerciseAPI.customExercise) ?? ExerciseAPI.customExercises, body)
      await reloadPlan()   // a logged name may match now
      return nil
    } catch {
      return Self.message(error)
    }
  }

  func deleteCustom(_ id: Int) async -> String? {
    do {
      _ = try await call(.delete, ExerciseAPI.customExercise(id))
      await reloadPlan()
      return nil
    } catch {
      return Self.message(error)
    }
  }

  // MARK: settings

  func saveGoals(focus: [String], goal: String) async -> String? {
    let text = goal.trimmingCharacters(in: .whitespacesAndNewlines)
    do {
      _ = try await call(.put, ExerciseAPI.goals, ["focus_muscles": focus, "physique_goal": text.isEmpty ? NSNull() : text])
      return nil
    } catch {
      return Self.message(error)
    }
  }

  /// Self-service connect of the training log (`POST /training/sparky/connect`).
  func connectTrainingLog() async -> (link: TrainingLogLink?, error: String?) {
    do {
      let res = try await call(.post, ExerciseAPI.connectLog)
      let link = TrainingLogLink(res)
      if link?.connected == true { await reloadPlan() }
      return (link, nil)
    } catch {
      return (nil, Self.message(error))
    }
  }
}
