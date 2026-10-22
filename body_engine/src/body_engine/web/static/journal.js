/* Journal on the Today page and Trends (rollout flag records_v2): the 记录 → 日记 segment is gone, its parts live here.
   - Today: a 日记 card for the day on screen (the date bar already switches days) that opens the day's editor sheet:
     tick / untick behaviours, enter numbers (water, red meat g ...), supplement check-ins (the 补剂 category), log a body event.
   - Trends: the 行为影响 card (journal behaviour -> next-day recovery, from GET /journal/analysis).
   Pure helpers (valuesOf, writeRequest, applyWrite, loggedItems, groupItems, behaviorModel) are run in node by tests/test_records_v2.py.
   ctx (from app.js): { api, load(key, path), invalidate() (drops cached analysis / frequent list, redraws the page), ownerPath(), isSelf(), esc, sheetAttr(fn), openSheet(title, html, after),
     closeSheet, setBack(fn|null), setCache(key, value), icon (ICON), toast(msg, actionLabel?, fn?), shortDay(day), categories, openEvent(), openAssistant(e),
     xpLabel(key, label), signed(v, digits), fmtNum(v, d) } */
(function (root) {
  "use strict";
  const tr = (k, p) => root.I18N.t(k, p);

  const itemLabel = (it) => it.label || it.label_zh;   // label = the name in the page language (default items), else the stored one
  // Short names on tags: no parenthesised explanation, no leading "吃了" ("ate")
  const shortLabel = (it) => itemLabel(it).replace(/（[^）]*）/g, "").replace(/\([^)]*\)/g, "").replace(/^吃了/, "").split(" / ")[0].trim() || itemLabel(it);   // i18n-ok: journal item labels are data

  // entries ([{date, key, value}]) -> {key: value} for one day
  function valuesOf(entries, day) {
    const out = {};
    for (const e of entries || []) if (e.date === day) out[e.key] = e.value;
    return out;
  }

  // The request that stores (value) or clears (null) one item of one day; always the signed-in user's own journal
  function writeRequest(day, key, value) {
    const path = `/v1/users/me/journal/${day}/${encodeURIComponent(key)}`;
    return value == null ? { method: "DELETE", path } : { method: "PUT", path, body: { value } };
  }

  // entries after writing (value) / clearing (null) one item of one day (a new list; the argument is not changed)
  function applyWrite(entries, day, key, value) {
    const list = (entries || []).filter((e) => !(e.date === day && e.key === key));
    if (value != null) list.push({ date: day, key, value });
    return list;
  }

  // Items with something recorded that day, in item order: ticked behaviours (1) and numbers that have a value.
  // A behaviour set to "no" (0) is a record too, but not something to list on the card.
  function loggedItems(items, values) {
    return (items || []).filter((it) => (it.kind === "bool" ? values[it.key] === 1 : values[it.key] != null));
  }

  function groupItems(items) {
    const groups = {};
    for (const it of items || []) (groups[it.category || "other"] ||= []).push(it);
    return Object.entries(groups);
  }

  // GET /journal/analysis -> rows for the 行为影响 card: items with a result, biggest effect first, and how many lack samples
  function behaviorModel(list) {
    if (!Array.isArray(list)) return null;
    const ok = list.map((a) => ({ a, o: a.outcomes?.next_recovery })).filter(({ o }) => o?.status === "ok")
      .sort((x, y) => Math.abs(y.o.diff_yes_minus_no || 0) - Math.abs(x.o.diff_yes_minus_no || 0));
    return { rows: ok.map(({ a, o }) => ({ item: a.item, diff: o.diff_yes_minus_no || 0, direction: o.direction, reliable: !!o.reliable })),
      lack: list.length - ok.length, max: Math.max(5, ...ok.map(({ o }) => Math.abs(o.diff_yes_minus_no || 0))) };
  }

  // ------------------------------------------------------------------ Trends: the 行为影响 card (same card as the other trend cards)
  function behaviorCardHtml(ctx, list) {
    const m = behaviorModel(list);
    if (!m) return "";   // no journal access / no endpoint: no card
    const rows = m.rows.map((r) => {
      const cls = r.direction === "better" ? "good" : r.direction === "worse" ? "bad" : "muted";
      return `<div class="contrib"><div class="contrib-head"><span>${ctx.esc(shortLabel(r.item))}</span><span class="y ${cls}">${ctx.signed(r.diff, 0)}${r.reliable ? "" : `<span class="muted"> ?</span>`}</span></div>
        <div class="dbar"><span class="${r.diff >= 0 ? "younger" : "older"} ${cls === "muted" ? "faint" : ""}" style="width:${(Math.abs(r.diff) / m.max * 50).toFixed(1)}%"></span></div></div>`;
    }).join("");
    const lack = m.lack ? `<div class="rcard-tags" style="margin-top:8px"><span class="badge plain">${ctx.esc(tr("{n} 项样本不足", { n: m.lack }))}</span></div>` : "";
    return `<div class="card trend trend-wide" id="behavior-card"><div class="trend-head"><div>${ctx.xpLabel("behavior", tr("行为影响"))}
        <div class="trend-value">${m.rows.length}<small>${tr("项")}</small></div></div><div class="trend-tip">${tr("次日恢复")} · ${tr("{n} 天", { n: 90 })}</div></div>
      ${rows || `<div class="empty faint" style="padding:8px">—</div>`}${lack}</div>`;
  }

  // ------------------------------------------------------------------ Today: the 日记 card
  // data: { items, entries } from journal/items and journal?start=day&end=day (null = not allowed / no endpoint -> no card)
  function cardHtml(ctx, day, data) {
    if (!data || !Array.isArray(data.items) || !Array.isArray(data.entries)) return "";
    const logged = loggedItems(data.items, valuesOf(data.entries, day));
    const shown = logged.slice(0, 4).map((it) => `<span class="badge plain">${ctx.esc(shortLabel(it))}</span>`).join("");
    const more = logged.length > 4 ? `<span class="badge plain">+${logged.length - 4}</span>` : "";
    return `<div class="section" id="journal-section"><div class="section-head"><h2>${tr("日记")}</h2></div>
      <button class="card" id="journal-card" ${ctx.sheetAttr(() => openDay(ctx, day))}>
        <div class="row"><span class="row-title">${logged.length ? tr("已记录 {n} 项", { n: logged.length }) : tr("未记录")}</span><span class="chev">${ctx.icon.chev}</span></div>
        ${logged.length ? `<div class="rcard-tags" style="margin-top:8px">${shown}${more}</div>` : ""}</button></div>`;
  }

  // ------------------------------------------------------------------ the day's editor sheet
  function tagHtml(ctx, it, val, self) {
    const v = val[it.key], dis = self ? "" : "disabled";
    if (it.kind !== "bool") {
      return `<button class="tag ${v != null ? "on" : ""}" data-num="${ctx.esc(it.key)}" ${dis}><span>${ctx.esc(shortLabel(it))}</span>${v != null ? `<span class="v">${ctx.fmtNum(v, 1)}${ctx.esc(it.unit || "")}</span>` : ""}</button>`;
    }
    return `<button class="tag ${v === 1 ? "on" : v === 0 ? "no" : ""}" data-jkey="${ctx.esc(it.key)}" aria-pressed="${v === 1}" ${dis}><span>${ctx.esc(shortLabel(it))}</span></button>`;
  }

  async function openDay(ctx, day, opts = {}) {
    const state = { showAll: !!opts.showAll };
    ctx.openSheet(`${tr("日记")} · ${ctx.shortDay(day)}`, `<div class="empty faint">…</div>`);
    let items, freq, entries;
    try {
      [items, freq, entries] = await Promise.all([
        ctx.load("jitems", `/v1/users/${ctx.ownerPath()}/journal/items?active_only=true`),
        ctx.load("jfreq", `/v1/users/${ctx.ownerPath()}/journal/frequent`),
        ctx.load(`jday${day}`, `/v1/users/${ctx.ownerPath()}/journal?start=${day}&end=${day}`),
      ]);
    } catch (e) {
      const body = document.getElementById("sheet-body");
      if (body) body.innerHTML = `<div class="empty">${ctx.esc(e.status === 0 ? tr("离线，连上网络后重试") : e.message)}</div>`;
      return;
    }
    const self = ctx.isSelf();
    const itemOf = (key) => items.find((i) => i.key === key) || { key, label_zh: key };

    const write = async (key, value) => {
      const r = writeRequest(day, key, value);
      await ctx.api(r.path, r.body ? { method: r.method, body: r.body } : { method: r.method });
      entries = applyWrite(entries, day, key, value);
      ctx.setCache(`jday${day}`, entries);
      ctx.invalidate();   // frequent order, the behaviour analysis; redraws the page behind the sheet
    };

    const draw = () => {
      const val = valuesOf(entries, day);
      let html = `<div class="section-head" style="margin-top:0"><h2>${tr("常用")}</h2><button class="link" id="show-all">${state.showAll ? tr("收起") : tr("全部")}</button></div>
        <div class="tags">${freq.map((it) => tagHtml(ctx, it, val, self)).join("")}</div>`;
      if (state.showAll) {
        html += `<div class="section" style="margin-top:18px">` + groupItems(items).map(([cat, list]) => `<div class="cat">${ctx.esc(ctx.categories[cat] || cat)}</div>
          <div class="tags">${list.map((it) => tagHtml(ctx, it, val, self)).join("")}</div>`).join("") + `</div>`;
      }
      if (self) {
        html += `<div class="section"><button class="card action" id="add-event"><span class="ic">${ctx.icon.pulse}</span><span class="row-title">${tr("记身体事件")}</span><span class="chev">${ctx.icon.chev}</span></button>
          <button class="card action" id="open-ai"><span class="ic">${ctx.icon.jarvis}</span><span class="row-title">${tr("Jarvis 记录")}</span><span class="chev">${ctx.icon.chev}</span></button></div>`;
      }
      return html;
    };

    const show = () => {
      ctx.openSheet(`${tr("日记")} · ${ctx.shortDay(day)}`, draw(), (sheet) => {
        const all = sheet.querySelector("#show-all");
        if (all) all.onclick = () => { state.showAll = !state.showAll; show(); };
        if (!self) return;
        for (const tag of sheet.querySelectorAll("[data-jkey]")) {
          tag.onclick = async () => {
            const key = tag.dataset.jkey, on = tag.classList.contains("on");
            const prev = on ? 1 : tag.classList.contains("no") ? 0 : null;
            tag.classList.add("busy");
            try {
              await write(key, on ? null : 1);
              show();
              const name = shortLabel(itemOf(key));
              ctx.toast(on ? tr("已撤销 · {name}", { name }) : tr("已记录 · {name}", { name }), tr("撤销"), async () => {
                try { await write(key, prev); if (document.getElementById("show-all")) show(); } catch (err) { ctx.toast(err.message); }
              });
            } catch (err) { ctx.toast(err.message); tag.classList.remove("busy"); }
          };
        }
        for (const tag of sheet.querySelectorAll("[data-num]")) tag.onclick = () => numberView(tag.dataset.num);
        const ev = sheet.querySelector("#add-event");
        if (ev) ev.onclick = () => ctx.openEvent();
        const ai = sheet.querySelector("#open-ai");
        if (ai) ai.onclick = (e) => { ctx.closeSheet(); ctx.openAssistant(e); };
      });
    };

    // A number item (water, red meat g ...): a small form in the same sheet; the back arrow returns to the list
    const numberView = (key) => {
      const it = itemOf(key), v = valuesOf(entries, day)[key];
      ctx.openSheet(shortLabel(it), `<div class="field"><label for="num-in">${ctx.esc(itemLabel(it))}${it.unit ? tr("（{u}）", { u: ctx.esc(it.unit) }) : ""} · ${ctx.shortDay(day)}</label>
        <input class="input" id="num-in" type="number" inputmode="decimal" step="any" min="0" value="${v ?? ""}" placeholder="${tr("没记录")}"></div>
        <div class="btns">${v != null ? `<button class="btn btn-danger" id="num-clear">${tr("清除")}</button>` : ""}<button class="btn btn-primary" id="num-save">${tr("保存")}</button></div>`,
      (sheet) => {
        ctx.setBack(show);
        const input = sheet.querySelector("#num-in");
        setTimeout(() => input.focus(), 260);
        const save = async (value) => {
          try { await write(key, value); show(); ctx.toast(value == null ? tr("已清除") : tr("已记录 {v}", { v: `${value}${it.unit || ""}` })); } catch (err) { ctx.toast(err.message); }
        };
        sheet.querySelector("#num-save").onclick = () => {
          const raw = input.value.trim(), value = raw === "" ? null : Number(raw);
          if (value != null && !(value >= 0)) { ctx.toast(tr("请输入不小于 0 的数")); return; }
          save(value);
        };
        const clear = sheet.querySelector("#num-clear");
        if (clear) clear.onclick = () => save(null);
      });
    };

    show();
  }

  const exported = { shortLabel, valuesOf, writeRequest, applyWrite, loggedItems, groupItems, behaviorModel, behaviorCardHtml, cardHtml, openDay };
  if (typeof module !== "undefined" && module.exports) module.exports = exported; else root.BodyJournal = exported;
})(typeof window !== "undefined" ? window : globalThis);
