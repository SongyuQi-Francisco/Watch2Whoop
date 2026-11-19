#if DEBUG
import Foundation

/// Synthetic data for the simulator (Debug builds only): the frozen screenshots (`W2W_WATCH_DEMO`) and the interactive
/// seed (`W2W_WATCH_SEED`). Nickname 小林; every number is made up and dated the day it is built, so it can never be
/// yesterday's (the 2026-11-18 acceptance run broke on a plan saved with a fixed date).
enum WatchDemoData {
  static func plan(day: String) -> WatchPlan {
    func ex(_ slug: String, _ name: String, _ sets: Int, _ kg: Double?, _ reps: Int, _ last: String) -> WatchExercise {
      WatchExercise(slug: slug, name: name, sets: sets, done: 0, reps: reps, weightKg: kg, durationS: nil, weighted: kg != nil,
                    restSec: 90, weightStepKg: 2.5, last: Array(repeating: last, count: sets))
    }
    return WatchPlan(day: day, title: tr("上肢 A", "Upper A"), exercises: [
      ex("bench", tr("卧推", "Bench"), 4, 60, 8, "60×7"), ex("ohp", tr("肩推", "OH press"), 3, 25, 10, "22.5×10"),
      ex("row", tr("划船", "Row"), 4, 60, 10, "57.5×10"), ex("pulldown", tr("下拉", "Pulldown"), 3, 50, 10, "50×10"),
      ex("curl", tr("弯举", "Curl"), 2, 12, 12, "12×12"), ex("pushdown", tr("下压", "Pushdown"), 2, 20, 12, "20×13"),
    ])
  }

  static let meals = [
    WatchQuickMeal(id: 1, name: tr("燕麦碗", "Oat bowl"), slot: tr("早餐", "Breakfast"), kcal: 420, proteinG: 18),
    WatchQuickMeal(id: 3, name: tr("鸡胸饭", "Chicken rice"), slot: tr("午餐", "Lunch"), kcal: 570, proteinG: 42),
    WatchQuickMeal(id: 7, name: tr("酸奶", "Yogurt"), slot: tr("加餐", "Snacks"), kcal: 150, proteinG: 9),
  ]

  /// D1 (training day, recovery 82), D2 (`rest`: no training, 54), S9 (`planMissing`: the phone's last plan is
  /// yesterday's), S3 (`stale`: the newest data is more than six hours old).
  static func context(day: String, sentAt t: Date, rest: Bool = false, planMissing: Bool = false, stale: Bool = false,
                      lang: String) -> WatchContext {
    let yesterday = WatchModel.day(t.addingTimeInterval(-86_400))
    let today = WatchToday(day: day, recovery: rest ? 54 : 82, tone: rest ? .mid : .good, usualLo: 58, usualHi: 74,
                           verdict: rest ? tr("正常练", "Train normally") : tr("可上强度", "Push hard"), reason: nil,
                           sleep: 88, strain: 2.8, computedAt: t,
                           dataAt: t.addingTimeInterval(stale ? -(6 * 3600 + 60) : -120), phone: WatchPhoneSync(.ok))
    let quick = WatchQuick(day: day, meals: meals, waterMl: WatchParams.waterQuickMl, kcal: 610, kcalGoal: 2300)
    let plan: WatchPlan? = rest ? nil : planMissing ? Self.plan(day: yesterday) : Self.plan(day: day)
    return WatchContext(signedIn: true, plan: plan, lang: lang, sentAt: t, planFor: planMissing ? yesterday : day,
                        today: today, quick: quick)
  }
}

/// The seed's token, in memory only (never the Keychain).
final class SeedToken {
  var token: String?
}

/// The synthetic phone's account for voice (the seed and the frozen screens).
extension WatchDemoData {
  static let account = "seed"

  static func voiceOffer(on: Bool = true) -> WatchVoiceOffer {
    WatchVoiceOffer(on: on, why: on ? nil : .mode, account: account)
  }

  /// The canned meal (M3): 小林's lunch, said by voice; the day's intake before it is the seed's 610 kcal.
  static func voiceMeal(day: String) -> WatchVoiceMeal {
    WatchVoiceMeal(writes: [WatchVoiceWrite(tool: "log_food", date: day, logId: nil, entryIds: ["seed-1", "seed-2"], meal: "lunch",
                                            foods: [tr("牛肉面", "Beef noodles"), tr("鸡蛋", "Egg")], calories: 620, proteinG: 34)],
                   dayKcal: 1230, goalKcal: 2300)
  }

  static let questions = [tr("今天适合练腿吗", "Good day for legs?"), tr("这周练得怎么样", "How was my week?"),
                          tr("撤销刚才那餐", "Undo that meal")]
  static let mealWords = tr("午饭一碗牛肉面加一个蛋", "Lunch: beef noodles and an egg")
  static let answer = tr("可以练\n恢复 82，睡眠 88。", "Yes, train\nRecovery 82, sleep 88.")
  static let longAnswer = tr("4 / 5 天\n负荷比平常高一点。详细的在手机上看", "4 of 5 days\nLoad a bit above usual. More on your phone")
  static let undoSummary = tr("删除午餐：牛肉面 + 鸡蛋（620 kcal）", "Delete lunch: beef noodles + egg (620 kcal)")
}

/// A synthetic server for the voice flow in the simulator (no network, no microphone): the holds go through a fixed
/// script, so every screen can be tapped: a meal (M2 → M3), a question (J2 → J3), a long answer (J4), 「undo」 (M4).
/// `W2W_WATCH_VOICE` picks one script, or an error: `limit`, `offline`, `quota`, `noai`, `consent`, `cutoff` (the stream
/// drops and is resumed), `server`.
final class WatchSeedVoice: WatchVoiceBackend {
  private var turn = 0
  private let fixed: String?
  private let day: () -> String
  private var current = "meal"

  init(script: String?, day: @escaping () -> String) {
    fixed = script
    self.day = day
  }

  private static let order = ["meal", "ask", "more", "undo"]

  private func wait(_ s: Double) async { try? await Task.sleep(nanoseconds: UInt64(s * 1_000_000_000)) }

  func redeem(_ code: WatchPairCode) async throws -> String {
    await wait(0.4)
    return "seed-token"
  }

  func transcribe(_ wav: Data) async throws -> String {
    current = fixed ?? Self.order[turn % Self.order.count]
    turn += 1
    await wait(0.7)
    switch current {
    case "limit": throw WatchVoiceError.http(429, "rate_limit")
    case "offline": throw WatchVoiceError.network
    case "server": throw WatchVoiceError.http(503, "asr_down")
    case "meal", "quota", "cutoff": return WatchDemoData.mealWords
    case "more": return WatchDemoData.questions[1]
    case "undo": return WatchDemoData.questions[2]
    default: return WatchDemoData.questions[0]
    }
  }

  private static func ev(_ id: Int?, _ name: String, _ j: [String: Any]) -> WatchSSEEvent {
    WatchSSEEvent(id: id, event: name, data: (try? JSONSerialization.data(withJSONObject: j)) ?? Data())
  }

  private func play(_ events: [WatchSSEEvent], breakAfter: Int? = nil, refuse: WatchVoiceError? = nil)
    -> AsyncThrowingStream<WatchSSEEvent, Error> {
    AsyncThrowingStream { cont in
      Task {
        await self.wait(0.5)
        if let refuse { cont.finish(throwing: refuse); return }
        for (i, e) in events.enumerated() {
          if let b = breakAfter, i == b { cont.finish(throwing: WatchVoiceError.network); return }
          cont.yield(e)
          await self.wait(0.25)
        }
        cont.finish()
      }
    }
  }

  private func mealEvents() -> [WatchSSEEvent] {
    let m = WatchDemoData.voiceMeal(day: day())
    let w = m.writes[0]
    return [Self.ev(1, "conversation", ["id": 101]), Self.ev(2, "tool", ["name": "log_food", "status": "running"]),
            Self.ev(3, "tool", ["name": "log_food", "status": "done"]),
            Self.ev(4, "card", ["call_id": "seed", "card": ["type": "meal", "totals": ["calories": m.dayKcal ?? 0], "goal": ["calories": m.goalKcal ?? 0],
                                                          "written": [["tool": w.tool ?? "", "date": w.date, "entry_ids": w.entryIds, "meal": w.meal ?? "",
                                                                       "foods": w.foods, "calories": w.calories ?? 0, "protein_g": w.proteinG ?? 0]]]]),
            Self.ev(5, "text", ["delta": tr("记到午餐：牛肉面 + 蛋，620 kcal", "Logged to lunch: noodles + egg, 620 kcal")]),
            Self.ev(6, "done", ["conversation_id": 101])]
  }

  private func textEvents(_ text: String, from: Int = 1) -> [WatchSSEEvent] {
    [Self.ev(from, "conversation", ["id": 101]), Self.ev(from + 1, "text", ["delta": text]), Self.ev(from + 2, "done", ["conversation_id": 101])]
  }

  func chat(_ text: String, conversation: Int?) -> AsyncThrowingStream<WatchSSEEvent, Error> {
    switch current {
    case "quota": return play([], refuse: .http(402, "quota"))
    case "noai": return play([], refuse: .http(409, "subscription"))
    case "consent": return play([], refuse: .http(403, "fallback_consent"))
    case "cutoff": return play(mealEvents(), breakAfter: 2)
    case "meal": return play(mealEvents())
    case "more": return play(textEvents(WatchDemoData.longAnswer))
    case "undo":
      return play([Self.ev(1, "conversation", ["id": 101]),
                   Self.ev(2, "confirm", ["conversation_id": 101, "calls": [["call_id": "seed-call", "tool": "delete_food_entries",
                                                                             "args": [:], "summary": WatchDemoData.undoSummary]]])])
    default: return play(textEvents(WatchDemoData.answer))
    }
  }

  func approve(callId: String, approve: Bool) -> AsyncThrowingStream<WatchSSEEvent, Error> {
    play(textEvents(approve ? tr("已撤销午餐，620 kcal", "Lunch undone, 620 kcal") : tr("好，保留", "Kept"), from: 3))
  }

  func events(conversation: Int, after: Int) -> AsyncThrowingStream<WatchSSEEvent, Error> {
    play(mealEvents().filter { ($0.id ?? 0) > after })
  }
}

/// A microphone that hears nothing but moves the ring like a voice (simulators have no usable microphone here).
final class WatchSeedRecorder: WatchVoiceRecorder {
  private var started: Date?

  func start() async -> Bool {
    started = Date()
    return true
  }

  var elapsed: Double { started.map { Date().timeIntervalSince($0) } ?? 0 }

  func level() -> Double {
    let t = elapsed
    return t < 0.3 ? 0 : 0.35 + 0.65 * abs(sin(t * 4.8) * sin(t * 1.9 + 1))
  }

  func stop(keep: Bool) -> Data? {
    defer { started = nil }
    guard keep else { return nil }
    let n = Int(max(elapsed, 0) * Double(WatchParams.voiceSampleRate))
    return WatchWAV.make(samples: (0..<n).map { Int16(truncatingIfNeeded: ($0 % 64) * 64 - 2048) })
  }
}
#endif

