import Foundation

/// Tunables of the watch app (docs/watch-app-plan.md sections 2.1, 2.2, 2.4, 2.6). Kept in one place so they can be
/// changed without touching the screens; none of them is a health threshold (the watch computes no score: recovery
/// zones, verdicts and the stale rule of the phone's own data come from the phone).
enum WatchParams {
  /// Rest between sets when the plan gives none: the server's own default (body_engine training/params.py
  /// `EST_DEFAULT_REST_SEC` / `ADD_DEFAULT_REST_SEC` = 90 s), so the watch and the plan card agree.
  static let defaultRestSec = 90
  /// Digital Crown step on the rest countdown (plan 2.1, input table: 「休息中：休息时间 ±15 秒」).
  static let restStepSec = 15
  /// Rest never goes below this (a 0 s rest would skip the rest screen without a tap).
  static let minRestSec = 15
  /// Rest never goes above the server's limit for `rest_sec` (training/api.py `le=600`).
  static let maxRestSec = 600
  /// Weight step of one crown detent when the exercise gives none: the smallest plate pair in most gyms
  /// (2 × 1.25 kg; plan 2.1 「每格 2.5 kg」).
  static let defaultWeightStepKg = 2.5
  /// How long the 「记好了」 receipt stays before the rest screen (mockup T5: 1.2 s).
  static let receiptSec: TimeInterval = 1.2
  /// Reps the crown can reach (a set of more than 100 reps is a typing error, not training).
  static let maxReps = 100
  /// Heaviest weight the crown can reach (the server accepts any; this only stops runaway scrolling).
  static let maxWeightKg = 500.0
  /// After this long without hearing from the phone the 「已连接」 mark shows the age in hours instead of minutes.
  static let ageHoursAfterMin = 60
  /// Event ids the phone remembers to drop duplicates (WatchConnectivity may deliver a transfer twice after a restart).
  static let phoneSeenIdsKept = 2000

  /// The phone has not been heard from for this long: its numbers are drawn grey with their age (mockup S3), the same
  /// limit as the phone's own capsule (TodayParams.syncStaleMinutes = 360 min: the design's 「6时前」 example and two
  /// missed rounds of the Shortcut's daytime runs).
  static let staleAfterMin = 360
  /// The phone's 「同步中」 is shown only while the context saying so is this fresh; an older one says nothing about now.
  static let phoneBusyFreshSec: TimeInterval = 120
  /// Quick meals on the quick-log screen: 3 + the water tile = the mockup's four tiles, no scrolling (plan 2.2).
  static let quickMealsMax = 3
  /// The water tile's amount when the phone gives none: the server's first quick button (body_engine
  /// water/params.py `QUICK_ML` = (250, 500)), the same as the phone's water card.
  static let waterQuickMl = 250
  /// Ends of the two readings' bars on the home screen: sleep score 0–100, strain 0–21 (the phone's TodayGlance gauges).
  static let sleepMax = 100.0
  static let strainMax = 21.0
  /// Quick logs the watch remembers for the receipt and undo: the ones of the last day only.
  static let quickLogsKeptHours: TimeInterval = 36
  /// Quick-log results the phone sends back (the newest; a day's logs never come near this).
  static let phoneResultsKept = 40
  /// A saved session older than this is not resumed on launch but ended (its sets were already handed to the phone):
  /// a strength session longer than this is a session someone forgot to end.
  static let resumeWithinHours: TimeInterval = 6

  // MARK: voice (plan 2.2 / 2.3 / 4.3 / Q6; backend #458)

  /// How long the finger stays on the core before a hold becomes talking (shorter = a tap: quick log). The phase-2a hold
  /// used 0.5 s; slightly shorter so talking starts as soon as the finger rests (the mockup film: the core moves at once).
  static let voiceHoldSec: TimeInterval = 0.4
  /// Shortest recording sent: the transcriber refuses less than 0.3 s (#458 B, asr `too_short`); a little more so a
  /// slip of the finger is not a request that only costs one of the day's voice messages.
  static let voiceMinSec: TimeInterval = 0.5
  /// Longest recording: the plan's limit (4.3: 「≤ 30 秒」, ≈ 0.96 MB at 16 kHz), half the server's 60 s.
  static let voiceMaxSec: TimeInterval = 30
  /// The transcriber's advised format (#458 B): 16 kHz, mono, 16-bit integer PCM.
  static let voiceSampleRate = 16_000
  /// M2: the words stay this long, cancellable, then go (plan 2.2 「核心外面一圈 1.5 秒走完就自动发送」, Q6).
  static let voiceSendAfterSec: TimeInterval = 1.5
  /// Slide up this far while listening = cancel (M1 「上滑取消」), points.
  static let voiceCancelSlidePt: CGFloat = 30
  /// A new question within this long of the last answer continues that conversation (plan 2.3: context by
  /// `conversation_id`; 「撤销刚才那餐」 must reach the conversation that logged it).
  static let voiceFollowUpSec: TimeInterval = 300
  /// A dropped stream is picked up again (`/chat/{id}/events?after=`) this many times before 「连接断了」 (the phone's
  /// JarvisChat.resume uses the same three).
  static let voiceResumeTries = 3
  /// Wait before each resume (the phone's 1.5 s).
  static let voiceResumeDelaySec: TimeInterval = 1.5
  /// After a request failed for lack of network, the core stays dark this long before voice is offered again (watchOS
  /// has no reliable network monitor for apps; the next request is the probe).
  static let voiceOfflineSec: TimeInterval = 30
  /// The watch asks the phone for a new code at most this often while it has no token.
  static let pairAskEverySec: TimeInterval = 60
  /// A code this close to its expiry is not redeemed (a late transfer): a new one is asked for instead.
  static let pairCodeMarginSec: TimeInterval = 30
  /// Request timeouts: the transcriber (30 s of audio over Bluetooth) and the chat stream (a turn with tools).
  static let transcribeTimeoutSec: TimeInterval = 30
  static let chatTimeoutSec: TimeInterval = 120
  /// J3: a headline longer than this is cut at its first sentence (the server is asked for ≤ 12 characters first,
  /// ai/params.py `WATCH_HEADLINE_CHARS_ZH`).
  static let answerHeadlineMax = 16
  /// Answers longer than this get 「在手机上继续」 (the server's limit is 40 characters, ai/params.py `WATCH_MAX_CHARS_ZH`;
  /// some room for the English words).
  static let answerMaxChars = 90
  /// The server's 「详细的在手机上看」 / "More on your phone" (ai/watch.py PROMPT), lower-cased: such an answer offers
  /// 「在手机上继续」.
  static let phoneHints = ["\u{8BE6}\u{7EC6}\u{7684}\u{5728}\u{624B}\u{673A}\u{4E0A}\u{770B}", "more on your phone"]

  /// Longest the app stays up when the phone wakes it in the background to deliver a context (it ends as soon as nothing
  /// is pending). watchOS gives a background task a few seconds and suspends the app when it runs over; Apple's
  /// WKWatchConnectivityRefreshBackgroundTask guidance: complete the task once `hasContentPending` is false.
  static let backgroundLinkSec: TimeInterval = 8
}
