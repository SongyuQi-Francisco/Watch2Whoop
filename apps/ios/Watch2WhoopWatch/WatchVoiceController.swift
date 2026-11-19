import Combine
import Foundation
import WatchKit

/// Hold to talk (plan 2.2 / 2.3, mockups M1–M4, J1–J4): carries out what `WatchVoiceFlow` (the screens' rules, in
/// WatchShared, unit-tested) decides: the microphone, the watch's own token, the requests, the haptics. The model owns it
/// and hands it the phone's context; the screens read it as an environment object.
@MainActor
final class WatchVoiceController: ObservableObject {
  @Published private(set) var flow = WatchVoiceFlow()
  @Published private(set) var pairing: WatchPairing
  /// The last request failed for lack of network (the watch's own, not the phone's): the core is dark for a while.
  @Published private(set) var offlineAt: Date?
  /// The voice's loudness while listening (M1's ring).
  @Published private(set) var level: Double = 0
  @Published private(set) var now = Date()
  /// Seconds recorded so far (M1's 「0:02」).
  @Published private(set) var seconds: Double = 0

  /// The phone's latest context (signed in? voice offered? which account?).
  private(set) var offer: WatchVoiceOffer?
  private(set) var reachable = false

  private let store: WatchStore
  private var backend: WatchVoiceBackend
  private var recorder: WatchVoiceRecorder
  private let saveToken: (String) -> Void
  private let deleteToken: () -> Void
  /// The Keychain holds a token (read once at launch, then kept with every save / delete: no Keychain read per frame).
  private var tokenPresent: Bool
  /// The recording between release and a successful transcription (kept in memory only for 「重试」).
  private var recording: Data?
  private var work: Task<Void, Never>?
  private var ticker: AnyCancellable?
  private var frozen = false

  /// Asks the phone for a code (the link's sendMessage / transfer).
  var askPhoneForCode: (() -> Void)?
  /// A meal was logged: the model shows its receipt (M3).
  var onMeal: ((WatchVoiceMeal) -> Void)?
  /// J4: the conversation goes to the phone.
  var onContinue: ((Int) -> Void)?

  init(store: WatchStore, backend: WatchVoiceBackend? = nil, recorder: WatchVoiceRecorder? = nil, hasToken: (() -> Bool)? = nil,
       saveToken: ((String) -> Void)? = nil, deleteToken: (() -> Void)? = nil) {
    self.store = store
    var p = store.pairing()
    let present = (hasToken ?? { WatchKeychain.token() != nil })()
    tokenPresent = present
    let save = saveToken ?? { WatchKeychain.save($0) }
    let delete = deleteToken ?? { WatchKeychain.delete() }
    self.saveToken = save
    self.deleteToken = delete
    if p.paired && !present { p.lost() }   // the Keychain is the truth (a restore to another watch has none)
    pairing = p
    let s = store
    let server = { () -> String? in s.pairing().server }
    self.backend = backend ?? WatchVoiceAPI(server: server)
    self.recorder = recorder ?? WatchRecorder()
  }

  // MARK: - what the screens and the home rule read

  var offline: Bool { offlineAt.map { (frozen ? now : Date()).timeIntervalSince($0) < WatchParams.voiceOfflineSec } ?? false }

  var credential: WatchVoice.Credential {
    WatchVoice.Credential(paired: pairing.usable(for: offer) && tokenPresent, online: !offline)
  }

  var stage: WatchVoiceFlow.Stage { flow.stage }

  // MARK: - inputs

  /// The phone's context arrived: a sign-out or another account drops the token; voice offered without a token asks.
  func context(_ c: WatchContext?) {
    offer = c?.voice
    if pairing.phoneSays(signedIn: c?.signedIn ?? false, offer: c?.voice) {
      deleteToken()
      tokenPresent = false
      save()
    }
    askIfNeeded()
  }

  func reachable(_ r: Bool) {
    reachable = r
    askIfNeeded()
  }

  /// A code from the phone: redeemed at once (an expired one is not tried: a new one is asked for).
  func received(_ code: WatchPairCode) {
    guard code.fresh(now: Date()) else {
      pairing.askedAt = nil
      save()
      askIfNeeded()
      return
    }
    Task {
      do {
        let token = try await backend.redeem(code)
        saveToken(token)
        tokenPresent = true
        pairing.redeemed(code)
        offlineAt = nil
      } catch let e as WatchVoiceError where e == .network {
        offlineAt = Date()
        pairing.askedAt = nil   // ask again when the network is back
      } catch {
        pairing.lost()   // a used / expired / refused code: the next ask brings a new one
      }
      save()
      now = Date()
    }
  }

  /// The core is dark for want of a token: the hold asks the phone (once a minute at most).
  func askPhoneNow() {
    pairing.askedAt = nil
    askIfNeeded()
  }

  /// The finger rested on the core long enough (home): M1.
  func hold() {
    run(flow.hold(now: Date()))
  }

  /// The finger left the core; `cancelled` = it slid up (M1 「上滑取消」).
  func release(cancelled: Bool) {
    guard flow.isListening else { return }
    if cancelled {
      run(flow.cancel())
    } else {
      run(flow.release(seconds: recorder.elapsed))
    }
  }

  /// ✕ on M2, the core on J2, back from a voice screen.
  func cancel() {
    work?.cancel()
    work = nil
    run(flow.cancel())
  }

  func sendNow() { run(flow.sendNow()) }
  func retry() { run(flow.retry()) }
  func answerCall(approve: Bool) { run(flow.answerCall(approve: approve)) }
  func dictated(_ text: String) { run(flow.dictated(text)) }

  /// J4 「在手机上继续」: the conversation goes to the phone (queued when it is away), then home.
  func continueOnPhone(_ conversation: Int) {
    onContinue?(conversation)
    WKInterfaceDevice.current().play(.click)
    close()
  }

  func close() {
    work?.cancel()
    work = nil
    flow.close()
    recording = nil
  }

  // MARK: - effects

  private func run(_ effects: [WatchVoiceFlow.Effect]) {
    for e in effects {
      switch e {
      case .startRecording: startRecording()
      case .stopRecording(let keep):
        stopTicker()
        let d = recorder.stop(keep: keep)
        recording = keep ? d : nil
        level = 0
      case .transcribe: transcribe()
      case .send(let t): stream(conversation: true) { [backend] c in backend.chat(t, conversation: c) }
      case .approve(let id, let ok): stream(conversation: false) { [backend] _ in backend.approve(callId: id, approve: ok) }
      case .resume(let c, let after): resume(c, after: after)
      case .receipt(let m):
        stopTicker()
        onMeal?(m)
      case .haptic(let h): haptic(h)
      case .dropToken:
        deleteToken()
        tokenPresent = false
        pairing.lost()
        save()
        askIfNeeded()
      }
    }
    now = Date()
    // M2's ring and the listening ring move with the clock; nothing runs while idle (plan 4.7)
    switch flow.stage {
    case .listening, .confirming, .thinking, .transcribing: startTicker()
    default: stopTicker()
    }
  }

  private func startRecording() {
    seconds = 0
    work = Task {
      let ok = await recorder.start()
      // the finger may have left while the permission sheet was up: nothing is recorded then
      guard flow.isListening, !Task.isCancelled else {
        if ok { _ = recorder.stop(keep: false) }
        return
      }
      if !ok {
        _ = recorder.stop(keep: false)
        run(flow.microphoneRefused())
      }
    }
  }

  private func transcribe() {
    guard let wav = recording, let info = WatchWAV.inspect(wav), info.accepted,
          WatchRecordingCheck.of(seconds: info.seconds) != .tooShort else {
      run(flow.failed(.unheard))
      return
    }
    work = Task {
      do {
        let text = try await backend.transcribe(wav)
        guard !Task.isCancelled else { return }
        recording = nil   // the server keeps no recording; neither does the watch once it has the words
        offlineAt = nil
        run(flow.transcribed(text, now: Date()))
      } catch {
        guard !Task.isCancelled else { return }
        fail(WatchVoiceError.of(error))
      }
    }
  }

  private func stream(conversation: Bool, _ open: @escaping (Int?) -> AsyncThrowingStream<WatchSSEEvent, Error>) {
    let follow = conversation ? flow.followUp(now: Date()) : nil
    consume(open(follow))
  }

  private func resume(_ c: Int, after: Int) {
    work = Task {
      try? await Task.sleep(nanoseconds: UInt64(WatchParams.voiceResumeDelaySec * 1_000_000_000))
      guard !Task.isCancelled else { return }
      consume(backend.events(conversation: c, after: after))
    }
  }

  private func consume(_ s: AsyncThrowingStream<WatchSSEEvent, Error>) {
    let t = Task {
      var got = false
      do {
        for try await e in s {
          guard !Task.isCancelled else { return }
          got = true
          offlineAt = nil
          flow.received(e)
        }
        guard !Task.isCancelled else { return }
        run(flow.streamEnded(broken: false, now: Date()))
      } catch {
        guard !Task.isCancelled else { return }
        let v = WatchVoiceError.of(error)
        if case .http = v { fail(v); return }
        // no answer at all = no network; a stream that started and broke = pick it up again
        if !got && flow.turn.conversationId == nil { fail(v); return }
        run(flow.streamEnded(broken: true, now: Date()))
      }
    }
    work = t
  }

  private func fail(_ e: WatchVoiceError) {
    if e == .network { offlineAt = Date() }
    run(flow.failed(e.failure))
  }

  private func askIfNeeded() {
    let n = Date()
    guard pairing.shouldAsk(offer: offer, reachable: reachable, now: n) else { return }
    pairing.asked(at: n)
    save()
    askPhoneForCode?()
  }

  private func save() { store.save(pairing) }

  private func haptic(_ h: WatchVoiceFlow.Haptic) {
    guard !frozen else { return }
    let d = WKInterfaceDevice.current()
    switch h {
    case .start: d.play(.start)
    case .stop: d.play(.stop)
    case .failure: d.play(.failure)
    case .arrived: d.play(.notification)
    case .success: d.play(.success)
    }
  }

  private func startTicker() {
    guard ticker == nil, !frozen else { return }
    ticker = Timer.publish(every: 1.0 / 30, on: .main, in: .common).autoconnect().sink { [weak self] d in self?.tick(d) }
  }

  private func stopTicker() {
    ticker = nil
  }

  private func tick(_ d: Date) {
    now = d
    if flow.isListening {
      level = recorder.level()
      seconds = recorder.elapsed
      if WatchRecordingCheck.of(seconds: seconds) == .clipped {
        run(flow.release(seconds: seconds))   // the limit: send what was said
        return
      }
    }
    let fx = flow.tick(now: d)
    if !fx.isEmpty { run(fx) }
  }

  #if DEBUG
  /// Debug: the interactive seed's paired state (no Keychain, a synthetic server).
  func seedPaired(_ account: String) {
    pairing = WatchPairing(server: "https://seed.invalid", account: account, paired: true, askedAt: nil)
  }

  /// Frozen screenshots: one voice screen, the clock stopped.
  func demo(_ s: WatchVoiceFlow.Stage, level: Double = 0.6, seconds: Double = 2, at: Date) {
    frozen = true
    flow.demo(s)
    self.level = level
    self.seconds = seconds
    now = at
  }

  func demoOffline(at: Date) {
    offlineAt = at
    now = at
  }
  #endif
}
