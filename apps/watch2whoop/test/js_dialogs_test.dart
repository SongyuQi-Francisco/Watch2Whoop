// The Today page's confirm() / alert() / prompt() as native dialogs (js_dialogs.dart): without them every confirm() on
// the page is false inside the app.
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:watch2whoop/src/i18n.dart';
import 'package:watch2whoop/src/js_dialogs.dart';

Future<T?> run<T>(WidgetTester t, Future<T> Function(BuildContext) open) async {
  T? result;
  var done = false;
  await t.pumpWidget(MaterialApp(home: Builder(builder: (ctx) => TextButton(
    onPressed: () async {
      result = await open(ctx);
      done = true;
    },
    child: const Text('open'),
  ))));
  await t.tap(find.text('open'));
  await t.pumpAndSettle();
  expect(done, isFalse, reason: 'waits for the person');
  return result;
}

void main() {
  tearDown(() => langOverride.value = null);

  testWidgets('confirm: OK is true, Cancel is false; the page text is shown as is', (t) async {
    langOverride.value = 'zh';
    bool? answer;
    await t.pumpWidget(MaterialApp(home: Builder(builder: (ctx) => TextButton(
      onPressed: () async => answer = await showJsConfirm(ctx, '退出「家人」？'),
      child: const Text('open'),
    ))));
    await t.tap(find.text('open'));
    await t.pumpAndSettle();
    expect(find.text('退出「家人」？'), findsOneWidget);
    await t.tap(find.text('确定'));
    await t.pumpAndSettle();
    expect(answer, isTrue);

    await t.tap(find.text('open'));
    await t.pumpAndSettle();
    await t.tap(find.text('取消'));
    await t.pumpAndSettle();
    expect(answer, isFalse);
  });

  testWidgets('buttons follow the app language', (t) async {
    langOverride.value = 'en';
    await run(t, (ctx) => showJsConfirm(ctx, 'Leave?'));
    expect(find.text('OK'), findsOneWidget);
    expect(find.text('Cancel'), findsOneWidget);
  });

  testWidgets('alert waits for OK; prompt returns the text, Cancel returns empty', (t) async {
    langOverride.value = 'en';
    var closed = false;
    await t.pumpWidget(MaterialApp(home: Builder(builder: (ctx) => Column(children: [
      TextButton(onPressed: () async {
        await showJsAlert(ctx, 'Saved');
        closed = true;
      }, child: const Text('alert')),
    ]))));
    await t.tap(find.text('alert'));
    await t.pumpAndSettle();
    expect(find.text('Saved'), findsOneWidget);
    expect(closed, isFalse);
    await t.tap(find.text('OK'));
    await t.pumpAndSettle();
    expect(closed, isTrue);

    String? text;
    await t.pumpWidget(MaterialApp(home: Builder(builder: (ctx) => TextButton(
      onPressed: () async => text = await showJsPrompt(ctx, 'Name?', 'Ann'),
      child: const Text('prompt'),
    ))));
    await t.tap(find.text('prompt'));
    await t.pumpAndSettle();
    await t.enterText(find.byType(EditableText), 'Bo');
    await t.tap(find.text('OK'));
    await t.pumpAndSettle();
    expect(text, 'Bo');
    await t.tap(find.text('prompt'));
    await t.pumpAndSettle();
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(text, '');
  });

  test('the Today page WebView uses them', () {
    final src = File('lib/src/pages/home_page.dart').readAsStringSync();
    for (final hook in ['setOnJavaScriptAlertDialog', 'setOnJavaScriptConfirmDialog', 'setOnJavaScriptTextInputDialog']) {
      expect(src, contains(hook));
    }
  });
}
