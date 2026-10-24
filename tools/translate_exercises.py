#!/usr/bin/env python3
"""Offline batch: machine-translate the exercises that only have English text into Chinese (issue #248).

    cd body_engine && uv run python ../tools/translate_exercises.py --estimate              # cost only, no AI call
    cd body_engine && uv run python ../tools/translate_exercises.py --sample 50              # a stratified sample
    cd body_engine && uv run python ../tools/translate_exercises.py --budget-cny 30          # everything still missing

- The library is rebuilt the way a deployment builds it (migrations, curated + free-exercise-db seed, openGym import) in a
  throwaway database whose name must start with body_test_ (default body_test_translate, dropped and recreated); the production
  database is never read. free-exercise-db's exercises.json is the pinned, sha256-checked file (training/media.py), cached in
  --cache-dir.
- Result: body_engine/src/body_engine/training/translations/exercises_zh.jsonl (sorted by slug, committed). A line is kept while
  the English it came from is unchanged (src_sha), so re-running only translates what is missing or stale: idempotent, resumable
  (the file is rewritten after every batch).
- Model: any OpenAI-compatible Chat Completions service, default DeepSeek deepseek-flash with thinking off. The key is read from
  a file (default <repo>/secrets/deepseek_api_key), never from the repository or the command line.
- Every name is checked against glossary.json: a missed term gets one retry with the term spelled out, what is still missing is
  stored in glossary_misses for a person to look at. Steps must come back one for one.
- Costs are counted from the usage the service returns, at the prices in ai/fallback_params.py; --budget-cny stops the run.
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


def todo_rows(rows: list[dict], have: dict[str, dict]) -> list[dict]:
    """Rows without human Chinese whose line is missing or stale."""
    out = []
    for r in rows:
        if r.get("zh_source") == "human" or (T.has_chinese(r["name_zh"]) and r.get("zh_source") != "machine"):
            continue
        e = have.get(r["slug"])
        if e is None or e["src_sha"] != T.src_sha(r):
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


def estimate(rows: list[dict], batch: int) -> dict:
    """Token and cost estimate before any call: ~4 English chars per input token, ~0.75 output token per Chinese char and the
    Chinese ~ 0.38 × the English length (measured on the 50-entry sample), plus the prompt and glossary of each batch."""
    chars = sum(len(json.dumps(T.src_text(r), ensure_ascii=False)) for r in rows)
    calls = -(-len(rows) // batch)
    tin = chars / 4 + calls * 1500
    tout = chars * 0.38 * 0.75 + len(rows) * 30
    cny = tin / 1e6 * FP.PRICE_CACHE_MISS_CNY + tout / 1e6 * FP.PRICE_OUTPUT_CNY
    return {"exercises": len(rows), "english_chars": chars, "calls": calls, "input_tokens": round(tin), "output_tokens": round(tout),
            "cost_cny": round(cny, 2), "prices": f"input ¥{FP.PRICE_CACHE_MISS_CNY}/M, output ¥{FP.PRICE_OUTPUT_CNY}/M ({FP.PRICE_SOURCE})"}


def _glossary_block(rows: list[dict]) -> str:
    g = T.glossary()
    terms: list[str] = []
    for r in rows:
        src = T.src_text(r)
        terms += T.terms_in(" ".join([src["name"], src["description"], *src["steps"]]))
    terms = list(dict.fromkeys(terms))
    return "\n".join(f"{t} = {'|'.join(g[t])}" for t in sorted(terms))


class Translator:
    def __init__(self, key: str, base_url: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=key, base_url=base_url, timeout=180)
        self.model = model
        self.tokens_in = self.tokens_out = 0

    def cost(self) -> float:
        return self.tokens_in / 1e6 * FP.PRICE_CACHE_MISS_CNY + self.tokens_out / 1e6 * FP.PRICE_OUTPUT_CNY

    def call(self, rows: list[dict], hints: dict[str, list[str]] | None = None) -> dict[str, dict]:
        items = []
        for r in rows:
            src = T.src_text(r)
            it = {"id": r["slug"], "name": src["name"], "description": src["description"], "steps": src["steps"]}
            if hints and hints.get(r["slug"]):
                it["必须用的译法"] = hints[r["slug"]]
            items.append(it)
        msgs = [{"role": "system", "content": PROMPT + "\n\n术语表：\n" + _glossary_block(rows)},
                {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)}]
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=msgs, temperature=0.2, response_format={"type": "json_object"},
                    max_tokens=8000, extra_body={"thinking": {"type": "disabled"}})
            except Exception as e:   # network / rate limit: back off and retry
                if attempt == 2:
                    raise
                print(f"  retry after error: {e}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
                continue
            u = resp.usage
            self.tokens_in += u.prompt_tokens if u else 0
            self.tokens_out += u.completion_tokens if u else 0
            try:
                out = json.loads(resp.choices[0].message.content)["items"]
                return {x["id"]: x for x in out if isinstance(x, dict) and "id" in x}
            except (ValueError, KeyError, TypeError):
                if attempt == 2:
                    return {}
        return {}


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


def translate_batch(tr: Translator, rows: list[dict], model: str) -> tuple[list[dict], list[str]]:
    got = tr.call(rows)
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
    ap.add_argument("--estimate", action="store_true", help="only print the size and cost estimate")
    ap.add_argument("--sample", type=int, help="translate only a stratified sample of N exercises")
    ap.add_argument("--slugs", nargs="*", help="only these slugs")
    ap.add_argument("--redo", action="store_true", help="with --slugs: translate them again even when their line is up to date")
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--budget-cny", type=float, default=30.0, help="stop when the counted cost reaches this")
    ap.add_argument("--model", default=FP.MODEL)
    ap.add_argument("--base-url", default=FP.BASE_URL)
    ap.add_argument("--key-file", type=Path, default=Path(os.environ.get("BODY_ROOT", ROOT)) / "secrets" / "deepseek_api_key")
    a = ap.parse_args(argv)

    rows = build_rows(a.db, a.cache_dir)
    table = T.load(a.out)
    todo = todo_rows(rows, {} if a.redo and a.slugs else table)
    if a.slugs:
        todo = [r for r in todo if r["slug"] in set(a.slugs)]
    if a.sample:
        todo = sample(todo, a.sample)
    print(json.dumps({"library": len(rows), "lines_in_file": len(table), "to_translate": len(todo),
                      "estimate": estimate(todo, a.batch)}, ensure_ascii=False, indent=1))
    if a.estimate or not todo:
        return 0
    key = a.key_file.read_text(encoding="utf-8").strip()
    tr = Translator(key, a.base_url, a.model)
    batches = [todo[i:i + a.batch] for i in range(0, len(todo), a.batch)]
    failed: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = []
        for b in batches:
            futs.append(pool.submit(translate_batch, tr, b, a.model))
        for f in futs:
            lines, bad = f.result()
            failed += bad
            for ln in lines:
                table[ln["slug"]] = ln
            done += len(lines)
            write(a.out, table)
            print(f"  {done}/{len(todo)} done, ¥{tr.cost():.2f} ({tr.tokens_in} in / {tr.tokens_out} out tokens)", flush=True)
            if tr.cost() >= a.budget_cny:
                print(f"budget ¥{a.budget_cny} reached, stopping (re-run continues where it stopped)")
                for g in futs:
                    g.cancel()
                break
    misses = sum(1 for s in table.values() if s.get("glossary_misses"))
    print(json.dumps({"translated": done, "failed": failed, "cost_cny": round(tr.cost(), 3), "tokens_in": tr.tokens_in,
                      "tokens_out": tr.tokens_out, "lines_in_file": len(table), "lines_with_glossary_misses": misses},
                     ensure_ascii=False, indent=1))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
