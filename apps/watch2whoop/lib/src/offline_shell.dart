import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

// The kept copy of the Today page (docs/offline-plan.md section 5, docs/ios-native-plan.md section 3.6).
// The app's WKWebView runs no service worker (no App-Bound Domains), so without network the page cannot open at all.
// While the page reports that offline use is on (bridge message `offline:on`, rollout flag offline_mode), the app keeps
// the server's /app/offline-shell.html — the page with its scripts and styles inlined, no user data — in Application
// Support, refreshed once per app run with If-None-Match (304 while it is current). `offline:off` (flag off, logout)
// deletes it. Two uses:
// - offline cold start: a main-frame load error opens the copy as it is (window.W2W_SHELL: it sends nothing);
// - local-first start (#337): while the page reports `local-first:true` (kill switch local_first_start_off is off), a cold
//   start opens the copy right away with its marker renamed to window.W2W_LOCAL: then it is the ordinary page, which draws
//   the last data from IndexedDB first and asks the server for fresh data as usual. Only the HTML round trip is saved.
// Both load it with the page's address as base URL: same origin, so cookies, localStorage and IndexedDB are the page's own.

/// The page's `local-first:true|false` (#337): whether the next cold start opens the kept copy of the page; null = another message.
bool? parseLocalFirst(String message) => switch (message) {
      'local-first:true' => true,
      'local-first:false' => false,
      _ => null,
    };

class OfflineShell {
  OfflineShell({Future<Directory> Function()? dir, http.Client? client})
      : _dir = dir ?? getApplicationSupportDirectory,
        _client = client ?? http.Client();

  final Future<Directory> Function() _dir;
  final http.Client _client;
  String? _savedFor; // the server whose copy was checked during this app run
  bool _changed = false;

  static const _html = 'offline_shell.html';
  static const _origin = 'offline_shell.origin';
  static const _etag = 'offline_shell.etag';
  static const _first = 'offline_shell.first';

  /// The marker web/api.py offline_shell_html puts in the copy, and what the local-first start renames it to.
  static const shellMarker = '<script>window.W2W_SHELL = ';
  static const localMarker = '<script>window.W2W_LOCAL = ';

  /// The copy's address on [entry]'s server (next to the page).
  static Uri shellUri(Uri entry) => entry.removeFragment().resolve('offline-shell.html');

  /// The copy as the local-first page (window.W2W_LOCAL instead of W2W_SHELL), or null when the marker is not there.
  static String? asLocal(String html) => html.contains(shellMarker) ? html.replaceFirst(shellMarker, localMarker) : null;

  /// The page version in the copy (web/api.py WEB_VERSION), for the logs.
  static String? versionOf(String html) => RegExp(r'window\.W2W_(?:SHELL|LOCAL) = "([^"]*)"').firstMatch(html)?.group(1);

  /// True once a newer copy was saved after the one on disk was last opened (load / loadLocal): the next start, or a
  /// reload when the app comes back after a while, shows the newer page.
  bool get changed => _changed;

  Future<File> _file(String name) async => File('${(await _dir()).path}/$name');

  Future<String?> _read(String name) async {
    final f = await _file(name);
    return await f.exists() ? await f.readAsString() : null;
  }

  /// Keeps / refreshes the copy for [entry]'s server, once per app run. False when it could not be fetched.
  Future<bool> save(Uri entry) async {
    if (_savedFor == entry.origin) return true;
    try {
      final same = await _read(_origin) == entry.origin && await (await _file(_html)).exists();
      final etag = same ? await _read(_etag) : null;
      final r = await _client
          .get(shellUri(entry), headers: {if (etag != null && etag.isNotEmpty) 'If-None-Match': etag})
          .timeout(const Duration(seconds: 20));
      if (r.statusCode == 304 && same) {
        _savedFor = entry.origin;
        return true;
      }
      if (r.statusCode != 200 || !r.body.contains('W2W_SHELL')) return false; // an older server without the endpoint
      final old = same ? await _read(_html) : null;
      await (await _file(_html)).writeAsString(r.body, flush: true);
      await (await _file(_origin)).writeAsString(entry.origin, flush: true);
      await (await _file(_etag)).writeAsString(r.headers['etag'] ?? '', flush: true);
      if (old != null && old != r.body) _changed = true;
      _savedFor = entry.origin;
      return true;
    } catch (_) {
      return false;
    }
  }

  /// The kept copy when it belongs to [entry]'s server, else null.
  Future<String?> load(Uri entry) async {
    try {
      if (await _read(_origin) != entry.origin) return null;
      final html = await _read(_html);
      if (html != null) _changed = false;
      return html;
    } catch (_) {
      return null;
    }
  }

  /// The copy for a local-first start: only while the page said the start is on, for [entry]'s server, with the marker
  /// renamed; null = load the page from the server as before.
  Future<String?> loadLocal(Uri entry) async {
    if (!await localFirst()) return null;
    final html = await load(entry);
    return html == null ? null : asLocal(html);
  }

  /// The page's `local-first:true|false` (kill switch local_first_start_off on the server), remembered for the next start.
  Future<void> setLocalFirst(bool on) async {
    try {
      await (await _file(_first)).writeAsString(on ? '1' : '0', flush: true);
    } catch (_) {/* not kept: the next start loads from the server */}
  }

  /// Whether the last page said the local-first start is on; never told (older server, first run) = off.
  Future<bool> localFirst() async {
    try {
      return await _read(_first) == '1';
    } catch (_) {
      return false;
    }
  }

  /// Deletes the copy (offline use turned off, or the user logged out).
  Future<void> forget() async {
    _savedFor = null;
    _changed = false;
    for (final name in [_html, _origin, _etag, _first]) {
      try {
        final f = await _file(name);
        if (await f.exists()) await f.delete();
      } catch (_) {/* nothing kept */}
    }
  }
}
