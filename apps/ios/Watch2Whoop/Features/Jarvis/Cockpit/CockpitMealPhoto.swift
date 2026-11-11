import AVFoundation
import PhotosUI
import SwiftUI
import UIKit

/// Jarvis → 记一餐 → 拍照 (docs/design/2.0.1/16-photo.html, 16b-photo-result.html). A viewfinder whose shutter is the Jarvis
/// core, the photo library on the left and the flash on the right. The photo goes to the user's own AI the way 拍照记一餐
/// always went (JarvisChat.send with the picture; the AI reads it and logs the food with log_food). The result shows what
/// the AI answered and the day's total as the server counted it after the turn (its meal card). The app has no id of what
/// the AI wrote, so nothing is guessed from before / after: 撤销 asks the AI, who undoes it after a yes in the conversation;
/// amounts are changed there too (or on the 饮食 page).
@MainActor
final class CockpitMealPhoto: ObservableObject {
  enum Phase: Equatable {
    case viewfinder
    case working
    case result(Result)
  }

  struct Result: Equatable {
    var answer: CockpitReply.Answer
    /// The day's kcal and goal from the turn's meal card (nil = the AI logged nothing the server counted).
    var dayEaten: Double?
    var dayGoal: Double?
  }

  @Published private(set) var phase = Phase.viewfinder
  @Published private(set) var photo: UIImage?
  @Published var error: String?

  private weak var router: JarvisRouter?
  private var work: Task<Void, Never>?

  init(router: JarvisRouter) {
    self.router = router
  }

  /// What a turn after a photo says (pure: tested).
  static func result(_ items: [JarvisChat.Item]) -> Result? {
    guard case .answer(let a) = TurnReading.read(items, question: "", flow: .meal, today: nil) else { return nil }
    let card = items.reversed().compactMap { i -> JSONValue? in
      if case .card(let c) = i.kind, c["type"]?.string == "meal" { return c } else { return nil }
    }.first
    var r = Result(answer: a, dayEaten: card?["totals"]?["calories"]?.double, dayGoal: card?["goal"]?["calories"]?.double)
    r.answer.readings = []   // the day is drawn as a bar here
    return r
  }

  func send(_ image: UIImage) {
    guard let router, let chat = router.chat else { return }
    guard router.ai.vision else { error = tr("这个 AI 看不了图片", "This AI can't read pictures"); return }
    guard !chat.busy else { error = tr("上一条还在回答，等它说完再发", "Still answering the last message; wait for it to finish"); return }
    guard let a = JarvisMedia.photo(image) else { return }
    photo = image
    phase = .working
    work?.cancel()
    work = Task { [weak self] in
      guard let self else { return }
      let start = chat.items.count
      chat.page = "food"
      chat.send(tr("拍照记一餐", "Log this meal from the photo"), attachments: [a])
      guard let items = await CockpitController.turn(chat, from: start), !Task.isCancelled else {
        self.phase = .viewfinder
        self.error = tr("Jarvis 没有回应，稍后再试", "Jarvis didn't answer; try again later")
        return
      }
      if items.contains(where: { if case .confirm(_, approved: nil) = $0.kind { return true } else { return false } }) {
        router.present(.chat)   // a yes is needed first: that happens in the conversation
        return
      }
      guard let r = Self.result(items) else {
        self.phase = .viewfinder
        self.error = tr("Jarvis 没有回应，稍后再试", "Jarvis didn't answer; try again later")
        return
      }
      if r.answer.undoText != nil { JarvisHaptics.notice.notificationOccurred(.success) }
      self.phase = .result(r)
    }
  }

  func again() {
    work?.cancel()
    photo = nil
    phase = .viewfinder
  }
}

/// The camera (AVCaptureSession on its own queue) and its preview.
final class MealCamera: NSObject, ObservableObject, AVCapturePhotoCaptureDelegate {
  let session = AVCaptureSession()
  private let output = AVCapturePhotoOutput()
  private let queue = DispatchQueue(label: "org.watch2whoop.jarvis.camera", qos: .userInitiated)
  private var done: ((UIImage?) -> Void)?
  @Published var flash = false
  @Published private(set) var available = true

  func start() {
    queue.async { [weak self] in
      guard let self else { return }
      if self.session.inputs.isEmpty {
        self.session.beginConfiguration()
        self.session.sessionPreset = .photo
        guard let dev = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: dev), self.session.canAddInput(input), self.session.canAddOutput(self.output) else {
          self.session.commitConfiguration()
          DispatchQueue.main.async { self.available = false }
          return
        }
        self.session.addInput(input)
        self.session.addOutput(self.output)
        self.session.commitConfiguration()
      }
      if !self.session.isRunning { self.session.startRunning() }
    }
  }

  func stop() {
    queue.async { [weak self] in
      if self?.session.isRunning == true { self?.session.stopRunning() }
    }
  }

  func shoot(_ then: @escaping (UIImage?) -> Void) {
    let on = flash
    queue.async { [weak self] in
      guard let self, self.session.isRunning else { DispatchQueue.main.async { then(nil) }; return }
      let s = AVCapturePhotoSettings()
      if self.output.supportedFlashModes.contains(.on) { s.flashMode = on ? .on : .off }
      self.done = then
      self.output.capturePhoto(with: s, delegate: self)
    }
  }

  func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
    let img = photo.fileDataRepresentation().flatMap(UIImage.init(data:))
    let f = done
    done = nil
    DispatchQueue.main.async { f?(img) }
  }
}

private struct CameraPreview: UIViewRepresentable {
  let session: AVCaptureSession

  final class PreviewView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var preview: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }   // swiftlint:disable:this force_cast
  }

  func makeUIView(context: Context) -> PreviewView {
    let v = PreviewView()
    v.preview.session = session
    v.preview.videoGravity = .resizeAspectFill
    return v
  }

  func updateUIView(_ uiView: PreviewView, context: Context) {}
}

/// 16 + 16b: the viewfinder, then the result over the photo.
struct CockpitMealPhotoView: View {
  @StateObject private var model: CockpitMealPhoto
  @StateObject private var camera = MealCamera()
  @State private var picked: PhotosPickerItem?
  @State private var toast: Toast?
  let close: () -> Void

  init(router: JarvisRouter, close: @escaping () -> Void) {
    _model = StateObject(wrappedValue: CockpitMealPhoto(router: router))
    self.close = close
  }

  var body: some View {
    ZStack {
      Color.black.ignoresSafeArea()
      if model.phase == .viewfinder { viewfinder } else { shot }
    }
    .onChange(of: picked) { item in
      guard let item else { return }
      Task {
        if let d = try? await item.loadTransferable(type: Data.self), let img = UIImage(data: d) { model.send(img) }
        picked = nil
      }
    }
    .onChange(of: model.error) { e in
      if let e { toast = Toast(e); model.error = nil }
    }
    .onAppear { camera.start() }
    .onDisappear { camera.stop() }
    .toast($toast)
    .environment(\.colorScheme, .dark)
  }

  // MARK: - 16: the viewfinder

  private var viewfinder: some View {
    VStack(spacing: 0) {
      topBar
      ZStack {
        if camera.available { CameraPreview(session: camera.session) } else { Color.black }
        FrameCorners().stroke(Palette.text, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
          .padding(.horizontal, 51).padding(.vertical, 118)
      }
      .clipped()
      Text(tr("对准这一餐", "Frame the meal")).font(Typo.label).foregroundColor(Palette.text2).padding(.top, 14)
      HStack {
        PhotosPicker(selection: $picked, matching: .images) {
          side(.image)
        }
        .accessibilityLabel(tr("从相册选", "Choose from photos"))
        Spacer()
        Button {
          JarvisHaptics.medium.impactOccurred()
          camera.shoot { img in if let img { model.send(img) } }
        } label: {
          CockpitCoreGlyph(size: 72)
        }
        .buttonStyle(PressScale())
        .disabled(!camera.available)
        .accessibilityLabel(tr("拍照", "Take photo"))
        Spacer()
        Button { camera.flash.toggle() } label: {
          side(.zap).opacity(camera.flash ? 1 : 0.5)
        }
        .accessibilityLabel(camera.flash ? tr("闪光灯开", "Flash on") : tr("闪光灯关", "Flash off"))
      }
      .padding(.horizontal, 56).padding(.vertical, 22)
    }
  }

  private var topBar: some View {
    HStack(spacing: 10) {
      Button(action: close) {
        Icon(.x, size: 18).foregroundColor(Palette.text).frame(width: 36, height: 36).background(Circle().fill(Palette.inSheet))
      }
      .accessibilityLabel(tr("关闭", "Close"))
      Text(tr("拍一餐", "Snap a meal")).font(Typo.bodyStrong).foregroundColor(Palette.text)
      Spacer()
    }
    .padding(.horizontal, Metrics.gutter).padding(.vertical, 10)
  }

  private func side(_ icon: Icon.Name) -> some View {
    Icon(icon, size: 22).foregroundColor(Palette.text).frame(width: 44, height: 44).background(Circle().fill(Palette.inSheet))
  }

  /// The photo just taken, while the AI reads it (and behind the result).
  private var shot: some View {
    VStack(spacing: 16) {
      topBar
      if let p = model.photo {
        Image(uiImage: p).resizable().scaledToFill().frame(maxWidth: .infinity).frame(height: 360).clipped()
      }
      switch model.phase {
      case .working:
        HStack(spacing: 10) { ProgressView().tint(Palette.text); Text(tr("在看这一餐", "Reading the meal")).font(Typo.caption).foregroundColor(Palette.text2) }
      case .result(let r):
        result(r)
      default:
        EmptyView()
      }
      Spacer()
    }
  }

  // MARK: - 16b: the result

  private func result(_ r: CockpitMealPhoto.Result) -> some View {
    let a = r.answer
    return VStack(alignment: .leading, spacing: 14) {
      HStack {
        HStack(spacing: 6) {
          Icon(a.undoText != nil ? .circleCheck : .messageCircle, size: 18).foregroundColor(a.undoText != nil ? Palette.good : Palette.text2)
          Text(a.verdict).font(Typo.heroWord).foregroundColor(Palette.text).lineLimit(1)
        }
        Spacer()
        HStack(spacing: 4) { Icon(.sparkles, size: 12); Text(tr("AI 生成", "AI-made")).font(Typo.label) }
          .foregroundColor(Palette.text2).padding(.vertical, 4).padding(.horizontal, 8)
          .overlay(Capsule().stroke(Palette.hair, lineWidth: 1))
      }
      if let sub = a.sub { Text(sub).font(Typo.body).foregroundColor(Palette.text2).lineLimit(2) }
      if let eaten = r.dayEaten {
        VStack(alignment: .leading, spacing: 8) {
          HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(tr("今天", "Today")).font(Typo.label).foregroundColor(Palette.text2)
            Text(CockpitText.kcal(eaten)).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
            Text(r.dayGoal.map { "/ \(CockpitText.kcal($0)) kcal" } ?? "kcal").font(Typo.unit).foregroundColor(Palette.text2)
          }
          GeometryReader { g in
            let full = max(r.dayGoal ?? eaten, eaten, 1)
            ZStack(alignment: .leading) {
              Capsule().fill(Palette.inSheet)
              Capsule().fill(Palette.text).frame(width: g.size.width * CGFloat(min(1, eaten / full)))
            }
          }
          .frame(height: 6)
        }
      }
      HStack(spacing: 10) {
        if let undo = a.undoText {
          capsule(.undo2, tr("撤销", "Undo")) {   // the AI undoes what it wrote, after a yes in the conversation
            close()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
              JarvisRouter.current?.present(.chat)
              JarvisRouter.current?.chat?.send(undo)
            }
          }
        }
        capsule(.camera, tr("再拍一张", "Another photo")) { model.again() }
        capsule(.messageCircle, tr("展开对话", "Open chat")) {
          close()
          DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { JarvisRouter.current?.present(.chat) }
        }
      }
    }
    .padding(Metrics.gutter)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.sheet))
    .padding(.horizontal, 12)
  }

  private func capsule(_ icon: Icon.Name, _ title: String, _ action: @escaping () -> Void) -> some View {
    Button(action: action) {
      HStack(spacing: 6) { Icon(icon, size: 15); Text(title).font(Typo.bodyStrong) }
        .foregroundColor(Palette.text).frame(maxWidth: .infinity, minHeight: 44)
        .overlay(Capsule().stroke(Palette.text.opacity(0.3), lineWidth: 1))
    }
    .buttonStyle(PressScale())
  }
}

/// The viewfinder's four corners (plain strokes).
private struct FrameCorners: Shape {
  func path(in r: CGRect) -> Path {
    var p = Path()
    let l: CGFloat = 26
    p.move(to: CGPoint(x: r.minX, y: r.minY + l)); p.addLine(to: r.origin); p.addLine(to: CGPoint(x: r.minX + l, y: r.minY))
    p.move(to: CGPoint(x: r.maxX - l, y: r.minY)); p.addLine(to: CGPoint(x: r.maxX, y: r.minY)); p.addLine(to: CGPoint(x: r.maxX, y: r.minY + l))
    p.move(to: CGPoint(x: r.maxX, y: r.maxY - l)); p.addLine(to: CGPoint(x: r.maxX, y: r.maxY)); p.addLine(to: CGPoint(x: r.maxX - l, y: r.maxY))
    p.move(to: CGPoint(x: r.minX + l, y: r.maxY)); p.addLine(to: CGPoint(x: r.minX, y: r.maxY)); p.addLine(to: CGPoint(x: r.minX, y: r.maxY - l))
    return p
  }
}

/// The approved core as a static mark (docs/design/2.0.1/core.js): 36 ticks, three segments, an outlined inverted triangle
/// with a lit inner triangle. Drawn in a 58 × 58 box, scaled to `size`.
struct CockpitCoreGlyph: View {
  var size: CGFloat = 58

  var body: some View {
    Canvas { ctx, s in
      let k = s.width / 58
      ctx.scaleBy(x: k, y: k)
      func P(_ r: CGFloat, _ deg: Double) -> CGPoint {
        let a = deg * .pi / 180
        return CGPoint(x: 29 + r * CGFloat(cos(a)), y: 29 + r * CGFloat(sin(a)))
      }
      func tri(_ r: CGFloat) -> Path {
        var p = Path()
        p.move(to: P(r, 90)); p.addLine(to: P(r, 210)); p.addLine(to: P(r, 330)); p.closeSubpath()
        return p
      }
      var ticks = Path()
      for i in 0..<36 { ticks.move(to: P(24.6, Double(i) * 10 - 90)); ticks.addLine(to: P(27.2, Double(i) * 10 - 90)) }
      ctx.stroke(ticks, with: .color(Palette.text2.opacity(0.7)), lineWidth: 1)
      var arcs = Path()
      for i in 0..<3 {
        let a0 = Double(i) * 120 - 90 + 12
        for j in 0...24 {
          let q = P(20.6, a0 + 96 * Double(j) / 24)
          if j == 0 { arcs.move(to: q) } else { arcs.addLine(to: q) }
        }
      }
      ctx.stroke(arcs, with: .color(Palette.text), style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
      ctx.stroke(tri(13.2), with: .color(Palette.text), style: StrokeStyle(lineWidth: 1.6, lineJoin: .round))
      var lit = ctx
      lit.addFilter(.shadow(color: CockpitPalette.accent, radius: 3))
      lit.fill(tri(5.6), with: .color(CockpitPalette.accent))
      lit.stroke(tri(5.6), with: .color(CockpitPalette.accent), style: StrokeStyle(lineWidth: 1.2, lineJoin: .round))
    }
    .frame(width: size, height: size)
    .accessibilityHidden(true)
  }
}
