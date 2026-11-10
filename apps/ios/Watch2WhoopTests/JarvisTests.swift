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
    c.isActive = { true }   // the test host may not be the active app
    return c
  }

  /// Until the condition holds, returning at once when it does. The limit only has to outlast a busy CI machine, which can
  /// stall for seconds; a state that never comes still fails.
  private func wait(_ cond: @autoclosure () -> Bool, _ file: StaticString = #filePath, _ line: UInt = #line) async {
    let deadline = Date().addingTimeInterval(30)
    while !cond(), Date() < deadline { try? await Task.sleep(nanoseconds: 10_000_000) }
    XCTAssertTrue(cond(), "timed out after 30 s", file: file, line: line)
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

  // MARK: - a reply shown twice (2.0.0): the server resumes only a conversation's LATEST turn

  /// Q2's turn, as the server streams it, and the rest of it after event 2 (what GET …/events?after=2 gives once Q2 is the
  /// latest turn).
  private let turn2 = [("conversation", #"{"id":7}"#), ("tool", #"{"name":"report_feedback","status":"running"}"#),
                       ("tool", #"{"name":"report_feedback","status":"done"}"#), ("card", #"{"card":{"type":"water","total_ml":900}}"#),
                       ("text", #"{"delta":"Answer two"}"#), ("done", #"{"conversation_id":7}"#)]
  private var turn2After2: String {
    sse(turn2).components(separatedBy: "\n\n").filter { !$0.isEmpty }.dropFirst(2).map { $0 + "\n\n" }.joined()
  }
  /// The conversation as stored: Q1's answer complete, then Q2 and its answer.
  private let stored7 = #"""
    {"id":7,"messages":[{"id":1,"role":"user","text":"Q1","images":0},{"id":2,"role":"assistant","text":"Answer one","cards":[]},
     {"id":3,"role":"user","text":"Q2","images":0},{"id":4,"role":"assistant","text":"","tools":["report_feedback"],"cards":[]},
     {"id":5,"role":"assistant","text":"Answer two","cards":[{"type":"water","total_ml":900}]}],"pending":[]}
    """#

  private func routeConversation7() {
    let tail = turn2After2, stored = stored7
    StubProtocol.route = { r in
      switch r.url?.path {
      case "/v1/assistant/chat/7/events": return .init(status: 200, body: tail)
      case "/v1/assistant/conversations/7": return .init(status: 200, body: stored)
      default: return nil
      }
    }
  }

  private func count(_ c: JarvisChat, _ k: JarvisChat.Item.Kind) -> Int { c.items.filter { $0.kind == k }.count }
  private func cards(_ c: JarvisChat) -> Int { c.items.filter { if case .card = $0.kind { return true } else { return false } }.count }

  /// Q1's stream drops while the app is not active (no resume then); Q2 is asked and answered; when the app is active
  /// again, the left-over resume of Q1 must not run (on 2.0.0 it fetched Q2's reply a second time). Q1's reply is read back
  /// from the server instead, complete.
  func testACutTurnLeftWhileInactiveNeverRepeatsTheNextReply() async {
    StubProtocol.reset([
      .init(status: 200, body: sse([("conversation", #"{"id":7}"#), ("tool", #"{"name":"get_sleep","status":"running"}"#)])),   // Q1, cut
      .init(status: 200, body: sse(turn2)),                                                                                      // Q2
    ])
    routeConversation7()
    let c = chat()
    c.isActive = { false }
    c.send("Q1")
    await wait(!c.busy && StubProtocol.requests.count == 1)
    c.send("Q2")
    await wait(!c.busy && count(c, .assistant("Answer two")) >= 1)
    try? await Task.sleep(nanoseconds: 300_000_000)
    c.isActive = { true }
    await c.resume()   // JarvisLayer: the app became active
    await wait(!c.busy)
    try? await Task.sleep(nanoseconds: 200_000_000)
    XCTAssertEqual(count(c, .assistant("Answer two")), 1, "\(c.items.map(\.kind))")
    XCTAssertEqual(cards(c), 1, "\(c.items.map(\.kind))")
    XCTAssertFalse(StubProtocol.requests.contains { $0.url?.path == "/v1/assistant/chat/7/events" })
    XCTAssertEqual(c.items.map(\.kind).prefix(2), [.user("Q1", images: 0, voiceSeconds: nil), .assistant("Answer one")])
  }

  /// The same while active: the second try to resume Q1 waits a moment (1.5 s); a message sent meanwhile ends that try.
  func testAResumeLeftFromAnEarlierTurnNeverRepeatsTheNextReply() async {
    let gate = Gate()
    StubProtocol.reset([
      .init(status: 200, body: sse([("conversation", #"{"id":7}"#), ("tool", #"{"name":"get_sleep","status":"running"}"#)])),   // Q1, cut
      .init(status: 0, error: .networkConnectionLost),                                                                             // its resume fails too
      .init(status: 200, body: sse(turn2)),                                                                                        // Q2
    ])
    let c = chat()
    c.sleep = { _ in await gate.wait() }   // the pause before the second try, held open
    c.send("Q1")
    await wait(StubProtocol.requests.count == 2 && !c.busy && gate.waiting)
    routeConversation7()
    c.send("Q2")
    await wait(!c.busy && count(c, .assistant("Answer two")) >= 1)
    gate.open()
    try? await Task.sleep(nanoseconds: 300_000_000)
    await wait(!c.busy)
    XCTAssertEqual(count(c, .assistant("Answer two")), 1, "\(c.items.map(\.kind))")
    XCTAssertEqual(cards(c), 1, "\(c.items.map(\.kind))")
    XCTAssertFalse(StubProtocol.requests.dropFirst(2).contains { $0.url?.path == "/v1/assistant/chat/7/events" })
    XCTAssertEqual(c.items.map(\.kind).prefix(2), [.user("Q1", images: 0, voiceSeconds: nil), .assistant("Answer one")])
  }

  /// A resume answered `resync` (the turn is only in the database now): what the server stored replaces what had been
  /// shown of that reply, instead of being added under it.
  func testAResyncReplacesTheShownPartOfTheReply() async {
    StubProtocol.reset([
      .init(status: 200, body: sse([("conversation", #"{"id":9}"#), ("text", #"{"delta":"Par"}"#)])),   // cut
      .init(status: 200, body: "event: resync\ndata: {\"conversation_id\":9}\n\n"),
      .init(status: 200, body: #"""
        {"id":9,"messages":[{"id":1,"role":"user","text":"Q","images":0},{"id":2,"role":"assistant","text":"","tools":["get_sleep"],"cards":[]},
         {"id":3,"role":"assistant","text":"Partial answer, all of it","cards":[{"type":"water","total_ml":1200}]}],"pending":[]}
        """#),
    ])
    let c = chat()
    c.send("Q")
    await wait(!c.busy && StubProtocol.requests.count == 3 && c.items.count >= 3)
    try? await Task.sleep(nanoseconds: 200_000_000)
    XCTAssertEqual(c.items.map(\.kind).dropFirst().first, .assistant("Partial answer, all of it"), "\(c.items.map(\.kind))")
    XCTAssertEqual(c.items.count, 3, "\(c.items.map(\.kind))")
    guard case .card = c.items[2].kind else { return XCTFail("\(c.items[2])") }
  }

  // MARK: - voice (2.0.1: the words at once, the correction after)

  func testVoiceWordsAreInTheBoxAtOnceAndTheCorrectionReplacesThem() async {
    let c = chat()
    c.sleep = { _ in try? await Task.sleep(nanoseconds: 30_000_000_000) }   // no timeout in this test
    let gate = Gate()
    c.draft = "Note"
    c.insertVoice("legs fourty minutes", correction: Task { await gate.wait(); return "legs forty minutes" })
    XCTAssertEqual(c.draft, "Note\nlegs fourty minutes")   // before anything comes back
    XCTAssertEqual(c.correcting, 1)
    c.draft += " then stretch"   // typing after the spoken words does not stop the correction
    gate.open()
    await wait(c.correcting == 0)
    XCTAssertEqual(c.draft, "Note\nlegs forty minutes then stretch")
  }

  func testAnEditedVoiceTextIsNeverOverwritten() async {
    let c = chat()
    c.sleep = { _ in try? await Task.sleep(nanoseconds: 30_000_000_000) }
    let gate = Gate()
    c.insertVoice("legs fourty minutes", correction: Task { await gate.wait(); return "legs forty minutes" })
    c.draft = "legs 40 minutes"   // the user fixed it already
    gate.open()
    await wait(c.correcting == 0)
    XCTAssertEqual(c.draft, "legs 40 minutes")
  }

  func testALateCorrectionKeepsThePhonesWords() async {
    let c = chat()   // sleep returns at once: every wait for a correction times out
    c.insertVoice("legs fourty minutes", correction: Task { try? await Task.sleep(nanoseconds: 5_000_000_000); return "legs forty minutes" })
    await wait(c.correcting == 0)
    XCTAssertEqual(c.draft, "legs fourty minutes")
    XCTAssertEqual(JarvisChat.replacing(in: "早上 跑步3公里 晚上", at: 3, old: "跑步3公里", new: "跑步 3 公里"), "早上 跑步 3 公里 晚上")
    XCTAssertNil(JarvisChat.replacing(in: "早上 跑步", at: 3, old: "跑步3公里", new: "x"))
  }

  func testADirectVoiceMessageShowsAtOnceAndGoesOutCorrected() async {
    StubProtocol.reset([.init(status: 200, body: sse([("conversation", #"{"id":3}"#), ("text", #"{"delta":"ok"}"#), ("done", "{}")]))])
    let c = chat()
    c.sleep = { _ in try? await Task.sleep(nanoseconds: 30_000_000_000) }
    let gate = Gate()
    c.sendVoice("legs fourty minutes", seconds: 3, correction: Task { await gate.wait(); return "legs forty minutes" })
    XCTAssertEqual(c.items.first?.kind, .user("legs fourty minutes", images: 0, voiceSeconds: 3))   // on screen at once
    XCTAssertTrue(c.busy)
    XCTAssertTrue(StubProtocol.requests.isEmpty)
    gate.open()
    await wait(!c.busy && c.items.count == 2)
    XCTAssertEqual(c.items.first?.kind, .user("legs forty minutes", images: 0, voiceSeconds: 3))
    XCTAssertEqual(body(StubProtocol.requests[0])["message"] as? String, tr("［语音］", "[voice] ") + "legs forty minutes")
  }

  func testAVoiceCorrectionFallsBackToThePhone() async {
    // Chinese: the server's recogniser; when it fails, the phone's own final word
    StubProtocol.reset([.init(status: 200, body: #"{"text":"练腿四十分钟"}"#), .init(status: 500)])
    let c = chat()
    let zh = JarvisVoice.Take(text: "练腿四十", seconds: 2, wav: Data(count: 64), final: Task { "练腿四十分" })
    let a = await c.voiceCorrection(zh).value
    XCTAssertEqual(a, "练腿四十分钟")
    XCTAssertEqual(StubProtocol.requests.first?.url?.path, "/v1/assistant/transcribe")
    let b = await c.voiceCorrection(zh).value
    XCTAssertEqual(b, "练腿四十分")
    // English: the phone's final word through /voice-fix
    StubProtocol.reset([.init(status: 200, body: #"{"text":"Legs, forty minutes"}"#)])
    let en = JarvisVoice.Take(text: "legs fourty", seconds: 2, wav: nil, final: Task { "legs fourty minutes" })
    let e = await c.voiceCorrection(en).value
    XCTAssertEqual(e, "Legs, forty minutes")
    XCTAssertEqual(body(StubProtocol.requests[0])["text"] as? String, "legs fourty minutes")
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

  // MARK: - posture photos (#174)

  private func photo(_ data: String) -> ChatAttachment {
    ChatAttachment(kind: .photo, images: [ChatImage(media_type: "image/jpeg", data: data)])
  }

  func testPosturePhotosCarryTheirPurposeFrontSideBack() async {
    StubProtocol.reset([.init(status: 200, body: sse([
      ("conversation", #"{"id":5}"#), ("notice", #"{"code":"posture","message":"Limited"}"#),
      ("text", #"{"delta":"Looks fine"}"#), ("done", #"{"conversation_id":5}"#),
    ]))])
    var flow = PostureFlow()
    flow.photos[.back] = photo("Qg==")
    flow.photos[.front] = photo("Rg==")
    XCTAssertFalse(flow.ready)
    flow.photos[.side] = photo("Uw==")
    XCTAssertTrue(flow.ready)
    let c = chat()
    c.send(PostureFlow.question, attachments: flow.attachments, purpose: PostureFlow.purpose)
    await wait(!c.busy && c.items.count == 3)
    let b = body(StubProtocol.requests[0])
    XCTAssertEqual(b["purpose"] as? String, "posture")
    XCTAssertEqual((b["images"] as? [[String: Any]])?.compactMap { $0["data"] as? String }, ["Rg==", "Uw==", "Qg=="])
    XCTAssertEqual(c.items[1].kind, .note("Limited", warn: true, retry: false))   // before the answer
    XCTAssertEqual(c.items[2].kind, .assistant("Looks fine"))
  }

  func testAnOrdinaryPhotoHasNoPurpose() async {
    StubProtocol.reset([.init(status: 200, body: sse([("done", "{}")]))])
    let c = chat()
    c.send("meal", attachments: [photo("Rg==")])
    await wait(!c.busy && StubProtocol.requests.count == 1)
    XCTAssertNil(body(StubProtocol.requests[0])["purpose"])
  }

  /// In the composer they would go out later without their purpose, as ordinary photos, which stay on the server.
  func testPosturePhotosNeverFallIntoTheComposerWhileAnAnswerRuns() async {
    let cfg = URLSessionConfiguration.ephemeral
    cfg.protocolClasses = [HoldProtocol.self]
    let c = JarvisChat(api: APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" },
                                      session: URLSession(configuration: cfg), sleep: { _ in }))
    c.send("first")
    await wait(c.busy && HoldProtocol.open != nil)
    let before = c.items.count
    c.send(PostureFlow.question, attachments: [photo("Rg==")], purpose: PostureFlow.purpose)
    XCTAssertTrue(c.attachments.isEmpty)
    XCTAssertEqual(c.draft, "")
    XCTAssertEqual(c.items.count, before + 1)
    c.send("second", attachments: [photo("Uw==")])   // an ordinary one still waits in the composer, as before
    XCTAssertEqual(c.attachments.count, 1)
    HoldProtocol.fail()
    await wait(!c.busy)
  }

  func testPostureEntryFollowsTheServer() {
    XCTAssertEqual(JarvisAI().posture, .off)
    XCTAssertEqual(JarvisAI(JSONValue.object(["mode": .string("api")])).posture, .off)   // an older server
    XCTAssertEqual(JarvisAI(JSONValue.object(["mode": .string("api"), "posture": .string("photos")])).posture, .photos)
    XCTAssertEqual(JarvisAI(JSONValue.object(["mode": .string("fallback"), "posture": .string("guide")])).posture, .guide)
    XCTAssertEqual(JarvisAI(JSONValue.object(["mode": .string("api"), "posture": .string("new")])).posture, .off)
  }

  func testTheFirstUseNoticeIsPerAccountAndPerAI() {
    let d = UserDefaults(suiteName: "posture-test-\(UUID().uuidString)")!
    XCTAssertFalse(PostureNotice.seen(account: 1, ai: "A", defaults: d))
    PostureNotice.mark(account: 1, ai: "A", defaults: d)
    XCTAssertTrue(PostureNotice.seen(account: 1, ai: "A", defaults: d))
    XCTAssertFalse(PostureNotice.seen(account: 1, ai: "B", defaults: d))
    XCTAssertFalse(PostureNotice.seen(account: 2, ai: "A", defaults: d))
  }

  func testAReopenedPostureMessageShowsItsNotice() async {
    StubProtocol.reset([.init(status: 200, body: #"{"id":2,"messages":[{"id":1,"role":"user","text":"Posture","images":3,"notice":"Limited"},{"id":2,"role":"assistant","text":"Fine."}],"pending":[]}"#)])
    let c = chat()
    await c.open(2)
    XCTAssertEqual(c.items.map(\.kind), [.user("Posture", images: 3, voiceSeconds: nil), .note("Limited", warn: true, retry: false), .assistant("Fine.")])
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
    XCTAssertEqual(s.firstMs, 0)
    // with a start time: the wait for the first frame counts (a step that kept the main thread 120 ms)
    let late = JarvisPerf.summarise("y", [0.12, 0.12 + 1.0 / 60], maxFps: 60, start: 0)
    XCTAssertEqual(late.firstMs, 120, accuracy: 0.01)
    XCTAssertEqual(late.worstMs, 120, accuracy: 0.01)
    XCTAssertTrue(late.line.contains("first=120.0ms"))
  }

  /// 2.0.1: the menu keys never overlap the Jarvis key or each other, in any step of the dial nor on the way between two
  /// steps, with the keys up to a tenth of the move apart (their own small delays). The old close slid all four into
  /// the key, where they piled up for a moment.
  func testDialKeysNeverOverlap() {
    let c = CGPoint(x: 201, y: 700)
    let keys = JarvisMenuKey.allCases
    let moves: [(JarvisLayer.DialStep, JarvisLayer.DialStep)] = [(.parked, .out), (.out, .up), (.up, .folding)]
    let grid = stride(from: 0.0, through: 1.0, by: 0.1).map { CGFloat($0) }
    func at(_ k: JarvisMenuKey, _ m: (JarvisLayer.DialStep, JarvisLayer.DialStep), _ f: CGFloat) -> (CGPoint, CGFloat) {
      let a = JarvisLayer.pose(k, m.0, center: c), b = JarvisLayer.pose(k, m.1, center: c)
      let p = CGPoint(x: a.point.x + (b.point.x - a.point.x) * f, y: a.point.y + (b.point.y - a.point.y) * f)
      return (p, JarvisLayer.subSize / 2 * (a.scale + (b.scale - a.scale) * f))
    }
    for m in moves {
      for (i, k) in keys.enumerated() {
        for f in grid {
          let (p, r) = at(k, m, f)
          XCTAssertGreaterThan(hypot(p.x - c.x, p.y - c.y), r + JarvisLayer.keySize / 2, "\(k) \(m) \(f) touches the Jarvis key")
          for k2 in keys.dropFirst(i + 1) {
            for f2 in grid where abs(f - f2) <= 0.1 + 1e-9 {
              let (p2, r2) = at(k2, m, f2)
              XCTAssertGreaterThan(hypot(p.x - p2.x, p.y - p2.y), r + r2, "\(k) and \(k2) overlap (\(m) \(f) \(f2))")
            }
          }
        }
      }
    }
    // folding = gone; up = fully there, on the arc
    for k in keys {
      XCTAssertEqual(JarvisLayer.pose(k, .folding, center: c).opacity, 0)
      XCTAssertEqual(JarvisLayer.pose(k, .parked, center: c).opacity, 0)
      XCTAssertEqual(JarvisLayer.pose(k, .up, center: c), JarvisLayer.KeyPose(point: JarvisLayer.position(k, center: c), scale: 1, opacity: 1))
    }
  }
}

/// Keeps a request open until the test lets go, so an answer stays running.
private final class HoldProtocol: URLProtocol {
  nonisolated(unsafe) static var open: HoldProtocol?

  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() { Self.open = self }
  override func stopLoading() {}

  static func fail() {
    guard let o = open else { return }
    open = nil
    o.client?.urlProtocol(o, didFailWithError: URLError(.networkConnectionLost))
  }
}

/// A door a test opens: `wait()` suspends until `open()`; `waiting` = something is waiting at it.
@MainActor
private final class Gate {
  private var opened = false
  private var waiters: [CheckedContinuation<Void, Never>] = []
  private(set) var waiting = false

  func wait() async {
    if opened { return }
    waiting = true
    await withCheckedContinuation { waiters.append($0) }
  }

  func open() {
    opened = true
    waiters.forEach { $0.resume() }
    waiters = []
  }
}
