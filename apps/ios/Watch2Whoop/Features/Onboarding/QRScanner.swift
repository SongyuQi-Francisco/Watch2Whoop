import SwiftUI
import VisionKit

/// Scans the setup QR code with VisionKit's DataScannerViewController (iOS 16, devices with the Neural Engine; not in the
/// simulator — the sign-in screen then hides the button and pasting still works). The first QR payload is handed back.
struct QRScannerSheet: View {
  let found: (String) -> Void
  @Environment(\.dismiss) private var dismiss

  static var isAvailable: Bool { DataScannerViewController.isSupported && DataScannerViewController.isAvailable }

  var body: some View {
    ZStack(alignment: .topTrailing) {
      Scanner(found: found).ignoresSafeArea()
      Button { dismiss() } label: {
        Icon(.x, size: 18).foregroundColor(.white)
          .frame(width: 36, height: 36)
          .background(Circle().fill(Color.black.opacity(0.5)))
      }
      .padding(16)
      .accessibilityLabel(tr("关闭", "Close"))
      VStack {
        Spacer()
        Text(tr("对准设置二维码", "Point at the setup QR code"))
          .font(Typo.bodyStrong).foregroundColor(.white)
          .padding(.vertical, 10).padding(.horizontal, 16)
          .background(Capsule().fill(Color.black.opacity(0.5)))
          .padding(.bottom, 40)
      }
      .frame(maxWidth: .infinity)
    }
  }

  private struct Scanner: UIViewControllerRepresentable {
    let found: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
      let vc = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])], qualityLevel: .balanced,
                                         isHighlightingEnabled: true)
      vc.delegate = context.coordinator
      try? vc.startScanning()
      return vc
    }

    func updateUIViewController(_ vc: DataScannerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(found: found) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
      let found: (String) -> Void
      private var done = false
      init(found: @escaping (String) -> Void) { self.found = found }

      func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
        guard !done else { return }
        for case .barcode(let b) in items {
          guard let s = b.payloadStringValue, !s.isEmpty else { continue }
          done = true
          scanner.stopScanning()
          found(s)
          return
        }
      }
    }
  }
}
