"""Heartbeat series and ECG recordings from an Apple Health export (export.zip or the extracted export.xml).

- importers/apple_zip.py calls heartbeat_from_record() for every HRV record and import_ecgs() after parsing, so the history
  import of new users gets them too;
- run() is the light version for people whose history came in another way (the owner's went through OW's XML import):
  it only reads HRV records and ECG files and touches no other table (`body-engine import-raw --user-id N --file export.zip`);
- slim() writes a small zip with just those (HRV records with their beats, ECG csv without the name / birth-date lines), so a 700 MB
  export does not have to be copied to the server: `body-engine raw-slim --file export.xml --out raw.zip` (about 2 MB per year).
"""
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import psycopg

from . import ecg as E
from . import hrv as H
from . import store

HRV_TYPE = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
TS_FORMAT = "%Y-%m-%d %H:%M:%S %z"
BATCH = 2000


def heartbeat_from_record(el: ET.Element) -> dict | None:
    """An HRV <Record> with its <HeartRateVariabilityMetadataList> -> a save_heartbeats() item."""
    lst = el.find("HeartRateVariabilityMetadataList")
    if lst is None:
        return None
    beats = H.beats_from_clock([b.get("time") or "" for b in lst.findall("InstantaneousBeatsPerMinute")])
    if len(beats) < 2:
        return None
    try:
        start = datetime.strptime(el.get("startDate"), TS_FORMAT)
        end = datetime.strptime(el.get("endDate"), TS_FORMAT)
    except (TypeError, ValueError):
        return None
    try:
        apple = float(el.get("value"))
    except (TypeError, ValueError):
        apple = None
    return {"start_at": start, "end_at": max(end, start), "source": el.get("sourceName") or "", "origin": "apple_export",
            "beats_s": beats, "apple_sdnn": apple}


def ecg_files(path: Path):
    """Yield (name, csv text) for every electrocardiograms/*.csv in the zip, or next to export.xml."""
    with open(path, "rb") as f:
        is_zip = f.read(2) == b"PK"
    if is_zip:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                parts = info.filename.lower().split("/")
                if len(parts) >= 2 and parts[-2] == "electrocardiograms" and parts[-1].endswith(".csv"):
                    if info.file_size > 2_000_000:
                        continue
                    yield info.filename, zf.read(info).decode("utf-8-sig", "replace")
    else:
        d = path.parent / "electrocardiograms"
        if d.is_dir():
            for p in sorted(d.glob("*.csv")):
                if p.stat().st_size <= 2_000_000:
                    yield p.name, p.read_text(encoding="utf-8-sig", errors="replace")


def import_ecgs(conn: psycopg.Connection, user_id: int, path: Path, import_id: int | None = None) -> dict:
    n, bad = 0, []
    for name, text in ecg_files(path):
        try:
            e = E.parse_csv(text)
        except E.EcgError as err:
            bad.append(f"{Path(name).name}: {err}")
            continue
        with conn.transaction():
            store.save_ecg(conn, user_id, e, "apple_export", import_id)
        n += 1
    return {"ecg": n, "ecg_skipped": bad}


def run(conn: psycopg.Connection, user_id: int, path: Path, import_id: int | None = None) -> dict:
    from ..importers import apple_zip   # avoid a cycle: apple_zip imports this module
    raw, _size, close = apple_zip.open_export(path)
    items, n = [], 0
    try:
        for el in apple_zip.iter_elements(io.BufferedReader(raw, 1 << 20)):
            if el.tag == "Record" and el.get("type") == HRV_TYPE:
                it = heartbeat_from_record(el)
                if it:
                    items.append(it)
                if len(items) >= BATCH:
                    n += store.save_heartbeats(conn, user_id, items, import_id)
                    items.clear()
    finally:
        close()
    n += store.save_heartbeats(conn, user_id, items, import_id)
    return {"heartbeat_series": n, **import_ecgs(conn, user_id, path, import_id)}


PII_LINE = re.compile(r"^(name|date of birth|姓名|名字|出生日期)\s*(,|$)", re.I)


def slim(path: Path, out: Path) -> dict:
    """export.zip / export.xml -> out (zip): ExportDate + HRV records with beat lists + ECG csv files without PII lines."""
    from ..importers import apple_zip
    raw, _size, close = apple_zip.open_export(path)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        with z.open("apple_health_export/export.xml", "w") as f:
            f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<HealthData>\n')
            try:
                for el in apple_zip.iter_elements(io.BufferedReader(raw, 1 << 20)):
                    if el.tag == "ExportDate" or (el.tag == "Record" and el.get("type") == HRV_TYPE
                                                  and el.find("HeartRateVariabilityMetadataList") is not None):
                        el.tail = "\n"
                        f.write(ET.tostring(el))
                        n += el.tag == "Record"
            finally:
                close()
            f.write(b"</HealthData>\n")
        ecgs = 0
        for name, text in ecg_files(path):
            clean = "\n".join(line for line in text.splitlines() if not PII_LINE.match(line.strip()))
            z.writestr(f"apple_health_export/electrocardiograms/{Path(name).name}", clean + "\n")
            ecgs += 1
    return {"hrv_records": n, "ecg": ecgs, "out": str(out), "bytes": out.stat().st_size}
