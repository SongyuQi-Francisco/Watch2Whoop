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
#endif
