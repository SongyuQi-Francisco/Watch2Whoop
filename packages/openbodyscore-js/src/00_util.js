/* util: time, dates, time zones and the Python-compatible arithmetic the ports rely on.

Conventions of the whole package (they make the JS give the same numbers as the Python package):
- An instant is an integer number of MICROSECONDS since 1970-01-01T00:00Z (exact up to year 2255 in a double). Python's
  datetime has microsecond resolution and its timedelta arithmetic is exact, so durations computed here are exact too.
  The public API (api.js) also accepts epoch milliseconds (numbers, fractions allowed) and ISO 8601 strings with an offset.
- A local date is a 'YYYY-MM-DD' string; a duration (timedelta) is an integer number of microseconds.
- A time zone is a fixed offset (minutes east of UTC, or '+10:00') or an IANA name (needs Intl, which JavaScriptCore has).
- Floats follow CPython: sum() uses Neumaier compensation (3.12+), statistics.fmean uses math.fsum, statistics.stdev /
  pstdev are exact (rational arithmetic, correctly rounded square root), round() rounds half to even on the exact binary
  value. Only libm functions (exp, log, pow, sin, cos) may differ in the last bit between engines.
*/
OBS.util = (function () {
  "use strict";
  var US_SEC = 1e6, US_MIN = 6e7, US_HOUR = 3.6e9, US_DAY = 8.64e10;

  // ------------------------------------------------------------------ instants
  var ISO_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?(Z|[+-]\d{2}(?::?\d{2})?)$/;

  function parseIso(s) {
    var m = ISO_RE.exec(s);
    if (!m) throw new Error("not an ISO 8601 instant with an offset: " + s);
    var frac = m[7] ? Number((m[7] + "000000").slice(0, 6)) : 0;
    var ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], m[6] ? +m[6] : 0);
    var off = 0;
    if (m[8] !== "Z") {
      var sign = m[8][0] === "-" ? -1 : 1, rest = m[8].slice(1).replace(":", "");
      off = sign * (Number(rest.slice(0, 2)) * 60 + (rest.length > 2 ? Number(rest.slice(2, 4)) : 0));
    }
    return ms * 1000 + frac - off * US_MIN;
  }

  /** Instant -> microseconds: a number is epoch milliseconds (fractions = sub-millisecond), a string is ISO 8601. */
  function toUs(x) {
    if (x === null || x === undefined) return x;
    if (typeof x === "number") return Math.round(x * 1000);
    if (typeof x === "string") return parseIso(x);
    if (x instanceof Date) return x.getTime() * 1000;
    throw new Error("not an instant: " + x);
  }

  // ------------------------------------------------------------------ dates
  function dayNum(d) { return Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10)) / 86400000; }
  function fromDayNum(n) { return new Date(n * 86400000).toISOString().slice(0, 10); }
  function addDays(d, k) { return fromDayNum(dayNum(d) + k); }
  function daysBetween(a, b) { return dayNum(b) - dayNum(a); }   // b - a in days
  function weekday(d) { return (new Date(dayNum(d) * 86400000).getUTCDay() + 6) % 7; }   // Monday = 0, as Python
  function dateRange(a, b) { var out = []; for (var n = dayNum(a), e = dayNum(b); n <= e; n++) out.push(fromDayNum(n)); return out; }

  // ------------------------------------------------------------------ time zones
  var tzCache = new Map();

  function fixedTz(minutes, name) {
    return { name: name || ("fixed" + minutes), fixed: minutes, offsetAt: function () { return minutes; } };
  }

  function ianaTz(name) {
    if (typeof Intl === "undefined" || !Intl.DateTimeFormat) throw new Error("IANA time zone needs Intl: " + name);
    var fmt = new Intl.DateTimeFormat("en-US", { timeZone: name, hourCycle: "h23", year: "numeric", month: "numeric",
      day: "numeric", hour: "numeric", minute: "numeric", second: "numeric" });
    var cache = new Map();
    return {
      name: name,
      offsetAt: function (us) {
        // offsets change only at quarter-hour instants, so one lookup per 15 minutes is exact
        var b = Math.floor(us / 9e8), o = cache.get(b);
        if (o === undefined) {
          var ms = b * 9e5, p = {};
          fmt.formatToParts(new Date(ms)).forEach(function (x) { p[x.type] = x.value; });
          var h = +p.hour === 24 ? 0 : +p.hour;
          o = Math.round((Date.UTC(+p.year, +p.month - 1, +p.day, h, +p.minute, +p.second) - ms) / 60000);
          cache.set(b, o);
        }
        return o;
      }
    };
  }

  /** A time zone object from minutes east of UTC, '+10:00', 'UTC' or an IANA name (cached). */
  function tzOf(spec) {
    if (spec && typeof spec === "object" && typeof spec.offsetAt === "function") return spec;
    var key = (typeof spec) + ":" + spec, tz = tzCache.get(key);
    if (tz) return tz;
    if (typeof spec === "number") tz = fixedTz(spec);
    else if (spec === "UTC" || spec === "Z") tz = fixedTz(0, "UTC");
    else if (/^[+-]\d{2}:?\d{2}$/.test(spec)) {
      var r = spec.replace(":", "");
      tz = fixedTz((spec[0] === "-" ? -1 : 1) * (Number(r.slice(1, 3)) * 60 + Number(r.slice(3, 5))), spec);
    } else tz = ianaTz(spec);
    tzCache.set(key, tz);
    return tz;
  }

  /** Local wall-clock fields of an instant (Python: dt.astimezone(tz)). */
  function local(us, tz) {
    var off = tz.offsetAt(us), w = us + off * US_MIN, day = Math.floor(w / US_DAY), rem = w - day * US_DAY;
    return { date: fromDayNum(day), hour: Math.floor(rem / US_HOUR), minute: Math.floor((rem % US_HOUR) / US_MIN),
      second: Math.floor((rem % US_MIN) / US_SEC), micro: rem % US_SEC, offset: off };
  }
  function localDate(us, tz) { return local(us, tz).date; }

  /** Local wall time (date + microseconds after its midnight) -> instant (Python: datetime.combine(d, t, tz), fold 0). */
  function combine(d, usOfDay, tz) {
    var wall = dayNum(d) * US_DAY + usOfDay;
    if (tz.fixed !== undefined) return wall - tz.fixed * US_MIN;
    var o = tz.offsetAt(wall - tz.offsetAt(wall) * US_MIN), t = wall - o * US_MIN, o2 = tz.offsetAt(t);
    return o2 === o ? t : wall - o2 * US_MIN;
  }

  function pad(n, w) { var s = String(n); while (s.length < w) s = "0" + s; return s; }
  function offsetStr(off) { var a = Math.abs(off); return (off < 0 ? "-" : "+") + pad(Math.floor(a / 60), 2) + ":" + pad(a % 60, 2); }

  /** Python datetime.isoformat() of the local time; timespec "minutes" or "auto" (seconds, plus microseconds when not 0). */
  function isoformat(us, tz, timespec) {
    var p = local(us, tz), s = p.date + "T" + pad(p.hour, 2) + ":" + pad(p.minute, 2);
    if (timespec !== "minutes") s += ":" + pad(p.second, 2) + (p.micro ? "." + pad(p.micro, 6) : "");
    return s + offsetStr(p.offset);
  }

  // ------------------------------------------------------------------ Python-compatible arithmetic
  /** CPython 3.12+ built-in sum() of floats (Neumaier compensated summation). */
  function pySum(xs) {
    var f = 0, c = 0;
    for (var i = 0; i < xs.length; i++) {
      var x = xs[i], t = f + x;
      if (Math.abs(f) >= Math.abs(x)) c += (f - t) + x; else c += (x - t) + f;
      f = t;
    }
    return c && isFinite(c) ? f + c : f;
  }

  /** math.fsum (Shewchuk, as CPython's C implementation, correctly rounded). */
  function fsum(xs) {
    var partials = [];
    for (var k = 0; k < xs.length; k++) {
      var x = xs[k], i = 0;
      for (var j = 0; j < partials.length; j++) {
        var y = partials[j];
        if (Math.abs(x) < Math.abs(y)) { var tmp = x; x = y; y = tmp; }
        var hi = x + y, lo = y - (hi - x);
        if (lo) partials[i++] = lo;
        x = hi;
      }
      partials.length = i;
      partials.push(x);
    }
    var n = partials.length, sum = 0;
    if (n > 0) {
      sum = partials[--n];
      var lo2 = 0;
      while (n > 0) {
        var xx = sum, yy = partials[--n];
        sum = xx + yy;
        var yr = sum - xx;
        lo2 = yy - yr;
        if (lo2 !== 0) break;
      }
      if (n > 0 && ((lo2 < 0 && partials[n - 1] < 0) || (lo2 > 0 && partials[n - 1] > 0))) {
        var y2 = lo2 * 2, x2 = sum + y2, yr2 = x2 - sum;
        if (y2 === yr2) sum = x2;
      }
    }
    return sum;
  }

  function fmean(xs) {
    if (!xs.length) throw new Error("fmean requires at least one data point");
    return fsum(xs) / xs.length;
  }

  /** Python `sum(xs) / len(xs)` (not statistics.mean). */
  function mean(xs) { return pySum(xs) / xs.length; }

  function median(xs) {
    if (!xs.length) throw new Error("no median for empty data");
    var s = xs.slice().sort(function (a, b) { return a - b; }), n = s.length, i = n >> 1;
    return n % 2 ? s[i] : (s[i - 1] + s[i]) / 2;
  }

  // exact binary value of a double: x = m * 2^e (m BigInt)
  var dv = new DataView(new ArrayBuffer(8));
  function exact(x) {
    if (x === 0) return { m: BigInt(0), e: 0 };
    dv.setFloat64(0, x);
    var hi = dv.getUint32(0), lo = dv.getUint32(4), ex = (hi >>> 20) & 0x7ff;
    var m = (BigInt(hi & 0xfffff) << BigInt(32)) | BigInt(lo), e;
    if (ex === 0) e = -1074; else { m = m | (BigInt(1) << BigInt(52)); e = ex - 1075; }
    return { m: hi >>> 31 ? -m : m, e: e };
  }
  function bitLength(b) { return b === BigInt(0) ? 0 : b.toString(2).length; }
  function isqrt(n) {
    if (n < BigInt(2)) return n;
    var x = BigInt(1) << BigInt((bitLength(n) + 1) >> 1);
    for (;;) { var y = (x + n / x) >> BigInt(1); if (y >= x) return x; x = y; }
  }
  function gcd(a, b) { a = a < 0 ? -a : a; while (b) { var t = a % b; a = b; b = t; } return a; }
  // statistics._float_sqrt_of_frac: sqrt(n / m) correctly rounded (round-to-odd at 109 bits, then one rounding)
  function sqrtFrac(n, m) {
    var rto = function (a, b) { var r = isqrt(a / b); return r * r * b !== a ? r | BigInt(1) : r; };
    var q = Math.floor((bitLength(n) - bitLength(m) - 109) / 2), num, den;
    if (q >= 0) { num = rto(n, m << BigInt(2 * q)) << BigInt(q); den = 1; } else { num = rto(n << BigInt(-2 * q), m); den = Math.pow(2, -q); }
    return Number(num) / den;
  }
  // exact sum of squared deviations as a fraction [num, den] (statistics._ss)
  function sumSquares(xs) {
    var rs = xs.map(exact), emin = 0, i;
    for (i = 0; i < rs.length; i++) if (rs[i].m && rs[i].e < emin) emin = rs[i].e;
    var sx = BigInt(0), sxx = BigInt(0);
    for (i = 0; i < rs.length; i++) { var X = rs[i].m << BigInt(rs[i].e - emin); sx += X; sxx += X * X; }
    var n = BigInt(xs.length), num = n * sxx - sx * sx, den = n;
    if (emin < 0) den = den << BigInt(-2 * emin);
    return [num, den];
  }
  function variance(xs, ddof) {
    var f = sumSquares(xs), num = f[0], den = f[1] * BigInt(xs.length - ddof), g = gcd(num, den);
    if (g > BigInt(1)) { num /= g; den /= g; }
    return sqrtFrac(num, den);
  }
  /** statistics.stdev (sample, n - 1), exact like CPython. */
  function stdev(xs) { if (xs.length < 2) throw new Error("stdev requires at least two data points"); return variance(xs, 1); }
  /** statistics.pstdev (population). */
  function pstdev(xs) { if (xs.length < 1) throw new Error("pstdev requires at least one data point"); return variance(xs, 0); }

  /** Python round(x, nd): half to even on the exact binary value; round(x) (nd undefined) gives an integer. */
  function round(x, nd) {
    if (x === null || x === undefined || !isFinite(x)) return x;
    var toInt = nd === undefined;   // round(x) is an int in Python: never -0
    nd = nd || 0;
    var r = exact(x), num = r.m, den = BigInt(1);
    if (r.e >= 0) num = num << BigInt(r.e); else den = den << BigInt(-r.e);
    if (nd >= 0) num = num * BigInt(10) ** BigInt(nd); else den = den * BigInt(10) ** BigInt(-nd);
    var neg = num < 0, a = neg ? -num : num, q = a / den, rem = a % den, twice = rem * BigInt(2);
    if (twice > den || (twice === den && (q & BigInt(1)) === BigInt(1))) q += BigInt(1);
    var v = nd >= 0 ? Number(q.toString() + "e-" + nd) : Number(q * BigInt(10) ** BigInt(-nd));
    return neg && !(toInt && v === 0) ? -v : v;
  }
  function r(x, nd) { return x === null || x === undefined ? null : round(x, nd === undefined ? 3 : nd); }

  /** Python max(xs, key=f): the first item with the largest key. */
  function maxBy(xs, f) { var best = null, bk; for (var i = 0; i < xs.length; i++) { var k = f(xs[i]); if (best === null || k > bk) { best = xs[i]; bk = k; } } return best; }
  function minBy(xs, f) { var best = null, bk; for (var i = 0; i < xs.length; i++) { var k = f(xs[i]); if (best === null || k < bk) { best = xs[i]; bk = k; } } return best; }

  /** Compare tuples the way Python does (numbers and strings, element by element). */
  function cmp(a, b) {
    if (Array.isArray(a)) {
      for (var i = 0; i < a.length && i < b.length; i++) { var c = cmp(a[i], b[i]); if (c) return c; }
      return a.length - b.length;
    }
    return a < b ? -1 : a > b ? 1 : 0;
  }

  // bisect over a sorted array of numbers
  function bisectLeft(a, x) { var lo = 0, hi = a.length; while (lo < hi) { var m = (lo + hi) >> 1; if (a[m] < x) lo = m + 1; else hi = m; } return lo; }
  function bisectRight(a, x) { var lo = 0, hi = a.length; while (lo < hi) { var m = (lo + hi) >> 1; if (x < a[m]) hi = m; else lo = m + 1; } return lo; }

  /** A copy of `defaults` with `changes` applied; unknown names throw (Python: <module>.override(**changes)). */
  function override(defaults, changes, name) {
    var out = {}, k;
    for (k in defaults) out[k] = defaults[k];
    var unknown = Object.keys(changes || {}).filter(function (x) { return !(x in defaults); }).sort();
    if (unknown.length) throw new Error("unknown parameter(s) for " + name + ": " + unknown.join(", "));
    for (k in changes) out[k] = changes[k];
    return out;
  }

  return { US_SEC: US_SEC, US_MIN: US_MIN, US_HOUR: US_HOUR, US_DAY: US_DAY, parseIso: parseIso, toUs: toUs,
    dayNum: dayNum, fromDayNum: fromDayNum, addDays: addDays, daysBetween: daysBetween, weekday: weekday, dateRange: dateRange,
    tzOf: tzOf, local: local, localDate: localDate, combine: combine, isoformat: isoformat, offsetStr: offsetStr, pad: pad,
    pySum: pySum, fsum: fsum, fmean: fmean, mean: mean, median: median, stdev: stdev, pstdev: pstdev, round: round, r: r,
    exact: exact, maxBy: maxBy, minBy: minBy, cmp: cmp, bisectLeft: bisectLeft, bisectRight: bisectRight, override: override };
})();
