// The Today page reads native features from window.Watch2WhoopFeatures (#295 A7): every one it reads must be declared by
// the app (or listed below as page-only with its reason), and the bridge messages must match the page's.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/apple_bridge.dart';
import 'package:watch2whoop/src/sync_bridge.dart';
import 'package:watch2whoop/src/voice_bridge.dart';

const _static = '../../body_engine/src/body_engine/web/static';

void main() {
  final dir = Directory(_static);

  test('every feature the page reads is declared by the app', () {
    if (!dir.existsSync()) return; // the app built on its own
    final read = <String>{};
    for (final f in dir.listSync().whereType<File>().where((f) => f.path.endsWith('.js'))) {
      for (final m in RegExp(r'Watch2WhoopFeatures\??\.(\w+)').allMatches(f.readAsStringSync())) {
        read.add(m.group(1)!);
      }
    }
    final declared = [
      featuresJs(voiceLangs: const ['zh']),
      kSyncBridgeJs,
      kAppleFeaturesJs,
      netFeatureJs('wifi')!,
      kSyncLinkFeaturesJs,
    ].join();
    for (final name in read) {
      expect(RegExp('[{,.]$name[:=]').hasMatch(declared), isTrue, reason: 'the page reads Watch2WhoopFeatures.$name');
    }
  });

  test('Apple: the page posts the messages the app takes, the app answers through appleResult', () {
    if (!dir.existsSync()) return;
    expect(File('$_static/account.js').readAsStringSync(), contains('postMessage("$kAppleSignInMessage")'));
    final ids = File('$_static/identities.js').readAsStringSync();
    expect(ids, contains('postMessage("$kAppleLinkMessage")'));
    expect(ids, contains('appleResult: (res) =>'));
    expect(ids, contains('res.identity_token'));
    expect(ids, contains('res.nonce'));
  });

  test('Apple: the result JS carries the raw nonce, or null when cancelled', () {
    expect(appleResultJs(null), 'window.BodyIdentities&&window.BodyIdentities.appleResult(null);');
    final js = appleResultJs(const AppleCredential('tok"en', 'raw-nonce'));
    final arg = js.substring(js.indexOf('appleResult(') + 'appleResult('.length, js.lastIndexOf(')'));
    expect(jsonDecode(arg), {'identity_token': 'tok"en', 'nonce': 'raw-nonce'});
    expect(kAppleFeaturesJs, contains('{appleSignIn:true,appleLink:true}'));
  });
}
