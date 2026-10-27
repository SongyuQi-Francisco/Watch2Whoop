import Flutter
import PhotosUI
import SwiftUI
import UIKit

// Native SwiftUI views over the Today page (#247 phase 2, docs/ios-native-plan.md 5.3). Dart: lib/src/native_ui.dart;
// page: body_engine/.../web/static/native-bridge.js. Nothing here runs until Dart switches a view on (server flags
// native_jarvis / native_today, both off by default), and switching both off removes the overlay again.
//
// - The overlay is one transparent view on top of the Flutter window (above the WebView, below anything presented later:
//   sheets, the camera, alerts). Touches pass through it except where a native view is (OverlayHost.PassView), so the page
//   underneath keeps working exactly as before.
// - Positions come from the page in CSS pixels of the full-screen WebView = points of the window.
// - Channel "watch2whoop/nativeui": Dart -> update {jarvis, today, meter} / camera; native -> action {kind, id | card},
//   photo {b64, mime}, meter {label, ...frame timing}.

/// What the page and Dart say about the Jarvis key.
struct JarvisState: Equatable {
  var enabled = false, show = false, menu = false, offline = false, dark = false, talk = false
  var rect: CGRect = .zero
  var voice = "off"
  /// What the page is doing: idle / recording / recognizing / review / sending / answering (native-bridge.js jvState).
  var phase = "idle"
  var theme = Theme()
  var labels: [String: String] = [:]

  init() {}
  init(_ a: [String: Any]) {
    enabled = a["enabled"] as? Bool ?? false
    show = a["show"] as? Bool ?? false
    menu = a["menu"] as? Bool ?? false
    offline = a["offline"] as? Bool ?? false
    dark = a["dark"] as? Bool ?? false
    rect = NativeUI.rect(a["rect"]) ?? .zero
    voice = a["voice"] as? String ?? "off"
    talk = a["talk"] as? Bool ?? false
    phase = a["phase"] as? String ?? "idle"
    theme = Theme(a["theme"] as? [String: Any], dark: dark)
    labels = a["labels"] as? [String: String] ?? [:]
  }
  func label(_ k: String, _ fallback: String) -> String { labels[k] ?? fallback }
}

/// What the page and Dart say about 今天 (the model is built by native_ui.dart todayModel).
struct TodayState: Equatable {
  var enabled = false, show = false, dark = false
  var rect: CGRect = .zero
  var theme = Theme()
  var model = TodayModel()

  init() {}
  init(_ a: [String: Any]) {
    enabled = a["enabled"] as? Bool ?? false
    show = a["show"] as? Bool ?? false
    dark = a["dark"] as? Bool ?? false
    rect = NativeUI.rect(a["rect"]) ?? .zero
    theme = Theme(a["theme"] as? [String: Any], dark: dark)
    model = TodayModel(a["model"] as? [String: Any] ?? [:])
  }
}

/// The page's colour tokens (app.css :root), so the native views use the same colours in light and dark.
struct Theme: Equatable {
  var bg = Color.white, surface = Color(white: 0.957), surface2 = Color(white: 0.92), text = Color(white: 0.067)
  var text2 = Color(white: 0.43), text3 = Color(white: 0.65), good = Color.green, mid = Color.orange, bad = Color.red
  var sleep = Color.indigo, train = Color.teal

  init() {}
  init(_ t: [String: Any]?, dark: Bool) {
    func c(_ k: String, _ light: String, _ darkHex: String) -> Color {
      Theme.parse(t?[k] as? String) ?? Theme.parse(dark ? darkHex : light)!
    }
    // fallbacks = app.css as of v2.146
    bg = c("bg", "#ffffff", "#000000"); surface = c("surface", "#f4f4f6", "#121214"); surface2 = c("surface-2", "#e9e9ed", "#1d1d20")
    text = c("text", "#111113", "#f4f4f6"); text2 = c("text-2", "#6c6c73", "#8d8d94"); text3 = c("text-3", "#a3a3aa", "#55555b")
    good = c("good", "#4e9a6b", "#72bf8e"); mid = c("mid", "#b58a34", "#d3ae63"); bad = c("bad", "#be5a44", "#de7f69")
    sleep = c("sleep", "#6870c4", "#8d93e4"); train = c("train", "#3f8f9a", "#5fb3bd")
  }

  /// #rgb, #rrggbb, rgb(r, g, b), rgba(r, g, b, a); nil for anything else.
  static func parse(_ s: String?) -> Color? {
    guard var v = s?.trimmingCharacters(in: .whitespaces).lowercased(), !v.isEmpty else { return nil }
    if v.hasPrefix("#") {
      v.removeFirst()
      if v.count == 3 { v = v.map { "\($0)\($0)" }.joined() }
      guard v.count == 6, let n = UInt32(v, radix: 16) else { return nil }
      return Color(.sRGB, red: Double((n >> 16) & 255) / 255, green: Double((n >> 8) & 255) / 255, blue: Double(n & 255) / 255)
    }
    if v.hasPrefix("rgb"), let o = v.firstIndex(of: "("), let e = v.lastIndex(of: ")") {
      let p = v[v.index(after: o)..<e].split(whereSeparator: { $0 == "," || $0 == " " || $0 == "/" }).compactMap { Double($0) }
      guard p.count >= 3 else { return nil }
      return Color(.sRGB, red: p[0] / 255, green: p[1] / 255, blue: p[2] / 255, opacity: p.count > 3 ? p[3] : 1)
    }
    return nil
  }

  func tone(_ k: String) -> Color {
    switch k {
    case "good": return good
    case "mid": return mid
    case "bad": return bad
    case "sleep": return sleep
    case "train": return train
    default: return text
    }
  }
}

final class NativeUIModel: ObservableObject {
  @Published var jarvis = JarvisState()
  @Published var today = TodayState()
  @Published var menuOpen = false
  /// A finger holds the Jarvis key (talking): the key stays on screen until it lifts, whatever covers it meanwhile.
  @Published var holding = false
  /// Debug builds only (`W2W_NATIVE_UI=…,material`): the pre-iOS 26 material keys on an iOS 26 simulator, for screenshots.
  @Published var material = false
  /// Debug builds only (`W2W_NATIVE_UI=…,demo`): the menu opens / closes and 今天 scrolls by itself, for frame timing.
  @Published var demo = false
  var demo2 = false
  /// What the native views send back (NativeUI.send).
  var send: (String, [String: Any]) -> Void = { _, _ in }
  let meter = FrameMeter()
}

final class NativeUI: NSObject {
  static let shared = NativeUI()
  var channel: FlutterMethodChannel?
  let model = NativeUIModel()
  private var host: OverlayHost?
  private var picker: PhotoPicker?

  override init() {
    super.init()
    model.send = { [weak self] method, args in self?.channel?.invokeMethod(method, arguments: args) }
    model.meter.report = { [weak self] r in
      NSLog("[frame-meter] %@", r.map { "\($0.key)=\($0.value)" }.sorted().joined(separator: " "))
      self?.channel?.invokeMethod("meter", arguments: r)
    }
  }

  func handle(_ call: FlutterMethodCall, _ result: @escaping FlutterResult) {
    switch call.method {
    case "update":
      if let a = call.arguments as? [String: Any] { apply(a) }
      result(nil)
    case "camera":
      presentPicker()
      result(nil)
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  static func rect(_ v: Any?) -> CGRect? {
    guard let a = v as? [NSNumber], a.count == 4 else { return nil }
    let r = CGRect(x: a[0].doubleValue, y: a[1].doubleValue, width: a[2].doubleValue, height: a[3].doubleValue)
    return r.width > 0 && r.height > 0 ? r : nil
  }

  private func apply(_ a: [String: Any]) {
    let j = JarvisState(a["jarvis"] as? [String: Any] ?? [:])
    let t = TodayState(a["today"] as? [String: Any] ?? [:])
    if model.jarvis != j { model.jarvis = j }
    if model.today != t { model.today = t }
    if !j.show || !j.menu, model.menuOpen { model.menuOpen = false }   // covered, a recording started, went offline …
    model.meter.enabled = (a["meter"] as? Bool) == true && (j.enabled || t.enabled)
    if model.material != ((a["material"] as? Bool) == true) { model.material = !model.material }
    model.demo2 = (a["demo2"] as? Bool) == true
    if model.demo != ((a["demo"] as? Bool) == true) { model.demo = !model.demo }
    #if DEBUG && targetEnvironment(simulator)
    VoiceBridge.scripted = model.demo   // the demo talks without a microphone or the speech permission sheet (VoiceInput.swift)
    #endif
    if j.enabled || t.enabled {
      if host == nil { host = OverlayHost(model: model) }
      host?.attach()
    } else {
      host?.detach()
      host = nil
    }
  }

  // The page's 拍照 while the app draws the key: the camera (a photo from the library where there is none, e.g. the simulator)
  private func presentPicker() {
    guard let top = OverlayHost.window()?.rootViewController?.topPresented() else { return }
    let p = PhotoPicker { [weak self] data in
      self?.picker = nil
      guard let data = data else { return }
      self?.model.send("photo", ["b64": data.base64EncodedString(), "mime": "image/jpeg"])
    }
    picker = p
    p.present(from: top)
  }
}

extension UIViewController {
  func topPresented() -> UIViewController { presentedViewController?.topPresented() ?? self }
}

/// The overlay: a pass-through view on the window with the SwiftUI views in it.
final class OverlayHost {
  private let model: NativeUIModel
  private let view: PassView
  private let hosting: UIHostingController<OverlayRoot>

  /// Takes a touch only where a native view is; everything else goes to the page under it.
  final class PassView: UIView {
    var model: NativeUIModel?
    override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
      guard let m = model else { return false }
      if m.jarvis.show && m.menuOpen { return true }   // the scrim closes the menu
      if (m.jarvis.show || m.holding) && m.jarvis.rect.insetBy(dx: -6, dy: -6).contains(point) { return true }
      if m.today.show && m.today.rect.contains(point) { return true }
      return false
    }
  }

  init(model: NativeUIModel) {
    self.model = model
    view = PassView()
    view.model = model
    view.backgroundColor = .clear
    hosting = UIHostingController(rootView: OverlayRoot(model: model))
    hosting.view.backgroundColor = .clear
    if #available(iOS 16.4, *) { hosting.safeAreaRegions = [] }
    hosting.view.translatesAutoresizingMaskIntoConstraints = false
    view.addSubview(hosting.view)
    NSLayoutConstraint.activate([
      hosting.view.leadingAnchor.constraint(equalTo: view.leadingAnchor), hosting.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
      hosting.view.topAnchor.constraint(equalTo: view.topAnchor), hosting.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
    ])
  }

  static func window() -> UIWindow? {
    let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap { $0.windows }
    return windows.first(where: { $0.isKeyWindow }) ?? windows.first
  }

  func attach() {
    guard view.superview == nil, let w = OverlayHost.window(), let root = w.rootViewController else { return }
    // Not a child view controller of the Flutter one: its view lives on the window, outside the Flutter view (UIKit
    // refuses a child whose view is elsewhere). Traits (light / dark) still come from the window.
    view.frame = w.bounds
    view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
    // right above the Flutter view: a sheet / alert presented later still comes on top
    if root.view.superview === w { w.insertSubview(view, aboveSubview: root.view) } else { w.addSubview(view) }
  }

  func detach() { view.removeFromSuperview() }
}

struct OverlayRoot: View {
  @ObservedObject var model: NativeUIModel
  var body: some View {
    ZStack(alignment: .topLeading) {
      if model.today.enabled && model.today.show {
        NativeTodayView(state: model.today, send: model.send, meter: model.meter, demo: model.demo)
          .frame(width: model.today.rect.width, height: model.today.rect.height)
          .offset(x: model.today.rect.minX, y: model.today.rect.minY)
          .transition(.opacity)
      }
      if model.jarvis.enabled && (model.jarvis.show || model.holding) {
        NativeJarvisView(model: model)
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .ignoresSafeArea()
    .animation(.easeOut(duration: 0.12), value: model.today.show)
  }
}

/// Frame timing while something moves (debug / test builds with `W2W_NATIVE_UI=…,meter`): a CADisplayLink that counts frames
/// longer than 1.5 frame budgets ("hitches") per labelled window (the Jarvis menu opening, scrolling 今天) and reports them.
final class FrameMeter {
  var enabled = false { didSet { if !enabled { stop() } } }
  var report: ([String: Any]) -> Void = { _ in }
  private var link: CADisplayLink?
  private var label = "", until: CFTimeInterval = 0, last: CFTimeInterval = 0
  private var frames = 0, hitches = 0, worst: CFTimeInterval = 0, start: CFTimeInterval = 0, hitchTime: CFTimeInterval = 0

  /// Measure the next [seconds] under [label] (extends a running window of the same label).
  func mark(_ l: String, seconds: CFTimeInterval = 0.8) {
    guard enabled else { return }
    let now = CACurrentMediaTime()
    if link != nil && l == label { until = max(until, now + seconds); return }
    flush()
    label = l; until = now + seconds; start = now; last = 0; frames = 0; hitches = 0; worst = 0; hitchTime = 0
    let d = CADisplayLink(target: self, selector: #selector(tick(_:)))
    if #available(iOS 15.0, *) {
      let maxFps = Float(UIScreen.main.maximumFramesPerSecond)
      d.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: maxFps, preferred: maxFps)
    }
    d.add(to: .main, forMode: .common)
    link = d
  }

  @objc private func tick(_ d: CADisplayLink) {
    let budget = max(d.targetTimestamp - d.timestamp, 1.0 / Double(UIScreen.main.maximumFramesPerSecond))
    if last > 0 {
      let dt = d.timestamp - last
      frames += 1
      worst = max(worst, dt)
      if dt > budget * 1.5 { hitches += 1; hitchTime += dt - budget }
    }
    last = d.timestamp
    if d.timestamp > until { flush() }
  }

  private func flush() {
    guard let d = link else { return }
    d.invalidate()
    link = nil
    let secs = max(last - start, 0.001)
    guard frames > 0 else { return }
    report([
      "label": label, "frames": frames, "seconds": (secs * 1000).rounded() / 1000,
      "fps": (Double(frames) / secs * 10).rounded() / 10, "maxFps": UIScreen.main.maximumFramesPerSecond,
      "hitches": hitches, "worstMs": (worst * 10000).rounded() / 10, "hitchMsPerS": (hitchTime * 1000 / secs * 10).rounded() / 10,
    ])
  }

  private func stop() { link?.invalidate(); link = nil }
}

/// The camera (or the photo library where there is no camera), one photo, JPEG at most 2048 px on the long side.
final class PhotoPicker: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate, PHPickerViewControllerDelegate {
  private let done: (Data?) -> Void
  init(done: @escaping (Data?) -> Void) { self.done = done }

  func present(from vc: UIViewController) {
    if UIImagePickerController.isSourceTypeAvailable(.camera) {
      let p = UIImagePickerController()
      p.sourceType = .camera
      p.delegate = self
      vc.present(p, animated: true)
    } else {
      var c = PHPickerConfiguration()
      c.filter = .images
      c.selectionLimit = 1
      let p = PHPickerViewController(configuration: c)
      p.delegate = self
      vc.present(p, animated: true)
    }
  }

  static func jpeg(_ img: UIImage) -> Data? {
    let side = max(img.size.width, img.size.height), k = min(1, 2048 / max(side, 1))
    let size = CGSize(width: (img.size.width * k).rounded(), height: (img.size.height * k).rounded())
    let f = UIGraphicsImageRendererFormat.default()
    f.scale = 1
    let out = UIGraphicsImageRenderer(size: size, format: f).image { _ in img.draw(in: CGRect(origin: .zero, size: size)) }
    return out.jpegData(compressionQuality: 0.82)
  }

  func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
    let img = info[.originalImage] as? UIImage
    picker.dismiss(animated: true) { self.done(img.flatMap(PhotoPicker.jpeg)) }
  }

  func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { picker.dismiss(animated: true) { self.done(nil) } }

  func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
    picker.dismiss(animated: true)
    guard let item = results.first?.itemProvider, item.canLoadObject(ofClass: UIImage.self) else { done(nil); return }
    item.loadObject(ofClass: UIImage.self) { obj, _ in
      let data = (obj as? UIImage).flatMap(PhotoPicker.jpeg)
      DispatchQueue.main.async { self.done(data) }
    }
  }
}
