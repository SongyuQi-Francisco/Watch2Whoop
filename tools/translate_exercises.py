#!/usr/bin/env python3
"""Offline batch: machine-translate the exercises that only have English text into Chinese (issue #248).

    cd body_engine && uv run python ../tools/translate_exercises.py --estimate                       # cost only, no AI call
    cd body_engine && uv run python ../tools/translate_exercises.py --sample 50 --prepare-only req.jsonl  # look at the requests
    cd body_engine && uv run python ../tools/translate_exercises.py --sample 50                       # a stratified sample
    cd body_engine && uv run python ../tools/translate_exercises.py --batch-api --budget 20           # everything still missing

- The library is rebuilt the way a deployment builds it (migrations, curated + free-exercise-db seed, openGym import) in a
  throwaway database whose name must start with body_test_ (default body_test_translate, dropped and recreated); the production
  database is never read. free-exercise-db's exercises.json is the pinned, sha256-checked file (training/media.py), cached in
  --cache-dir.
- Result: body_engine/src/body_engine/training/translations/exercises_zh.jsonl (sorted by slug, committed). A line is kept while
  the English it came from is unchanged (src_sha), so re-running only translates what is missing or stale: idempotent, resumable
  (the file is rewritten after every batch).
- Model: Claude claude-sonnet-5-5 through the official Anthropic SDK (default; --batch-api sends the bulk through the Message
  Batches API at half price, structured JSON output, thinking off), or --provider deepseek (any OpenAI-compatible service).
  The key is read from a file (default <repo>/secrets/anthropic_api_key or deepseek_api_key), never from the repository or
  the command line, and is never printed.
- Every name is checked against glossary.json: a missed term gets one retry with the term spelled out, what is still missing is
  stored in glossary_misses for a person to look at. Steps must come back one for one.
- Costs are counted from the usage the service returns (Claude: the list prices below; DeepSeek: ai/fallback_params.py);
  --budget stops the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "body_engine" / "src"))

from body_engine.ai import fallback_params as FP  # noqa: E402
from body_engine.training import translations as T  # noqa: E402

PROMPT = """你是健身动作库的专业译者，把英文动作资料翻成简体中文（中国大陆健身房的常用说法）。
规则：
1. 名称（name_zh）：简洁、地道，按中文习惯排成「器械 + 体位 / 握法 + 动作」，例如 Dumbbell Incline Bench Press → 哑铃上斜卧推、Cable Standing Lateral Raise → 绳索站姿侧平举。
   必须使用下面术语表给的译法（「|」分隔的是可选的几种，优先第一种）。英文里的括号内容也要译，放在中文全角括号里。
   保留数字和角度（45°）；「V. 2」这类版本号译成「（版本 2）」。术语表没有的人名 / 专有名词音译，或保留英文。不要自己加解释。
2. 描述（description_zh）和步骤（steps_zh）：忠实翻译，不增不减，不合并、不拆分；steps_zh 的条数必须和原文 steps 完全一样。原文为空就给空。
   用省略主语的祈使句（「双脚与肩同宽站立……」），专业但口语化，单位照原文。
3. 只输出一个 JSON 对象：{"items": [{"id": ..., "name_zh": ..., "description_zh": ..., "steps_zh": [...]}]}，id 原样带回，每个输入都要有。"""


def _db_name(name: str) -> str:
    if not name.startswith("body_test_"):
        raise SystemExit(f"--db {name}: must start with body_test_ (the library is rebuilt from scratch, never in a real database)")
    return name


def build_rows(db: str, cache: Path) -> list[dict]:
    """The full library as a deployment builds it, in a throwaway database."""
    import psycopg
    from psycopg.rows import dict_row

    from body_engine.config import Settings
    from body_engine.db import migrate
    from body_engine.training import catalog, media
    from body_engine.training.opengym import importer

    with psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{db}"')
    js = media.fetch_json(cache)
    with psycopg.connect(Settings(db_name=db).dsn, row_factory=dict_row, autocommit=True) as conn:
        migrate(conn)
        catalog.seed_catalog(conn, js)
        importer.run(conn)
        rows = conn.execute("SELECT * FROM exercise_catalog ORDER BY slug").fetchall()
    with psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
    return rows


def todo_rows(rows: list[dict], have: dict[str, dict], model: str | None = None) -> list[dict]:
    """Rows without human Chinese whose line is missing or stale (or, with model, was made by another model)."""
    out = []
    for r in rows:
        if r.get("zh_source") == "human" or (T.has_chinese(r["name_zh"]) and r.get("zh_source") != "machine"):
            continue
        e = have.get(r["slug"])
        if e is None or e["src_sha"] != T.src_sha(r) or (model and e.get("model") != model):
            out.append(r)
    return out


def sample(rows: list[dict], n: int) -> list[dict]:
    """A deterministic sample spread over source and category (largest groups first), e.g. for checking quality and cost."""
    groups: dict[tuple, list[dict]] = {}
    for r in sorted(rows, key=lambda r: hashlib.sha256(r["slug"].encode()).hexdigest()):
        groups.setdefault((r["source"], r["category"]), []).append(r)
    order = sorted(groups, key=lambda k: -len(groups[k]))
    picked: list[dict] = []
    while len(picked) < n and any(groups.values()):
        for k in order:
            if groups[k] and len(picked) < n:
                picked.append(groups[k].pop(0))
    return picked


ZH_PER_EN_CHAR = 0.265   # Chinese characters per English character of the source, measured on the committed 50-line sample


def _tokens(text: str) -> float:
    """Conservative token count for Claude's tokenizer: ~3.5 ASCII characters per token, ~1.3 tokens per CJK character."""
    cjk = sum(1 for ch in text if T.CJK.match(ch))
    return (len(text) - cjk) / 3.5 + cjk * 1.3


def estimate(rows: list[dict], batch: int, provider: str) -> dict:
    """Size and cost before any call, from the real prompts of every group. Output = the Chinese the sample suggests
    (ZH_PER_EN_CHAR) plus ~40 tokens of JSON per exercise. Claude: Message Batches price and, for comparison, synchronous."""
    groups = [rows[i:i + batch] for i in range(0, len(rows), batch)]
    src = sum(len(" ".join([x["name"], x["description"], *x["steps"]])) for x in map(T.src_text, rows))
    tout = src * ZH_PER_EN_CHAR * 1.3 + len(rows) * 40
    out = {"exercises": len(rows), "requests": len(groups), "english_chars": src, "output_tokens": round(tout)}
    if provider == "claude" and groups:
        prefix = _tokens(full_glossary_system())             # the cached system block, the same in every request
        var = sum(_tokens(user_text(g)) for g in groups)     # what changes per request
        n, pi, po = len(groups), CLAUDE_PRICE_IN / 1e6, CLAUDE_PRICE_OUT / 1e6
        best = var * pi + prefix * pi * (CACHE_WRITE_1H + (n - 1) * CACHE_READ) + tout * po   # one write, then cache reads
        worst = var * pi + prefix * pi * CACHE_WRITE_1H * n + tout * po                       # every request writes the prefix again
        sync = var * pi + prefix * pi * (CACHE_WRITE_5M + (n - 1) * CACHE_READ) + tout * po
        out.update(model=CLAUDE_MODEL, cached_prefix_tokens=round(prefix), input_tokens_per_request=round(var / n),
                   cost_usd_batch=round(best * BATCH_DISCOUNT, 2), cost_usd_batch_if_no_cache_hits=round(worst * BATCH_DISCOUNT, 2),
                   cost_usd_sync=round(sync, 2),
                   prices=f"${CLAUDE_PRICE_IN}/M in, ${CLAUDE_PRICE_OUT}/M out; cache read ×{CACHE_READ}, 1h write ×{CACHE_WRITE_1H}; "
                          f"Message Batches ×{BATCH_DISCOUNT} (cache hits inside a batch are best effort)",
                   time="Message Batches: most batches end within 1 hour, at most 24 hours")
    elif provider == "claude":
        out.update(model=CLAUDE_MODEL, cost_usd_batch=0.0)
    else:   # DeepSeek's tokenizer is denser for Chinese (~0.6 token / character)
        tin = sum(_tokens("".join(build_prompt(g))) for g in groups)
        dout = src * ZH_PER_EN_CHAR * 0.75 + len(rows) * 30
        out["input_tokens"] = round(tin)
        out.update(model=FP.MODEL, output_tokens=round(dout), cost_cny=round(tin * 0.8 / 1e6 * FP.PRICE_CACHE_MISS_CNY + dout / 1e6 * FP.PRICE_OUTPUT_CNY, 2),
                   prices=f"input ¥{FP.PRICE_CACHE_MISS_CNY}/M, output ¥{FP.PRICE_OUTPUT_CNY}/M ({FP.PRICE_SOURCE})")
    return out


def _glossary_block(rows: list[dict]) -> str:
    g = T.glossary()
    terms: list[str] = []
    for r in rows:
        src = T.src_text(r)
        terms += T.terms_in(" ".join([src["name"], src["description"], *src["steps"]]))
    terms = list(dict.fromkeys(terms))
    return "\n".join(f"{t} = {'|'.join(g[t])}" for t in sorted(terms))


def full_glossary_system() -> str:
    """The prompt plus the WHOLE glossary: byte-identical for every request, so Claude can serve it from the prompt cache."""
    g = T.glossary()
    return PROMPT + "\n\n术语表：\n" + "\n".join(f"{t} = {'|'.join(g[t])}" for t in sorted(g))


def user_text(rows: list[dict], hints: dict[str, list[str]] | None = None) -> str:
    items = []
    for r in rows:
        src = T.src_text(r)
        it = {"id": r["slug"], "name": src["name"], "description": src["description"], "steps": src["steps"]}
        if hints and hints.get(r["slug"]):
            it["必须用的译法"] = hints[r["slug"]]
        items.append(it)
    return json.dumps({"items": items}, ensure_ascii=False)


def build_prompt(rows: list[dict], hints: dict[str, list[str]] | None = None) -> tuple[str, str]:
    """(system, user) for one group of exercises with only the glossary terms that group uses (DeepSeek: no prompt cache)."""
    items = []
    for r in rows:
        src = T.src_text(r)
        it = {"id": r["slug"], "name": src["name"], "description": src["description"], "steps": src["steps"]}
        if hints and hints.get(r["slug"]):
            it["必须用的译法"] = hints[r["slug"]]
        items.append(it)
    return PROMPT + "\n\n术语表：\n" + _glossary_block(rows), json.dumps({"items": items}, ensure_ascii=False)


def _items(text: str | None) -> dict[str, dict]:
    try:
        out = json.loads(text or "")["items"]
        return {x["id"]: x for x in out if isinstance(x, dict) and "id" in x}
    except (ValueError, KeyError, TypeError):
        return {}


# Claude: Anthropic's list prices for claude-sonnet-5-5 (USD per million tokens, checked 2026-10-25); the Message Batches API bills 50%.
CLAUDE_MODEL = "claude-sonnet-5-5"
CLAUDE_PRICE_IN, CLAUDE_PRICE_OUT, BATCH_DISCOUNT = 2.0, 10.0, 0.5
CACHE_READ, CACHE_WRITE_5M, CACHE_WRITE_1H = 0.1, 1.25, 2.0   # × the input price (prompt caching)
ITEMS_SCHEMA = {   # structured output (output_config.format): the reply is exactly this JSON
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name_zh": {"type": "string"}, "description_zh": {"type": "string"},
                       "steps_zh": {"type": "array", "items": {"type": "string"}}},
        "required": ["id", "name_zh", "description_zh", "steps_zh"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def claude_params(rows: list[dict], model: str, hints: dict[str, list[str]] | None = None, ttl: str | None = None) -> dict:
    """One Messages request. The system block (prompt + whole glossary) is identical in every request and carries the prompt-cache
    breakpoint; only the exercises change. ttl "1h" for Message Batches (its requests can be spread over a long time), the default
    5 minutes for synchronous calls. Thinking off (between_tools, the lowest setting on Sonnet 5.5) at low effort: translation needs
    no reasoning, and thinking tokens would be billed as output."""
    cache = {"type": "ephemeral", **({"ttl": ttl} if ttl else {})}
    system = [{"type": "text", "text": full_glossary_system(), "cache_control": cache}]
    return {"model": model, "max_tokens": 16000, "system": system, "messages": [{"role": "user", "content": user_text(rows, hints)}],
            "thinking": {"type": "between_tools"},
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": ITEMS_SCHEMA}}}


class DeepSeekTranslator:
    """Any OpenAI-compatible Chat Completions service (default DeepSeek deepseek-flash, thinking off); costs in CNY."""
    currency = "CNY"

    def __init__(self, key: str, base_url: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=key, base_url=base_url, timeout=180)
        self.model = model
        self.tokens_in = self.tokens_out = 0

    def cost(self) -> float:
        return self.tokens_in / 1e6 * FP.PRICE_CACHE_MISS_CNY + self.tokens_out / 1e6 * FP.PRICE_OUTPUT_CNY

    def call(self, rows: list[dict], hints: dict[str, list[str]] | None = None) -> dict[str, dict]:
        system, user = build_prompt(rows, hints)
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=msgs, temperature=0.2, response_format={"type": "json_object"},
                    max_tokens=8000, extra_body={"thinking": {"type": "disabled"}})
            except Exception as e:   # network / rate limit: back off and retry
                if attempt == 2:
                    raise
                print(f"  retry after error: {type(e).__name__}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
                continue
            u = resp.usage
            self.tokens_in += u.prompt_tokens if u else 0
            self.tokens_out += u.completion_tokens if u else 0
            got = _items(resp.choices[0].message.content)
            if got or attempt == 2:
                return got
        return {}


class ClaudeTranslator:
    """Claude through the official Anthropic SDK; costs in USD. Synchronous calls (retries, glossary fixes) go through
    client.messages.create; the bulk goes through the Message Batches API (run_claude_batch) at half price."""
    currency = "USD"

    def __init__(self, key: str, model: str = CLAUDE_MODEL):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=key, max_retries=4)
        self.model = model
        self.tokens_in = self.tokens_out = 0
        self.batch_in = self.batch_out = 0
        self.usd = 0.0                                  # counted per response from its usage (cache reads / writes included)
        self.cache = {"read": 0, "write": 0}

    def cost(self) -> float:
        return self.usd

    def count(self, usage, batch: bool, ttl: str | None) -> None:
        read = usage.cache_read_input_tokens or 0
        write = usage.cache_creation_input_tokens or 0
        self.cache["read"] += read
        self.cache["write"] += write
        usd = (usage.input_tokens * CLAUDE_PRICE_IN + read * CLAUDE_PRICE_IN * CACHE_READ
               + write * CLAUDE_PRICE_IN * (CACHE_WRITE_1H if ttl == "1h" else CACHE_WRITE_5M)
               + usage.output_tokens * CLAUDE_PRICE_OUT) / 1e6
        self.usd += usd * (BATCH_DISCOUNT if batch else 1.0)
        if batch:
            self.batch_in += usage.input_tokens + read + write
            self.batch_out += usage.output_tokens
        else:
            self.tokens_in += usage.input_tokens + read + write
            self.tokens_out += usage.output_tokens

    def _text(self, msg) -> str | None:
        if msg.stop_reason == "refusal":   # a safety classifier declined: the rows are reported as failed, nothing is guessed
            return None
        return next((b.text for b in msg.content if b.type == "text"), None)

    def call(self, rows: list[dict], hints: dict[str, list[str]] | None = None) -> dict[str, dict]:
        a = self.anthropic
        for attempt in range(2):
            try:
                msg = self.client.messages.create(**claude_params(rows, self.model, hints))
            except (a.RateLimitError, a.InternalServerError, a.APIConnectionError) as e:   # SDK already retried 4 times
                if attempt == 1:
                    raise
                print(f"  retry after {type(e).__name__}", file=sys.stderr)
                time.sleep(30)
                continue
            self.count(msg.usage, batch=False, ttl=None)
            got = _items(self._text(msg))
            if got or attempt == 1:
                return got
        return {}


def run_claude_batch(tr: ClaudeTranslator, groups: list[list[dict]], state: Path) -> dict[str, dict]:
    """Submit every group as one Message Batches request (custom_id = group number), wait, and return slug -> item.
    The batch id is kept in `state`, so an interrupted run picks the same batch up again instead of paying twice."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    doc = json.loads(state.read_text(encoding="utf-8")) if state.is_file() else {}
    slugs = [[r["slug"] for r in g] for g in groups]
    if doc.get("groups") != slugs:
        reqs = [Request(custom_id=f"g{i:05d}", params=MessageCreateParamsNonStreaming(**claude_params(g, tr.model, ttl="1h")))
                for i, g in enumerate(groups)]
        batch = tr.client.messages.batches.create(requests=reqs)
        doc = {"batch_id": batch.id, "groups": slugs, "model": tr.model}
        state.write_text(json.dumps(doc), encoding="utf-8")
        print(f"  batch {batch.id}: {len(reqs)} requests submitted", flush=True)
    while True:
        b = tr.client.messages.batches.retrieve(doc["batch_id"])
        if b.processing_status == "ended":
            break
        c = b.request_counts
        print(f"  batch {b.id}: {c.processing} processing, {c.succeeded} done, {c.errored} errored", flush=True)
        time.sleep(60)
    got: dict[str, dict] = {}
    for res in tr.client.messages.batches.results(doc["batch_id"]):   # any order: keyed by custom_id
        if res.result.type != "succeeded":
            continue
        msg = res.result.message
        tr.count(msg.usage, batch=True, ttl="1h")
        group = set(doc["groups"][int(res.custom_id[1:])])
        got.update({k: v for k, v in _items(tr._text(msg)).items() if k in group})
    return got


def check(r: dict, x: dict | None) -> str | None:
    """Why a returned item cannot be used (None = usable)."""
    if not x:
        return "missing"
    src = T.src_text(r)
    if not T.has_chinese(x.get("name_zh")):
        return "name not Chinese"
    steps = x.get("steps_zh") or []
    if not isinstance(steps, list) or len(steps) != len(src["steps"]):
        return f"steps {len(steps) if isinstance(steps, list) else '?'} ≠ {len(src['steps'])}"
    if src["description"] and not T.has_chinese(x.get("description_zh")):
        return "description not Chinese"
    return None


def translate_batch(tr, rows: list[dict], model: str, got: dict[str, dict] | None = None) -> tuple[list[dict], list[str]]:
    """Translate one group (or check what the Message Batches run already returned for it), fix what failed or missed a
    glossary term with synchronous calls, and return (lines, failures)."""
    got = dict(got) if got is not None else tr.call(rows)
    bad = [r for r in rows if check(r, got.get(r["slug"]))]
    if bad:   # once more, one by one
        for r in bad:
            got.update(tr.call([r]))
    g = T.glossary()
    hints = {}
    for r in rows:
        x = got.get(r["slug"])
        if x and not check(r, x):
            miss = T.glossary_misses(r["name_en"], x["name_zh"])
            if miss:
                hints[r["slug"]] = [f"{t} → {g[t][0]}" for t in miss]
    if hints:   # names that missed a glossary term: once more with the terms spelled out
        again = tr.call([r for r in rows if r["slug"] in hints], hints)
        for slug, x in again.items():
            r = next(r for r in rows if r["slug"] == slug)
            if not check(r, x) and len(T.glossary_misses(r["name_en"], x["name_zh"])) < len(hints[slug]):
                got[slug] = x
    lines, failed = [], []
    for r in rows:
        x = got.get(r["slug"])
        why = check(r, x)
        if why:
            failed.append(f"{r['slug']}: {why}")
            continue
        steps = [str(s).strip() for s in x["steps_zh"]]
        lines.append({"slug": r["slug"], "src_sha": T.src_sha(r), "name_en": r["name_en"], "name_zh": x["name_zh"].strip(),
                      "description_zh": (x.get("description_zh") or "").strip() or None, "instructions_zh": steps,
                      "zh_source": "machine", "model": model, "glossary_misses": T.glossary_misses(r["name_en"], x["name_zh"])})
    return lines, failed


def write(path: Path, table: dict[str, dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(table[s], ensure_ascii=False, sort_keys=True) + "\n" for s in sorted(table)), encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default="body_test_translate", type=_db_name)
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "media", help="where the pinned free-exercise-db JSON is kept")
    ap.add_argument("--out", type=Path, default=T.DATA)
    ap.add_argument("--provider", choices=("claude", "deepseek"), default="claude")
    ap.add_argument("--batch-api", action="store_true", help="claude: send the bulk through the Message Batches API (half price)")
    ap.add_argument("--estimate", action="store_true", help="only print the size and cost estimate")
    ap.add_argument("--prepare-only", type=Path, metavar="FILE",
                    help="claude: write the requests as JSON lines to FILE (to look at them) and stop; needs no key")
    ap.add_argument("--sample", type=int, help="translate only a stratified sample of N exercises")
    ap.add_argument("--slugs", nargs="*", help="only these slugs")
    ap.add_argument("--redo", action="store_true", help="with --slugs: translate them again even when their line is up to date")
    ap.add_argument("--batch", type=int, help="exercises per request (default 20 for claude, 12 for deepseek)")
    ap.add_argument("--same-model", action="store_true", help="also translate again the lines another model made (one model "
                                                               "for the whole library)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--budget", type=float, help="stop when the counted cost reaches this (USD for claude, CNY for deepseek); "
                                                 "default 20 USD / 30 CNY")
    ap.add_argument("--model", help=f"default {CLAUDE_MODEL} / {FP.MODEL}")
    ap.add_argument("--base-url", default=FP.BASE_URL, help="deepseek: the OpenAI-compatible endpoint")
    ap.add_argument("--key-file", type=Path, help="default <repo>/secrets/anthropic_api_key or deepseek_api_key")
    a = ap.parse_args(argv)
    claude = a.provider == "claude"
    a.batch = a.batch or (20 if claude else 12)
    model = a.model or (CLAUDE_MODEL if claude else FP.MODEL)
    budget = a.budget if a.budget is not None else (20.0 if claude else 30.0)
    secrets = Path(os.environ.get("BODY_ROOT", ROOT)) / "secrets"
    key_file = a.key_file or secrets / ("anthropic_api_key" if claude else "deepseek_api_key")

    rows = build_rows(a.db, a.cache_dir)
    table = T.load(a.out)
    todo = todo_rows(rows, {} if a.redo and a.slugs else table, model if a.same_model else None)
    if a.slugs:
        todo = [r for r in todo if r["slug"] in set(a.slugs)]
    if a.sample:
        todo = sample(todo, a.sample)
    print(json.dumps({"library": len(rows), "lines_in_file": len(table), "to_translate": len(todo),
                      "estimate": estimate(todo, a.batch, a.provider)}, ensure_ascii=False, indent=1))
    groups = [todo[i:i + a.batch] for i in range(0, len(todo), a.batch)]
    if a.prepare_only:
        if not claude:
            raise SystemExit("--prepare-only is for --provider claude")
        a.prepare_only.write_text("".join(json.dumps({"custom_id": f"g{i:05d}", "params": claude_params(g, model)},
                                                     ensure_ascii=False) + "\n" for i, g in enumerate(groups)), encoding="utf-8")
        print(f"{len(groups)} requests written to {a.prepare_only}")
        return 0
    if a.estimate or not todo:
        return 0
    key = key_file.read_text(encoding="utf-8").strip()   # never printed or logged
    tr = ClaudeTranslator(key, model) if claude else DeepSeekTranslator(key, a.base_url, model)
    del key
    failed: list[str] = []
    done = 0

    def keep(lines: list[dict], bad: list[str]) -> None:
        nonlocal done
        failed.extend(bad)
        for ln in lines:
            table[ln["slug"]] = ln
        done += len(lines)
        write(a.out, table)
        print(f"  {done}/{len(todo)} done, {tr.currency} {tr.cost():.3f}", flush=True)

    if claude and a.batch_api:
        got = run_claude_batch(tr, groups, a.cache_dir / f"translate_batch_{hashlib.sha256(json.dumps([[r['slug'] for r in g] for g in groups]).encode()).hexdigest()[:12]}.json")
        print(f"  batch results in, {tr.currency} {tr.cost():.3f}; checking and fixing synchronously", flush=True)
        for g in groups:   # retries / glossary fixes are synchronous (few)
            keep(*translate_batch(tr, g, model, {k: v for k, v in got.items() if k in {r["slug"] for r in g}}))
            if tr.cost() >= budget:
                print(f"budget {tr.currency} {budget} reached, stopping (re-run continues where it stopped)")
                break
    else:
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            futs = [pool.submit(translate_batch, tr, g, model) for g in groups]
            for f in futs:
                keep(*f.result())
                if tr.cost() >= budget:
                    print(f"budget {tr.currency} {budget} reached, stopping (re-run continues where it stopped)")
                    for g in futs:
                        g.cancel()
                    break
    misses = sum(1 for s in table.values() if s.get("glossary_misses"))
    print(json.dumps({"translated": done, "failed": failed, "cost": round(tr.cost(), 3), "currency": tr.currency,
                      "tokens_in": tr.tokens_in + getattr(tr, "batch_in", 0), "tokens_out": tr.tokens_out + getattr(tr, "batch_out", 0),
                      "lines_in_file": len(table), "lines_with_glossary_misses": misses}, ensure_ascii=False, indent=1))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
