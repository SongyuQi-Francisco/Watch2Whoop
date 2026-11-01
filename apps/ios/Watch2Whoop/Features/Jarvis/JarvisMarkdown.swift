import SwiftUI

/// The Markdown the AI answers with, drawn natively: headings, paragraphs, bullet and numbered lists, quotes, code blocks,
/// tables and rules as blocks; bold, italic, code and links inside a line through `AttributedString(markdown:)`.
/// Never HTML. Parsing is cheap, so a streaming answer is parsed again on every repaint (at most 30 a second).
enum MDBlock: Equatable {
  case heading(Int, String)
  case paragraph(String)
  case bullet(String, level: Int)
  case numbered(String, String)
  case quote(String)
  case code(String)
  case table([[String]])
  case rule

  static func parse(_ text: String) -> [MDBlock] {
    var out: [MDBlock] = []
    var para: [String] = []
    var code: [String]?
    var table: [[String]] = []
    func endPara() { if !para.isEmpty { out.append(.paragraph(para.joined(separator: "\n"))); para = [] } }
    func endTable() { if !table.isEmpty { out.append(.table(table)); table = [] } }

    for raw in text.components(separatedBy: "\n") {
      if code != nil {
        if raw.trimmingCharacters(in: .whitespaces).hasPrefix("```") { out.append(.code(code!.joined(separator: "\n"))); code = nil }
        else { code!.append(raw) }
        continue
      }
      let line = raw.trimmingCharacters(in: .whitespaces)
      if line.hasPrefix("```") { endPara(); endTable(); code = []; continue }
      if line.hasPrefix("|") {
        endPara()
        let cells = line.trimmingCharacters(in: CharacterSet(charactersIn: "|")).components(separatedBy: "|")
          .map { $0.trimmingCharacters(in: .whitespaces) }
        if !cells.allSatisfy({ !$0.isEmpty && $0.allSatisfy { "-:".contains($0) } }) { table.append(cells) }
        continue
      }
      endTable()
      if line.isEmpty { endPara(); continue }
      if line == "---" || line == "***" || line == "___" { endPara(); out.append(.rule); continue }
      if let h = heading(line) { endPara(); out.append(h); continue }
      let indent = raw.prefix { $0 == " " }.count
      if let m = line.firstMatch(of: ["- ", "* ", "• ", "· "]) {
        endPara(); out.append(.bullet(String(line.dropFirst(m.count)), level: indent >= 2 ? 1 : 0)); continue
      }
      if let (n, rest) = numbered(line) { endPara(); out.append(.numbered(n, rest)); continue }
      if line.hasPrefix(">") { endPara(); out.append(.quote(line.drop { $0 == ">" || $0 == " " }.description)); continue }
      para.append(line)
    }
    if let code { out.append(.code(code.joined(separator: "\n"))) }
    endPara()
    endTable()
    return out
  }

  private static func heading(_ l: String) -> MDBlock? {
    let n = l.prefix { $0 == "#" }.count
    guard (1...6).contains(n), l.dropFirst(n).first == " " else { return nil }
    return .heading(n, String(l.dropFirst(n + 1)))
  }

  private static func numbered(_ l: String) -> (String, String)? {
    let digits = l.prefix { $0.isNumber }
    guard !digits.isEmpty, digits.count <= 3 else { return nil }
    let rest = l.dropFirst(digits.count)
    guard rest.hasPrefix(". ") || rest.hasPrefix("、") || rest.hasPrefix(") ") else { return nil }
    return (String(digits), rest.dropFirst(rest.hasPrefix("、") ? 1 : 2).trimmingCharacters(in: .whitespaces))
  }

  /// One line's inline Markdown; the text as it is when it does not parse (an unfinished `**` while streaming).
  static func inline(_ s: String) -> AttributedString {
    (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(s)
  }
}

private extension String {
  func firstMatch(of prefixes: [String]) -> String? { prefixes.first { hasPrefix($0) } }
}

struct MarkdownText: View {
  let text: String

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      ForEach(Array(MDBlock.parse(text).enumerated()), id: \.offset) { _, b in block(b) }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .textSelection(.enabled)
  }

  @ViewBuilder private func block(_ b: MDBlock) -> some View {
    switch b {
    case .heading(let n, let s):
      Text(MDBlock.inline(s)).font(.system(size: n <= 2 ? 17 : 15, weight: .semibold)).foregroundColor(Palette.text)
        .padding(.top, 2)
    case .paragraph(let s):
      Text(MDBlock.inline(s)).font(Typo.body).foregroundColor(Palette.text).lineSpacing(3)
    case .bullet(let s, let level):
      HStack(alignment: .firstTextBaseline, spacing: 8) {
        Circle().fill(Palette.text2).frame(width: 4, height: 4).alignmentGuide(.firstTextBaseline) { $0[.bottom] + 4 }
        Text(MDBlock.inline(s)).font(Typo.body).foregroundColor(Palette.text).lineSpacing(3)
      }
      .padding(.leading, CGFloat(level) * 16 + 2)
    case .numbered(let n, let s):
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text("\(n).").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
        Text(MDBlock.inline(s)).font(Typo.body).foregroundColor(Palette.text).lineSpacing(3)
      }
    case .quote(let s):
      HStack(spacing: 10) {
        Rectangle().fill(Palette.text3).frame(width: 2)
        Text(MDBlock.inline(s)).font(Typo.body).foregroundColor(Palette.text2)
      }
      .fixedSize(horizontal: false, vertical: true)
    case .code(let s):
      ScrollView(.horizontal, showsIndicators: false) {
        Text(s).font(.system(size: 13, design: .monospaced)).foregroundColor(Palette.text).padding(10)
      }
      .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Palette.surface2))
    case .table(let rows):
      ScrollView(.horizontal, showsIndicators: false) {
        VStack(alignment: .leading, spacing: 0) {
          ForEach(Array(rows.enumerated()), id: \.offset) { i, r in
            HStack(spacing: 0) {
              ForEach(Array(r.enumerated()), id: \.offset) { _, c in
                Text(MDBlock.inline(c)).font(i == 0 ? Typo.label : Typo.caption.monospacedDigit())
                  .foregroundColor(i == 0 ? Palette.text2 : Palette.text)
                  .frame(minWidth: 64, alignment: .leading).padding(.vertical, 6).padding(.trailing, 12)
              }
            }
            if i < rows.count - 1 { Divider().overlay(Palette.hair) }
          }
        }
      }
    case .rule:
      Divider().overlay(Palette.hair)
    }
  }
}
