import PhotosUI
import SwiftUI

/// The three photos of a posture check (#174, docs/posture-photos-plan.md phase 1), kept in memory only until they are sent.
/// Always sent front, side, back: the prefilled question names them in that order.
struct PostureFlow {
  enum Angle: CaseIterable, Identifiable {
    case front, side, back
    var id: Self { self }

    var label: String {
      switch self {
      case .front: return tr("正面", "Front")
      case .side: return tr("侧面", "Side")
      case .back: return tr("背面", "Back")
      }
    }

    var hint: String {
      switch self {
      case .front: return tr("站直，全身入镜", "Stand straight, full body in frame")
      case .side: return tr("露出耳朵和肩膀", "Ear and shoulder showing")
      case .back: return tr("背对镜头，手臂放松", "Back to the camera, arms relaxed")
      }
    }
  }

  /// `POST /v1/assistant/chat` → `purpose` (ai/posture_params.py PURPOSES): the server replaces these photos when the answer ends.
  static let purpose = "posture"

  static var question: String {
    tr("这是我的体态照片，依次是正面、侧面、背面。结合我的肌肉负荷、训练目标和动作限制，说说你观察到什么、照片够不够清楚，训练上可以怎么调整。", "These are my posture photos: front, side and back, in that order. Using my muscle load, training goals and exercise limits, tell me what you observe, whether the photos are clear enough, and how I could adjust my training.")
  }

  /// The start of the message when this AI takes no posture photos.
  static var inWords: String { tr("想聊聊我的体态：", "About my posture: ") }

  var photos: [Angle: ChatAttachment] = [:]

  var ready: Bool { Angle.allCases.allSatisfy { photos[$0] != nil } }
  var attachments: [ChatAttachment] { Angle.allCases.compactMap { photos[$0] } }
}

/// Whether the first-use note was seen, per account and per AI: the note names the AI the photos go to.
enum PostureNotice {
  private static func key(_ account: Int?, _ ai: String) -> String { "jarvis.posture.noticed.\(account ?? 0).\(ai)" }
  static func seen(account: Int?, ai: String, defaults: UserDefaults = .standard) -> Bool { defaults.bool(forKey: key(account, ai)) }
  static func mark(account: Int?, ai: String, defaults: UserDefaults = .standard) { defaults.set(true, forKey: key(account, ai)) }
}

/// The posture check, opened from the chat's photo menu. `photos`: guided photos + a prefilled question, sent once to the AI in
/// use. `guide` (the fallback AI until it passes the plan's phase 0, or an AI that can't read pictures): the same guide, no photo
/// leaves the phone, the user describes it in words. Which of the two comes from the server (ai/posture.py `mode`).
struct PostureSheet: View {
  @ObservedObject var chat: JarvisChat
  let account: Int?
  let close: () -> Void
  let describe: () -> Void

  @State private var flow = PostureFlow()
  @State private var question = PostureFlow.question
  @State private var noticed = false
  @State private var target: PostureFlow.Angle?
  @State private var camera = false
  @State private var library = false
  @State private var picked: [PhotosPickerItem] = []
  @State private var unreadable = false

  private var name: String { chat.ai.shortName }

  var body: some View {
    SheetContainer(title: tr("体态分析", "Posture check"), close: close) {
      VStack(alignment: .leading, spacing: 14) {
        if chat.ai.posture != .photos { guide } else if noticed { photos } else { notice }
      }
    }
    .sheet(isPresented: $camera) {
      CameraPicker { img in
        camera = false
        store(img)
      }
      .ignoresSafeArea()
    }
    .photosPicker(isPresented: $library, selection: $picked, maxSelectionCount: 1, matching: .images)
    .onChange(of: picked) { items in
      guard !items.isEmpty else { return }
      picked = []
      Task {
        let r = await JarvisMedia.load(items, room: 1)
        if let a = r.added.first { put(a) } else { unreadable = true }
      }
    }
    .onAppear {
      noticed = PostureNotice.seen(account: account, ai: name)
      #if DEBUG
      if JarvisDemo.mode == "posture-filled" {
        flow = JarvisDemo.postureFlow()
        noticed = true
      }
      #endif
    }
  }

  // MARK: - first use

  /// Not the consent (it is not versioned and asks for nothing new): what happens to the photos, before the first one is taken.
  private var notice: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text(tr("体态照片只随这次提问发给你选的 AI（\(name)），回答完服务器上就删掉原图，只留 AI 的文字结论。可以不露脸；建议穿贴身的运动服。AI 的结论只是观察，不是诊断。", "Posture photos go only to the AI you picked (\(name)), with this question. Once it has answered, the server deletes the originals and keeps only the AI's written result. You don't have to show your face; wear fitted sportswear. The result is an observation, not a diagnosis."))
        .font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
      PrimaryButton(title: tr("知道了", "Got it")) {
        PostureNotice.mark(account: account, ai: name)
        noticed = true
      }
    }
  }

  // MARK: - photos

  private var photos: some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack(alignment: .top, spacing: 10) {
        ForEach(PostureFlow.Angle.allCases) { a in tile(a) }
      }
      cameraLine
      if unreadable {
        Text(tr("这张读不了，换一张", "Couldn't read that photo; try another")).font(Typo.caption).foregroundColor(Palette.text2)
      }
      TextField(tr("想问什么", "What to ask"), text: $question, axis: .vertical)
        .lineLimit(2...5)
        .font(Typo.body)
        .padding(.vertical, 10).padding(.horizontal, 14)
        .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.inSheet))
      PrimaryButton(title: tr("发给 \(name)", "Send to \(name)")) { send() }
        .disabled(!flow.ready || chat.busy)
        .opacity(flow.ready && !chat.busy ? 1 : 0.4)
    }
  }

  private func tile(_ a: PostureFlow.Angle) -> some View {
    VStack(spacing: 6) {
      Menu {
        Button {
          target = a
          camera = true
        } label: { Label(tr("拍照", "Camera"), image: Icon.Name.camera.assetName) }
        Button {
          target = a
          library = true
        } label: { Label(tr("从相册选", "From library"), image: Icon.Name.image.assetName) }
      } label: {
        Palette.inSheet
          .aspectRatio(3.0 / 4.0, contentMode: .fit)
          .overlay {
            if let t = flow.photos[a]?.thumb {
              Image(uiImage: t).resizable().scaledToFill()
            } else {
              Icon(.personStanding, size: 28).foregroundColor(Palette.text3)
            }
          }
          .clipShape(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous))
      }
      .accessibilityLabel(a.label)
      .accessibilityValue(flow.photos[a] == nil ? "" : tr("已选", "Added"))
      Text(a.label).font(Typo.bodyStrong).foregroundColor(Palette.text)
      Text(a.hint).font(Typo.label).foregroundColor(Palette.text2)
        .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
    }
    .frame(maxWidth: .infinity)
  }

  private var cameraLine: some View {
    Text(tr("相机齐腰高，约 2 米外", "Camera at waist height, about 2 m away")).font(Typo.caption).foregroundColor(Palette.text2)
  }

  private func store(_ image: UIImage?) {
    guard let image else { return }
    if let a = JarvisMedia.photo(image) { put(a) } else { unreadable = true }
  }

  private func put(_ a: ChatAttachment) {
    guard let target else { return }
    flow.photos[target] = a
    unreadable = false
  }

  private func send() {
    let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
    chat.send(q.isEmpty ? PostureFlow.question : q, attachments: flow.attachments, purpose: PostureFlow.purpose)
    close()
  }

  // MARK: - guide only

  private var guide: some View {
    VStack(alignment: .leading, spacing: 14) {
      ForEach(PostureFlow.Angle.allCases) { a in
        HStack(spacing: 10) {
          Icon(.personStanding, size: 20).foregroundColor(Palette.text2)
          Text(a.label).font(Typo.bodyStrong).foregroundColor(Palette.text)
          Text(a.hint).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
        }
      }
      cameraLine
      Text(tr("这个 AI 现在不看体态照片", "This AI doesn't take posture photos for now")).font(Typo.caption).foregroundColor(Palette.text2)
      PrimaryButton(title: tr("用文字描述", "Describe in words")) { describe() }
    }
  }
}
