// Hold-to-talk inside the app (#171): which WebKit media capture requests the app grants, and the feature flag the page reads.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/web_mic.dart';
import 'package:webview_flutter/webview_flutter.dart';

void main() {
  final today = Uri.parse('https://body.example.org/app/');
  const mic = {WebViewPermissionResourceType.microphone};

  bool allow(Set<WebViewPermissionResourceType> types, String? page, {bool onShell = false}) =>
      allowMediaCapture(types: types, pageUrl: page, todayEntry: today, onShell: onShell);

  test('the microphone is granted to our own Today page', () {
    expect(allow(mic, 'https://body.example.org/app/'), isTrue);
    expect(allow(mic, 'https://BODY.example.org/app/#today'), isTrue);
    expect(allow(mic, 'https://body.example.org/app/handbook/'), isTrue);
  });

  test('other origins, schemes and the kept offline copy are denied', () {
    expect(allow(mic, 'https://evil.example.org/app/'), isFalse);
    expect(allow(mic, 'http://body.example.org/app/'), isFalse); // other scheme = other origin
    expect(allow(mic, 'https://body.example.org:8443/app/'), isFalse);
    expect(allow(mic, 'data:text/html,<p>hi</p>'), isFalse);
    expect(allow(mic, 'about:blank'), isFalse);
    expect(allow(mic, null), isFalse);
    expect(allow(mic, ''), isFalse);
    expect(allow(mic, 'https://body.example.org/app/', onShell: true), isFalse);
  });

  test('cameras are never granted, alone or with the microphone', () {
    const cam = {WebViewPermissionResourceType.camera};
    const both = {WebViewPermissionResourceType.camera, WebViewPermissionResourceType.microphone};
    expect(allow(cam, 'https://body.example.org/app/'), isFalse);
    expect(allow(both, 'https://body.example.org/app/'), isFalse);
    expect(allow({}, 'https://body.example.org/app/'), isFalse);
  });

  test('a local development server works the same way', () {
    final local = Uri.parse('http://localhost:8100/app/');
    expect(allowMediaCapture(types: mic, pageUrl: 'http://localhost:8100/app/', todayEntry: local, onShell: false), isTrue);
    expect(allowMediaCapture(types: mic, pageUrl: 'http://localhost:8200/', todayEntry: local, onShell: false), isFalse);
  });

  test('the feature flag keeps other flags and turns mic on', () {
    expect(kFeaturesJs, contains('Object.assign(window.Watch2WhoopFeatures||{}'));
    expect(kFeaturesJs, contains('{mic:true,syncCenter:true}'));
  });

  test('the page reads the same flag and sends the same settings message', () {
    final page = File('../../body_engine/src/body_engine/web/static/ai-media.js');
    if (!page.existsSync()) return; // the app built on its own, outside the repository
    final js = page.readAsStringSync();
    expect(js, contains('Watch2WhoopFeatures?.mic'));
    expect(js, contains('"$kMicSettingsMessage"'));
  });
}
