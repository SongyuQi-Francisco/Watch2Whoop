/// The retired AI-first Today (flag `ui_ai_first`, #279). 2.0.1 has one Today (docs/design/2.0.1 direction.md 4.2): the
/// page, its document, its sheets and its search field are gone (asking moved to the Jarvis key). What is left are the names
/// an older install may still have in its local tables: an edit still in the offline queue goes out as any other request
/// and keeps its label in the queue sheet.
enum TodayHome {
  static let flag = "ui_ai_first"
  /// `docs` key of the page's document (an older install may still have it cached; nothing reads it).
  static let docKey = "ui:home"
  /// Kind and group of its writes in the offline queue.
  static let queueKind = "ui.home"
}
