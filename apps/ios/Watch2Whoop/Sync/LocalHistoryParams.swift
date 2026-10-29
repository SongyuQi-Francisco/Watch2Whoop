import Foundation

/// How much raw data the phone keeps, how it downloads its history and how it recomputes it (#340 local history;
/// docs/ios-native-app.md section 9.1 「本机完整历史」). Change here; each value says where it comes from. None of these
/// are health thresholds.
///
/// The owner's direction (2026-10-29): move the server's work onto the phone; 200–300 MB of local storage per account is
/// fine. Measured with synthetic data (iPhone 17 simulator, `LocalHistoryMeasureTests`, figures in the doc): a dense
/// Apple Watch day (≈2,000 heart-rate samples, 8 beat-to-beat readings, a night of stages) takes ≈45 KB in the compact
/// history and ≈0.45 MB in the uuid-keyed `hk_samples` table, so keeping *all* history of every scoring kind and the
/// beats stays far below the budget: 1 year ≈ 16 MB, 2 years ≈ 31 MB, 3 years ≈ 47 MB, plus ≈26 MB for the 60 hot days.
/// Hence: nothing is dropped by age (`keepDays` all nil); a kind can be given a limit here if a later measurement says so.
enum LocalHistoryParams {
  // MARK: - what is kept

  /// The compact history's sample kinds (table `hist_samples`, column `kind`): the scores' raw inputs as the server merges
  /// them (body_engine scores/inputs.load; packages/openbodyscore-js inputs.KINDS). Codes are stored, never renumber.
  /// `steps` (2.0.0 M1, decision 3 of docs/ios-native-2.0.md): 15-minute step counts (t = the bucket's start), for stress
  /// (M2) and body age (M4) over the whole history; not a scoring input.
  enum Kind: Int, CaseIterable {
    case hr = 1, hrv = 2, resp = 3, spo2 = 4, temp = 5, steps = 6
    var name: String { ["", "hr", "hrv", "resp", "spo2", "temp", "steps"][rawValue] }
    init?(name: String) {
      guard let k = Kind.allCases.first(where: { $0.name == name }) else { return nil }
      self = k
    }
  }

  /// Days the uuid-keyed `hk_samples` keeps what the phone reads from HealthKit itself (deletions are matched by uuid
  /// there). Older days move into the compact history (`LocalRawStore.prune`). 60 = N2b's window: the 60-day baselines
  /// are recomputed from it in a seeded run while the history is not complete.
  static let hotDays = 60
  /// Days of the compact history kept per kind (nil = all of it). All nil: measured ≈16 MB a year for all kinds together
  /// (heart rate), well inside the 200–300 MB budget, and a full recompute needs every day back to the scoring origin.
  static let keepDays: [Kind: Int?] = [.hr: nil, .hrv: nil, .resp: nil, .spo2: nil, .temp: nil, .steps: nil]
  /// Sleep-stage segments of the history (`hist_segments`): all (≈30 a night, <2 KB).
  static let segmentKeepDays: Int? = nil
  /// Beat-to-beat readings with their beats (`heartbeat_series`): all. The beats (≈600 bytes a reading as JSON) let the
  /// phone recompute RMSSD itself if the HRV algorithm changes (body_engine rawdata/hrv.py, the bundle's analyzeHrv).
  static let beatsKeepDays: Int? = nil
  /// Per-day heart-rate histograms (`hr_day_hist`, HRmax window): all (0.4 KB a day); a chunked recompute needs the
  /// 365 days before each chunk.
  static let histogramKeepDays: Int? = nil

  // MARK: - downloading the history (stream raw_history, body_engine nativesync/history.py)

  /// BGProcessingTask identifier (Info.plist `BGTaskSchedulerPermittedIdentifiers`, project.yml).
  static let backgroundTask = "watch2whoop.history.download"
  /// The earliest a scheduled background run may start (iOS picks the moment: charging, idle, usually at night).
  static let backgroundDelay: TimeInterval = 15 * 60
  /// Retry-After waits the phone sits out inside a run; a longer one ends the run (the next background task resumes).
  static let maxInlineWait: TimeInterval = 30
  /// Pages per foreground run (≈14 days each): a few minutes of the server's throttle at most, the rest in the background.
  static let foregroundPages = 12
  /// After the download, how often changed old days are asked for (an export.zip import, a re-sync, a late OW import).
  static let refreshEvery: TimeInterval = 24 * 3600

  // MARK: - recomputing the whole history (a new algorithm bundle)

  /// Days per JavaScriptCore call of a chunked recompute. Each chunk is seeded with the rows the previous one wrote (the
  /// bundle's seeded run, engine.js), so memory follows the chunk, not the history. Measured over three dense years:
  /// 7 / 14 / 30-day chunks peak at ≈+113 / +136 / +176 MB, flat over the history; one call over 180 days is ≈+490 MB.
  /// 14 is the trade-off between time (≈12 s for three years in the simulator) and memory.
  static let scoreChunkDays = 14
  /// Days per muscle-load request (`GET /v1/sync/scoring?from=&to=`; the server allows MUSCLE_MAX_DAYS = 120).
  static let muscleWindowDays = 120
  /// Days per `PUT /v1/users/me/scores/app` (the server takes ≤ 400, nativesync/params.UPLOAD_MAX_DAYS); newest batch first
  /// so the server's spot check (recent days only) sees the new bundle on the first upload.
  static let uploadBatchDays = 120
  /// A foreground scoring run stops starting new chunks after this long (a normal run is one chunk, well under a second);
  /// the rest of a full recompute waits for the background task, on power.
  static let foregroundScoringSeconds: TimeInterval = 20

  // MARK: - storage hygiene

  /// `PRAGMA incremental_vacuum` after pruning / a cache clear; a full `VACUUM` (defragments, needs ~2× the file in free
  /// space) at most this often, only in a background task on power, and only when this share of pages is free.
  static let vacuumEvery: TimeInterval = 7 * 24 * 3600
  static let vacuumFreeShare = 0.2
}
