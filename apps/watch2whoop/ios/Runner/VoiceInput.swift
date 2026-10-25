// Jarvis voice input on the phone itself (#53, app 1.0.8): Apple's speech recogniser, on device only, so words appear on the
// Today page while the person is still talking (web/static/jarvis-entry.js). Dart (lib/src/voice_bridge.dart) passes the
// page's voice:* messages here over the channel "watch2whoop/voice" and hands the events back to the page.
//
// - requiresOnDeviceRecognition = true: the sound is never sent to Apple for this; a language whose on-device model this
//   phone does not have is not offered (`langs`), and the page records and lets its own server recognise instead.
// - The microphone is captured here (AVAudioEngine), not by the WebView, and only between start and stop / cancel; the
//   audio session is given back right after.
// - Chinese (the page asks with audio = true): the same recording is also kept in memory as 16 kHz mono PCM and handed to the
//   page once, inside the final event, so the server's recogniser can finalise it (it is more accurate for Chinese). It is
//   never written to disk and is dropped with the session. English stays on the phone.
// - A call, Siri or another app taking the audio session ends the session with error "interrupted".
// - Haptics for the page (haptic:<kind>) are here too: the same channel, method "haptic".
import AVFoundation
import Flutter
import Speech
import UIKit

final class VoiceBridge: NSObject {
  static let shared = VoiceBridge()
  var channel: FlutterMethodChannel?

  /// At most this much audio is kept (the page stops at 60 s).
  static let maxSeconds = 61.0
  /// After stop: the recogniser's final result usually comes within a moment; after this the last partial text is used.
  static let finalWait = 2.5
  /// Level events to the page at most this often (seconds).
  static let levelEvery = 0.05
  static let pcmFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!

  private let engine = AVAudioEngine()
  private let audioQueue = DispatchQueue(label: "watch2whoop.voice.audio")
  private var request: SFSpeechAudioBufferRecognitionRequest?
  private var task: SFSpeechRecognitionTask?
  private var converter: AVAudioConverter?
  private var session: String?
  private var lang = "zh"
  private var keepAudio = false
  private var pcm = Data()          // audioQueue only
  private var lastText = ""
  private var startedAt: Date?
  private var lastLevelAt = Date.distantPast
  private var finishing = false
  private var finishTimer: Timer?
  private var observing = false
  private var run = 0               // one per started recogniser: late callbacks of an earlier one are ignored

  // ---------------------------------------------------------------- channel
  func handle(_ call: FlutterMethodCall, _ result: @escaping FlutterResult) {
    let a = call.arguments as? [String: Any] ?? [:]
    let s = a["session"] as? String ?? ""
    switch call.method {
    case "langs": result(VoiceBridge.supportedLangs())
    case "start": start(session: s, lang: a["lang"] as? String ?? "zh", audio: a["audio"] as? Bool ?? false); result(nil)
    case "stop": stop(session: s); result(nil)
    case "cancel": cancel(session: s); result(nil)
    case "lang":
      // a new language starts recognition again from the beginning (the page clears what it showed)
      if s == session { teardown(); start(session: s, lang: a["lang"] as? String ?? lang, audio: a["audio"] as? Bool ?? false) }
      result(nil)
    case "haptic": VoiceBridge.haptic(call.arguments as? String ?? ""); result(nil)
    default: result(FlutterMethodNotImplemented)
    }
  }

  private func emit(_ event: [String: Any], session given: String? = nil) {
    guard let s = given ?? session else { return }
    var e = event
    e["session"] = s
    DispatchQueue.main.async { self.channel?.invokeMethod("event", arguments: e) }
  }

  // ---------------------------------------------------------------- languages
  /// The locale for "zh" / "en" whose recogniser runs on this phone, or nil. English: the phone's own English region first.
  static func locale(for lang: String) -> Locale? {
    var ids = ["zh-CN"]
    if lang == "en" {
      let cur = Locale.current.identifier.components(separatedBy: "@")[0].replacingOccurrences(of: "_", with: "-")
      ids = (cur.hasPrefix("en-") ? [cur] : []) + ["en-AU", "en-US", "en-GB"]
    } else if lang != "zh" {
      return nil
    }
    for id in ids {
      let loc = Locale(identifier: id)
      if let r = SFSpeechRecognizer(locale: loc), r.supportsOnDeviceRecognition { return loc }
    }
    return nil
  }

  static func supportedLangs() -> [String] { ["zh", "en"].filter { locale(for: $0) != nil } }

  // ---------------------------------------------------------------- session
  private func start(session s: String, lang l: String, audio: Bool) {
    teardown()   // a new session replaces any earlier one
    guard !s.isEmpty else { return }
    session = s; lang = l; keepAudio = audio
    VoiceBridge.authorize { [weak self] ok in
      guard let self, self.session == s else { return }   // cancelled while iOS was asking
      guard ok else { self.fail("permission"); return }
      do { try self.begin() } catch let e as VoiceFailure { self.fail(e.code) } catch { self.fail("unavailable", error.localizedDescription) }
    }
  }

  private struct VoiceFailure: Error { let code: String }

  /// Speech recognition, then the microphone (each asks only the first time).
  static func authorize(_ done: @escaping (Bool) -> Void) {
    let mic = {
      let answer: (Bool) -> Void = { ok in DispatchQueue.main.async { done(ok) } }
      if #available(iOS 17.0, *) { AVAudioApplication.requestRecordPermission(completionHandler: answer) }
      else { AVAudioSession.sharedInstance().requestRecordPermission(answer) }
    }
    switch SFSpeechRecognizer.authorizationStatus() {
    case .authorized: mic()
    case .notDetermined:
      SFSpeechRecognizer.requestAuthorization { st in DispatchQueue.main.async { st == .authorized ? mic() : done(false) } }
    default: done(false)
    }
  }

  private func begin() throws {
    guard let loc = VoiceBridge.locale(for: lang), let rec = SFSpeechRecognizer(locale: loc), rec.isAvailable else {
      throw VoiceFailure(code: "unsupported_lang")
    }
    let av = AVAudioSession.sharedInstance()
    try av.setCategory(.record, mode: .measurement, options: [.duckOthers])
    try av.setActive(true, options: .notifyOthersOnDeactivation)
    let req = SFSpeechAudioBufferRecognitionRequest()
    req.shouldReportPartialResults = true
    req.requiresOnDeviceRecognition = true   // never to Apple's servers
    req.taskHint = .dictation
    if #available(iOS 16.0, *) { req.addsPunctuation = true }
    let input = engine.inputNode
    let fmt = input.outputFormat(forBus: 0)
    guard fmt.sampleRate > 0, fmt.channelCount > 0 else { throw VoiceFailure(code: "unavailable") }   // no microphone
    converter = keepAudio ? AVAudioConverter(from: fmt, to: VoiceBridge.pcmFormat) : nil
    audioQueue.sync { pcm = Data() }
    run += 1
    let r = run, s = session
    input.removeTap(onBus: 0)
    input.installTap(onBus: 0, bufferSize: 1024, format: fmt) { [weak self] buf, _ in
      req.append(buf)
      self?.tapped(buf, s)
    }
    request = req
    task = rec.recognitionTask(with: req) { [weak self] res, err in
      DispatchQueue.main.async { self?.recognized(res, err, r) }
    }
    engine.prepare()
    try engine.start()
    startedAt = Date()
    lastText = ""
    observe()
    emit(["type": "started"])
  }

  /// Audio thread: the loudness for the page's waveform, and for Chinese the 16 kHz copy for the server's final pass.
  private func tapped(_ buf: AVAudioPCMBuffer, _ s: String?) {
    if let ch = buf.floatChannelData?[0], buf.frameLength > 0 {
      var sum: Float = 0
      let n = Int(buf.frameLength)
      for i in stride(from: 0, to: n, by: 4) { sum += ch[i] * ch[i] }
      let rms = sqrt(sum / Float(max(1, n / 4)))
      let level = min(1, sqrt(rms) * 2)
      let now = Date()
      if now.timeIntervalSince(lastLevelAt) >= VoiceBridge.levelEvery {
        lastLevelAt = now
        emit(["type": "level", "rms": Double(level)], session: s)
      }
    }
    guard let conv = converter else { return }
    let ratio = VoiceBridge.pcmFormat.sampleRate / buf.format.sampleRate
    guard let out = AVAudioPCMBuffer(pcmFormat: VoiceBridge.pcmFormat,
                                     frameCapacity: AVAudioFrameCount(Double(buf.frameLength) * ratio) + 32) else { return }
    var fed = false
    var error: NSError?
    conv.convert(to: out, error: &error) { _, status in
      if fed { status.pointee = .noDataNow; return nil }
      fed = true
      status.pointee = .haveData
      return buf
    }
    guard error == nil, out.frameLength > 0, let samples = out.int16ChannelData?[0] else { return }
    let bytes = Data(bytes: samples, count: Int(out.frameLength) * 2)
    audioQueue.async {
      if Double(self.pcm.count) < VoiceBridge.maxSeconds * VoiceBridge.pcmFormat.sampleRate * 2 { self.pcm.append(bytes) }
    }
  }

  private func recognized(_ res: SFSpeechRecognitionResult?, _ err: Error?, _ r: Int) {
    guard r == run, session != nil else { return }
    if let r = res {
      lastText = r.bestTranscription.formattedString
      if r.isFinal { finish(lastText); return }
      if !finishing { emit(["type": "partial", "text": lastText]) }
    }
    if err != nil {
      if finishing { finish(lastText) }   // e.g. nothing recognised after the end of the audio: the page decides
      else if res == nil { fail("unavailable", err?.localizedDescription) }
    }
  }

  private func stop(session s: String) {
    guard s == session else { return }
    guard request != nil, !finishing else {
      if request == nil { fail("unavailable") }   // never started (no permission answer yet, or it failed)
      return
    }
    finishing = true
    stopAudio()
    request?.endAudio()
    finishTimer = Timer.scheduledTimer(withTimeInterval: VoiceBridge.finalWait, repeats: false) { [weak self] _ in
      guard let self else { return }
      self.finish(self.lastText)
    }
  }

  private func finish(_ text: String) {
    guard session != nil else { return }
    let seconds = startedAt.map { Date().timeIntervalSince($0) } ?? 0
    var e: [String: Any] = ["type": "final", "text": text, "seconds": seconds, "lang": lang]
    if keepAudio {
      let data = audioQueue.sync { pcm }
      if !data.isEmpty { e["wav"] = VoiceBridge.wav(data).base64EncodedString() }
    }
    emit(e)
    teardown()
  }

  private func cancel(session s: String) {
    if s == "*" || s == session { teardown() }
  }

  /// The page shows its own text per code; `message` (the system's wording) is only for logs.
  private func fail(_ code: String, _ message: String? = nil) {
    emit(["type": "error", "code": code, "message": message ?? code])
    teardown()
  }

  private func stopAudio() {
    if engine.isRunning { engine.stop() }
    engine.inputNode.removeTap(onBus: 0)
  }

  /// Ends everything of the session: recogniser, microphone, audio session, the kept recording.
  private func teardown() {
    finishTimer?.invalidate(); finishTimer = nil
    stopAudio()
    run += 1   // whatever the cancelled recogniser still reports is dropped
    task?.cancel(); task = nil
    request = nil; converter = nil
    audioQueue.sync { pcm = Data() }
    if session != nil { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
    session = nil; finishing = false; startedAt = nil; lastText = ""
    if observing { NotificationCenter.default.removeObserver(self); observing = false }
  }

  private func observe() {
    guard !observing else { return }
    observing = true
    NotificationCenter.default.addObserver(self, selector: #selector(interrupted(_:)),
                                           name: AVAudioSession.interruptionNotification, object: nil)
  }

  @objc private func interrupted(_ n: Notification) {
    guard let raw = n.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
          AVAudioSession.InterruptionType(rawValue: raw) == .began else { return }
    DispatchQueue.main.async { if self.session != nil { self.fail("interrupted") } }
  }

  // ---------------------------------------------------------------- helpers
  /// 16-bit mono PCM at 16 kHz -> a WAV file (what POST /v1/assistant/transcribe takes).
  static func wav(_ pcm: Data) -> Data {
    var d = Data()
    func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
    func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
    d.append(contentsOf: Array("RIFF".utf8)); u32(36 + UInt32(pcm.count)); d.append(contentsOf: Array("WAVE".utf8))
    d.append(contentsOf: Array("fmt ".utf8)); u32(16); u16(1); u16(1); u32(16000); u32(32000); u16(2); u16(16)
    d.append(contentsOf: Array("data".utf8)); u32(UInt32(pcm.count))
    d.append(pcm)
    return d
  }

  /// The page's haptic:<kind> (only sent when the app says it has haptics).
  static func haptic(_ kind: String) {
    DispatchQueue.main.async {
      switch kind {
      case "light": UIImpactFeedbackGenerator(style: .light).impactOccurred()
      case "medium": UIImpactFeedbackGenerator(style: .medium).impactOccurred()
      case "selection": UISelectionFeedbackGenerator().selectionChanged()
      case "success": UINotificationFeedbackGenerator().notificationOccurred(.success)
      case "warning": UINotificationFeedbackGenerator().notificationOccurred(.warning)
      default: break
      }
    }
  }
}
