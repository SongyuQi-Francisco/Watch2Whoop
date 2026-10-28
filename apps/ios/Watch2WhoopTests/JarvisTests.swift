import XCTest
@testable import Watch2Whoop

/// N6 Jarvis: the SSE parser, the chat's turn against a fake SSE server (StubProtocol, no AI is called), Markdown
/// blocks, cards, the router's ask(), media helpers and the frame meter. Made-up numbers only.
@MainActor
final class JarvisTests: XCTestCase {
  private func sse(_ events: [(String, String)], end: Bool = true) -> String {
    events.enumerated().map { "id: \($0.offset + 1)\nevent: \($0.element.0)\ndata: \($0.element.1)\n\n" }.joined()
  }

  private func chat() -> JarvisChat {
    let api = APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" },
                        session: StubProtocol.session(), sleep: { _ in })
    let c = JarvisChat(api: api)
    c.sleep = { _ in }
    return c
  }

  private func wait(_ cond: @autoclosure () -> Bool, _ file: StaticString = #filePath, _ line: UInt = #line) async {
    for _ in 0..<300 where !cond() { try? await Task.sleep(nanoseconds: 10_000_000) }
    XCTAssertTrue(cond(), "timed out", file: file, line: line)
  }

  private func body(_ r: URLRequest) -> [String: Any] {
    var data = r.httpBody ?? Data()
    if data.isEmpty, let s = r.httpBodyStream {
      s.open()
      var buf = [UInt8](repeating: 0, count: 65_536)
      while s.hasBytesAvailable { let n = s.read(&buf, maxLength: buf.count); if n <= 0 { break }; data.append(buf, count: n) }
      s.close()
    }
    return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
  }

  // MARK: - SSE

  func testParserSplitsFramesOnBlankLines() {
    var p = SSEParser()
    let raw = "id: 3\r\nevent: text\r\ndata: {\"delta\":\"a\"}\r\n\r\n: keep-alive\n\nevent: done\ndata: {}\ndata: x\n\n"
    let frames = p.feed(Data(raw.utf8))
    XCTAssertEqual(frames, [SSEFrame(id: 3, event: "text", data: #"{"delta":"a"}"#), SSEFrame(id: nil, event: "done", data: "{}\nx")])
  }

  func testEventsDecode() {
    let confirm = ChatEvent(SSEFrame(id: 1, event: "confirm", data: #"{"conversation_id":4,"calls":[{"call_id":9,"tool":"undo_meal_log","args":{},"summary":"Undo"}]}"#))
    guard case .confirm(let calls) = confirm else { return XCTFail() }
    XCTAssertEqual(calls.map(\.callId), [9])
    XCTAssertTrue(confirm.isTerminal)
    XCTAssertEqual(ChatEvent(SSEFrame(id: 2, event: "usage", data: #"{"currency":"CNY","remaining_cny":1.5,"quota_cny":3}"#)), .usage(remainingPct: 50))
    XCTAssertEqual(ChatEvent(SSEFrame(id: 3, event: "text", data: #"{"delta":"hi"}"#)), .text("hi"))
    XCTAssertFalse(ChatEvent(SSEFrame(id: 3, event: "text", data: "{}")).isTerminal)
    XCTAssertEqual(ChatEvent(SSEFrame(id: 4, event: "whatever", data: "{}")), .unknown("whatever"))
  }

  // MARK: - a turn

  func testSendStreamsTextCardsAndQuota() async {
    let card = #"{"card":{"type":"metric","metric":"hrv","values":[50,null,60],"latest":60,"avg":55},"call_id":3}"#
    StubProtocol.reset([.init(status: 200, body: sse([
      ("conversation", #"{"id":12,"quota":{"quota_cny":3,"remaining_cny":2.4}}"#),
      ("text", #"{"delta":"**Good** "}"#), ("text", #"{"delta":"to go"}"#),
      ("tool", #"{"name":"get_sleep","status":"done"}"#),
      ("card", card), ("usage", #"{"currency":"CNY","remaining_cny":1.5,"quota_cny":3}"#),
      ("done", #"{"conversation_id":12}"#),
    ]), headers: ["Content-Type": "text/event-stream"])])
    let c = chat()
    var wrote = false
    c.onWrote = { wrote = true }
    c.page = "today"
    c.send("Should I train")
    await wait(!c.busy && c.items.count == 3)
    XCTAssertEqual(c.conversationId, 12)
    XCTAssertEqual(c.quotaPct, 50)
    XCTAssertEqual(c.items.map(\.kind).first, .user("Should I train", images: 0, voiceSeconds: nil))
    XCTAssertEqual(c.items[1].kind, .assistant("**Good** to go"))
    guard case .card(let v) = c.items[2].kind else { return XCTFail() }
    XCTAssertEqual(v["type"]?.string, "metric")
    XCTAssertTrue(wrote)
    let r = StubProtocol.requests[0]
    XCTAssertEqual(r.url?.path, "/v1/assistant/chat")
    XCTAssertEqual(r.value(forHTTPHeaderField: "Accept"), "text/event-stream")
    let b = body(r)
    XCTAssertEqual(b["message"] as? String, "Should I train")
    XCTAssertEqual(b["page"] as? String, "today")
  }

  func testVoiceAndVideoAreMarkedForTheModel() async {
    StubProtocol.reset([.init(status: 200, body: sse([("done", "{}")]))])
    let c = chat()
    let frames = [ChatImage(media_type: "image/jpeg", data: "AA=="), ChatImage(media_type: "image/jpeg", data: "AA==")]
    c.send("legs", attachments: [ChatAttachment(kind: .video(seconds: 12), images: frames)], voiceSeconds: 3)
    await wait(!c.busy && StubProtocol.requests.count == 1)
    let b = body(StubProtocol.requests[0])
    let msg = b["message"] as? String ?? ""
    XCTAssertTrue(msg.hasSuffix("legs"))
    XCTAssertEqual((b["images"] as? [[String: Any]])?.count, 2)
    XCTAssertEqual(c.items.first?.kind, .user("legs\n" + tr("［视频 2 帧，约 12 秒］", "[video, 2 frames, about 12 s]"), images: 2, voiceSeconds: 3))
  }

  func testConfirmWaitsForTheUser() async {
    StubProtocol.reset([
      .init(status: 200, body: sse([("conversation", #"{"id":5}"#),
                                    ("confirm", #"{"conversation_id":5,"calls":[{"call_id":41,"tool":"undo_meal_log","summary":"Undo lunch"}]}"#)])),
      .init(status: 200, body: sse([("tool", #"{"name":"undo_meal_log","status":"done","call_id":41}"#), ("text", #"{"delta":"Done."}"#), ("done", "{}")])),
    ])
    let c = chat()
    c.send("delete lunch")
    await wait(!c.busy && c.items.count == 2)
    guard case .confirm(let call, nil) = c.items[1].kind else { return XCTFail("\(c.items)") }
    XCTAssertEqual(call.summary, "Undo lunch")
    c.confirm(call, approve: true)
    await wait(!c.busy && c.items.count == 3)
    XCTAssertEqual(c.items[1].kind, .confirm(call, approved: true))
    XCTAssertEqual(c.items[2].kind, .assistant("Done."))
    XCTAssertEqual(StubProtocol.requests[1].url?.path, "/v1/assistant/chat/confirm")
    XCTAssertEqual(body(StubProtocol.requests[1])["call_id"] as? Int, 41)
    XCTAssertEqual(body(StubProtocol.requests[1])["approve"] as? Bool, true)
  }

  func testFallbackConsentFirstThenTheSameMessage() async {
    StubProtocol.reset([
      .init(status: 403, body: #"{"detail":{"code":"fallback_consent","message":"agree first"}}"#),
      .init(status: 200, body: #"{"points":["A","B"],"consent_version":"v9"}"#),
      .init(status: 200, body: "{}"),   // POST consent
      .init(status: 200, body: #"{"options":[{"key":"fallback","label":"Jarvis","current":true,"consent":true}],"ai":{"mode":"fallback","consent":true,"remaining_pct":90}}"#),
      .init(status: 200, body: sse([("text", #"{"delta":"hi"}"#), ("done", "{}")])),
    ])
    let c = chat()
    c.send("hello")
    await wait(c.consent != nil)
    XCTAssertEqual(c.consent?.points, ["A", "B"])
    XCTAssertTrue(c.items.isEmpty)   // the message waits for the terms
    let err = await c.acceptConsent()
    XCTAssertNil(err)
    await wait(!c.busy && c.items.contains { $0.kind == .assistant("hi") })
    XCTAssertEqual(body(StubProtocol.requests[2])["version"] as? String, "v9")
    XCTAssertEqual(c.ai.mode, .fallback)
    XCTAssertEqual(c.quotaPct, 90)
    XCTAssertEqual(StubProtocol.requests[4].url?.path, "/v1/assistant/chat")
  }

  func testRestartingServerIsRetriedThenErrorsShowARetry() async {
    StubProtocol.reset([.init(status: 503), .init(status: 200, body: sse([("text", #"{"delta":"ok"}"#), ("done", "{}")]))])
    let c = chat()
    c.send("a")
    await wait(!c.busy && c.items.count == 2)
    XCTAssertEqual(c.items[1].kind, .assistant("ok"))

    StubProtocol.reset([.init(status: 0, error: .notConnectedToInternet)])
    c.send("b")
    await wait(!c.busy && c.items.count == 4)
    guard case .note(_, true, true) = c.items[3].kind else { return XCTFail("\(c.items[3])") }
    StubProtocol.reset([.init(status: 200, body: sse([("text", #"{"delta":"again"}"#), ("done", "{}")]))])
    c.retry(c.items[3])
    await wait(!c.busy && c.items.last?.kind == .assistant("again"))
  }

  func testADroppedStreamIsPickedUpFromItsLastEvent() async {
    StubProtocol.reset([
      .init(status: 200, body: sse([("conversation", #"{"id":7}"#), ("text", #"{"delta":"par"}"#)])),   // cut, no done
      .init(status: 200, body: "id: 3\nevent: text\ndata: {\"delta\":\"t\"}\n\nid: 4\nevent: done\ndata: {}\n\n"),
    ])
    let c = chat()
    c.send("go")
    await wait(!c.busy && StubProtocol.requests.count == 2 && c.items.last?.kind == .assistant("t"))
    let r = StubProtocol.requests[1]
    XCTAssertEqual(r.url?.path, "/v1/assistant/chat/7/events")
    XCTAssertEqual(URLComponents(url: r.url!, resolvingAgainstBaseURL: false)?.queryItems?.first?.value, "2")
  }

  func testReopenAConversation() async {
    StubProtocol.reset([.init(status: 200, body: #"""
      {"id":2,"messages":[{"id":1,"role":"user","text":"Week?","images":0},{"id":2,"role":"assistant","text":"Fine.","cards":[{"type":"water","total_ml":1500}]}],
       "pending":[{"call_id":8,"tool":"delete_journal_entry","summary":"Clear"}]}
      """#)])
    let c = chat()
    await c.open(2)
    XCTAssertEqual(c.conversationId, 2)
    XCTAssertEqual(c.items.count, 4)
    XCTAssertEqual(c.items[1].kind, .assistant("Fine."))
    guard case .confirm(let p, nil) = c.items[3].kind else { return XCTFail() }
    XCTAssertEqual(p.callId, 8)
  }

  func testChoicesAndAIModes() async {
    StubProtocol.reset([.init(status: 200, body: #"""
      {"options":[{"key":"fallback","label":"Jarvis","note":"80% left","consent":false},{"key":"butler","label":"Butler","current":true}],
       "ai":{"mode":"butler","display_name":"Butler","assistant_url":"https://claude.example.com/x"}}
      """#)])
    let c = chat()
    var seen: JarvisAI?
    c.onAIChange = { seen = $0 }
    await c.loadChoices()
    XCTAssertEqual(c.choices.map(\.key), ["fallback", "butler"])
    XCTAssertFalse(c.choices[0].consent)
    XCTAssertEqual(seen?.mode, .butler)
    XCTAssertFalse(c.ai.inApp)
    XCTAssertEqual(c.ai.assistantURL?.host, "claude.example.com")
    XCTAssertTrue(JarvisAI(JSONValue.object(["mode": .string("api"), "vision": .bool(false)])).inApp)
    XCTAssertFalse(JarvisAI(JSONValue.object(["mode": .string("api"), "vision": .bool(false)])).vision)
  }

  // MARK: - router, Markdown, cards, media

  func testAskCopiesForAnAIOutsideTheApp() {
    let r = JarvisRouter()
    r.ask(prompt: "Explain HRV 64 ms")
    XCTAssertEqual(UIPasteboard.general.string, "Explain HRV 64 ms")
    XCTAssertNotNil(r.toast)
    JarvisRouter.current = r
    JarvisRouter.ask(prompt: "Second")
    XCTAssertEqual(UIPasteboard.general.string, "Second")
  }

  func testQuickQuestionsByPage() {
    XCTAssertEqual(JarvisRouter.quick(for: "today").count, 3)
    XCTAssertTrue(JarvisRouter.quick(for: "food").contains { $0.photo })
    XCTAssertFalse(JarvisRouter.quick(for: "food", vision: false).contains { $0.photo })
    XCTAssertEqual(JarvisRouter.quick(for: "record.training").count, 2)
    XCTAssertEqual(JarvisRouter.quick(for: "food.unknown"), JarvisRouter.quick(for: "food"))
    XCTAssertTrue(JarvisRouter.quick(for: "nowhere").isEmpty)
  }

  func testMarkdownBlocks() {
    let md = "# Plan\n\nSleep **well**.\nKeep going.\n\n- one\n  - two\n1. first\n> note\n```\ncode\n```\n| a | b |\n|---|---|\n| 1 | 2 |\n---"
    XCTAssertEqual(MDBlock.parse(md), [
      .heading(1, "Plan"), .paragraph("Sleep **well**.\nKeep going."), .bullet("one", level: 0), .bullet("two", level: 1),
      .numbered("1", "first"), .quote("note"), .code("code"), .table([["a", "b"], ["1", "2"]]), .rule,
    ])
    XCTAssertEqual(MDBlock.parse("```\nunfinished"), [.code("unfinished")])
    XCTAssertEqual(String(MDBlock.inline("**x").characters), "**x")   // unfinished while streaming: shown as it is
  }

  func testCards() {
    XCTAssertTrue(JarvisCards.nativeTypes.contains("product"))
    XCTAssertNotNil(JarvisCards.send("adjust_training", .object([:])))
    XCTAssertNil(JarvisCards.send("no_such_key", .object([:])))
    XCTAssertNotNil(JarvisCards.openLabel("food.recipes"))
    XCTAssertEqual(JarvisCards.hm(462), "7:42")
    XCTAssertEqual(JarvisCards.mmdd("2026-01-02"), "01-02")
    let v = JSONValue.parse(Data(#"{"type":"stress","date":"2026-01-02","score":41.5,"label":"Low","actions":[],"nested":{"a":1}}"#.utf8))!
    XCTAssertEqual(GenericCard.rows(v).map(\.0), ["label", "score"])
    XCTAssertEqual(GenericCard.rows(v).map(\.1), ["Low", "41.5"])
  }

  func testJSONValue() {
    let v = JSONValue.parse(Data(#"{"a":[1,true,"x",null],"b":{"c":2.5}}"#.utf8))!
    XCTAssertEqual(v["a"]?[1]?.bool, true)
    XCTAssertEqual(v["a"]?[0]?.int, 1)
    XCTAssertEqual(v["a"]?[0]?.string, "1")
    XCTAssertTrue(v["a"]?[3]?.isNull ?? false)
    XCTAssertEqual(v["b"]?["c"]?.double, 2.5)
    XCTAssertNil(v["missing"])
    let round = try? JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(v))
    XCTAssertEqual(round, v)
  }

  func testMediaHelpers() {
    XCTAssertEqual(JarvisMedia.frameTimes(duration: 8, count: 4), [1, 3, 5, 7])
    let img = UIGraphicsImageRenderer(size: CGSize(width: 3200, height: 1600)).image { c in UIColor.gray.setFill(); c.fill(CGRect(x: 0, y: 0, width: 3200, height: 1600)) }
    let a = JarvisMedia.photo(img)
    let data = Data(base64Encoded: a?.images.first?.data ?? "")!
    XCTAssertEqual(UIImage(data: data)?.size, CGSize(width: 1600, height: 800))
    let wav = JarvisVoice.wav(Data(count: 32000))
    XCTAssertEqual(wav.count, 44 + 32000)
    XCTAssertEqual(String(data: wav.prefix(4), encoding: .ascii), "RIFF")
  }

  func testFrameSummary() {
    let t: [CFTimeInterval] = [0, 1.0 / 60, 2.0 / 60, 4.0 / 60, 5.0 / 60]
    let s = JarvisPerf.summarise("x", t, maxFps: 60)
    XCTAssertEqual(s.frames, 4)
    XCTAssertEqual(s.dropped, 1)
    XCTAssertEqual(s.worstMs, 1000.0 / 30, accuracy: 0.01)
  }
}
