import PhotosUI
import SwiftUI
import UIKit

/// Pictures of people and circles. They are behind the account's token (`/v1/avatars/users/{id}` and
/// `/v1/friends/circles/{id}/avatar`, members only), so they are fetched through the API client, never with a plain
/// image URL. The URL carries the picture's id (`?v=…`), so a cached copy (memory, then the caches folder) is valid until
/// the URL changes. Exercise pictures in posts go the same way (public URLs are fetched without the token).
actor ImageCache {
  static let shared = ImageCache()
  private let memory = NSCache<NSString, UIImage>()
  private var running: [String: Task<UIImage?, Never>] = [:]

  private var folder: URL? {
    guard let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first else { return nil }
    let dir = base.appendingPathComponent("W2W/images", isDirectory: true)
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
  }

  private func file(_ key: String) -> URL? {
    let safe = key.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) ? String($0) : "_" }.joined()
    return folder?.appendingPathComponent(String(safe.suffix(120)))
  }

  /// In memory only (what a view can draw in its first frame).
  nonisolated func memoryImage(_ key: String) -> UIImage? { memory.object(forKey: key as NSString) }

  func image(_ url: String, api: APIClient) async -> UIImage? {
    if let m = memory.object(forKey: url as NSString) { return m }
    if let f = file(url), let d = try? Data(contentsOf: f), let img = UIImage(data: d) {
      memory.setObject(img, forKey: url as NSString)
      return img
    }
    if let t = running[url] { return await t.value }
    let t = Task<UIImage?, Never> {
      let data: Data?
      if url.hasPrefix("http") {
        data = try? await URLSession.shared.data(from: URL(string: url)!).0
      } else {
        data = try? await api.data(apiRequest(relative: url))
      }
      guard let data, let img = UIImage(data: data) else { return nil }
      return img
    }
    running[url] = t
    let img = await t.value
    running[url] = nil
    if let img {
      memory.setObject(img, forKey: url as NSString)
      if let f = file(url), let d = img.jpegData(compressionQuality: 0.9) { try? d.write(to: f) }
    }
    return img
  }
}

/// A round picture, or the first letter of the name on a grey disc while there is none (or it has not loaded yet).
struct AvatarView: View {
  @EnvironmentObject private var session: AccountSession
  let url: String?
  let name: String
  var size: CGFloat = 36
  @State private var image: UIImage?

  var body: some View {
    ZStack {
      Circle().fill(Palette.surface2)
      if let image {
        Image(uiImage: image).resizable().scaledToFill()
      } else {
        Text(Self.initial(name))
          .font(.system(size: size * 0.42, weight: .semibold))
          .foregroundColor(Palette.text2)
      }
    }
    .frame(width: size, height: size)
    .clipShape(Circle())
    .accessibilityHidden(true)
    .task(id: url) {
      guard let url else { image = nil; return }
      if let m = ImageCache.shared.memoryImage(url) { image = m; return }
      image = await ImageCache.shared.image(url, api: session.api)
    }
  }

  static func initial(_ name: String) -> String {
    String(name.trimmingCharacters(in: .whitespaces).first ?? "?").uppercased()
  }
}

/// A row of overlapping avatars (the summary card, a circle's members).
struct AvatarStack: View {
  let people: [(url: String?, name: String)]
  var size: CGFloat = 28
  var max = 5

  var body: some View {
    HStack(spacing: -size * 0.3) {
      ForEach(Array(people.prefix(max).enumerated()), id: \.offset) { _, p in
        AvatarView(url: p.url, name: p.name, size: size)
          .overlay(Circle().stroke(Palette.bg, lineWidth: 2))
      }
      if people.count > max {
        Text("+\(people.count - max)")
          .font(.system(size: size * 0.36, weight: .semibold)).foregroundColor(Palette.text2)
          .frame(width: size, height: size)
          .background(Circle().fill(Palette.surface2))
          .overlay(Circle().stroke(Palette.bg, lineWidth: 2))
      }
    }
  }
}

/// Change a picture: choose from the library (PhotosPicker, no permission prompt) or take one with the camera, or remove
/// it. The picture is cropped to a centred square and sent as a 512-pixel JPEG (the server crops and shrinks it again,
/// images/params.py), which also drops the photo's metadata (location, device).
struct AvatarEditor: View {
  @EnvironmentObject private var session: AccountSession
  let store: CirclesStore
  let title: String
  let url: String?
  let name: String
  /// nil = my own picture; else the circle whose logo this is.
  let circle: Int?
  @State private var item: PhotosPickerItem?
  @State private var camera = false
  @State private var busy = false
  @State private var preview: UIImage?

  var body: some View {
    VStack(spacing: 18) {
      ZStack {
        if let preview {
          Image(uiImage: preview).resizable().scaledToFill().frame(width: 112, height: 112).clipShape(Circle())
        } else {
          AvatarView(url: url, name: name, size: 112)
        }
        if busy { ProgressView().tint(Palette.text) }
      }
      .padding(.top, 4)
      VStack(spacing: 10) {
        PhotosPicker(selection: $item, matching: .images, photoLibrary: .shared()) {
          Label { Text(tr("从相册选", "Choose a photo")).font(Typo.bodyStrong) } icon: { Icon(.image, size: 18) }
            .foregroundColor(Palette.text)
            .frame(maxWidth: .infinity, minHeight: 48)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.inSheet))
        }
        .buttonStyle(PressScale())
        if UIImagePickerController.isSourceTypeAvailable(.camera) {
          SecondaryButton(icon: .camera, title: tr("拍一张", "Take a photo"), background: Palette.inSheet) { camera = true }
        }
        if url != nil {
          SecondaryButton(icon: .trash, title: tr("去掉图片", "Remove picture"), tone: .bad, background: Palette.inSheet) {
            Task { busy = true; await store.deleteAvatar(circle: circle); busy = false }
          }
        }
      }
      .disabled(busy)
    }
    .onChange(of: item) { new in
      guard let new else { return }
      Task {
        busy = true
        defer { busy = false; item = nil }
        guard let data = try? await new.loadTransferable(type: Data.self), let img = UIImage(data: data) else { return }
        await upload(img)
      }
    }
    .fullScreenCover(isPresented: $camera) {
      CameraPicker { img in
        camera = false
        if let img { Task { busy = true; await upload(img); busy = false } }
      }
      .ignoresSafeArea()
    }
  }

  private func upload(_ img: UIImage) async {
    guard let jpeg = Self.squareJPEG(img) else { return }
    preview = UIImage(data: jpeg)
    if await !store.uploadAvatar(jpeg, circle: circle) { preview = nil }
  }

  /// Centre square, `side` pixels, JPEG; the camera's rotation is applied by redrawing.
  static func squareJPEG(_ img: UIImage, side: CGFloat = 512) -> Data? {
    let s = min(img.size.width, img.size.height)
    guard s > 0 else { return nil }
    let scale = side / s
    let fmt = UIGraphicsImageRendererFormat()
    fmt.scale = 1
    let out = UIGraphicsImageRenderer(size: CGSize(width: side, height: side), format: fmt).image { _ in
      let w = img.size.width * scale, h = img.size.height * scale
      img.draw(in: CGRect(x: (side - w) / 2, y: (side - h) / 2, width: w, height: h))
    }
    return out.jpegData(compressionQuality: 0.85)
  }
}

/// The system camera (SwiftUI has none on iOS 16).
private struct CameraPicker: UIViewControllerRepresentable {
  let done: (UIImage?) -> Void

  func makeUIViewController(context: Context) -> UIImagePickerController {
    let c = UIImagePickerController()
    c.sourceType = .camera
    c.cameraDevice = .front
    c.delegate = context.coordinator
    return c
  }

  func updateUIViewController(_ c: UIImagePickerController, context: Context) {}
  func makeCoordinator() -> Coordinator { Coordinator(done: done) }

  final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
    let done: (UIImage?) -> Void
    init(done: @escaping (UIImage?) -> Void) { self.done = done }
    func imagePickerController(_ p: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
      done(info[.originalImage] as? UIImage)
    }
    func imagePickerControllerDidCancel(_ p: UIImagePickerController) { done(nil) }
  }
}
