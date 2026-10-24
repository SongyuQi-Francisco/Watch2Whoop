// Jarvis voice input on the phone (voice_bridge.dart, #53): the page's voice:* / haptic:* messages, the JS that hands the
// recogniser's events back, the features the page reads, and that the page and the app speak the same protocol.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/voice_bridge.dart';
import 'package:watch2whoop/src/web_mic.dart';

void main() {
  test('voice messages are checked before they reach the recogniser', () {
    final start = parseVoiceMessage('voice:start {"session":"jv1","lang":"zh","audio":true}')!;
    expect(start.verb, 'start');
    expect(start.args, {'session': 'jv1', 'lang': 'zh', 'audio': true});
    expect(parseVoiceMessage('voice:lang {"session":"jv1","lang":"en"}')!.args, {'session': 'jv1', 'lang': 'en', 'audio': false});
    expect(parseVoiceMessage('voice:stop {"session":"jv1"}')!.args, {'session': 'jv1'});
    expect(parseVoiceMessage('voice:cancel {"session":"jv1","lang":"zh"}')!.args, {'session': 'jv1'});
    for (final bad in [
      'voice:start {"session":"jv1","lang":"fr"}', // a language the page never asks for
      'voice:start {"session":"jv1"}',
      'voice:start {"session":"","lang":"zh"}',
      'voice:start {"session":"${'x' * 65}","lang":"zh"}',
      'voice:record {"session":"jv1"}', // unknown verb
      'voice:stop',
      'voice:stop not-json',
      'voice:stop ["jv1"]',
      'mic-settings',
    ]) {
      expect(parseVoiceMessage(bad), isNull, reason: bad);
    }
  });

  test('haptic messages', () {
    for (final k in kHapticKinds) {
      expect(parseHapticMessage('haptic:$k'), k);
    }
    expect(parseHapticMessage('haptic:heavy'), isNull);
    expect(parseHapticMessage('voice:stop {"session":"a"}'), isNull);
  });

  test('events reach the page as JSON, whatever the recognised text contains', () {
    final js = voiceEventJs({'type': 'partial', 'session': 'jv1', 'text': '昨晚"睡得"</script>\n怎么样'});
    expect(js, startsWith('window.Watch2WhoopVoice&&window.Watch2WhoopVoice.event('));
    final arg = js.substring(js.indexOf('(') + 1, js.lastIndexOf(')'));
    expect(jsonDecode(arg), {'type': 'partial', 'session': 'jv1', 'text': '昨晚"睡得"</script>\n怎么样'});
  });

  test('features: microphone and sync center as before, haptics, voice only with languages', () {
    final none = featuresJs();
    expect(none, startsWith(kFeaturesJs));
    expect(none, contains('window.Watch2WhoopFeatures.haptics=true;'));
    expect(none, isNot(contains('.voice=')));
    expect(featuresJs(voiceLangs: ['zh', 'en', 'fr']),
        contains('window.Watch2WhoopFeatures.voice={"langs":["zh","en"],"onDevice":true};'));
  });

  test('the page speaks the same protocol', () {
    final page = File('../../body_engine/src/body_engine/web/static/jarvis-entry.js');
    if (!page.existsSync()) return; // the app built on its own, or before the page side is merged
    final js = page.readAsStringSync();
    expect(js, contains('window.Watch2WhoopVoice = { event(e)'));
    expect(js, contains('tell(`voice:\${verb} \${JSON.stringify({ session, ...extra })}`)'));
    for (final verb in kVoiceVerbs) {
      expect(js, contains('"$verb"'), reason: verb);
    }
    expect(js, contains('tell(`haptic:\${kind}`)'));
    expect(js, contains('Watch2WhoopFeatures?.haptics'));
    final media = File('../../body_engine/src/body_engine/web/static/ai-media.js').readAsStringSync();
    expect(media, contains('w.Watch2WhoopFeatures?.voice'));
  });

  test('the native side asks for on-device recognition only', () {
    final swift = File('ios/Runner/VoiceInput.swift').readAsStringSync();
    expect(swift, contains('req.requiresOnDeviceRecognition = true'));
    expect(swift, contains('supportsOnDeviceRecognition'));
    expect(swift, contains('static func haptic('));
    expect(File('ios/Runner/AppDelegate.swift').readAsStringSync(), contains('"watch2whoop/voice"'));
    expect(File('ios/Runner.xcodeproj/project.pbxproj').readAsStringSync(), contains('VoiceInput.swift in Sources'));
  });
}
