import Foundation

/// Same shape as Flutter's `FlutterResult`: the callback a method handler answers with (a value, a FlutterError or nil).
public typealias FlutterResult = (Any?) -> Void

/// Same shape as Flutter's `FlutterMethodCall`: a method name and its arguments (property lists only).
public final class FlutterMethodCall: NSObject {
  public let method: String
  public let arguments: Any?

  public init(methodName: String, arguments: Any?) {
    self.method = methodName
    self.arguments = arguments
  }
}

/// Same shape as Flutter's `FlutterError`: an error answer with a short code.
public final class FlutterError: NSObject, Error {
  public let code: String
  public let message: String?
  public let details: Any?

  public init(code: String, message: String?, details: Any?) {
    self.code = code
    self.message = message
    self.details = details
  }
}
