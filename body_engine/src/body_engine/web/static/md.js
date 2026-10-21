/* A small, safe Markdown subset for the AI's chat answers (ai.js, issue #92). No dependencies, no network (CSP: self only).
   - Supported: **bold** / __bold__, *italic* / _italic_, ~~strike~~, `code`, ``` fenced code ```, # headings, > quotes,
     - / * / + and 1. lists (one nested level), simple | tables | with a --- separator row, --- rules, [links](https://…) and
     bare https:// links. Everything else is plain text; line breaks inside a paragraph are kept (chat style).
   - Safety: the source is HTML-escaped before any markup is added, so the only tags in the output are the ones made here.
     Links: http(s) only (anything else stays text), target=_blank rel="noopener noreferrer nofollow". No images, no raw HTML.
   - Streaming (render(text, { partial: true })): an opener that has no closer yet (a trailing **, `, ~~, an unfinished
     [link](…) or an unclosed ``` fence) never turns the rest of the answer bold / code; a dangling marker at the very end is
     simply hidden until its closer arrives, so the text does not flash between styles while it streams.
   Works in the browser (window.BodyMarkdown) and in node (module.exports) for tests/test_markdown.py. */
(function (root) {
  "use strict";

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

  // ---------------------------------------------------------------- inline
  // Works on already-escaped text. Code spans and links are swapped out for placeholders first, so emphasis rules never
  // reach into them; placeholders use a private-use character the source cannot contain (stripped on input).
  const PH = "";
  function inline(src) {
    const slots = [];
    const hold = (html) => { slots.push(html); return PH + (slots.length - 1) + PH; };
    let s = src;
    s = s.replace(/`([^`\n]+)`/g, (_, c) => hold(`<code>${c}</code>`));
    s = s.replace(/\[([^\]\n]+)\]\(([^()\s]+)\)/g, (m, text, url) => (/^https?:\/\//i.test(url)
      ? hold(`<a href="${url}" target="_blank" rel="noopener noreferrer nofollow">${emph(text)}</a>`) : m));
    s = s.replace(/(^|[\s(（：:，,])(https?:\/\/(?:(?!&(?:quot|lt|gt|#39);)[^\s）)，。])+)/g, (_, pre, raw) => {
      const url = raw.replace(/[.,;:!?]+$/, "");   // sentence punctuation after a bare link is not part of it
      return pre + hold(`<a href="${url}" target="_blank" rel="noopener noreferrer nofollow">${url}</a>`) + raw.slice(url.length);
    });
    s = emph(s);
    return s.replace(new RegExp(PH + "(\\d+)" + PH, "g"), (_, i) => slots[+i]);
  }
  function emph(s) {
    return s
      .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, "$1<strong>$2</strong>")
      .replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>")
      .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/g, "$1<em>$2</em>")
      .replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, "$1<em>$2</em>");
  }

  // While streaming: hide an opener at the end of the text that has no closer yet.
  function trimDangling(text) {
    let t = text;
    if ((t.match(/^\s*```/gm) || []).length % 2) return t;   // inside an open code block: it simply grows
    const lastLine = t.slice(t.lastIndexOf("\n\n") + 1);   // the paragraph being written
    const count = (str, re) => (str.match(re) || []).length;
    if (count(lastLine.replace(/`[^`]*`/g, ""), /\*\*/g) % 2) t = t.replace(/\*\*(?![\s\S]*\*\*)/, "");
    if (count(lastLine.replace(/`[^`]*`/g, ""), /~~/g) % 2) t = t.replace(/~~(?![\s\S]*~~)/, "");
    if (count(lastLine.replace(/```/g, ""), /`/g) % 2) t = t.replace(/`(?![\s\S]*`)/, "");
    t = t.replace(/\[([^\]\n]*)\]\([^)\s]*$/, "$1").replace(/\[([^\]\n]*)$/, "$1");
    return t;
  }

  // ---------------------------------------------------------------- blocks
  const RE = {
    fence: /^\s*```\s*([\w+-]*)\s*$/,
    heading: /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/,
    rule: /^\s{0,3}([-*_])(\s*\1){2,}\s*$/,
    quote: /^\s{0,3}&gt;\s?(.*)$/,          // the source is escaped before block parsing
    ul: /^(\s*)[-*+]\s+(.*)$/,
    ol: /^(\s*)(\d{1,3})[.)]\s+(.*)$/,
    tableSep: /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/,
  };
  const isTableRow = (l) => /\|/.test(l) && !/^\s*```/.test(l);
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

  function blocks(lines) {
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      let m;
      if ((m = RE.fence.exec(line))) {
        const body = [];
        i++;
        while (i < lines.length && !RE.fence.test(lines[i])) body.push(lines[i++]);
        i++;   // closing fence (or end of text while streaming: the block simply grows)
        out.push(`<pre><code>${body.join("\n")}</code></pre>`);
        continue;
      }
      if ((m = RE.heading.exec(line))) {
        out.push(`<p class="md-h">${inline(m[2])}</p>`);
        i++;
        continue;
      }
      if (RE.rule.test(line)) { out.push("<hr>"); i++; continue; }
      if (RE.quote.test(line)) {
        const body = [];
        while (i < lines.length && (m = RE.quote.exec(lines[i]))) { body.push(m[1]); i++; }
        out.push(`<blockquote>${blocks(body)}</blockquote>`);
        continue;
      }
      if (isTableRow(line) && i + 1 < lines.length && RE.tableSep.test(lines[i + 1])) {
        const head = cells(line);
        const rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim() && isTableRow(lines[i])) rows.push(cells(lines[i++]));
        const td = (tag, c) => `<${tag}>${inline(c)}</${tag}>`;
        out.push(`<div class="md-table"><table><thead><tr>${head.map((c) => td("th", c)).join("")}</tr></thead><tbody>`
          + rows.map((r) => `<tr>${head.map((_, k) => td("td", r[k] ?? "")).join("")}</tr>`).join("") + "</tbody></table></div>");
        continue;
      }
      if (RE.ul.test(line) || RE.ol.test(line)) { i = list(lines, i, out); continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) para.push(lines[i++].trim());
      out.push(`<p>${inline(para.join("\n")).replace(/\n/g, "<br>")}</p>`);
    }
    return out.join("");
  }

  function startsBlock(lines, i) {
    const l = lines[i];
    return RE.fence.test(l) || RE.heading.test(l) || RE.rule.test(l) || RE.quote.test(l) || RE.ul.test(l) || RE.ol.test(l)
      || (isTableRow(l) && i + 1 < lines.length && RE.tableSep.test(lines[i + 1]));
  }

  // A list from line i: items of the same kind at the base indent; deeper-indented items become one nested level.
  function list(lines, i, out) {
    const first = RE.ol.exec(lines[i]) || RE.ul.exec(lines[i]);
    const ordered = RE.ol.test(lines[i]) && !RE.ul.test(lines[i]);
    const base = first[1].length;
    const items = [];
    while (i < lines.length && lines[i].trim()) {
      const o = RE.ol.exec(lines[i]), u = RE.ul.exec(lines[i]);
      const m = o || u;
      if (m && m[1].length <= base + 1) {
        if (!!o !== ordered) break;
        items.push({ text: o ? o[3] : u[2], sub: [], start: o ? +o[2] : null });
        i++;
      } else if (m && items.length) {
        items[items.length - 1].sub.push(lines[i].slice(Math.min(m[1].length, base + 2)));
        i++;
      } else if (items.length && /^\s{2,}\S/.test(lines[i])) {   // continuation line of the item
        items[items.length - 1].text += "\n" + lines[i].trim();
        i++;
      } else break;
    }
    const tag = ordered ? "ol" : "ul";
    const start = ordered && items[0].start > 1 ? ` start="${items[0].start}"` : "";
    out.push(`<${tag}${start}>` + items.map((it) => `<li>${inline(it.text).replace(/\n/g, "<br>")}`
      + (it.sub.length ? blocks(it.sub) : "") + "</li>").join("") + `</${tag}>`);
    return i;
  }

  function render(text, opts) {
    let t = String(text ?? "").replace(/\r\n?/g, "\n").split(PH).join("");
    if (opts && opts.partial) t = trimDangling(t);
    return blocks(esc(t).split("\n"));
  }

  const api = { render, escape: esc };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BodyMarkdown = api;
})(typeof window !== "undefined" ? window : this);
