import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/push.dart';

void main() {
  final entry = Uri.parse('https://body.example.com/app/');

  test('a tapped notification opens a page on the same server only', () {
    expect(notificationTarget(entry, '/app/?tab=friends&feed=3').toString(), 'https://body.example.com/app/?tab=friends&feed=3');
    expect(notificationTarget(entry, '/app/#tab=friends').toString(), 'https://body.example.com/app/#tab=friends');
    expect(notificationTarget(entry, '/app/#notifications').toString(), 'https://body.example.com/app/#notifications');
    for (final bad in ['https://evil.example/app/', '//evil.example/x', 'javascript:alert(1)', 'app/', '']) {
      expect(notificationTarget(entry, bad).toString(), 'https://body.example.com/app/#notifications', reason: bad);
    }
    // a server behind a port keeps it
    expect(notificationTarget(Uri.parse('http://127.0.0.1:8100/app/'), '/app/?tab=friends').toString(),
        'http://127.0.0.1:8100/app/?tab=friends');
  });

  test('bridge messages and page events', () {
    expect(parseBadgeMessage('badge:3'), 3);
    expect(parseBadgeMessage('badge:x'), isNull);
    expect(parseBadgeMessage('sync'), isNull);
    expect(kPushAskMessage, 'push:ask');
    expect(pushStateJs('granted'), contains("{state: 'granted'}"));
    expect(pushStateJs("x');alert(1);//"), contains("{state: 'xalert'}")); // only letters reach the page
    expect(registrationKey('https://a', 'abc', 'production'), 'https://a|abc|production');
  });

  test('push capability: entitlement and native bridge are in place', () {
    final ent = File('ios/Runner/Runner.entitlements').readAsStringSync();
    expect(RegExp(r'<key>aps-environment</key>\s*<string>development</string>').hasMatch(ent), isTrue);
    final app = File('ios/Runner/AppDelegate.swift').readAsStringSync();
    for (final s in ['UNUserNotificationCenter.current().delegate = self', 'didRegisterForRemoteNotificationsWithDeviceToken',
        'FlutterMethodChannel(name: "watch2whoop/push"', 'didReceive response']) {
      expect(app, contains(s));
    }
    // never asked at launch: requestAuthorization only inside the "request" call
    expect(RegExp(r'UNUserNotificationCenter\.current\(\)\.requestAuthorization').allMatches(app).length, 1);
  });
}
