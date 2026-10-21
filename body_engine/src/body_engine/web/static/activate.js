/* Activation page (/app/activate.html#<code>): exchanges a reusable activation code for a fresh watch2whoop://setup link
   (POST /v1/web/activate, web/activation.py) and opens it so the app sets itself up.
   The code is read from the URL fragment, which browsers never send to the server, so it stays out of URLs and access logs.
   Without a fragment the person types the code. Each tap mints new one-time codes, so tapping again is always safe. */
(function () {
  "use strict";
  const form = document.getElementById("form");
  const input = document.getElementById("code");
  const btn = document.getElementById("go");
  const err = document.getElementById("err");
  const tr = window.I18N ? window.I18N.t : (k) => k;

  const clean = (s) => (s || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  let code = clean(location.hash.replace(/^#/, ""));
  if (!code) input.hidden = false;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!input.hidden) code = clean(input.value);
    err.textContent = "";
    if (!code) {
      err.textContent = tr("activate.enterCode");
      input.focus();
      return;
    }
    btn.disabled = true;
    try {
      const r = await fetch("/v1/web/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
        credentials: "omit",
        cache: "no-store",
      });
      if (r.ok) {
        const data = await r.json();
        if (data && typeof data.setup_link === "string" && data.setup_link.startsWith("watch2whoop://")) {
          location.href = data.setup_link;
          return;
        }
        err.textContent = tr("activate.invalid");
      } else if (r.status === 429) {
        err.textContent = tr("activate.tooMany");
      } else {
        err.textContent = tr("activate.invalid");
        input.hidden = false;
        if (!input.value) input.value = code;
      }
    } catch (_) {
      err.textContent = tr("activate.network");
    } finally {
      setTimeout(() => { btn.disabled = false; }, 1500);
    }
  });
})();
