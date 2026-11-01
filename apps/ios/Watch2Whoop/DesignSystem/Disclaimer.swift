import SwiftUI

/// A short label with an info mark that opens the longer text in a sheet (App Store guidelines 1.4.1 and 5.1.1: health
/// estimates and AI answers say what they are). The label stays a few words (UI principle: numbers, charts and short
/// labels); the sentences live only in the sheet.
/// - `.ai`: under Jarvis answers — 「AI 生成」 / "AI-generated".
/// - `.medical`: next to estimates such as illness risk — 「非诊断」 / "Not a diagnosis".
struct DisclaimerTag: View {
  enum Kind { case ai, medical }

  let kind: Kind
  @State private var shown = false

  var label: String {
    switch kind {
    case .ai: return tr("AI 生成", "AI-generated")
    case .medical: return tr("非诊断", "Not a diagnosis")
    }
  }

  var body: some View {
    Button { shown = true } label: {
      HStack(spacing: 3) {
        Text(label).font(Typo.label)
        Icon(.info, size: 11)
      }
      .foregroundColor(Palette.text3)
      .padding(.vertical, 2)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityLabel(label)
    .accessibilityHint(tr("打开说明", "Opens the explanation"))
    .w2wSheet(isPresented: $shown, title: tr("不是医疗建议", "Not medical advice")) { DisclaimerBody(kind: kind) }
  }
}

/// The sheet's text: a few short points, AI first when it is about an answer.
struct DisclaimerBody: View {
  let kind: DisclaimerTag.Kind

  static func points(_ kind: DisclaimerTag.Kind) -> [String] {
    var out: [String] = []
    if kind == .ai {
      out.append(tr("这段回答由 AI 生成，可能出错。AI 读你的数据来回答，但它不是医生。", "This answer was written by an AI and may be wrong. It reads your data to answer, but it is not a doctor."))
    }
    out.append(tr("分数和风险提示由手表数据按公开研究的方法估算，只供参考，不能诊断或排除任何疾病。", "Scores and risk hints are estimates from watch data, made with methods from published research. They are for reference only and cannot diagnose or rule out any illness."))
    out.append(tr("身体不适、症状加重或担心健康时，请咨询医生。紧急情况请立即拨打当地急救电话。", "If you feel unwell, your symptoms get worse or you are worried about your health, see a doctor. In an emergency, call your local emergency number at once."))
    return out
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      ForEach(Array(Self.points(kind).enumerated()), id: \.offset) { _, p in
        HStack(alignment: .firstTextBaseline, spacing: 8) {
          Circle().fill(Palette.text3).frame(width: 5, height: 5).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 1 }
          Text(p).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
        }
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}
