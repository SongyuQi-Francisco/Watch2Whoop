import AVFoundation
import Foundation

/// The microphone while the finger is on the core (plan 4.3 B): 16 kHz, mono, 16-bit integer PCM WAV, the format the
/// server's transcriber takes (#458 B). Foreground only; the file lives in the app's temporary folder for the seconds
/// between release and upload and is deleted as soon as its bytes are read (or the hold is cancelled).
protocol WatchVoiceRecorder: AnyObject {
  /// Starts listening; false = no permission or the microphone could not start.
  func start() async -> Bool
  /// 0…1, the voice's loudness now (the ring around the core follows it).
  func level() -> Double
  /// Seconds since `start`.
  var elapsed: Double { get }
  /// Stops; `keep` = the WAV's bytes (the file is deleted either way).
  func stop(keep: Bool) -> Data?
}

final class WatchRecorder: WatchVoiceRecorder {
  private var recorder: AVAudioRecorder?
  private var url: URL?

  /// The permission sheet appears the first time (only the user can answer it, plan: 「权限由用户自己点」).
  static func permission() async -> Bool {
    switch AVAudioApplication.shared.recordPermission {
    case .granted: return true
    case .denied: return false
    default: return await AVAudioApplication.requestRecordPermission()
    }
  }

  func start() async -> Bool {
    guard await Self.permission() else { return false }
    let s = AVAudioSession.sharedInstance()
    do {
      try s.setCategory(.record, mode: .default)
      try s.setActive(true)
    } catch {
      return false
    }
    let u = FileManager.default.temporaryDirectory.appendingPathComponent("w2w-voice-\(UUID().uuidString).wav")
    let settings: [String: Any] = [
      AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: Double(WatchParams.voiceSampleRate), AVNumberOfChannelsKey: 1,
      AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false,
    ]
    guard let r = try? AVAudioRecorder(url: u, settings: settings) else { return false }
    r.isMeteringEnabled = true
    guard r.record(forDuration: WatchParams.voiceMaxSec + 0.5) else { return false }
    recorder = r
    url = u
    return true
  }

  var elapsed: Double { recorder?.currentTime ?? 0 }

  func level() -> Double {
    guard let r = recorder else { return 0 }
    r.updateMeters()
    // −50 dB (a quiet room) … −10 dB (speaking close) → 0…1
    return min(1, max(0, (Double(r.averagePower(forChannel: 0)) + 50) / 40))
  }

  func stop(keep: Bool) -> Data? {
    recorder?.stop()
    recorder = nil
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    defer {
      if let url { try? FileManager.default.removeItem(at: url) }
      url = nil
    }
    guard keep, let url else { return nil }
    return try? Data(contentsOf: url)
  }
}
