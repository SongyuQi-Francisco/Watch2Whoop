"""Unified names, standard units and conversions for lab tests, used by add_lab_results (photo of a lab report -> structured -> write lab_results).

test_key matches what md_seed.py migrated in (uric_acid, triglycerides, alt...), so successive results form a trend.
Units are unified to the SI units common on Australian lab reports; when a report uses other units (mg/dL etc.), convert by factor, converting the reference range too.
plausible is a wide range of "values a living person could measure"; anything outside it is most likely a misread unit or decimal point and is rejected outright.
"""
import re

# test_key: (Chinese name, standard unit, category, aliases, {other unit: factor -> standard unit}, (plausible low, plausible high))
CATALOG: dict[str, tuple] = {
    "uric_acid": ("尿酸", "μmol/L", "blood", ["urate", "uric acid", "血尿酸"], {"mmol/L": 1000, "mg/dL": 59.48}, (50, 1500)),
    "triglycerides": ("甘油三酯", "mmol/L", "blood", ["tg", "trig", "triglyceride", "甘油三脂"], {"mg/dL": 1 / 88.57}, (0.1, 50)),
    "total_cholesterol": ("总胆固醇", "mmol/L", "blood", ["cholesterol", "chol", "tc", "total chol"], {"mg/dL": 1 / 38.67}, (1, 20)),
    "ldl": ("低密度脂蛋白胆固醇", "mmol/L", "blood", ["ldl-c", "ldl cholesterol", "低密度脂蛋白", "ldl-c (calc)"], {"mg/dL": 1 / 38.67}, (0.1, 15)),
    "hdl": ("高密度脂蛋白胆固醇", "mmol/L", "blood", ["hdl-c", "hdl cholesterol", "高密度脂蛋白"], {"mg/dL": 1 / 38.67}, (0.1, 5)),
    "non_hdl": ("非高密度脂蛋白胆固醇", "mmol/L", "blood", ["non-hdl", "non hdl cholesterol"], {"mg/dL": 1 / 38.67}, (0.1, 18)),
    "chol_hdl_ratio": ("总胆固醇 / HDL 比值", None, "blood", ["chol/hdl ratio", "tc/hdl"], {}, (0.5, 20)),
    "alt": ("ALT（谷丙转氨酶）", "U/L", "blood", ["alanine aminotransferase", "谷丙转氨酶", "sgpt", "alt (sgpt)"], {"IU/L": 1}, (1, 5000)),
    "ast": ("AST（谷草转氨酶）", "U/L", "blood", ["aspartate aminotransferase", "谷草转氨酶", "sgot"], {"IU/L": 1}, (1, 5000)),
    "ggt": ("GGT（γ-谷氨酰转移酶）", "U/L", "blood", ["gamma gt", "γ-gt", "谷氨酰转肽酶", "gamma-glutamyl transferase"], {"IU/L": 1}, (1, 3000)),
    "alp": ("ALP（碱性磷酸酶）", "U/L", "blood", ["alkaline phosphatase", "碱性磷酸酶", "alk phos"], {"IU/L": 1}, (5, 3000)),
    "bilirubin_total": ("总胆红素", "μmol/L", "blood", ["bilirubin", "total bilirubin", "tbil"], {"mg/dL": 17.1}, (1, 600)),
    "albumin": ("白蛋白", "g/L", "blood", ["alb"], {"g/dL": 10}, (10, 70)),
    "total_protein": ("总蛋白", "g/L", "blood", ["protein total", "tp"], {"g/dL": 10}, (30, 120)),
    "glucose_fasting": ("空腹血糖", "mmol/L", "blood", ["fasting glucose", "glucose (fasting)", "fbg", "glucose", "血糖"], {"mg/dL": 1 / 18.016}, (1, 40)),
    "hba1c": ("糖化血红蛋白 HbA1c", "%", "blood", ["a1c", "glycated haemoglobin", "glycated hemoglobin", "糖化血红蛋白"], {}, (3, 20)),
    "insulin_fasting": ("空腹胰岛素", "mU/L", "blood", ["insulin", "fasting insulin"], {"μIU/mL": 1, "uIU/mL": 1, "pmol/L": 1 / 6}, (0.5, 300)),
    "creatinine": ("肌酐", "μmol/L", "blood", ["creat", "血肌酐", "serum creatinine"], {"mg/dL": 88.4}, (20, 2000)),
    "egfr": ("eGFR", "mL/min/1.73m²", "blood", ["e-gfr", "estimated gfr", "肾小球滤过率"], {"mL/min/1.73m2": 1}, (5, 200)),
    "urea": ("尿素", "mmol/L", "blood", ["bun", "urea nitrogen", "血尿素"], {"mg/dL": 1 / 2.8}, (0.5, 60)),
    "sodium": ("钠", "mmol/L", "blood", ["na"], {}, (100, 180)),
    "potassium": ("钾", "mmol/L", "blood", ["k"], {}, (1.5, 9)),
    "crp": ("C 反应蛋白", "mg/L", "blood", ["c-reactive protein", "hs-crp", "hscrp", "c反应蛋白"], {"mg/dL": 10}, (0, 500)),
    "ferritin": ("铁蛋白", "μg/L", "blood", ["ferr"], {"ng/mL": 1}, (1, 5000)),
    "tsh": ("TSH（促甲状腺激素）", "mIU/L", "blood", ["促甲状腺激素", "thyroid stimulating hormone"], {"μIU/mL": 1, "uIU/mL": 1}, (0.005, 150)),
    "free_t4": ("游离 T4", "pmol/L", "blood", ["ft4", "free thyroxine"], {"ng/dL": 12.87}, (1, 100)),
    "haemoglobin": ("血红蛋白", "g/L", "blood", ["hb", "hgb", "hemoglobin", "haemoglobin"], {"g/dL": 10}, (40, 250)),
    "wbc": ("白细胞", "×10⁹/L", "blood", ["white cell count", "white blood cells", "wcc"], {"×10^9/L": 1, "x10^9/L": 1, "10^9/L": 1}, (0.5, 100)),
    "lymphocyte_pct": ("淋巴细胞百分比", "%", "blood", ["lymphocytes %", "lymph%"], {}, (1, 90)),
    "platelets": ("血小板", "×10⁹/L", "blood", ["plt", "platelet count"], {"×10^9/L": 1, "x10^9/L": 1, "10^9/L": 1}, (5, 2000)),
    "mcv": ("平均红细胞体积 MCV", "fL", "blood", [], {}, (50, 150)),
    "rdw": ("红细胞分布宽度 RDW", "%", "blood", [], {}, (8, 30)),
    "vitamin_d": ("维生素 D（25-OH）", "nmol/L", "blood", ["25-oh vitamin d", "vit d", "25(oh)d"], {"ng/mL": 2.496}, (5, 400)),
    "vitamin_b12": ("维生素 B12", "pmol/L", "blood", ["b12", "active b12"], {"pg/mL": 0.738}, (20, 3000)),
    "testosterone": ("睾酮", "nmol/L", "blood", ["total testosterone"], {"ng/dL": 0.0347}, (0.1, 80)),
    "body_weight": ("体重", "kg", "anthropometry", ["weight"], {}, (30, 250)),
    "bmi": ("BMI", "kg/m²", "anthropometry", [], {"kg/m2": 1}, (10, 70)),
    "blood_pressure_systolic": ("收缩压", "mmHg", "anthropometry", ["sbp", "systolic"], {}, (60, 260)),
    "blood_pressure_diastolic": ("舒张压", "mmHg", "anthropometry", ["dbp", "diastolic"], {}, (30, 160)),
}


def _norm(s: str | None) -> str:
    return re.sub(r"[\s（）()]", "", (s or "").lower())


_BY_NAME: dict[str, str] = {}
for _k, (_zh, _u, _c, _aliases, _conv, _r) in CATALOG.items():
    for _n in (_k, _zh, *_aliases):
        _BY_NAME[_norm(_n)] = _k


def resolve_key(name: str, given_key: str | None, existing_keys: set[str]) -> str | None:
    """Name on the lab report -> test_key. Order: an explicitly given key (in the catalog, already in the database, or a new valid slug) -> name / alias."""
    if given_key:
        if given_key in CATALOG or given_key in existing_keys or re.fullmatch(r"[a-z][a-z0-9_]*", given_key):
            return given_key
        raise ValueError(f"test_key {given_key!r} 只能是小写字母、数字、下划线")
    return _BY_NAME.get(_norm(name))


def normalize_unit(key: str, value: float | None, unit: str | None, lo: float | None, hi: float | None):
    """Convert to the standard unit (value and reference range together). Returns (value, unit, ref_low, ref_high, note or None)."""
    if key not in CATALOG or unit is None:
        return value, unit, lo, hi, None
    std = CATALOG[key][1]
    u = unit.strip().replace("umol", "μmol").replace("µ", "μ")
    if std is None or u == std or u.lower() == std.lower():
        return value, std or u, lo, hi, None
    conv = {k.lower(): v for k, v in CATALOG[key][4].items()}
    if u.lower() not in conv:
        raise ValueError(f"{CATALOG[key][0]} 的单位 {unit!r} 不认识（标准单位 {std}）；核对化验单，或在 note 里写明后用标准单位")
    f = conv[u.lower()]
    r = lambda x: None if x is None else round(x * f, 4)
    return r(value), std, r(lo), r(hi), f"{unit} → {std}（× {f:.4g}）"


def check_plausible(key: str, value: float | None) -> None:
    if value is None or key not in CATALOG:
        return
    lo, hi = CATALOG[key][5]
    if not lo <= value <= hi:
        raise ValueError(f"{CATALOG[key][0]} = {value} {CATALOG[key][1]} 不在可能的范围 {lo}–{hi}，"
                         "多半是单位或小数点认错了，核对化验单")


def add_results(conn, user_id: int, collected_on, lab: str | None, results: list[dict], overwrite: bool,
                source: str = "lab", fasting: bool | None = None) -> dict:
    """Write several results of one lab report (or one home meter reading) at once (only for user_id itself).
    source: lab = lab report / home_device = home meter; fasting: True / False / None (unknown), one value for the whole draw.
    If the same item already exists on the same day from the same source: same value -> skip (a newly given fasting is filled in);
    different -> do not overwrite (only with overwrite=true). If flag is not given, compute it from the reference range.
    The status cards are refreshed straight away (assistant/status.py sync_labs)."""
    from . import lab_params, store
    from ..assistant import status as status_cards
    if not results:
        raise ValueError("results 不能为空")
    if source not in lab_params.SOURCES:
        raise ValueError(f"source 只能是 {' / '.join(lab_params.SOURCES)}")
    existing_keys = {r["test_key"] for r in conn.execute(
        "SELECT DISTINCT test_key FROM lab_results WHERE user_id = %s AND test_key IS NOT NULL", (user_id,))}
    rows, notes = [], []
    for r in results:
        key = resolve_key(r["test"], r.get("test_key"), existing_keys)
        if key is None:
            raise ValueError(f"「{r['test']}」对不上统一名称，给它一个 test_key（英文小写 + 下划线，例 apolipoprotein_b）")
        if r.get("value") is None and not r.get("value_text"):
            raise ValueError(f"{r['test']}：value 和 value_text 至少要有一个")
        value, unit, lo, hi, conv = normalize_unit(key, r.get("value"), r.get("unit"), r.get("ref_low"), r.get("ref_high"))
        check_plausible(key, value)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"{r['test']}：参考范围下限大于上限")
        if conv:
            notes.append(f"{CATALOG[key][0]}：{conv}")
        fields = {"collected_on": collected_on, "test_key": key,
                  "test_name": CATALOG[key][0] if key in CATALOG else r["test"].strip(),
                  "category": CATALOG[key][2] if key in CATALOG else (r.get("category") or "blood"),
                  "value": value, "value_text": r.get("value_text"), "unit": unit, "ref_low": lo, "ref_high": hi,
                  "flag": r.get("flag"), "lab": lab, "source": source, "fasting": fasting,
                  "note": "; ".join(x for x in (r.get("note"), f"化验单原名：{r['test']}" if key in CATALOG
                                                and _norm(r["test"]) != _norm(CATALOG[key][0]) else None) if x) or None}
        if fields["flag"] is None:
            status = store.compare_to_range(fields)
            fields["flag"] = {"high": "H", "low": "L", "normal": "normal"}.get(status)
        rows.append(fields)
    keys = [f["test_key"] for f in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("同一张化验单里有重复的项目：" + ", ".join(sorted({k for k in keys if keys.count(k) > 1})))
    out = {"created": [], "updated": [], "unchanged": [], "conflicts": [], "unit_conversions": notes}
    for f in rows:
        cur = conn.execute("""SELECT * FROM lab_results WHERE user_id = %s AND collected_on = %s AND test_key = %s
                              AND source = %s ORDER BY id DESC LIMIT 1""",
                           (user_id, collected_on, f["test_key"], source)).fetchone()
        brief = {"test_key": f["test_key"], "test_name": f["test_name"], "value": f["value"],
                 "value_text": f["value_text"], "unit": f["unit"], "flag": f["flag"]}
        same = cur is not None and cur["value"] == f["value"] and (cur["value_text"] or None) == (f["value_text"] or None)
        if cur is None:
            row = store.add_lab(conn, user_id, f, import_key=f"assistant:{collected_on}:{f['test_key']}:{source}")
            if row is None:
                raise ValueError(f"{f['test_name']}：{collected_on} 已经写过一次又被删改过，先 delete_lab_results 再写")
            out["created"].append({**brief, "id": row["id"]})
        elif same and (fasting is None or cur["fasting"] == fasting):
            out["unchanged"].append(brief)
        elif same:
            store.update_lab(conn, user_id, cur["id"], {"fasting": fasting})
            out["updated"].append({**brief, "id": cur["id"], "fasting": fasting})
        elif overwrite:
            store.update_lab(conn, user_id, cur["id"], {k: v for k, v in f.items() if k != "collected_on"})
            out["updated"].append({**brief, "id": cur["id"], "previous": cur["value"] if cur["value"] is not None
                                   else cur["value_text"]})
        else:
            out["conflicts"].append({**brief, "id": cur["id"], "existing": cur["value"] if cur["value"] is not None
                                     else cur["value_text"]})
    if out["conflicts"]:
        out["note"] = "conflicts 里的项目这天已经有不同的结果，没覆盖；确认化验单无误再用 overwrite=true"
    if out["created"] or out["updated"]:
        status_cards.sync_labs(conn, user_id)
    return out


def delete_results(conn, user_id: int, collected_on, test_keys: list[str]) -> dict:
    """Undo lab results written by the butler (import_key starting with assistant:); migrated and manually added ones are not deleted."""
    if not test_keys:
        raise ValueError("test_keys 不能为空")
    rows = conn.execute("""SELECT id, test_key, import_key FROM lab_results
                           WHERE user_id = %s AND collected_on = %s AND test_key = ANY(%s)""",
                        (user_id, collected_on, test_keys)).fetchall()
    others = [r["test_key"] for r in rows if not (r["import_key"] or "").startswith("assistant:")]
    if others:
        raise PermissionError(f"{', '.join(others)} 不是身体管家写的，不删（请在数据库 / API 里改）")
    missing = sorted(set(test_keys) - {r["test_key"] for r in rows})
    if missing:
        raise ValueError(f"{collected_on} 没有这些项目：{', '.join(missing)}")
    conn.execute("DELETE FROM lab_results WHERE id = ANY(%s)", ([r["id"] for r in rows],))
    from ..assistant import status
    status.sync_labs(conn, user_id)
    return {"deleted": {"collected_on": str(collected_on), "test_keys": sorted({r["test_key"] for r in rows})}}
