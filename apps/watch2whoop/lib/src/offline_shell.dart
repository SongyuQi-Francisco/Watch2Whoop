import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

// Offline cold start of the Today page (docs/offline-plan.md, section 5).
// The app's WKWebView runs no service worker (no App-Bound Domains), so without network the page cannot open at all.
// While the page reports that offline use is on (bridge message `offline:on`, rollout flag offline_mode), the app keeps
// the server's /app/offline-shell.html — the page with its scripts and styles inlined, no user data — in Application
// Support. A main-frame load error then opens that copy with the page's address as base URL: same origin, so the page
// reads the data offline.js kept in IndexedDB and queues new records. `offline:off` (flag off, logout) deletes it.

class OfflineShell {
  OfflineShell({Future<Directory> Function()? dir, http.Client? client})
      : _dir = dir ?? getApplicationSupportDirectory,
        _client = client ?? http.Client();

  final Future<Directory> Function() _dir;
  final http.Client _client;
  String? _savedFor; // the server whose copy was refreshed during this app run

  static const _html = 'offline_shell.html';
  static const _origin = 'offline_shell.origin';

  /// The copy's address on [entry]'s server (next to the page).
  static Uri shellUri(Uri entry) => entry.removeFragment().resolve('offline-shell.html');

  Future<File> _file(String name) async => File('${(await _dir()).path}/$name');

  /// Keeps / refreshes the copy for [entry]'s server, once per app run. False when it could not be fetched.
  Future<bool> save(Uri entry) async {
    if (_savedFor == entry.origin) return true;
    try {
      final r = await _client.get(shellUri(entry)).timeout(const Duration(seconds: 20));
      if (r.statusCode != 200 || !r.body.contains('W2W_SHELL')) return false; // an older server without the endpoint
      await (await _file(_html)).writeAsString(r.body, flush: true);
      await (await _file(_origin)).writeAsString(entry.origin, flush: true);
      _savedFor = entry.origin;
      return true;
    } catch (_) {
      return false;
    }
  }

  /// The kept copy when it belongs to [entry]'s server, else null.
  Future<String?> load(Uri entry) async {
    try {
      final o = await _file(_origin);
      if (!await o.exists() || await o.readAsString() != entry.origin) return null;
      return await (await _file(_html)).readAsString();
    } catch (_) {
      return null;
    }
  }

  /// Deletes the copy (offline use turned off, or the user logged out).
  Future<void> forget() async {
    _savedFor = null;
    for (final name in [_html, _origin]) {
      try {
        final f = await _file(name);
        if (await f.exists()) await f.delete();
      } catch (_) {/* nothing kept */}
    }
  }
}
