#!/usr/bin/env python3
"""Workaround for when Docker Desktop's `docker pull` hangs: download straight from the registry on this machine, then `docker load`.

Seen since 2026-10-16: the daemon hangs when it reaches the registry through the built-in proxy
http.docker.internal:3128, although this machine can reach the internet directly.
Standard library only: fetch the image index (multi-platform manifest) by tag@digest, then the manifest,
config and layers for this platform (linux/arm64), assemble an OCI-layout tar and `docker load` it. The index
digest stays unchanged, so `image:version@sha256:...` references in compose resolve to this local copy
(when Docker Desktop uses the containerd image store).

Usage: python3 deploy/ops/fetch_image.py grafana/grafana:13.2.4@sha256:<index digest> [--platform linux/arm64]
Only public images on Docker Hub and ghcr.io are supported. Every file is checked against its sha256.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

INDEX_TYPES = {"application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"}
ACCEPT = ",".join([*INDEX_TYPES, "application/vnd.oci.image.manifest.v1+json",
                   "application/vnd.docker.distribution.manifest.v2+json"])


def parse_ref(ref: str) -> tuple:
    name, _, digest = ref.partition("@")
    name, _, tag = name.rpartition(":") if ":" in name.split("/")[-1] else (name, "", "latest")
    registry = "registry-1.docker.io"
    if name.split("/")[0] in ("ghcr.io",):
        registry, _, name = name.partition("/")
    elif "/" not in name:
        name = f"library/{name}"
    return registry, name, tag, digest


class Registry:
    def __init__(self, registry: str, repo: str):
        self.base = f"https://{registry}/v2/{repo}"
        if registry == "registry-1.docker.io":
            url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
        else:
            url = f"https://{registry}/token?scope=repository:{repo}:pull"
        with urllib.request.urlopen(url, timeout=30) as r:
            self.token = json.load(r)["token"]

    def get(self, path: str, accept: str | None = None) -> tuple:
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {self.token}",
                                                                **({"Accept": accept} if accept else {})})
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read(), r.headers.get("Content-Type", "")


def check(data: bytes, digest: str) -> bytes:
    got = "sha256:" + hashlib.sha256(data).hexdigest()
    if got != digest:
        raise SystemExit(f"校验失败：期望 {digest}，实际 {got}")
    return data


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("ref", help="image:version@sha256:index-digest (the digest may be omitted; it is then printed for you to verify)")
    p.add_argument("--platform", default="linux/arm64")
    p.add_argument("--keep", help="keep the OCI tar at this path (default: delete it when done)")
    args = p.parse_args()

    registry, repo, tag, digest = parse_ref(args.ref)
    reg = Registry(registry, repo)
    top, ctype = reg.get(f"/manifests/{digest or tag}", ACCEPT)
    top_digest = "sha256:" + hashlib.sha256(top).hexdigest()
    if digest:
        check(top, digest)
    print(f"{repo}:{tag} → {top_digest}（{ctype}）")
    os_, arch = args.platform.split("/")[:2]
    blobs = {top_digest: top}
    top_json = json.loads(top)
    if ctype in INDEX_TYPES or top_json.get("manifests"):
        match = [m for m in top_json["manifests"]
                 if m.get("platform", {}).get("os") == os_ and m.get("platform", {}).get("architecture") == arch]
        if not match:
            raise SystemExit(f"索引里没有 {args.platform}")
        mdesc = match[0]
        manifest = check(reg.get(f"/manifests/{mdesc['digest']}", mdesc["mediaType"])[0], mdesc["digest"])
        blobs[mdesc["digest"]] = manifest
    else:
        manifest = top
    man = json.loads(manifest)
    for d in [man["config"], *man["layers"]]:
        size = d.get("size", 0)
        print(f"  下载 {d['digest'][:19]}… {size / 1e6:.1f}MB", flush=True)
        blobs[d["digest"]] = check(reg.get(f"/blobs/{d['digest']}")[0], d["digest"])

    full_name = ("docker.io/" if registry == "registry-1.docker.io" else f"{registry}/") + repo
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
             "manifests": [{"mediaType": ctype.split(";")[0] or "application/vnd.oci.image.index.v1+json",
                            "digest": top_digest, "size": len(top),
                            "annotations": {"io.containerd.image.name": f"{full_name}:{tag}",
                                            "org.opencontainers.image.ref.name": tag}}]}
    out = Path(args.keep) if args.keep else Path(tempfile.mkstemp(suffix=".tar")[1])
    with tarfile.open(out, "w") as tar:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add("oci-layout", json.dumps({"imageLayoutVersion": "1.0.0"}).encode())
        add("index.json", json.dumps(index).encode())
        for d, data in blobs.items():
            add(f"blobs/sha256/{d.split(':')[1]}", data)
    print(f"docker load（{out.stat().st_size / 1e6:.0f}MB）…", flush=True)
    r = subprocess.run(["docker", "load", "-i", str(out)], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if not args.keep:
        out.unlink()
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
