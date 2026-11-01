import SwiftUI

/// The page's bottom sheet (`.sheet`: sheet background, grabber, title, close button). Explain sheets (N3), record
/// editors (N5) and settings sub-pages (N8) use it: `.w2wSheet(isPresented:title:) { … }`.
struct SheetContainer<Content: View>: View {
  let title: String
  let close: () -> Void
  @ViewBuilder var content: () -> Content

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack {
        Text(title).font(.system(size: 17, weight: .semibold)).foregroundColor(Palette.text)
        Spacer()
        Button(action: close) {
          Icon(.x, size: 18).foregroundColor(Palette.text2)
            .frame(width: 32, height: 32)
            .background(Circle().fill(Palette.inSheet))
        }
        .buttonStyle(PressScale())
        .accessibilityLabel(tr("关闭", "Close"))
      }
      .padding(.horizontal, Metrics.gutter).padding(.top, 18).padding(.bottom, 12)
      ScrollView(.vertical, showsIndicators: false) {
        content().padding(.horizontal, Metrics.gutter).padding(.bottom, 24)
      }
    }
    .background(Palette.sheet.ignoresSafeArea())
  }
}

extension View {
  /// Medium / large detents with the grabber, the page's sheet colours.
  func w2wSheet<Content: View>(isPresented: Binding<Bool>, title: String,
                               @ViewBuilder content: @escaping () -> Content) -> some View {
    sheet(isPresented: isPresented) {
      SheetContainer(title: title, close: { isPresented.wrappedValue = false }, content: content)
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
  }
}
