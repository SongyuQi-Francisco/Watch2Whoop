import AVFoundation
import PhotosUI
import SwiftUI
import UIKit

/// Jarvis → 记一餐 → 拍照 (docs/design/2.0.1/16-photo.html, 16b-photo-result.html). A viewfinder whose shutter is the Jarvis
/// core, the photo library on the left and the flash on the right. The photo goes to the user's own AI the way 拍照记一餐
/// always went (JarvisChat.send with the picture; the AI reads it and logs the food with log_food); the result card shows the
/// entries the day gained during that turn, with their amounts as the food log keeps them (quantity × unit: no grams are made
/// up), each amount editable (FoodStore.setPortion), 撤销 (FoodStore.delete) and 再拍一张.
@MainActor
final class CockpitMealPhoto: ObservableObject {
  enum Phase: Equatable {
    case viewfinder
    case working
    case result
    /// The AI answered without logging anything (it asked something, or could not read the picture): its words.
    case answered(String)
  }

  @Published private(set) var phase = Phase.viewfinder
  @Published private(set) var photo: UIImage?
  @Published private(set) var added: [FoodEntry] = []
  @Published private(set) var dayBefore: Double = 0
  @Published var error: String?

  let food: FoodStore
  private weak var router: JarvisRouter?
  private var work: Task<Void, Never>?

  init(session: AccountSession, router: JarvisRouter) {
    food = FoodStore(session: session, cardOnly: true)
    self.router = router
  }

  /// The entries as they are now (an edited amount shows at once).
  var entries: [FoodEntry] {
    let ids = Set(added.map(\.id))
    let now = (food.nutrition?.entries ?? []).filter { ids.contains($0.id) }
    return now.isEmpty ? added : now
  }

  var total: Macros { entries.reduce(Macros(kcal: 0, protein: 0, carbs: 0, fat: 0)) { $0 + $1.macros } }

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
      await self.food.refreshToday()
      let before = Set(self.food.nutrition?.entries.map(\.id) ?? [])
      self.dayBefore = self.food.nutrition?.intake.kcal ?? 0
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
      await self.food.refreshToday()
      let new = (self.food.nutrition?.entries ?? []).filter { !before.contains($0.id) && !$0.isLocal }
      if new.isEmpty {
        let text = items.reversed().compactMap { i -> String? in if case .assistant(let t) = i.kind { return t } else { return nil } }.first
        self.phase = .answered(CockpitText.clean(text ?? tr("没有记上", "Nothing was logged")))
      } else {
        JarvisHaptics.notice.notificationOccurred(.success)
        self.added = new
        self.phase = .result
      }
    }
  }

  func again() {
    work?.cancel()
    added = []
    photo = nil
    phase = .viewfinder
  }

  /// 撤销: every entry this photo added.
  func undo() async -> Bool {
    var ok = true
    for e in entries {
      let out = await food.delete(e)
      ok = ok && !out.failed && !out.conflict
    }
    return ok
  }

  func setAmount(_ e: FoodEntry, _ quantity: Double) async -> String? {
    let out = await food.setPortion(e, quantity: quantity)
    return out.sent || out.queued ? nil : out.message
  }

  /// `150 g`, `1.5 份`: the amount as the food log keeps it.
  static func amount(_ e: FoodEntry) -> (value: String, unit: String) {
    (RecordFormat.num(e.quantity, 1), e.unit ?? tr("份", "serving"))
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
  @State private var editing: FoodEntry?
  @State private var amountText = ""
  @State private var toast: Toast?
  let close: () -> Void

  init(session: AccountSession, router: JarvisRouter, close: @escaping () -> Void) {
    _model = StateObject(wrappedValue: CockpitMealPhoto(session: session, router: router))
    self.close = close
  }

  var body: some View {
    ZStack {
      Color.black.ignoresSafeArea()
      switch model.phase {
      case .viewfinder: viewfinder
      case .working, .answered: shot
      case .result: shot
      }
    }
    .sheet(isPresented: Binding(get: { model.phase == .result }, set: { if !$0 && model.phase == .result { close() } })) {
      result
        .presentationDetents([.fraction(0.72), .large])
        .presentationDragIndicator(.visible)
        .interactiveDismissDisabled(false)
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
      case .answered(let t):
        Text(t).font(Typo.body).foregroundColor(Palette.text).lineLimit(4).padding(.horizontal, Metrics.gutter)
        HStack(spacing: 10) {
          capsule(.camera, tr("再拍一张", "Another photo")) { model.again() }
          capsule(.messageCircle, tr("展开对话", "Open chat")) { close(); DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { JarvisRouter.current?.present(.chat) } }
        }
      default:
        EmptyView()
      }
      Spacer()
    }
  }

  // MARK: - 16b: the result

  private var result: some View {
    let t = model.total
    let goal = model.food.nutrition?.goal.kcal
    let day = model.food.nutrition?.intake.kcal ?? (model.dayBefore + (t.kcal ?? 0))
    let slot = model.entries.first?.meal.title ?? ""
    return ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 18) {
        HStack {
          HStack(spacing: 6) {
            Icon(.circleCheck, size: 18).foregroundColor(Palette.good)
            Text(tr("已记到\(slot)", "Logged to \(slot)")).font(.system(size: 17, weight: .semibold)).foregroundColor(Palette.text)
          }
          Spacer()
          HStack(spacing: 4) { Icon(.sparkles, size: 12); Text(tr("AI 生成", "AI-made")).font(Typo.label) }
            .foregroundColor(Palette.text2).padding(.vertical, 4).padding(.horizontal, 8)
            .overlay(Capsule().stroke(Palette.hair, lineWidth: 1))
        }
        HStack(alignment: .firstTextBaseline, spacing: 4) {
          Text(t.kcal.map { RecordFormat.num($0) } ?? "—").font(.system(size: 44, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
          Text("kcal").font(Typo.unit).foregroundColor(Palette.text2)
        }
        MacroBar(macros: t).frame(height: 8)
        HStack(spacing: 16) {
          legend(tr("蛋白", "Protein"), t.protein, 1)
          legend(tr("碳水", "Carbs"), t.carbs, 0.55)
          legend(tr("脂肪", "Fat"), t.fat, 0.28)
        }
        VStack(spacing: 0) {
          ForEach(model.entries) { e in
            HStack {
              Text(e.food).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
              Spacer()
              Button {
                amountText = RecordFormat.num(e.quantity, 1)
                editing = e
              } label: {
                let a = CockpitMealPhoto.amount(e)
                HStack(alignment: .firstTextBaseline, spacing: 2) {
                  Text(a.value).font(Typo.bodyStrong.monospacedDigit()).underline()
                  Text(a.unit).font(Typo.unit)
                }
                .foregroundColor(Palette.text)
              }
              .accessibilityLabel(tr("改份量 \(e.food)", "Change amount of \(e.food)"))
              .frame(width: 96, alignment: .trailing)
              HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(e.macros.kcal.map { RecordFormat.num($0) } ?? "—").font(Typo.body.monospacedDigit())
                Text("kcal").font(Typo.unit)
              }
              .foregroundColor(Palette.text2).frame(width: 80, alignment: .trailing)
            }
            .padding(.vertical, 10)
            Divider().overlay(Palette.hair)
          }
        }
        VStack(alignment: .leading, spacing: 8) {
          HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(tr("今天", "Today")).font(Typo.label).foregroundColor(Palette.text2)
            Text(RecordFormat.num(day)).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
            Text(goal.map { "/ \(CockpitText.kcal($0)) kcal" } ?? "kcal").font(Typo.unit).foregroundColor(Palette.text2)
          }
          GeometryReader { g in
            let full = max(goal ?? day, day, 1)
            ZStack(alignment: .leading) {
              Capsule().fill(Palette.inSheet)
              Capsule().fill(Palette.text).frame(width: g.size.width * CGFloat(min(1, day / full)))
            }
          }
          .frame(height: 6)
        }
        HStack(spacing: 10) {
          capsule(.undo2, tr("撤销", "Undo")) {
            Task {
              let ok = await model.undo()
              toast = Toast(ok ? tr("已撤销", "Undone") : tr("撤销要联网，稍后再试", "Undo needs a connection, try again later"))
              if ok { model.again() }
            }
          }
          capsule(.camera, tr("再拍一张", "Another photo")) { model.again() }
        }
      }
      .padding(Metrics.gutter)
    }
    .background(Palette.sheet.ignoresSafeArea())
    .alert(tr("份量", "Amount"), isPresented: Binding(get: { editing != nil }, set: { if !$0 { editing = nil } })) {
      TextField(editing.map { CockpitMealPhoto.amount($0).unit } ?? "", text: $amountText).keyboardType(.decimalPad)
      Button(tr("取消", "Cancel"), role: .cancel) { editing = nil }
      Button(tr("好", "OK")) {
        guard let e = editing, let q = Double(amountText.replacingOccurrences(of: ",", with: ".")), q > 0 else { return }
        editing = nil
        Task { if let m = await model.setAmount(e, q) { toast = Toast(m) } }
      }
    }
    .toast($toast)
  }

  private func legend(_ label: String, _ g: Double?, _ o: Double) -> some View {
    HStack(spacing: 5) {
      Capsule().fill(Palette.text.opacity(o)).frame(width: 10, height: 4)
      Text(label).font(Typo.label).foregroundColor(Palette.text2)
      Text(g.map { RecordFormat.num($0) } ?? "—").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
      Text("g").font(Typo.unit).foregroundColor(Palette.text2)
    }
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
