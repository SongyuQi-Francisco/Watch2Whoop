"""body-engine import-records: migrate the health records in body-management.md into the database (idempotent).

- Each record has a stable import_key (exercise_ref for exercises, date + key for journal); existing ones are skipped, so later edits in the database are not overwritten.
- Also creates the default journal items (defaults.py) and the journal items for supplements.
- Returns a reconciliation table: per category, "expected in file / newly written this run / present in the database".
"""
from datetime import date

import psycopg

from . import md_seed, store
from .defaults import DEFAULT_JOURNAL_ITEMS, init_journal_items


def _present(conn, table: str, user_id: int, keys: list[str]) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {table} WHERE user_id = %s AND import_key = ANY(%s)",
                        (user_id, keys)).fetchone()["n"]


def run(conn: psycopg.Connection, user_id: int) -> list[dict]:
    if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise SystemExit(f"没有用户 {user_id}")
    report = []
    with conn.transaction():
        # Journal items: the defaults + the supplements' (add_supplement creates them as a side effect)
        r = init_journal_items(conn, user_id)
        supp_items = [(f["journal_key"], f["name"]) for _, f in md_seed.SUPPLEMENTS if f.get("journal_key")]
        before = conn.execute("SELECT count(*) AS n FROM journal_items WHERE user_id = %s AND key = ANY(%s)",
                              (user_id, [k for k, _ in supp_items])).fetchone()["n"]

        new = sum(store.add_supplement(conn, user_id, f, import_key=f"md:supplement:{k}") is not None
                  for k, f in md_seed.SUPPLEMENTS)
        keys = [f"md:supplement:{k}" for k, _ in md_seed.SUPPLEMENTS]
        report.append({"类别": "补剂 supplements", "应有": len(keys), "本次写入": new,
                       "现有": _present(conn, "supplements", user_id, keys)})

        new = sum(store.add_lab(conn, user_id, {**f, "test_key": k, "collected_on": md_seed.EXAM_2026_08,
                                                "lab": md_seed.LAB_SOURCE,
                                                "note": " ".join(filter(None, [f.get("note"), md_seed.LAB_DATE_NOTE]))},
                                import_key=f"md:lab:2026-08:{k}") is not None
                  for k, f in md_seed.LABS)
        keys = [f"md:lab:2026-08:{k}" for k, _ in md_seed.LABS]
        report.append({"类别": "化验 lab_results", "应有": len(keys), "本次写入": new,
                       "现有": _present(conn, "lab_results", user_id, keys)})

        new = sum(store.add_event(conn, user_id, f, import_key=f"md:event:{k}") is not None for k, f in md_seed.EVENTS)
        keys = [f"md:event:{k}" for k, _ in md_seed.EVENTS]
        report.append({"类别": "身体事件 events", "应有": len(keys), "本次写入": new,
                       "现有": _present(conn, "events", user_id, keys)})

        new = sum(store.upsert_exercise(conn, user_id, ref, f, overwrite=False) is not None
                  for ref, f in md_seed.EXERCISES)
        refs = [ref for ref, _ in md_seed.EXERCISES]
        present = conn.execute("SELECT status, count(*) AS n FROM exercise_meta WHERE user_id = %s "
                               "AND exercise_ref = ANY(%s) GROUP BY status", (user_id, refs)).fetchall()
        by_status = {r["status"]: r["n"] for r in present}
        report.append({"类别": "动作 exercise_meta（合计）", "应有": len(refs), "本次写入": new,
                       "现有": sum(by_status.values())})
        for s, label in (("banned", "禁用"), ("caution", "要小心"), ("active", "正常")):
            report.append({"类别": f"　其中{label}", "应有": sum(f.get("status", "active") == s for _, f in md_seed.EXERCISES),
                           "本次写入": None, "现有": by_status.get(s, 0)})

        after = conn.execute("SELECT count(*) AS n FROM journal_items WHERE user_id = %s AND key = ANY(%s)",
                             (user_id, [k for k, _ in supp_items])).fetchone()["n"]
        all_keys = [k for k, *_ in DEFAULT_JOURNAL_ITEMS] + [k for k, _ in supp_items]
        report.append({"类别": "日记项 journal_items（默认 + 补剂）", "应有": len(all_keys),
                       "本次写入": r["journal_items_created"] + after - before,
                       "现有": conn.execute("SELECT count(*) AS n FROM journal_items WHERE user_id = %s AND key = ANY(%s)",
                                            (user_id, all_keys)).fetchone()["n"]})

        new = sum(store.log_journal(conn, user_id, date.fromisoformat(d), key, v, note=note, source="md_backfill",
                                    overwrite=False) is not None
                  for d, key, v, note in md_seed.JOURNAL_BACKFILL)
        present = sum(conn.execute("SELECT 1 FROM journal_entries WHERE user_id = %s AND date = %s AND key = %s",
                                   (user_id, date.fromisoformat(d), key)).fetchone() is not None
                      for d, key, _, _ in md_seed.JOURNAL_BACKFILL)
        report.append({"类别": "日记回填 journal_entries", "应有": len(md_seed.JOURNAL_BACKFILL), "本次写入": new,
                       "现有": present})
    return report


def format_report(report: list[dict]) -> str:
    lines = ["| 类别 | 应有 | 本次写入 | 现有 | 核对 |", "|---|---|---|---|---|"]
    for r in report:
        ok = "✓" if r["现有"] == r["应有"] else "✗"
        lines.append(f"| {r['类别']} | {r['应有']} | {'' if r['本次写入'] is None else r['本次写入']} | {r['现有']} | {ok} |")
    return "\n".join(lines)
