import CoreGraphics
import Foundation

/// The structured card that unfolds above the core when a flow ends (jarvis-cockpit.html `cardBody`): one graphic, a few
/// numbers, one or two actions. The full answer stays in the conversation (「展开对话 ›」).
enum CockpitReply: Equatable {
  case answer(Answer)
  case meal(Meal)
  case set(SetLog)

  /// The card's height before it unfolds (the prototype's CARDH).
  var height: CGFloat {
    switch self {
    case .answer(let a): return a.gauge == nil && a.readings.isEmpty ? 150 : 214
    case .meal: return 190
    case .set: return 160
    }
  }

  /// A question's verdict: the answer's first words, today's recovery as the gauge, three readings.
  struct Answer: Equatable {
    var question: String
    var verdict: String
    var sub: String?
    var gauge: Gauge?
    var readings: [Reading] = []
    /// One page the answer points to (from a card's 「open」 action).
    var primary: Primary?
  }

  struct Gauge: Equatable {
    var value: String
    var fraction: Double
    var tone: Tone
  }

  struct Reading: Equatable {
    var value: String
    var label: String
  }

  struct Primary: Equatable {
    var title: String
    var page: String
  }

  /// 「已记到午餐」: what was logged, its kcal and macros, and the day's kcal before and after.
  struct Meal: Equatable {
    var slot: String
    var name: String
    var kcal: Double?
    var macros: [Macro]
    var dayBefore: Double
    var dayAfter: Double
    var goal: Double?
  }

  struct Macro: Equatable {
    var label: String
    var grams: Double?
    /// Of the day's goal for this macro (nil = no goal: no bar).
    var fraction: Double?
  }

  /// 「已记到 上肢 A」: the exercise, the set, the exercise's sets as dots, the session's sets.
  struct SetLog: Equatable {
    var session: String
    var exercise: String
    var set: String
    var index: Int
    var planned: Int
    var done: Int
    var total: Int
  }
}

/// Pure helpers the controller uses (tested in JarvisCockpitTests).
enum CockpitText {
  /// The verdict and the line under it, from the AI's answer: the first bold run if the answer starts with one
  /// (「**可以练**，恢复在正常范围。」), otherwise the first clause; the rest of the first sentence is the sub line.
  static func verdict(from answer: String) -> (verdict: String, sub: String?) {
    let text = answer.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty else { return ("…", nil) }
    let firstLine = text.split(whereSeparator: \.isNewline).first.map(String.init) ?? text
    let enders: Set<Character> = ["。", "！", "？", ".", "!", "?", "；", ";"]
    let breaks: Set<Character> = ["，", ",", "：", ":", "、", "—"]
    var sentence = String(firstLine.prefix { !enders.contains($0) })
    if sentence.isEmpty { sentence = firstLine }
    var verdict: String
    var rest: String
    if sentence.hasPrefix("**"), let end = sentence.dropFirst(2).range(of: "**") {
      verdict = String(sentence[sentence.index(sentence.startIndex, offsetBy: 2)..<end.lowerBound])
      rest = String(sentence[end.upperBound...])
    } else {
      let head = sentence.prefix { !breaks.contains($0) }
      verdict = String(head)
      rest = String(sentence.dropFirst(head.count))
    }
    verdict = clean(verdict)
    rest = clean(String(rest.drop { breaks.contains($0) || $0 == " " }))
    if verdict.isEmpty { verdict = clean(sentence) }
    return (short(verdict, 12), rest.isEmpty ? nil : short(rest, 22))
  }

  /// Markdown marks out, spaces trimmed.
  static func clean(_ s: String) -> String {
    s.replacingOccurrences(of: "**", with: "").replacingOccurrences(of: "__", with: "").replacingOccurrences(of: "`", with: "")
      .trimmingCharacters(in: .whitespacesAndNewlines)
  }

  /// At most `n` characters, with an ellipsis when cut.
  static func short(_ s: String, _ n: Int) -> String {
    s.count > n ? String(s.prefix(n - 1)) + "…" : s
  }

  /// `60 kg × 8`, `45 s`, `× 12`.
  static func set(weightKg: Double?, reps: Int?, seconds: Double?) -> String {
    if let seconds, reps == nil { return "\(RecordFormat.num(seconds)) s" }
    switch (weightKg, reps) {
    case let (w?, r?): return "\(RecordFormat.num(w, 1)) kg × \(r)"
    case let (nil, r?): return "× \(r)"
    case let (w?, nil): return "\(RecordFormat.num(w, 1)) kg"
    default: return "✓"
    }
  }

  /// `990 / 2,300 kcal`.
  static func kcal(_ v: Double) -> String {
    let f = NumberFormatter()
    f.numberStyle = .decimal
    f.maximumFractionDigits = 0
    return f.string(from: NSNumber(value: v.rounded())) ?? "\(Int(v.rounded()))"
  }
}
