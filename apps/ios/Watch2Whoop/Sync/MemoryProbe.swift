import Darwin
import Foundation
import os

/// The app's memory footprint (what iOS's memory limits count: `phys_footprint`), sampled while a long job runs, so the
/// history download and a full recompute can show they stay flat (#340 local history; the figures are in the doc).
/// The peak of the last foreground / background run is kept in `meta` (`perf.peak.<label>`) and logged as `W2W_PERF`.
final class MemoryProbe {
  private let timer: DispatchSourceTimer
  private let lock = NSLock()
  private var peak: UInt64
  let start: UInt64

  /// The current footprint in bytes (0 when the kernel does not answer).
  static func footprint() -> UInt64 {
    var info = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
    let kr = withUnsafeMutablePointer(to: &info) {
      $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count) }
    }
    return kr == KERN_SUCCESS ? info.phys_footprint : 0
  }

  /// Samples every `interval` seconds until `stop()`.
  init(interval: TimeInterval = 0.02) {
    start = Self.footprint()
    peak = start
    timer = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "w2w.memory-probe", qos: .utility))
    timer.schedule(deadline: .now(), repeating: interval)
    timer.setEventHandler { [weak self] in self?.sample() }
    timer.resume()
  }

  private func sample() {
    let f = Self.footprint()
    lock.lock()
    if f > peak { peak = f }
    lock.unlock()
  }

  /// (peak, start) in bytes.
  func stop() -> (peak: UInt64, start: UInt64) {
    timer.cancel()
    sample()
    lock.lock()
    defer { lock.unlock() }
    return (peak, start)
  }

  /// Runs `work`, keeps its peak in `meta` and logs it.
  static func measure<T>(_ label: String, db: AppDatabase, _ work: () async -> T) async -> T {
    let probe = MemoryProbe()
    let out = await work()
    let (peak, start) = probe.stop()
    let mb = { (b: UInt64) in String(format: "%.1f", Double(b) / 1_048_576) }
    try? db.setMeta("perf.peak.\(label)", "\(mb(peak)) MB (start \(mb(start)) MB)")
    Logger(subsystem: "org.watch2whoop", category: "perf").notice("W2W_PERF \(label, privacy: .public) peak=\(mb(peak), privacy: .public)MB start=\(mb(start), privacy: .public)MB")
    return out
  }
}
