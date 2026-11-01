// Test harness (Node only; the bundle itself never sees any of this): loads dist/openbodyscore.js the way JavaScriptCore's
// evaluateScript does, gets the shared inputs from cases.py, and compares results with the Python golden files.
"use strict";
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const REPO = path.resolve(ROOT, "..", "..");

/** A bare context with only the language built-ins (no require / process / console / DOM), like JavaScriptCore. */
function loadBundle() {
  const ctx = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(ROOT, "dist", "openbodyscore.js"), "utf8"), ctx, { filename: "openbodyscore.js" });
  return ctx.OpenBodyScore;
}

let cached = null;
/** The inputs of every case (cases.py --dump; Python 3.11+ standard library only, `PYTHON` overrides the interpreter). */
function cases() {
  if (cached) return cached;
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "obs-cases-")), "cases.json");
  execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "cases.py"), "--dump", out], { stdio: "pipe" });
  cached = JSON.parse(fs.readFileSync(out, "utf8"));
  return cached;
}

function golden(rel) { return JSON.parse(fs.readFileSync(path.join(REPO, rel), "utf8")); }

// Cross-language float tolerance. The Python goldens compare Python against itself at 1e-12 (only libm's last bit differs
// between macOS and Linux). JavaScript adds two more sources of last-bit differences: Math.exp / log / pow / sin in
// JavaScriptCore / V8 are not the C library's (none of them is correctly rounded, they differ by an ulp here and there), and a
// few sums run in a different order. Those differences grow through the logistic, exp / log interpolation and
// least-squares chains to ~1e-13 relative at most (when this was written 21 of the compared floats differed at all, the
// largest by 5e-14 relative; the test prints the current figures); 1e-9 leaves a wide margin and is still six orders of magnitude below
// anything the product shows (scores are integers, values are rounded to 1–3 decimals before they are stored or shown).
// Everything that is not a float — keys, null, integers produced by int() / round(), booleans, strings — must be equal.
const FLOAT_REL_TOL = 1e-9;
const FLOAT_ABS_TOL = 1e-9;

/** The largest relative float difference seen so far (reported by the test, for the tolerance note). */
const worst = { rel: 0, path: "", count: 0 };

/** null when got matches want, else the path of the first difference (golden_cases.mismatch with the tolerance above). */
function mismatch(got, want, p) {
  p = p || "";
  if (typeof want === "number" && typeof got === "number") {
    if (got === want) return null;
    const rel = Math.abs(got - want) / Math.max(Math.abs(got), Math.abs(want));
    if (rel > worst.rel) { worst.rel = rel; worst.path = p; }
    worst.count += 1;
    const ok = Math.abs(got - want) <= Math.max(FLOAT_REL_TOL * Math.max(Math.abs(got), Math.abs(want)), FLOAT_ABS_TOL);
    return ok ? null : `${p}: ${got} != ${want}`;
  }
  if (Array.isArray(want) && Array.isArray(got)) {
    if (got.length !== want.length) return `${p}: length ${got.length} != ${want.length}`;
    for (let i = 0; i < want.length; i++) { const m = mismatch(got[i], want[i], `${p}[${i}]`); if (m) return m; }
    return null;
  }
  if (want && got && typeof want === "object" && typeof got === "object" && !Array.isArray(want) && !Array.isArray(got)) {
    const a = Object.keys(got).sort(), b = Object.keys(want).sort();
    const diff = a.filter((k) => !b.includes(k)).concat(b.filter((k) => !a.includes(k)));
    if (diff.length) return `${p}: keys ${JSON.stringify(diff)} differ`;
    for (const k of b) { const m = mismatch(got[k], want[k], `${p}/${k}`); if (m) return m; }
    return null;
  }
  return got === want ? null : `${p}: ${JSON.stringify(got)} != ${JSON.stringify(want)}`;
}

/** Results from the vm context -> plain JSON (Maps, typed arrays and the context's own prototypes normalised). */
function plain(x) { return JSON.parse(JSON.stringify(x)); }

module.exports = { worst, loadBundle, cases, golden, mismatch, plain, FLOAT_REL_TOL, FLOAT_ABS_TOL, REPO, ROOT };
