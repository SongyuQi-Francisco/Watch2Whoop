/* Offline use of the Today page (rollout flag offline_mode, issue #77, docs/offline-plan.md).
   The Watch2Whoop app's WKWebView has no service worker (the app does not use App-Bound Domains), so the page keeps its
   own copies in IndexedDB (database "be-offline"):
   - read cache: the signed-in user's own GET responses (/v1/web/me, /v1/users/me/…; never other people's or AI data),
     newest 300 / ~4 MB, at most 14 days old (offline/params.py). app.js shows them when the network fails, with the
     "offline · updated hh:mm" banner;
   - write queue: water, weight and training sets made while offline wait here and are sent in order once the network is
     back. Each item carries its client_id and the server writes are idempotent, so a resend never doubles anything.
     Items of one group (a training day, a weight day) stop at the first rejected one; a rejected item stays in the list
     with its reason until the user retries, overrides (conflict) or drops it;
   - controls that need the server (Jarvis, Friends, [data-online]) are greyed out and say "needs a connection".
   Everything is wiped on logout and when another account signs in on this device; the read cache also on a 401.
   app.js calls nothing here while the flag is off, except clear() on logout (deleting a database that is not there).
   The core (createOffline) takes its storage as a parameter, so tests/test_offline.py runs it in node with memStore. */
(() => {
  "use strict";
  const root = typeof window !== "undefined" ? window : globalThis;
  const DB = "be-offline";
  const LIMITS = { entries: 300, bytes: 4000000, ageDays: 14, queue: 200 };   // = offline/params.py
  // Own data only: the page always asks for its own with "me"; a family member's (/v1/users/7/…), friends, AI chat,
  // ECG waveforms and feedback are never kept.
  const CACHEABLE = /^\/v1\/(web\/me$|consent$|explain\/glossary|users\/me\/(?!ai-|assistant|ecg|feedback))/;
  // Statuses worth trying again later without asking: no network, signed out, rate limit, server trouble
  const LATER = (s) => !s || s === 401 || s === 408 || s === 425 || s === 429 || s >= 500;
  // A reply that needs the user's decision: the patch that resends it anyway
  const CONFLICT = {
    "training-submit": (r) => (r.ok && r.data?.duplicate ? { patch: { allow_duplicate: true } } : null),
    weight: (r) => (r.status === 409 && r.data?.detail?.conflict === "weight" ? { patch: { overwrite: true } } : null),
  };
  const uuid = () => (root.crypto?.randomUUID ? root.crypto.randomUUID() : `o${Date.now()}${Math.random().toString(36).slice(2, 10)}`);

  function memStore() {
    let m = new Map();
    const copy = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
    return {
      get: async (k) => copy(m.get(k)),
      set: async (k, v) => { m.set(k, copy(v)); },
      del: async (k) => { m.delete(k); },
      clear: async () => { m = new Map(); },
    };
  }

  function idbStore() {
    let dbp = null;
    const open = () => (dbp ??= new Promise((res, rej) => {
      const r = root.indexedDB.open(DB, 1);
      r.onupgradeneeded = () => r.result.createObjectStore("kv");
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    }));
    const tx = async (mode, fn) => {
      const db = await open();
      return new Promise((res, rej) => {
        const t = db.transaction("kv", mode), req = fn(t.objectStore("kv"));
        t.oncomplete = () => res(req?.result);
        t.onerror = t.onabort = () => rej(t.error);
      });
    };
    return {
      get: (k) => tx("readonly", (s) => s.get(k)),
      set: (k, v) => tx("readwrite", (s) => s.put(v, k)).then(() => undefined),
      del: (k) => tx("readwrite", (s) => s.delete(k)).then(() => undefined),
      clear: async () => {   // drop the whole database (also when it was never opened on this load)
        if (dbp) { try { (await dbp).close(); } catch (e) { /* not open */ } dbp = null; }
        await new Promise((res) => { const r = root.indexedDB.deleteDatabase(DB); r.onsuccess = r.onerror = r.onblocked = () => res(); });
      },
    };
  }

  // Falls back to memory when IndexedDB is missing or refuses (private mode): offline use then lasts until the page closes.
  function safeStore(primary) {
    const mem = memStore();
    let use = primary;
    const call = (name) => async (...a) => {
      if (use !== mem) { try { return await use[name](...a); } catch (e) { use = mem; } }
      return mem[name](...a);
    };
    return { get: call("get"), set: call("set"), del: call("del"), clear: async () => { await mem.clear(); try { await primary.clear(); } catch (e) { /* none */ } } };
  }

  function createOffline({ store = memStore(), now = () => Date.now() } = {}) {
    let user = null, queue = [], idx = {}, flushing = null;
    const listeners = new Set();
    const changed = () => { for (const fn of listeners) { try { fn(); } catch (e) { /* a listener never breaks the queue */ } } };
    const save = () => store.set("queue", queue);
    const ckey = (path, lang) => `c|${user}|${lang || "zh"}|${path}`;
    const maxAge = LIMITS.ageDays * 86400000;

    async function prune() {
      const keys = Object.keys(idx).sort((a, b) => idx[a][0] - idx[b][0]);   // oldest first
      let bytes = keys.reduce((n, k) => n + idx[k][1], 0), count = keys.length;
      for (const k of keys) {
        if (now() - idx[k][0] <= maxAge && count <= LIMITS.entries && bytes <= LIMITS.bytes) break;
        bytes -= idx[k][1]; count -= 1;
        delete idx[k];
        await store.del(k);
      }
    }

    const api = {
      LIMITS,
      cacheable: (path) => CACHEABLE.test(String(path || "")),
      // The account that last used offline mode on this device (null = none): a cold start offline uses its copies.
      async restore() {
        const meta = await store.get("meta");
        user = meta?.user ?? null;
        queue = (user != null && (await store.get("queue"))) || [];
        idx = (user != null && (await store.get("cidx"))) || {};
        changed();
        return user;
      },
      // Signed in as `id` with the flag on: another account's copies and queue are wiped first.
      async setUser(id) {
        const meta = await store.get("meta");
        if (meta && meta.user !== id) { await store.clear(); queue = []; idx = {}; }
        user = id;
        await store.set("meta", { user: id });
        changed();
      },
      user: () => user,

      async put(path, lang, data) {
        if (user == null || !api.cacheable(path)) return;
        const text = JSON.stringify(data ?? null);
        if (text.length > LIMITS.bytes / 4) return;   // one huge answer would push out everything else
        const k = ckey(path, lang);
        await store.set(k, { at: now(), data: data ?? null });
        idx[k] = [now(), text.length];
        await prune();
        await store.set("cidx", idx);
      },
      async get(path, lang) {
        if (user == null || !api.cacheable(path)) return null;
        const hit = await store.get(ckey(path, lang));
        if (!hit || now() - hit.at > maxAge) return null;
        return { data: hit.data, at: new Date(hit.at).toISOString() };
      },
      async clearCache() {
        for (const k of Object.keys(idx)) await store.del(k);
        idx = {};
        await store.set("cidx", idx);
      },

      // ---- write queue
      items: () => queue.slice(),
      pending: (kind) => queue.filter((it) => it.status === "pending" && (!kind || it.kind === kind)),
      failed: () => queue.filter((it) => it.status === "failed"),
      // New writes join the queue while anything is still waiting, so they never overtake an older one
      mustQueue: () => queue.some((it) => it.status === "pending"),
      // {kind, method, path, body, label, group?, coalesce?} -> the stored item, or null when the queue is full.
      // coalesce: a later write of the same key replaces the waiting one (a training day's draft, a day's weight) as long as
      // nothing else of its group came after it.
      async enqueue(w) {
        const last = w.group ? [...queue].reverse().find((it) => it.group === w.group) : null;
        if (w.coalesce && last && last.coalesce === w.coalesce && last.status === "pending") {
          last.body = w.body; last.label = w.label; last.at = now();
          await save(); changed();
          return last;
        }
        if (queue.length >= LIMITS.queue) return null;
        const it = { id: uuid(), user, kind: w.kind, method: w.method, path: w.path, body: w.body, label: w.label || "",
          group: w.group || null, coalesce: w.coalesce || null, at: now(), status: "pending", error: null, conflict: null };
        queue.push(it);
        await save(); changed();
        return it;
      },
      async drop(id) { queue = queue.filter((it) => it.id !== id); await save(); changed(); },
      // Send it again; force = apply the conflict's patch (overwrite the day's weight, submit the duplicate workout)
      async retry(id, force) {
        const it = queue.find((x) => x.id === id);
        if (!it) return;
        if (force && it.conflict?.patch) it.body = { ...(it.body || {}), ...it.conflict.patch };
        it.status = "pending"; it.error = null; it.conflict = null;
        await save(); changed();
      },
      // Send the waiting items in order with send(item) -> {ok, status, data, message} (status 0 = no network).
      // Stops at the first one that should wait (no network, signed out, server trouble). Resolves {sent, failed, left}.
      flush(send) {
        if (flushing) return flushing;
        flushing = (async () => {
          let sent = 0, failed = 0;
          const blocked = new Set();
          for (const it of queue.slice()) {
            if (it.status === "failed") { if (it.group) blocked.add(it.group); continue; }
            if (it.group && blocked.has(it.group)) continue;
            let r;
            try { r = await send(it); } catch (e) { r = { ok: false, status: 0, message: String(e?.message || e) }; }
            const conflict = CONFLICT[it.kind]?.(r) || null;
            if (r.ok && !conflict) {
              queue = queue.filter((x) => x !== it);
              sent += 1;
            } else if (!conflict && LATER(r.status)) {
              break;
            } else {
              it.status = "failed"; it.conflict = conflict;
              it.error = r.data?.detail?.message || (typeof r.data?.detail === "string" ? r.data.detail : null) || r.message || String(r.status);
              if (it.group) blocked.add(it.group);
              failed += 1;
            }
            await save(); changed();
          }
          return { sent, failed, left: queue.filter((x) => x.status === "pending").length };
        })().finally(() => { flushing = null; });
        return flushing;
      },

      async clear() { user = null; queue = []; idx = {}; await store.clear(); changed(); },
      onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    };
    return api;
  }

  // The banner's pieces: 「离线 · 数据更新于 hh:mm」, how many writes wait / failed. at = the oldest copy shown (ISO, "" = none).
  // tr is I18N.t, when(date) formats the copy's time. [] = no banner.
  function bannerParts({ offline, at, waiting, failed }, tr, when) {
    const t = at ? new Date(at) : null;
    const parts = offline ? [t && !isNaN(t) ? tr("离线 · 数据更新于 {when}", { when: when(t) }) : tr("离线")] : [];
    if (waiting) parts.push(offline ? tr("{n} 条待同步", { n: waiting }) : tr("正在同步 {n} 条", { n: waiting }));
    if (failed) parts.push(tr("{n} 条没提交", { n: failed }));
    return parts;
  }

  root.BodyOfflineLib = { createOffline, memStore, LIMITS, CACHEABLE, bannerParts };
  if (root.indexedDB || root.document) root.BodyOffline = createOffline({ store: safeStore(root.indexedDB ? idbStore() : memStore()) });
})();
