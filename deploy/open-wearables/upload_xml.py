#!/usr/bin/env python3
"""Uploads a full Apple Health export (export.xml) to Open Wearables in chunks and triggers the import.

The OW direct-upload endpoint (/import/apple/xml/direct) reads the whole file into memory and stuffs it into a Celery message, which fails for a 700MB file,
so we use S3 multipart upload (create → sign → PUT each part → complete); storage is the SeaweedFS in compose.override.yaml.
Standard library only. The API key is read from secrets/open_wearables_api_key.

Usage (repo root):
  python3 deploy/open-wearables/upload_xml.py --ow-user-id <UUID> health_data/full_export/apple_health_export/export.xml
OW user UUID: cd body_engine && uv run body-engine ow-link --user-id 1
"""
import argparse
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def call(method: str, url: str, key: str, body: dict | None = None, timeout: float = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-Open-Wearables-API-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {url} → {e.code}: {e.read()[:500].decode(errors='replace')}")


def put_part(url: str, body: bytes) -> str:
    req = urllib.request.Request(url, data=body, method="PUT")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=1800) as r:
                return r.headers["ETag"].strip()
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 2:
                raise
            print(f"  重试（{e}）")
            time.sleep(5)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("xml", type=Path)
    p.add_argument("--ow-user-id", required=True)
    p.add_argument("--api-url", default="http://127.0.0.1:8200")
    p.add_argument("--no-wait", action="store_true", help="do not wait for the import to finish after uploading")
    args = p.parse_args()

    key = (ROOT / "secrets/open_wearables_api_key").read_text().strip()
    size = args.xml.stat().st_size
    base = f"{args.api_url}/api/v1/users/{args.ow_user_id}/import/apple/xml/s3/multipart"

    created = call("POST", f"{base}/create", key,
                   {"filename": args.xml.name, "content_type": "application/xml", "file_size": size})
    part_size = created["part_size"]
    n = math.ceil(size / part_size)
    print(f"{args.xml.name}: {size / 1e6:.0f} MB，{n} 个分片 × {part_size / 1e6:.0f} MB")
    try:
        signed = call("POST", f"{base}/sign", key, {
            "key": created["key"], "upload_id": created["upload_id"], "part_numbers": list(range(1, n + 1))})
        urls = {u["part_number"]: u["url"] for u in signed["urls"]}
        parts = []
        with args.xml.open("rb") as f:
            for i in range(1, n + 1):
                parts.append({"part_number": i, "etag": put_part(urls[i], f.read(part_size))})
                print(f"  分片 {i}/{n} 完成")
    except BaseException:
        call("POST", f"{base}/abort", key, {"key": created["key"], "upload_id": created["upload_id"]})
        raise
    result = call("POST", f"{base}/complete", key,
                  {"key": created["key"], "upload_id": created["upload_id"], "parts": parts})
    task_id = result.get("task_id")
    print(f"{result['status']}，task_id={task_id}")
    if args.no_wait or not task_id:
        return

    # Wait for the import to finish: in sync runs, run_id = task_id
    runs_url = f"{args.api_url}/api/v1/users/{args.ow_user_id}/sync/runs"
    started = time.time()
    while True:
        time.sleep(30)
        run = next((r for r in call("GET", runs_url, key) if r.get("run_id") == task_id), None)
        status = run.get("status") if run else "pending"
        progress = f"{run.get('stage')} {run.get('items_processed')}/{run.get('items_total')}" if run else ""
        print(f"  {int(time.time() - started)}s：{status} {progress}")
        if status not in ("pending", "in_progress"):
            print(json.dumps(run, ensure_ascii=False, indent=2))
            return


if __name__ == "__main__":
    main()
