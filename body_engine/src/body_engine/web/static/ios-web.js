/* The Today page in iOS Safari outside the Watch2Whoop app (BODY_IOS_WEB; GET /v1/auth/methods → ios_web).
   On iPhone / iPad the app is the way in; the web version (incl. the home-screen web app) is being retired.
   - warn (default): a banner at the top 「iPhone 请改用 Watch2Whoop App，网页版即将停用」 linking to install.html.
   - off: the page goes to install.html instead of the login screen or the content (signed-in sessions too).
   Never inside the app's WebView (window.Watch2Whoop) and never on a computer. Loaded before account.js / app.js. */
(function () {
  "use strict";
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent || "") || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (!ios || window.Watch2Whoop) return;
  const tr = window.I18N ? window.I18N.t : (k) => k;
  const root = document.documentElement;
  root.style.visibility = "hidden";   // no flash of the login screen before the mode is known
  const show = () => { root.style.visibility = ""; };
  fetch("/v1/auth/methods", { credentials: "same-origin", cache: "no-store" })
    .then((r) => (r.ok ? r.json() : {})).catch(() => ({}))
    .then((m) => {
      if (m.ios_web === "off") { location.replace("install.html"); return; }
      const bar = document.createElement("div");
      bar.className = "ios-banner";
      bar.setAttribute("role", "status");
      bar.innerHTML = "<span></span> <a href=\"install.html\"></a>";
      bar.querySelector("span").textContent = tr("activate.iosBanner");
      bar.querySelector("a").textContent = tr("activate.iosBannerLink");
      document.body.prepend(bar);
      show();
    });
  setTimeout(show, 4000);   // never leave the page blank if the request hangs
})();
