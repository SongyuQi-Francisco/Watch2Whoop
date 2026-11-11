import AVFoundation
import Speech
import UIKit

/// Hold-to-talk on the phone itself (from the Flutter app's VoiceInput.swift, 1.0.8+): Apple's speech recogniser, on
/// device only, so words appear while the person is still talking.
///
/// - `requiresOnDeviceRecognition`: the sound is never sent to Apple. A language whose on-device model this phone lacks
///   is not offered (`supportedLangs`).
/// - The microphone is open only between `start` and `stop` / `cancel`; the audio session is given back right after.
/// - Chinese (`keepAudio`): the same recording is kept in memory as 16 kHz mono PCM and returned once as a WAV, so the
///   server's recogniser can finalise it with the user's own terms (more accurate for Chinese). Never written to disk.
/// - A call, Siri or another app taking the audio session ends the session (`onInterrupted`).
@MainActor
final class JarvisVoice: ObservableObject {
  enum Failure: Error, Equatable { case permission, unsupportedLang, unavailable, interrupted }

  /// What 完成 gives at once (2.0.1): the words recognised so far and, for Chinese, the whole recording (the microphone
  /// is already off, so it is complete). `final` is the recogniser's last word on it: its final result, or the words once
  /// they have not changed for `stableSeconds`, at most `finalCap` after 完成. On-device recognition often never says
  /// final; 2.0.0 waited 2.5 s for it before anything was shown, and only then asked the server.
  struct Take {
    let text: String
    let seconds: Double
    let wav: Data?
    let final: Task<String, Never>
  }

  /// The last `bars` loudness values, 0…1 (the waveform), newest last.
  @Published private(set) var levels: [Double]
  /// Words so far (the last few may still change).
  @Published private(set) var partial = ""
  @Published private(set) var running = false
  var onInterrupted: (() -> Void)?

  static let bars = 34
  /// At most this much audio is kept (the page stops at 60 s).
  static let maxSeconds = 60.0
  /// After 完成: words that have not changed for this long count as final, and nothing is waited for longer than finalCap.
  static let stableSeconds = 0.4
  static let finalCap = 1.0
  nonisolated static let pcmFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!

  private let engine = AVAudioEngine()
  /// The audio session and the engine are set up and torn down here, never on the main thread: the first touch of the
  /// microphone input takes a few hundred ms, which would freeze the key's animation.
  private let audioQueue = DispatchQueue(label: "org.watch2whoop.jarvis.audio", qos: .userInitiated)
  nonisolated(unsafe) private var tapped = false   // audioQueue only
  private let audio = AudioSink()
  private var request: SFSpeechAudioBufferRecognitionRequest?
  private var task: SFSpeechRecognitionTask?
  private var startedAt: Date?
  /// When `partial` last changed (CACurrentMediaTime).
  private var partialAt: CFTimeInterval = 0
  private var run = 0
  private var finishing: CheckedContinuation<String, Never>?
  private var observer: NSObjectProtocol?

  init() {
    levels = Array(repeating: 0, count: Self.bars)
  }

  var elapsed: Double { startedAt.map { Date().timeIntervalSince($0) } ?? 0 }

  // MARK: - languages

  /// The locale for "zh" / "en" whose recogniser runs on this phone, or nil. English: the phone's own English region first.
  nonisolated static func locale(for lang: String) -> Locale? {
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

  /// Read once per launch: asking the recognisers costs milliseconds, and the key asks on every touch.
  nonisolated static let supportedLangs: [String] = {
    #if DEBUG && targetEnvironment(simulator)
    if JarvisDemo.scriptedVoice { return ["zh", "en"] }
    #endif
    return ["zh", "en"].filter { locale(for: $0) != nil }
  }()

  /// The recognition language remembered on this phone, else the UI language.
  static var lang: String {
    get { UserDefaults.standard.string(forKey: "w2w.jarvis.voiceLang.v1") ?? Lang.code }
    set { UserDefaults.standard.set(newValue, forKey: "w2w.jarvis.voiceLang.v1") }
  }

  // MARK: - session

  /// Asks for speech recognition and the microphone (only the first time), then listens.
  func start(lang: String, keepAudio: Bool) async throws {
    teardown()
    #if DEBUG && targetEnvironment(simulator)
    if JarvisDemo.scriptedVoice { scriptKeepsAudio = keepAudio; startScript(); return }
    #endif
    guard await Self.authorize() else { throw Failure.permission }
    guard let loc = Self.locale(for: lang), let rec = SFSpeechRecognizer(locale: loc), rec.isAvailable else {
      throw Failure.unsupportedLang
    }
    let req = SFSpeechAudioBufferRecognitionRequest()
    req.shouldReportPartialResults = true
    req.requiresOnDeviceRecognition = true   // never to Apple's servers
    req.taskHint = .dictation
    req.addsPunctuation = true
    run += 1
    let r = run
    let sink = audio, engine = self.engine
    let ok: Bool = await withCheckedContinuation { c in
      audioQueue.async { [weak self] in
        do {
          let av = AVAudioSession.sharedInstance()
          try av.setCategory(.record, mode: .measurement, options: [.duckOthers])
          try av.setActive(true, options: .notifyOthersOnDeactivation)
          let input = engine.inputNode
          let fmt = input.outputFormat(forBus: 0)
          guard fmt.sampleRate > 0, fmt.channelCount > 0 else { c.resume(returning: false); return }   // no microphone
          sink.reset(converter: keepAudio ? AVAudioConverter(from: fmt, to: Self.pcmFormat) : nil)
          input.removeTap(onBus: 0)
          input.installTap(onBus: 0, bufferSize: 1024, format: fmt) { buf, _ in
            req.append(buf)
            if let level = sink.take(buf) {
              Task { @MainActor in self?.push(level, run: r) }
            }
          }
          self?.tapped = true
          engine.prepare()
          try engine.start()
          c.resume(returning: true)
        } catch {
          c.resume(returning: false)
        }
      }
    }
    guard ok, r == run else { teardown(); throw Failure.unavailable }
    request = req
    task = rec.recognitionTask(with: req) { [weak self] res, err in
      let text = res?.bestTranscription.formattedString
      let isFinal = res?.isFinal ?? false
      Task { @MainActor in self?.recognized(text, isFinal: isFinal, failed: err != nil, run: r) }
    }
    startedAt = Date()
    running = true
    observer = NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] n in
      guard let raw = n.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
            AVAudioSession.InterruptionType(rawValue: raw) == .began else { return }
      Task { @MainActor in
        guard let self, self.running else { return }
        self.teardown()
        self.onInterrupted?()
      }
    }
  }

  /// 完成: stops listening and returns at once (a few ms: the engine stops on its own queue) with what is there; the
  /// recogniser keeps going in the background for its final word (`Take.final`), then the session is torn down. nil when
  /// nothing was said and nothing recorded.
  func finish() async -> Take? {
    guard running else { return nil }
    let seconds = elapsed
    let r = run
    running = false
    levels = Array(repeating: 0, count: Self.bars)
    var pcm = Data()
    #if DEBUG && targetEnvironment(simulator)
    let scripted = JarvisDemo.scriptedVoice
    if scripted {
      script?.invalidate()
      script = nil
      if scriptKeepsAudio { pcm = Data(count: Int(seconds * 16000) * 2) }
    }
    #else
    let scripted = false
    #endif
    if !scripted {
      let sink = audio
      pcm = await withCheckedContinuation { c in
        stopAudio { c.resume(returning: sink.pcm()) }
        request?.endAudio()
      }
    }
    let now = partial.trimmingCharacters(in: .whitespacesAndNewlines)
    partialAt = CACurrentMediaTime()
    let final = Task { @MainActor [weak self] () -> String in
      guard let self, self.run == r else { return now }
      let text: String = await withCheckedContinuation { c in
        self.finishing = c
        self.watchFinal(run: r, since: CACurrentMediaTime())
      }
      if self.run == r { self.teardown() }
      let said = text.trimmingCharacters(in: .whitespacesAndNewlines)
      return said.isEmpty ? now : said
    }
    if now.isEmpty && pcm.isEmpty {
      final.cancel()
      if run == r { teardown() }
      return nil
    }
    return Take(text: now, seconds: seconds, wav: pcm.isEmpty ? nil : Self.wav(pcm), final: final)
  }

  /// Ends the wait for the final word when the words have been still for `stableSeconds`, or at `finalCap`.
  private func watchFinal(run r: Int, since start: CFTimeInterval) {
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
      guard let self, self.run == r, self.finishing != nil else { return }
      let t = CACurrentMediaTime()
      if t - self.partialAt >= Self.stableSeconds || t - start >= Self.finalCap {
        self.finish(self.partial)
      } else {
        self.watchFinal(run: r, since: start)
      }
    }
  }

  func cancel() { teardown() }

  // MARK: - private

  static func authorize() async -> Bool {
    let speech: Bool = await withCheckedContinuation { c in
      switch SFSpeechRecognizer.authorizationStatus() {
      case .authorized: c.resume(returning: true)
      case .notDetermined: SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0 == .authorized) }
      default: c.resume(returning: false)
      }
    }
    guard speech else { return false }
    return await withCheckedContinuation { c in
      if #available(iOS 17.0, *) {
        AVAudioApplication.requestRecordPermission { c.resume(returning: $0) }
      } else {
        AVAudioSession.sharedInstance().requestRecordPermission { c.resume(returning: $0) }
      }
    }
  }

  private func push(_ level: Double, run r: Int) {
    guard r == run, running else { return }
    levels.removeFirst()
    levels.append(level)
  }

  private func recognized(_ text: String?, isFinal: Bool, failed: Bool, run r: Int) {
    guard r == run else { return }
    if let text, text != partial { partial = text; partialAt = CACurrentMediaTime() }
    if isFinal || (failed && finishing != nil) { finish(partial) }
  }

  private func finish(_ text: String) {
    guard let c = finishing else { return }
    finishing = nil
    c.resume(returning: text)
  }

  /// `then` runs on the audio queue once the engine has stopped (nothing more reaches the sink after that).
  private func stopAudio(deactivate: Bool = false, then: (@Sendable () -> Void)? = nil) {
    let engine = self.engine
    audioQueue.async { [weak self] in
      if engine.isRunning { engine.stop() }
      if self?.tapped == true { engine.inputNode.removeTap(onBus: 0); self?.tapped = false }
      then?()
      if deactivate { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
    }
  }

  private func teardown() {
    #if DEBUG && targetEnvironment(simulator)
    script?.invalidate()
    script = nil
    #endif
    let wasRunning = running || request != nil
    stopAudio(deactivate: wasRunning)
    run += 1   // whatever the cancelled recogniser still reports is dropped
    task?.cancel()
    task = nil
    request = nil
    finish(partial)
    audio.reset(converter: nil)
    if let observer { NotificationCenter.default.removeObserver(observer) }
    observer = nil
    running = false
    startedAt = nil
    partial = ""
    levels = Array(repeating: 0, count: Self.bars)
  }

  /// 16-bit mono PCM at 16 kHz → a WAV file (what `POST /v1/assistant/transcribe` takes).
  nonisolated static func wav(_ pcm: Data) -> Data {
    var d = Data()
    func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
    func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
    d.append(contentsOf: Array("RIFF".utf8)); u32(36 + UInt32(pcm.count)); d.append(contentsOf: Array("WAVE".utf8))
    d.append(contentsOf: Array("fmt ".utf8)); u32(16); u16(1); u16(1); u32(16000); u32(32000); u16(2); u16(16)
    d.append(contentsOf: Array("data".utf8)); u32(UInt32(pcm.count))
    d.append(pcm)
    return d
  }

  #if DEBUG && targetEnvironment(simulator)
  /// Simulator debug builds with the demo on (`JarvisDemo`): a scripted recogniser with the real one's events, so the
  /// hold-to-talk gestures can be recorded without a microphone or the permission sheet.
  private var script: Timer?
  private var scriptKeepsAudio = false

  // `finish` treats it like a phone's recogniser after `endAudio`: no final result (the words count as final once still for
  // `stableSeconds`); Chinese also gives a WAV (silence of the same length) for /transcribe.

  private func startScript() {
    let fallback = [tr("今天", "Slept"), tr("今天睡得", "Slept well"), tr("今天睡得不错", "Slept well, legs"),
                 tr("今天睡得不错，练腿", "Slept well, legs today"), tr("今天睡得不错，练腿四十分钟", "Slept well, legs for forty minutes")]
    // a cockpit demo: its own words, two characters at a time
    let phrase = JarvisDemo.cockpitPhrase.map { p in stride(from: 2, through: p.count + 1, by: 2).map { String(p.prefix($0)) } }
    let words = phrase ?? fallback
    var tick = 0
    running = true
    startedAt = Date()
    script = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
      Task { @MainActor in
        guard let self, self.running else { return }
        tick += 1
        self.push(0.35 + 0.3 * abs(sin(Double(tick) / 3)), run: self.run)
        if tick % 8 == 0 { self.recognized(words[min(tick / 8 - 1, words.count - 1)], isFinal: false, failed: false, run: self.run) }
      }
    }
  }
  #endif
}

/// Audio-thread side of the recording: the loudness for the waveform (at most 20 a second) and, for Chinese, the 16 kHz
/// copy. Its own lock; the tap closure runs on the audio thread.
private final class AudioSink: @unchecked Sendable {
  private let lock = NSLock()
  private var data = Data()
  private var converter: AVAudioConverter?
  private var lastLevel = Date.distantPast

  func reset(converter c: AVAudioConverter?) {
    lock.lock(); defer { lock.unlock() }
    data = Data()
    converter = c
  }

  func pcm() -> Data {
    lock.lock(); defer { lock.unlock() }
    return data
  }

  /// Keeps the buffer when asked; returns a loudness when one is due.
  func take(_ buf: AVAudioPCMBuffer) -> Double? {
    var level: Double?
    if let ch = buf.floatChannelData?[0], buf.frameLength > 0 {
      let n = Int(buf.frameLength)
      var sum: Float = 0
      for i in stride(from: 0, to: n, by: 4) { sum += ch[i] * ch[i] }
      let rms = sqrt(sum / Float(max(1, n / 4)))
      let now = Date()
      lock.lock()
      if now.timeIntervalSince(lastLevel) >= 0.05 { lastLevel = now; level = Double(min(1, sqrt(rms) * 2)) }
      lock.unlock()
    }
    lock.lock()
    let conv = converter
    lock.unlock()
    guard let conv else { return level }
    let ratio = JarvisVoice.pcmFormat.sampleRate / buf.format.sampleRate
    guard let out = AVAudioPCMBuffer(pcmFormat: JarvisVoice.pcmFormat,
                                     frameCapacity: AVAudioFrameCount(Double(buf.frameLength) * ratio) + 32) else { return level }
    var fed = false
    var error: NSError?
    conv.convert(to: out, error: &error) { _, status in
      if fed { status.pointee = .noDataNow; return nil }
      fed = true
      status.pointee = .haveData
      return buf
    }
    guard error == nil, out.frameLength > 0, let samples = out.int16ChannelData?[0] else { return level }
    let bytes = Data(bytes: samples, count: Int(out.frameLength) * 2)
    lock.lock()
    if Double(data.count) < JarvisVoice.maxSeconds * JarvisVoice.pcmFormat.sampleRate * 2 { data.append(bytes) }
    lock.unlock()
    return level
  }
}
