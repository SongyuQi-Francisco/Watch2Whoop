// Events the app sends to the Today page in the WebView.
//
// Back in the foreground (#114): WKWebView does not reliably fire `visibilitychange` / `pageshow` when the whole app
// returns from the background, so the app tells the page itself. The page (web/static/ai.js) then picks up an in-page
// chat answer whose connection iOS dropped while the app was suspended.

/// The DOM event the page listens for; keep it in sync with `watch2whoop:foreground` in body_engine/.../web/static/ai.js.
const kForegroundEvent = 'watch2whoop:foreground';

/// JavaScript that dispatches [kForegroundEvent] on `window`; harmless on a page that does not listen for it.
const kForegroundJs = "window.dispatchEvent(new Event('$kForegroundEvent'));";
