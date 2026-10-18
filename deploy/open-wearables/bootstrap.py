"""Open Wearables initialization after the first start, run inside the app container (which already has all config plus boto3 / httpx).

  bucket   create the S3 bucket (for chunked export.xml upload), skipped if it exists
  api-key  log in with the admin account, create a new API key, and print only the key itself (bootstrap.sh writes it to secrets/)

Called by deploy/open-wearables/bootstrap.sh; do not run it directly.
"""
import sys

import httpx

from app.config import settings
from app.services.s3_client import create_s3_client

API = "http://localhost:8000/api/v1"


def bucket() -> None:
    s3 = create_s3_client()
    names = {b["Name"] for b in s3.list_buckets()["Buckets"]}
    if settings.aws_bucket_name in names:
        print(f"bucket 已存在：{settings.aws_bucket_name}", file=sys.stderr)
        return
    s3.create_bucket(Bucket=settings.aws_bucket_name)
    print(f"bucket 已创建：{settings.aws_bucket_name}", file=sys.stderr)


def api_key(name: str) -> None:
    login = httpx.post(f"{API}/auth/login", data={
        "username": settings.admin_email, "password": settings.admin_password.get_secret_value()})
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = httpx.post(f"{API}/developer/api-keys", headers=headers, json={"name": name})
    r.raise_for_status()
    print(r.json()["key"])


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "bucket":
        bucket()
    elif cmd == "api-key":
        api_key(sys.argv[2])
    else:
        raise SystemExit(f"unknown command: {cmd}")
