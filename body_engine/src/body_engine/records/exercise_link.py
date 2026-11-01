"""Write SparkyFitness exercise IDs into exercise_meta.app_exercise_id (body-engine link-exercises, idempotent).

Matching (strongest to weakest; each exercise matches only one):
  0. The SparkyFitness exercise's source_id is bm:<exercise_ref> (exercises created by the body-management.md migration script, when key and slug are the same)
  1. After normalizing the name (full-width to half-width, strip spaces and punctuation, lowercase), identical to one of name_zh / name_en / aliases
  2. Compare again after removing a trailing parenthetical note (e.g. 「（禁用）」 (banned), 「（轻重量）」 (light weight))
If one exercise matches several in the same tier -> ambiguous, not written; the stronger tier claims first.
If app_exercise_id is already filled: leave it if identical; if different, do not overwrite and list as a conflict (manual edits win).
"""
import re
import unicodedata

import psycopg

from .. import sparky_client
from . import store

# Known banned / caution exercise IDs of user 1 in SparkyFitness (verified by the maintainer), cross-checked after matching
KNOWN_IDS = {
    "upright_row": "76b3e962-96e8-495d-a647-47ab4be59d6e",
    "behind_neck_press": "f9933ea9-1f23-40df-a139-30c0cd28385e",
    "behind_neck_pulldown": "6c68fe8d-eff9-4b0e-b69a-0fe68d34865f",
    "dips": "bebe100c-9e6a-493f-a57c-cd789ed34e41",
    "cable_face_pull": "a9436fe3-f896-4ef4-8df5-c7e9edeaf8ac",
}
TIER_LABELS = {0: "source_id", 1: "名称 / 别名", 2: "去掉括号注释后的名称"}
_PAREN_TAIL = re.compile(r"[（(][^（()）]*[)）]\s*$")


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\s/+·・,，、.。:：\-_'\"“”‘’()（）\[\]]", "", s)


def strip_note(s: str | None) -> str:
    return _PAREN_TAIL.sub("", s or "").strip()


def match(meta: list[dict], sparky: list[dict]) -> dict:
    """Pure function: returns {pairs: [(ref, sparky_id, tier)], ambiguous: [...]}."""
    names = {m["exercise_ref"]: {norm(x) for x in [m.get("name_zh"), m.get("name_en"), *(m.get("aliases") or [])] if x}
             for m in meta}
    cands: list[tuple[int, str, str]] = []
    for e in sparky:
        sid = e.get("source_id") or ""
        full, short = norm(e["name"]), norm(strip_note(e["name"]))
        for ref, ns in names.items():
            if sid == f"bm:{ref}":
                cands.append((0, ref, e["id"]))
            elif full in ns:
                cands.append((1, ref, e["id"]))
            elif short and short != full and short in ns:
                cands.append((2, ref, e["id"]))
    pairs, ambiguous, used_ref, used_id = [], [], set(), set()
    for tier in sorted({c[0] for c in cands}):
        level = [(r, i) for t, r, i in cands if t == tier and r not in used_ref and i not in used_id]
        by_ref, by_id = {}, {}
        for r, i in level:
            by_ref.setdefault(r, set()).add(i)
            by_id.setdefault(i, set()).add(r)
        for r, i in level:
            if len(by_ref[r]) > 1 or len(by_id[i]) > 1:
                ambiguous.append({"exercise_ref": r, "sparky_id": i, "tier": tier})
                continue
            pairs.append((r, i, tier))
            used_ref.add(r)
            used_id.add(i)
    return {"pairs": pairs, "ambiguous": [a for a in ambiguous if a["exercise_ref"] not in used_ref
                                          and a["sparky_id"] not in used_id]}


def run(conn: psycopg.Connection, user_id: int, client: sparky_client.SparkyClient | None = None,
        dry_run: bool = False) -> dict:
    client = client or sparky_client.reader_for_user(conn, user_id)
    sparky = sparky_client.exercises(client)
    by_id = {e["id"]: e for e in sparky}
    meta = store.list_exercises(conn, user_id)
    meta_by_ref = {m["exercise_ref"]: m for m in meta}
    owner_of = {m["app_exercise_id"]: m["exercise_ref"] for m in meta if m.get("app_exercise_id")}
    res = match(meta, sparky)
    report = {"user_id": user_id, "dry_run": dry_run, "sparky_exercises": len(sparky), "exercise_meta": len(meta),
              "set": [], "unchanged": [], "conflicts": [], "ambiguous": res["ambiguous"], "known_id_check": []}
    for ref, sid, tier in sorted(res["pairs"]):
        cur = meta_by_ref[ref].get("app_exercise_id")
        row = {"exercise_ref": ref, "name_zh": meta_by_ref[ref].get("name_zh"), "sparky_id": sid,
               "sparky_name": by_id[sid]["name"], "matched_by": TIER_LABELS[tier]}
        if cur == sid:
            report["unchanged"].append(row)
        elif cur:
            report["conflicts"].append({**row, "current_app_exercise_id": cur, "why": "已经填了别的 ID，不覆盖"})
        elif sid in owner_of and owner_of[sid] != ref:
            report["conflicts"].append({**row, "why": f"这个 ID 已经属于 {owner_of[sid]}"})
        else:
            if not dry_run:
                store.update_exercise(conn, user_id, ref, {"app_exercise_id": sid})
            report["set"].append(row)
    linked_refs = {r["exercise_ref"] for r in report["set"] + report["unchanged"]}
    linked_ids = {r["sparky_id"] for r in report["set"] + report["unchanged"]} | set(owner_of)
    report["unmatched_meta"] = [{"exercise_ref": m["exercise_ref"], "name_zh": m.get("name_zh"), "status": m["status"]}
                                for m in meta if m["exercise_ref"] not in linked_refs and not m.get("app_exercise_id")]
    report["unmatched_sparky"] = [{"sparky_id": e["id"], "name": e["name"], "category": e.get("category"),
                                   "status_in_description": next((s for k, s in (("【禁用】", "banned"), ("【注意】", "caution"))
                                                                  if (e.get("description") or "").startswith(k)), None)}
                                  for e in sparky if e["id"] not in linked_ids]
    final = {r["exercise_ref"]: r["sparky_id"] for r in report["set"] + report["unchanged"]}
    for ref, sid in KNOWN_IDS.items():
        if sid in by_id:   # only check this user's own exercise library
            report["known_id_check"].append({"exercise_ref": ref, "expected": sid, "linked": final.get(ref) or
                                             (meta_by_ref.get(ref) or {}).get("app_exercise_id"),
                                             "ok": (final.get(ref) or (meta_by_ref.get(ref) or {}).get("app_exercise_id")) == sid})
    return report


def format_report(r: dict) -> str:
    lines = [f"用户 {r['user_id']}：SparkyFitness 动作 {r['sparky_exercises']} 个，exercise_meta {r['exercise_meta']} 个"
             + ("（dry run，没写库）" if r["dry_run"] else ""),
             f"新写入 {len(r['set'])}、已对上 {len(r['unchanged'])}、冲突 {len(r['conflicts'])}、歧义 {len(r['ambiguous'])}"]
    for k, title in (("set", "新写入"), ("unchanged", "已对上")):
        for x in r[k]:
            lines.append(f"  {title} {x['exercise_ref']:<30} ← {x['sparky_name']}（{x['matched_by']}）")
    for x in r["conflicts"]:
        lines.append(f"  冲突 {x['exercise_ref']} ← {x['sparky_name']}：{x['why']}")
    for x in r["ambiguous"]:
        lines.append(f"  歧义 {x['exercise_ref']} ↔ {x['sparky_id']}（{TIER_LABELS[x['tier']]}）")
    lines.append(f"exercise_meta 没对上（{len(r['unmatched_meta'])}）：" +
                 ("、".join(f"{x['exercise_ref']}（{x['name_zh']}）" for x in r["unmatched_meta"]) or "无"))
    lines.append(f"SparkyFitness 动作没对上（{len(r['unmatched_sparky'])}）：" +
                 ("、".join(x["name"] + (f"[{x['status_in_description']}]" if x["status_in_description"] else "")
                           for x in r["unmatched_sparky"]) or "无"))
    for x in r["known_id_check"]:
        lines.append(f"  核对 {x['exercise_ref']}: {'OK' if x['ok'] else '不一致'}（期望 {x['expected']}，实际 {x['linked']}）")
    return "\n".join(lines)
