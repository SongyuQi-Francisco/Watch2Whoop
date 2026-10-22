// alert() / confirm() / prompt() of the Today page as native dialogs. WKWebView shows none of them by itself, and
// webview_flutter answers confirm() with false and prompt() with '' unless the app handles them: in the app every
// "Delete … ?" / "Leave the circle?" on the page silently did nothing. The buttons follow the app language (the Today
// page's choice, else the system: i18n.dart); the message is the page's own text, already in that language.
import 'package:flutter/cupertino.dart';

import 'i18n.dart';

Future<void> showJsAlert(BuildContext context, String message) async {
  if (!context.mounted) return;
  await showCupertinoDialog<void>(
    context: context,
    builder: (ctx) => CupertinoAlertDialog(
      content: Text(message),
      actions: [
        CupertinoDialogAction(isDefaultAction: true, onPressed: () => Navigator.of(ctx).pop(), child: Text(tr('好', 'OK'))),
      ],
    ),
  );
}

/// true only when the person tapped OK; a dialog that could not be shown counts as Cancel (as in a browser).
Future<bool> showJsConfirm(BuildContext context, String message) async {
  if (!context.mounted) return false;
  final ok = await showCupertinoDialog<bool>(
    context: context,
    builder: (ctx) => CupertinoAlertDialog(
      content: Text(message),
      actions: [
        CupertinoDialogAction(onPressed: () => Navigator.of(ctx).pop(false), child: Text(tr('取消', 'Cancel'))),
        CupertinoDialogAction(isDefaultAction: true, onPressed: () => Navigator.of(ctx).pop(true), child: Text(tr('确定', 'OK'))),
      ],
    ),
  );
  return ok ?? false;
}

/// The entered text, or '' for Cancel (webview_flutter has no way to return null, which a browser would).
Future<String> showJsPrompt(BuildContext context, String message, String? defaultText) async {
  if (!context.mounted) return '';
  final field = TextEditingController(text: defaultText ?? '');
  try {
    final text = await showCupertinoDialog<String>(
      context: context,
      builder: (ctx) => CupertinoAlertDialog(
        content: Column(children: [
          Text(message),
          const SizedBox(height: 10),
          CupertinoTextField(controller: field, autofocus: true),
        ]),
        actions: [
          CupertinoDialogAction(onPressed: () => Navigator.of(ctx).pop(''), child: Text(tr('取消', 'Cancel'))),
          CupertinoDialogAction(
              isDefaultAction: true, onPressed: () => Navigator.of(ctx).pop(field.text), child: Text(tr('确定', 'OK'))),
        ],
      ),
    );
    return text ?? '';
  } finally {
    field.dispose();
  }
}
