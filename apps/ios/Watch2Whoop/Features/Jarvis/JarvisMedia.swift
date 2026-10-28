import AVFoundation
import CoreTransferable
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Photos and videos for Jarvis, prepared on the phone before anything is uploaded (same rules as the page's ai-media.js):
/// - photo: long edge ≤ 1600 px, JPEG 0.85 (enough for a nutrition label or a lab report);
/// - video: up to 8 frames spread over the clip (long edge 1024 px), with its length; the clip itself is not uploaded
///   and its temporary copy is deleted at once.
enum JarvisMedia {
  static let maxImages = 8
  static let photoEdge: CGFloat = 1600
  static let frameEdge: CGFloat = 1024

  static func jpeg(_ image: UIImage, edge: CGFloat, quality: CGFloat) -> ChatImage? {
    let size = image.size
    guard size.width > 0, size.height > 0 else { return nil }
    let s = min(1, edge / max(size.width, size.height))
    let target = CGSize(width: max(1, (size.width * s).rounded()), height: max(1, (size.height * s).rounded()))
    let format = UIGraphicsImageRendererFormat.default()
    format.scale = 1
    let out = UIGraphicsImageRenderer(size: target, format: format).image { _ in image.draw(in: CGRect(origin: .zero, size: target)) }
    guard let data = out.jpegData(compressionQuality: quality) else { return nil }
    return ChatImage(media_type: "image/jpeg", data: data.base64EncodedString())
  }

  static func photo(_ image: UIImage) -> ChatAttachment? {
    guard let img = jpeg(image, edge: photoEdge, quality: 0.85) else { return nil }
    return ChatAttachment(kind: .photo, images: [img], thumb: thumbnail(image))
  }

  /// Evenly spread frames: the middle of each of `count` equal parts of the clip.
  static func frameTimes(duration: Double, count: Int) -> [Double] {
    guard duration > 0, count > 0 else { return [0] }
    return (0..<count).map { duration * (Double($0) + 0.5) / Double(count) }
  }

  /// Up to `room` frames of the video at `url` (≤ 8).
  static func video(_ url: URL, room: Int) async throws -> ChatAttachment {
    let asset = AVURLAsset(url: url)
    let duration = try await asset.load(.duration).seconds
    let gen = AVAssetImageGenerator(asset: asset)
    gen.appliesPreferredTrackTransform = true
    gen.maximumSize = CGSize(width: frameEdge, height: frameEdge)
    gen.requestedTimeToleranceBefore = CMTime(seconds: 0.5, preferredTimescale: 600)
    gen.requestedTimeToleranceAfter = CMTime(seconds: 0.5, preferredTimescale: 600)
    var frames: [ChatImage] = []
    var first: UIImage?
    for t in frameTimes(duration: duration, count: max(1, min(maxImages, room))) {
      let cg = try await gen.image(at: CMTime(seconds: t, preferredTimescale: 600)).image
      let ui = UIImage(cgImage: cg)
      if first == nil { first = ui }
      if let j = jpeg(ui, edge: frameEdge, quality: 0.8) { frames.append(j) }
    }
    guard !frames.isEmpty else { throw CocoaError(.fileReadCorruptFile) }
    return ChatAttachment(kind: .video(seconds: Int(duration.rounded())), images: frames, thumb: first.map(thumbnail))
  }

  static func thumbnail(_ image: UIImage) -> UIImage {
    let side: CGFloat = 120
    let s = side / min(image.size.width, image.size.height)
    let size = CGSize(width: image.size.width * s, height: image.size.height * s)
    return UIGraphicsImageRenderer(size: CGSize(width: side, height: side)).image { _ in
      image.draw(in: CGRect(x: (side - size.width) / 2, y: (side - size.height) / 2, width: size.width, height: size.height))
    }
  }

  /// What the photo library picker returned, as attachments (photos first, then videos), within `room` pictures.
  static func load(_ items: [PhotosPickerItem], room: Int) async -> (added: [ChatAttachment], failed: Bool) {
    var out: [ChatAttachment] = []
    var left = room, failed = false
    for item in items where left > 0 {
      if item.supportedContentTypes.contains(where: { $0.conforms(to: .movie) }) {
        do {
          guard let m = try await item.loadTransferable(type: PickedMovie.self) else { failed = true; continue }
          defer { try? FileManager.default.removeItem(at: m.url) }
          let v = try await video(m.url, room: left)
          left -= v.images.count
          out.append(v)
        } catch {
          failed = true
        }
      } else if let d = try? await item.loadTransferable(type: Data.self), let img = UIImage(data: d), let p = photo(img) {
        left -= 1
        out.append(p)
      } else {
        failed = true
      }
    }
    return (out, failed)
  }
}

/// A picked video, copied to a temporary file only for reading its frames.
struct PickedMovie: Transferable {
  let url: URL

  static var transferRepresentation: some TransferRepresentation {
    FileRepresentation(contentType: .movie) { m in SentTransferredFile(m.url) } importing: { received in
      let dst = FileManager.default.temporaryDirectory.appendingPathComponent("jarvis-\(UUID().uuidString).\(received.file.pathExtension)")
      try FileManager.default.copyItem(at: received.file, to: dst)
      return PickedMovie(url: dst)
    }
  }
}

/// The camera (SwiftUI has none on iOS 16). Photo only; falls back to the library on a device without a camera (simulator).
struct CameraPicker: UIViewControllerRepresentable {
  let done: (UIImage?) -> Void

  func makeUIViewController(context: Context) -> UIImagePickerController {
    let p = UIImagePickerController()
    p.sourceType = UIImagePickerController.isSourceTypeAvailable(.camera) ? .camera : .photoLibrary
    p.mediaTypes = [UTType.image.identifier]
    p.delegate = context.coordinator
    return p
  }

  func updateUIViewController(_ vc: UIImagePickerController, context: Context) {}

  func makeCoordinator() -> Coordinator { Coordinator(done) }

  final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
    let done: (UIImage?) -> Void
    init(_ done: @escaping (UIImage?) -> Void) { self.done = done }

    func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
      done(info[.originalImage] as? UIImage)
    }

    func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { done(nil) }
  }
}
