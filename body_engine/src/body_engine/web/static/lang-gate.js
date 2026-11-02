/* Loads i18n-en.js (89 KB: the English texts) only when the page is going to be English, so a Chinese user never downloads it.
   A plain (parser-blocking) script placed right after i18n.js and the lazy declaration in index.html:
     <script type="w2w/lazy" data-lang="en" src="i18n-en.js"></script>   <- the browser neither fetches nor runs this one
     <script data-w2w="gate" src="lang-gate.js"></script>                 <- this file
   If the language is English it writes `<script src="i18n-en.js?v=…" defer>` here, so the English table runs in document order
   (after i18n.js, before every other script) exactly as when the tag was there all the time. The src carries the content
   fingerprint the server added to the declaration. The language is decided like i18n.js does (tests/test_i18n.py runs both on
   the same cases): #lang=zh|en in the address, else the saved choice (localStorage "be.lang"), else the device language.
   The offline copy of the page (web/api.py offline_shell_html) has no gate: it inlines the English table. A page that switches
   language without a reload (the login card) asks for the table with I18N.ensureEn() (i18n.js). */
(function () {
  "use strict";
  var lang = "";
  var fromAddress = /(?:^#|&)lang=(zh|en)(?=&|$)/.exec(location.hash);
  if (fromAddress) lang = fromAddress[1];
  else { try { lang = localStorage.getItem("be.lang") || ""; } catch (e) { lang = ""; } }
  if (lang !== "zh" && lang !== "en") {
    var device = String((navigator.languages && navigator.languages[0]) || navigator.language || "").toLowerCase();
    lang = device.indexOf("zh") === 0 ? "zh" : "en";
  }
  if (lang !== "en") return;
  var decl = document.querySelector('script[type="w2w/lazy"][data-lang="en"]');
  var src = decl && decl.getAttribute("src");
  if (src) document.write('<script src="' + encodeURI(src) + '" defer><\/script>');
})();
