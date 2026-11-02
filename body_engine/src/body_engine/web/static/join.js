/* Circle invite landing page (/app/join.html#<CODE>): what a shared invite link opens.
   - The code is in the URL fragment, which browsers never send to the server (as activate.js): it stays out of URLs and access logs.
   - Static: no API call and none of the Today page's scripts, so the forced upgrade (client/policy.py) never stands in front of it.
   - iPhone / iPad: opens watch2whoop://circle?code=<CODE> (AppModel.handle(url:) → the join sheet). If the page is still in front
     FALLBACK_MS later the app is not installed: the install guide, and 「在 App 里打开」 stays for another try. Inside WeChat's
     browser custom schemes do not open at all, so the page does not try by itself and says how to get to Safari.
   - Computers: the Today page's own join sheet (/app/?circle=<CODE>).
   - The code can always be copied and typed in the app. Universal Links would skip this page; they need the Apple team's setup. */
(function () {
  "use strict";
  const FALLBACK_MS = 1800;
  /** Upper case, letters, digits and dashes only, at most 60 (InviteCode.clean in the app; an activation code may have another shape). */
  const clean = (s) => String(s || "").toUpperCase().replace(/[^A-Z0-9-]/g, "").slice(0, 60);
  /** The code from the fragment, else from ?circle= (the older link form). */
  function codeFrom(hash, search) {
    const dec = (s) => { try { return decodeURIComponent(s); } catch (_) { return s; } };
    const q = /[?&]circle=([^&#]*)/.exec(search || "");
    return clean(dec(String(hash || "").replace(/^#/, ""))) || clean(q ? dec(q[1]) : "");
  }
  const appLink = (code) => "watch2whoop://circle?code=" + encodeURIComponent(code);
  const webLink = (code) => "./?circle=" + encodeURIComponent(code);
  const isIOS = (ua, platform, touch) => /iPhone|iPad|iPod/.test(ua || "") || (platform === "MacIntel" && touch > 1);
  const isWeChat = (ua) => /MicroMessenger/i.test(ua || "");

  if (typeof module !== "undefined") module.exports = { clean, codeFrom, appLink, webLink, isIOS, isWeChat };
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const tr = window.I18N ? window.I18N.t : (k) => k;
  const code = codeFrom(location.hash, location.search);
  const IOS = isIOS(navigator.userAgent, navigator.platform, navigator.maxTouchPoints || 0);
  const WECHAT = isWeChat(navigator.userAgent);
  let timer = 0;
  let left = false;   // the page was hidden since this attempt started (the app came up)
  const cancel = () => { clearTimeout(timer); timer = 0; };
  document.addEventListener("visibilitychange", () => { if (document.hidden) { left = true; cancel(); } });
  window.addEventListener("pagehide", () => { left = true; cancel(); });
  window.addEventListener("hashchange", () => location.reload());   // another invite link opened in this tab: no new page load

  function openTheApp() {
    cancel();
    left = false;
    $("status").textContent = tr("activate.opening");
    location.href = appLink(code);   // the app takes over; if it is not installed the page simply stays
    timer = setTimeout(() => {
      timer = 0;
      if (left || document.hidden) return;
      $("status").textContent = "";
      $("install").hidden = false;
    }, FALLBACK_MS);
  }

  async function copy() {
    try { await navigator.clipboard.writeText(code); } catch (_) { return; }   // not allowed here: the code stays selectable
    $("copy").textContent = tr("activate.copied");
    setTimeout(() => { $("copy").textContent = tr("activate.copyCode"); }, 1600);
  }

  if (!code) {
    $("lead").hidden = true;
    $("nocode").hidden = false;
    $("manual").hidden = false;
    return;
  }
  $("code").textContent = code;
  $("code").hidden = false;
  $("copy").hidden = false;
  $("copy").addEventListener("click", copy);
  $("manual").hidden = false;
  if (!IOS) {
    $("open-web").href = webLink(code);
    $("open-web").hidden = false;
    return;
  }
  $("open-app").hidden = false;
  $("open-app").addEventListener("click", openTheApp);
  if (WECHAT) $("wechat").hidden = false;
  else openTheApp();
})();
