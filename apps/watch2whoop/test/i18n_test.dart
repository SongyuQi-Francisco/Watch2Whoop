// i18n coverage (issue #13): every Chinese UI string in lib/ goes through tr('中文', 'English') (or a ('中文', 'English')
// pair in i18n.dart), so an English device never sees Chinese. Comments are ignored.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

final _cjk = RegExp(r'[一-鿿]');
// Single-quoted Dart string literals (with escapes); good enough for this code base, which only uses single quotes for UI text.
final _literal = RegExp(r"'(?:[^'\\\n]|\\.)*'");

String _stripComments(String src) => src.split('\n').map((l) {
      final i = l.indexOf('//');
      // keep '//' inside string literals such as URLs: only cut when it is not inside quotes
      if (i < 0) return l;
      final before = l.substring(0, i);
      return "'".allMatches(before).length.isEven ? before : l;
    }).join('\n');

void main() {
  test('every Chinese string literal is the zh half of a tr() call or of a (zh, en) pair', () {
    final problems = <String>[];
    final files = Directory('lib').listSync(recursive: true).whereType<File>().where((f) => f.path.endsWith('.dart'));
    for (final f in files) {
      final src = _stripComments(f.readAsStringSync());
      for (final m in _literal.allMatches(src)) {
        final lit = m.group(0)!;
        if (!_cjk.hasMatch(lit)) continue;
        final before = src.substring(0, m.start).trimRight();
        final ok = before.endsWith('tr(') || (f.path.endsWith('i18n.dart') && before.endsWith('('));
        if (!ok) {
          final line = '\n'.allMatches(src.substring(0, m.start)).length + 1;
          problems.add('${f.path}:$line $lit');
        }
      }
    }
    expect(problems, isEmpty, reason: 'Chinese text outside tr(): \n${problems.join('\n')}');
  });
}
