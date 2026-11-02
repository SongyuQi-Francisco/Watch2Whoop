import SwiftUI
import UIKit

/// The 「问 AI」 hook of explain sheets and detail pages: hands a prepared question (the server's `ai_prompt`, built from
/// the user's own numbers) to the user's AI. N6 sets it to open Jarvis with the question:
///
///     MainTabView().environment(\.askAI, AskAIAction { prompt in jarvis.open(prompt) })
///
/// Until then the default copies the question, so it can be pasted into any AI app (the page's fallback, app.js askAI).
struct AskAIAction {
  /// True when a real AI is connected (N6); false = the clipboard fallback.
  var connected: Bool
  private let handler: (String) -> Void

  init(connected: Bool = true, _ handler: @escaping (String) -> Void) {
    self.connected = connected
    self.handler = handler
  }

  func callAsFunction(_ prompt: String) { handler(prompt) }

  static let copyToClipboard = AskAIAction(connected: false) { UIPasteboard.general.string = $0 }
}

private struct AskAIKey: EnvironmentKey {
  static let defaultValue = AskAIAction.copyToClipboard
}

extension EnvironmentValues {
  var askAI: AskAIAction {
    get { self[AskAIKey.self] }
    set { self[AskAIKey.self] = newValue }
  }
}
