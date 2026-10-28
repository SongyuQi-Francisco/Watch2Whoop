// swift-tools-version:5.9
// A stand-in for the three Flutter types the copied sync files use (FlutterMethodCall, FlutterResult, FlutterError),
// so apps/ios/Watch2Whoop/HealthSync/{SyncEngine,RawSync,Backfill}.swift can stay byte-for-byte copies of the Flutter
// app's files (`import Flutter` included) until N8 gives them a native API. No Flutter engine, no Dart.
import PackageDescription

let package = Package(
  name: "FlutterShim",
  platforms: [.iOS(.v16)],
  products: [.library(name: "Flutter", targets: ["Flutter"])],
  targets: [.target(name: "Flutter")]
)
