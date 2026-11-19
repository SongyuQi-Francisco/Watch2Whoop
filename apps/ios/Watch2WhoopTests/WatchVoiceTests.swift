import XCTest
@testable import Watch2Whoop

// Hold to talk on the watch (docs/watch-app-plan.md 2.2 / 2.3 / 4.3–4.5, backend #458): the shared rules in
// apps/ios/WatchShared/WatchVoice.swift. All data is synthetic.

private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

private func sse(_ id: Int?, _ name: String, _ json: String) -> WatchSSEEvent {
  WatchSSEEvent(id: id, event: name, data: Data(json.utf8))
}

private func ctx(signedIn: Bool = true, voice: WatchVoiceOffer?) -> WatchContext {
  WatchContext(signedIn: signedIn, plan: nil, lang: "en", sentAt: t0, planFor: "2026-01-02", voice: voice)
}

private let onA = WatchVoiceOffer(on: true, why: nil, account: "a")

final class WatchVoiceOfferTests: XCTestCase {
  /// Plan 2.3 / Q6: only an in-app AI with direct voice on; the fallback only after its terms were agreed to on the phone.
  func testOfferFollowsTheAIAndTheFlag() {
    XCTAssertTrue(WatchVoiceOffer.of(mode: "api", consent: false, direct: true, account: "a").on)
    XCTAssertTrue(WatchVoiceOffer.of(mode: "fallback", consent: true, direct: true, account: "a").on)
    XCTAssertEqual(WatchVoiceOffer.of(mode: "fallback", consent: false, direct: true, account: "a").why, .consent)
    XCTAssertEqual(WatchVoiceOffer.of(mode: "mcp", consent: true, direct: true, account: "a").why, .mode)
    XCTAssertEqual(WatchVoiceOffer.of(mode: "butler", consent: true, direct: true, account: "a").why, .mode)
    XCTAssertEqual(WatchVoiceOffer.of(mode: nil, consent: true, direct: true, account: "a").why, .mode)
    XCTAssertEqual(WatchVoiceOffer.of(mode: "api", consent: true, direct: false, account: "a").why, .direct)
  }

  /// `WatchVoice.derive` is the one rule for the lit core: signed in, offered, a token, the watch's own network.
  func testVoiceRule() {
    let ok = WatchVoice.Credential(paired: true, online: true)
    let sync = WatchSyncState.linked(minutesAgo: 1)
    XCTAssertEqual(WatchVoice.derive(context: ctx(voice: onA), sync: sync, credential: ok), .available)
    XCTAssertEqual(WatchVoice.derive(context: ctx(signedIn: false, voice: onA), sync: sync, credential: ok), .unavailable(.signedOut))
    XCTAssertEqual(WatchVoice.derive(context: nil, sync: sync, credential: ok), .unavailable(.signedOut))
    XCTAssertEqual(WatchVoice.derive(context: ctx(voice: nil), sync: sync, credential: ok), .unavailable(.noAI))   // older phone
    XCTAssertEqual(WatchVoice.derive(context: ctx(voice: .of(mode: "mcp", consent: false, direct: true, account: "a")), sync: sync,
                                     credential: ok), .unavailable(.noAI))
    XCTAssertEqual(WatchVoice.derive(context: ctx(voice: .of(mode: "fallback", consent: false, direct: true, account: "a")), sync: sync,
                                     credential: ok), .unavailable(.consent))
    XCTAssertEqual(WatchVoice.derive(context: ctx(voice: onA), sync: sync, credential: .init(paired: false, online: true)),
                   .unavailable(.pairing))
    XCTAssertEqual(WatchVoice.derive(context: ctx(voice: onA), sync: sync, credential: .init(paired: true, online: false)),
                   .unavailable(.offline))
    // the phone being offline is not the watch being offline: voice works on the watch's own network
    XCTAssertEqual(WatchVoice.derive(context: ctx(voice: onA), sync: .offline, credential: ok), .available)
    XCTAssertEqual(WatchHome.derive(context: ctx(voice: onA), today: "2026-01-02", sync: sync, credential: ok).voice, .available)
  }

  /// A context with the voice block decodes; an unreadable block costs only itself; an older context has none.
  func testContextVoiceBlockIsLenient() throws {
    let c = ctx(voice: onA)
    let back = WatchCodec.decode(WatchContext.self, WatchCodec.encode(c))
    XCTAssertEqual(back?.voice, onA)
    var j = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(WatchCodec.encode(c))) as? [String: Any])
    j["voice"] = ["on": "maybe"]
    let bad = WatchCodec.decode(WatchContext.self, try JSONSerialization.data(withJSONObject: j))
    XCTAssertNotNil(bad)
    XCTAssertNil(bad?.voice)
    j.removeValue(forKey: "voice")
    XCTAssertNil(WatchCodec.decode(WatchContext.self, try JSONSerialization.data(withJSONObject: j))?.voice)
  }
}

final class WatchPairingTests: XCTestCase {
  private func code(at: Date = t0, account: String = "a") -> WatchPairCode {
    WatchPairCode(server: "https://example.invalid", code: "ABCDEFGHJKLM", account: account, at: at, expiresIn: 600)
  }

  func testAsksOnlyWhenOfferedUnpairedAndReachableAndSpaced() {
    var p = WatchPairing()
    XCTAssertFalse(p.shouldAsk(offer: nil, reachable: true, now: t0))
    XCTAssertFalse(p.shouldAsk(offer: WatchVoiceOffer(on: false, why: .direct, account: "a"), reachable: true, now: t0))
    XCTAssertFalse(p.shouldAsk(offer: onA, reachable: false, now: t0))
    XCTAssertTrue(p.shouldAsk(offer: onA, reachable: true, now: t0))
    p.asked(at: t0)
    XCTAssertFalse(p.shouldAsk(offer: onA, reachable: true, now: t0.addingTimeInterval(WatchParams.pairAskEverySec - 1)))
    XCTAssertTrue(p.shouldAsk(offer: onA, reachable: true, now: t0.addingTimeInterval(WatchParams.pairAskEverySec)))
    p.redeemed(code())
    XCTAssertTrue(p.usable(for: onA))
    XCTAssertFalse(p.shouldAsk(offer: onA, reachable: true, now: t0.addingTimeInterval(999)))
  }

  /// Any 401: the token is gone, the core goes dark and the phone is asked again at once.
  func testLostAsksAgainAtOnce() {
    var p = WatchPairing()
    p.redeemed(code())
    p.asked(at: t0)
    p.lost()
    XCTAssertFalse(p.usable(for: onA))
    XCTAssertTrue(p.shouldAsk(offer: onA, reachable: true, now: t0))
  }

  /// The phone signs out, or another account signs in: the token is dropped (the caller deletes it from the Keychain).
  func testPhoneSignOutOrAnotherAccountDropsTheToken() {
    var p = WatchPairing()
    p.redeemed(code())
    XCTAssertFalse(p.phoneSays(signedIn: true, offer: onA))
    XCTAssertTrue(p.usable(for: onA))
    XCTAssertTrue(p.phoneSays(signedIn: true, offer: WatchVoiceOffer(on: true, why: nil, account: "b")))
    XCTAssertFalse(p.paired)
    p.redeemed(code())
    XCTAssertTrue(p.phoneSays(signedIn: false, offer: nil))
    XCTAssertEqual(p, WatchPairing())
    XCTAssertFalse(p.phoneSays(signedIn: false, offer: nil))   // nothing left to drop
  }

  func testCodeFreshness() {
    let c = code()
    XCTAssertTrue(c.fresh(now: t0.addingTimeInterval(60)))
    XCTAssertFalse(c.fresh(now: t0.addingTimeInterval(600 - WatchParams.pairCodeMarginSec)))
  }

  /// The code travels outside the application context, in its own key.
  func testCodeMessageRoundTripAndNotInContext() {
    let c = code()
    XCTAssertEqual(WatchCodec.pairCode(WatchCodec.message(c)), c)
    XCTAssertNil(WatchCodec.pairCode(WatchCodec.message(ctx(voice: onA))))
    let context = String(decoding: WatchCodec.encode(ctx(voice: onA)) ?? Data(), as: UTF8.self)
    XCTAssertFalse(context.contains("code"))
    XCTAssertFalse(context.contains("token"))
    XCTAssertEqual(WatchCodec.continued(WatchCodec.continueMessage(conversation: 42)), 42)
  }
}

final class WatchRecordingTests: XCTestCase {
  func testLimits() {
    XCTAssertEqual(WatchRecordingCheck.of(seconds: 0.2), .tooShort)
    XCTAssertEqual(WatchRecordingCheck.of(seconds: WatchParams.voiceMinSec), .ok)
    XCTAssertEqual(WatchRecordingCheck.of(seconds: 12), .ok)
    XCTAssertEqual(WatchRecordingCheck.of(seconds: WatchParams.voiceMaxSec), .clipped)
    XCTAssertGreaterThanOrEqual(WatchParams.voiceMinSec, 0.3)   // the server's minimum
    XCTAssertLessThanOrEqual(WatchParams.voiceMaxSec, 60)       // the server's maximum
  }

  /// The WAV the watch sends is what the transcriber takes (16-bit PCM, mono, 16 kHz) and its length is read back.
  func testWavMadeAndInspected() throws {
    let d = WatchWAV.make(samples: Array(repeating: 100, count: 16_000 * 2))
    let w = try XCTUnwrap(WatchWAV.inspect(d))
    XCTAssertTrue(w.accepted)
    XCTAssertEqual(w.sampleRate, 16_000)
    XCTAssertEqual(w.seconds, 2, accuracy: 0.001)
    XCTAssertEqual(d.count, 44 + 64_000)
    XCTAssertLessThan(Double(44 + Int(WatchParams.voiceMaxSec) * 32_000), 6_000_000)   // asr.MAX_BYTES
  }

  /// AVAudioRecorder may write extra chunks (FLLR) and the EXTENSIBLE header; float or stereo is refused before sending.
  func testWavChunksAndRefusals() throws {
    var d = WatchWAV.make(samples: Array(repeating: 1, count: 8_000))
    // insert a FLLR chunk after `fmt `
    var fllr = Data("FLLR".utf8)
    var size = UInt32(6).littleEndian
    fllr.append(Data(bytes: &size, count: 4))
    fllr.append(Data(repeating: 0, count: 6))
    d.insert(contentsOf: fllr, at: 36)
    let w = try XCTUnwrap(WatchWAV.inspect(d))
    XCTAssertTrue(w.accepted)
    XCTAssertEqual(w.seconds, 0.5, accuracy: 0.001)
    XCTAssertFalse(WatchWAV(format: 3, channels: 1, sampleRate: 16_000, bits: 32, dataBytes: 10).accepted)
    XCTAssertFalse(WatchWAV(format: 1, channels: 2, sampleRate: 16_000, bits: 16, dataBytes: 10).accepted)
    XCTAssertTrue(WatchWAV(format: 0xFFFE, channels: 1, sampleRate: 48_000, bits: 16, dataBytes: 10).accepted)
    XCTAssertNil(WatchWAV.inspect(Data("not a wav file at all".utf8)))
  }
}

final class WatchVoiceFlowTests: XCTestCase {
  func testHoldReleaseTranscribeConfirmSend() {
    var f = WatchVoiceFlow()
    XCTAssertEqual(f.hold(now: t0), [.startRecording, .haptic(.start)])
    XCTAssertTrue(f.isListening)
    XCTAssertEqual(f.release(seconds: 2), [.stopRecording(keep: true), .haptic(.stop), .transcribe])
    XCTAssertEqual(f.stage, .transcribing)
    XCTAssertEqual(f.transcribed("  lunch noodles  ", now: t0), [])
    guard case .confirming(let text, _) = f.stage else { return XCTFail("M2") }
    XCTAssertEqual(text, "lunch noodles")
    // the ring: nothing before 1.5 s, then the words go
    XCTAssertEqual(f.tick(now: t0.addingTimeInterval(WatchParams.voiceSendAfterSec - 0.01)), [])
    XCTAssertEqual(f.tick(now: t0.addingTimeInterval(WatchParams.voiceSendAfterSec)), [.send("lunch noodles")])
    XCTAssertEqual(f.stage, .thinking(question: "lunch noodles"))
  }

  func testTimerProgress() {
    let t = WatchSendTimer(start: t0)
    XCTAssertEqual(t.progress(t0), 0)
    XCTAssertEqual(t.progress(t0.addingTimeInterval(0.75)), 0.5, accuracy: 0.001)
    XCTAssertEqual(t.progress(t0.addingTimeInterval(9)), 1)
    XCTAssertFalse(t.due(t0.addingTimeInterval(1.4)))
    XCTAssertTrue(t.due(t0.addingTimeInterval(1.5)))
  }

  func testCancelAndSendNow() {
    var f = WatchVoiceFlow()
    _ = f.hold(now: t0)
    XCTAssertEqual(f.cancel(), [.stopRecording(keep: false)])   // slide up
    XCTAssertTrue(f.isIdle)
    _ = f.hold(now: t0)
    _ = f.release(seconds: 1)
    _ = f.transcribed("ok", now: t0)
    XCTAssertEqual(f.cancel(), [])   // ✕ on M2: nothing is sent
    XCTAssertEqual(f.tick(now: t0.addingTimeInterval(5)), [])
    _ = f.hold(now: t0)
    _ = f.release(seconds: 1)
    _ = f.transcribed("ok", now: t0)
    XCTAssertEqual(f.sendNow(), [.send("ok")])   // tap on the core / Double Tap
  }

  func testTooShortAndUnheardAndClipped() {
    var f = WatchVoiceFlow()
    _ = f.hold(now: t0)
    XCTAssertEqual(f.release(seconds: 0.2), [.stopRecording(keep: false), .haptic(.failure)])
    XCTAssertTrue(f.isIdle)
    _ = f.hold(now: t0)
    _ = f.release(seconds: 40)   // the recorder stops itself at the limit: what was said is sent
    XCTAssertEqual(f.stage, .transcribing)
    XCTAssertEqual(f.transcribed("   ", now: t0), [.haptic(.failure)])
    XCTAssertEqual(f.stage, .failed(.unheard))
    XCTAssertEqual(f.retry(), [])   // not heard: hold and say it again (dictation is offered)
  }

  func testMealReceipt() {
    var f = WatchVoiceFlow()
    _ = f.hold(now: t0); _ = f.release(seconds: 2); _ = f.transcribed("lunch", now: t0); _ = f.sendNow()
    f.received(sse(1, "conversation", #"{"id": 7}"#))
    f.received(sse(2, "card", #"{"call_id": "x", "card": {"type": "meal", "totals": {"calories": 1230}, "goal": {"calories": 2300}, "written": [{"tool": "log_food", "date": "2026-01-02", "entry_ids": ["11", 12], "log_id": null, "meal": "lunch", "foods": ["Noodles", "Egg"], "calories": 620, "protein_g": 34}]}}"#))
    f.received(sse(3, "text", #"{"delta": "Logged"}"#))
    f.received(sse(4, "done", #"{"conversation_id": 7}"#))
    let fx = f.streamEnded(broken: false, now: t0)
    guard case .receipt(let m)? = fx.first else { return XCTFail("M3") }
    XCTAssertEqual(m.calories, 620)
    XCTAssertEqual(m.proteinG, 34)
    XCTAssertEqual(m.dayKcal, 1230)
    XCTAssertEqual(m.writes.first?.entryIds, ["11", "12"])
    XCTAssertTrue(f.isIdle)
    XCTAssertEqual(f.followUp(now: t0.addingTimeInterval(60)), 7)   // 「撤销刚才那餐」 goes to the same conversation
    XCTAssertNil(f.followUp(now: t0.addingTimeInterval(WatchParams.voiceFollowUpSec + 1)))
  }

  func testAnswerAndServerConfirm() {
    var f = WatchVoiceFlow()
    _ = f.hold(now: t0); _ = f.release(seconds: 2); _ = f.transcribed("legs?", now: t0); _ = f.sendNow()
    f.received(sse(1, "conversation", #"{"id": 9}"#))
    f.received(sse(2, "text", #"{"delta": "Yes, train\nRecovery 82."}"#))
    f.received(sse(3, "done", #"{"conversation_id": 9}"#))
    XCTAssertEqual(f.streamEnded(broken: false, now: t0), [.haptic(.arrived)])
    XCTAssertEqual(f.stage, .answer(question: "legs?", answer: WatchAnswer(headline: "Yes, train", body: "Recovery 82.", more: false),
                                    conversation: 9))
    // a new hold from the answer: 「undo that meal」 → the server asks first (M4)
    _ = f.hold(now: t0); _ = f.release(seconds: 2); _ = f.transcribed("undo", now: t0); _ = f.sendNow()
    f.received(sse(1, "conversation", #"{"id": 9}"#))
    f.received(sse(2, "confirm", #"{"conversation_id": 9, "calls": [{"call_id": "c1", "tool": "delete_food_entries", "args": {}, "summary": "Delete lunch"}]}"#))
    XCTAssertEqual(f.streamEnded(broken: false, now: t0), [.haptic(.arrived)])
    XCTAssertEqual(f.stage, .confirmCall(question: "undo", call: WatchConfirmCall(callId: "c1", tool: "delete_food_entries", summary: "Delete lunch")))
    XCTAssertEqual(f.answerCall(approve: true), [.approve(callId: "c1", approve: true)])
    XCTAssertEqual(f.stage, .thinking(question: "undo"))
  }

  /// A dropped stream is picked up after the last event it got, a few times, then 「连接断了」 with a retry that resumes.
  func testResumeAfterADroppedStream() {
    var f = WatchVoiceFlow()
    _ = f.hold(now: t0); _ = f.release(seconds: 2); _ = f.transcribed("q", now: t0); _ = f.sendNow()
    f.received(sse(1, "conversation", #"{"id": 5}"#))
    f.received(sse(2, "text", #"{"delta": "Par"}"#))
    for i in 0..<WatchParams.voiceResumeTries {
      XCTAssertEqual(f.streamEnded(broken: true, now: t0), [.resume(conversation: 5, after: 2)], "try \(i)")
    }
    XCTAssertEqual(f.streamEnded(broken: true, now: t0), [.haptic(.failure)])
    XCTAssertEqual(f.stage, .failed(.interrupted))
    XCTAssertEqual(f.retry(), [.resume(conversation: 5, after: 2)])
    // the resumed stream repeats nothing and finishes the turn
    f.received(sse(2, "text", #"{"delta": "Par"}"#))
    f.received(sse(3, "text", #"{"delta": "tly"}"#))
    f.received(sse(4, "done", #"{"conversation_id": 5}"#))
    XCTAssertEqual(f.streamEnded(broken: false, now: t0), [.haptic(.arrived)])
    if case .answer(_, let a, _) = f.stage { XCTAssertEqual(a.headline, "Partly") } else { XCTFail("J3") }
  }

  /// `resync`: the turn is gone from the server's memory: the watch shows what it has and offers the phone.
  func testResyncOffersThePhone() {
    var f = WatchVoiceFlow()
    _ = f.hold(now: t0); _ = f.release(seconds: 2); _ = f.transcribed("q", now: t0); _ = f.sendNow()
    f.received(sse(nil, "resync", #"{"conversation_id": 5}"#))
    _ = f.streamEnded(broken: false, now: t0)
    if case .answer(_, let a, let c) = f.stage {
      XCTAssertTrue(a.more)
      XCTAssertEqual(c, 5)
    } else {
      XCTFail("J4")
    }
  }

  func testRefusalsAndRetry() {
    var f = WatchVoiceFlow()
    _ = f.hold(now: t0); _ = f.release(seconds: 2)
    XCTAssertEqual(f.failed(.offline), [.haptic(.failure)])
    XCTAssertEqual(f.retry(), [.transcribe])   // the same recording again
    _ = f.transcribed("q", now: t0); _ = f.sendNow()
    XCTAssertEqual(f.failed(.signedOut), [.haptic(.failure), .dropToken])
    XCTAssertEqual(f.retry(), [])   // the phone has to give a new code first
    f.close()
    XCTAssertTrue(f.isIdle)
    // a stream that says `error` (a budget used up halfway)
    _ = f.hold(now: t0); _ = f.release(seconds: 2); _ = f.transcribed("q", now: t0); _ = f.sendNow()
    f.received(sse(1, "error", #"{"code": "budget", "message": "x"}"#))
    _ = f.streamEnded(broken: false, now: t0)
    XCTAssertEqual(f.stage, .failed(.allowance))
  }

  func testMicrophoneRefused() {
    var f = WatchVoiceFlow()
    _ = f.hold(now: t0)
    XCTAssertEqual(f.microphoneRefused(), [.haptic(.failure)])
    XCTAssertEqual(f.stage, .failed(.microphone))
  }
}

final class WatchSSETests: XCTestCase {
  /// The server's format (ai/runs.py): id, event, one data line, a blank line (which URLSession's line reader drops).
  func testParserEmitsAtTheDataLine() {
    var p = WatchSSEParser()
    var out: [WatchSSEEvent] = []
    for l in ["id: 1", "event: conversation", "data: {\"id\": 3}", "", "id: 2", "event: text", "data: {\"delta\": \"hi\"}",
              ": keep-alive", "event: resync", "data: {\"conversation_id\": 3}"] {
      if let e = p.feed(l) { out.append(e) }
    }
    XCTAssertEqual(out.map(\.event), ["conversation", "text", "resync"])
    XCTAssertEqual(out.map(\.id), [1, 2, nil])
    XCTAssertEqual(String(decoding: out[1].data, as: UTF8.self), "{\"delta\": \"hi\"}")
  }

  func testTurnAccumulatesAndSkipsRepeats() {
    var t = WatchChatTurn()
    t.apply(sse(1, "conversation", #"{"id": 3, "provider": "x"}"#))
    t.apply(sse(2, "tool", #"{"name": "log_food", "status": "running"}"#))
    XCTAssertTrue(t.working)
    t.apply(sse(3, "text", #"{"delta": "A"}"#))
    t.apply(sse(3, "text", #"{"delta": "A"}"#))   // replayed by a resume
    t.apply(sse(4, "text", #"{"delta": "B"}"#))
    t.apply(sse(5, "usage", #"{"tokens": 1}"#))
    XCTAssertEqual(t.text, "AB")
    XCTAssertEqual(t.lastId, 5)
    XCTAssertFalse(t.finished)
    t.apply(sse(6, "done", #"{"conversation_id": 3}"#))
    XCTAssertTrue(t.finished)
    XCTAssertEqual(t.conversationId, 3)
  }

  /// A card that is not a meal, or a meal card without what it wrote, is no receipt.
  func testOnlyMealCardsWithWritesMakeAReceipt() {
    var t = WatchChatTurn()
    t.apply(sse(1, "card", #"{"card": {"type": "recovery", "value": 82}}"#))
    t.apply(sse(2, "card", #"{"card": {"type": "meal", "totals": {"calories": 900}, "written": []}}"#))
    XCTAssertNil(t.meal)
    t.apply(sse(3, "card", #"{"card": {"type": "meal", "totals": {"calories": 900}, "goal": null, "written": [{"tool": "log_meal_template", "date": "2026-01-02", "entry_ids": [], "log_id": 55, "meal": "dinner", "foods": ["Rice"], "calories": 500, "protein_g": null}]}}"#))
    XCTAssertEqual(t.meal?.writes.first?.logId, 55)
    XCTAssertNil(t.meal?.goalKcal)
    XCTAssertNil(t.meal?.proteinG)
  }
}

final class WatchVoiceAnswerTests: XCTestCase {
  func testHeadlineBodyAndMore() {
    XCTAssertEqual(WatchAnswer.of("680 kcal left\nProtein 40 g short."),
                   WatchAnswer(headline: "680 kcal left", body: "Protein 40 g short.", more: false))
    // the server's own hint becomes the 「在手机上继续」 key, not a line of text
    let a = WatchAnswer.of("4 of 5 days\nLoad above usual. More on your phone")
    XCTAssertTrue(a.more)
    XCTAssertEqual(a.headline, "4 of 5 days")
    XCTAssertEqual(WatchAnswer.of("可以练\n恢复够。详细的在手机上看").body, "恢复够。")
    XCTAssertEqual(a.body, "Load above usual.")
    XCTAssertTrue(WatchAnswer.of("可以练\n恢复够。详细的在手机上看").more)
    // one long line: its first sentence is the headline
    let b = WatchAnswer.of("Yes. Recovery is high and sleep was long, so legs are fine today.")
    XCTAssertEqual(b.headline, "Yes.")
    XCTAssertEqual(b.body, "Recovery is high and sleep was long, so legs are fine today.")
    XCTAssertTrue(WatchAnswer.of(String(repeating: "word ", count: 40)).more)
  }
}

final class WatchVoiceErrorTests: XCTestCase {
  /// #458's error codes, each to one screen with a way on.
  func testHTTPMapping() {
    XCTAssertEqual(WatchVoiceFailure.http(status: 401, code: nil), .signedOut)
    XCTAssertEqual(WatchVoiceFailure.http(status: 429, code: "rate_limit"), .dailyLimit)
    XCTAssertEqual(WatchVoiceFailure.http(status: 402, code: "quota"), .allowance)
    XCTAssertEqual(WatchVoiceFailure.http(status: 402, code: "budget"), .allowance)
    XCTAssertEqual(WatchVoiceFailure.http(status: 403, code: "fallback_consent"), .consent)
    XCTAssertEqual(WatchVoiceFailure.http(status: 403, code: "needs_consent"), .consent)
    XCTAssertEqual(WatchVoiceFailure.http(status: 403, code: "read_only"), .readOnly)
    XCTAssertEqual(WatchVoiceFailure.http(status: 409, code: "subscription"), .noAI)
    XCTAssertEqual(WatchVoiceFailure.http(status: 409, code: "no_provider"), .noAI)
    XCTAssertEqual(WatchVoiceFailure.http(status: 409, code: "busy"), .server)
    XCTAssertEqual(WatchVoiceFailure.http(status: 422, code: "bad_audio"), .unheard)
    XCTAssertEqual(WatchVoiceFailure.http(status: 413, code: "too_large"), .unheard)
    XCTAssertEqual(WatchVoiceFailure.http(status: 503, code: "asr_off"), .server)
    XCTAssertEqual(WatchVoiceFailure.http(status: 502, code: "asr_failed"), .server)
    XCTAssertEqual(WatchVoiceFailure.stream(code: "quota"), .allowance)
    XCTAssertEqual(WatchVoiceFailure.stream(code: "llm_error"), .server)
  }

  /// Never a dead end: every failure has a retry, dictation or the way home; dictation where only the transcriber failed.
  func testEveryFailureHasAWayOn() {
    let all: [WatchVoiceFailure] = [.offline, .dailyLimit, .noAI, .consent, .allowance, .readOnly, .unheard, .server, .signedOut,
                                    .interrupted, .microphone]
    for f in all {
      XCTAssertFalse(f.label.isEmpty)
      XCTAssertFalse(f.icon.isEmpty)
    }
    XCTAssertTrue(WatchVoiceFailure.dailyLimit.dictation)
    XCTAssertFalse(WatchVoiceFailure.allowance.dictation)   // the chat itself is used up: dictation would fail too
    XCTAssertFalse(WatchVoiceFailure.allowance.retryable)
    XCTAssertTrue(WatchVoiceFailure.offline.retryable)
  }

  /// Both languages for every label (the watch follows the phone's language).
  func testLabelsInBothLanguages() {
    let saved = Lang.override
    defer { Lang.override = saved }
    Lang.override = "zh"
    let zh = WatchVoiceFailure.dailyLimit.label
    Lang.override = "en"
    let en = WatchVoiceFailure.dailyLimit.label
    XCTAssertNotEqual(zh, en)
    XCTAssertLessThanOrEqual(zh.count, 4)
  }
}

final class WatchVoiceReceiptTests: XCTestCase {
  private let meal = WatchVoiceMeal(writes: [WatchVoiceWrite(tool: "log_food", date: "2026-01-02", logId: nil, entryIds: ["1", "2"],
                                                             meal: "lunch", foods: ["Noodles", "Egg"], calories: 620, proteinG: 34)],
                                    dayKcal: 1230, goalKcal: 2300)

  /// M3 from the server's card: name, kcal, protein, the bar from the server's own totals (nothing from the phone).
  func testReceiptAndBar() {
    let l = WatchQuickLog.voiceMeal(meal, id: "v1", at: t0)
    XCTAssertEqual(l.name, "Noodles + Egg")
    XCTAssertEqual(l.kcal, 620)
    XCTAssertEqual(l.day, "2026-01-02")
    let bar = l.bar(logs: [l], quick: nil)
    XCTAssertEqual(bar?.before, 610)
    XCTAssertEqual(bar?.added, 620)
    XCTAssertEqual(bar?.goal, 2300)
    var noGoal = meal
    noGoal.goalKcal = nil
    XCTAssertNil(WatchQuickLog.voiceMeal(noGoal, id: "v2", at: t0).bar(logs: [], quick: nil))   // no denominator, no bar
  }

  /// Undo goes through the phone: a `voiceUndo` event carrying what the server wrote (the watch token cannot delete).
  func testUndoHandsTheWritesToThePhone() throws {
    let l = WatchQuickLog.voiceMeal(meal, id: "v1", at: t0)
    let e = try XCTUnwrap(l.voiceUndo(id: "u1", at: t0))
    XCTAssertEqual(e.kind, .voiceUndo)
    XCTAssertEqual(e.target, "v1")
    XCTAssertEqual(e.writes, meal.writes)
    // it survives the trip (transferUserInfo)
    XCTAssertEqual(WatchCodec.event(WatchCodec.message(e)), e)
    // a quick log is not undone this way
    XCTAssertNil(WatchQuickLog.water(250, id: "w", day: "2026-01-02", at: t0).voiceUndo(id: "u2", at: t0))
    // the phone's merge of training events ignores it
    let r = WatchEventMerge.apply([e], to: [:], day: "2026-01-02", exercises: [], seen: [])
    XCTAssertTrue(r.applied.isEmpty)
  }

  func testSlotNames() {
    XCTAssertNotNil(WatchVoiceWrite.slotName("breakfast"))
    XCTAssertNotNil(WatchVoiceWrite.slotName("snacks"))
    XCTAssertNil(WatchVoiceWrite.slotName(nil))
    XCTAssertNil(WatchVoiceWrite.slotName("mixed"))
  }
}
