// The foreground event the app sends (#114) is the one the Today page listens for.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/page_events.dart';

void main() {
  test('the page listens for the foreground event the app dispatches', () {
    expect(kForegroundJs, "window.dispatchEvent(new Event('watch2whoop:foreground'));");
    final page = File('../../body_engine/src/body_engine/web/static/ai.js');
    if (!page.existsSync()) return; // the app built on its own, outside the repository
    expect(page.readAsStringSync(), contains('"$kForegroundEvent"'));
  });
}
