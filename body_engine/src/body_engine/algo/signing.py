"""Ed25519 signatures of the algorithm bundle (#340 N2; docs/ios-native-app.md section 6.3).

The maintainer signs on their own machine; the private key never goes into git or onto a server:
  body-engine algo-keygen --out secrets/algo_signing_key      # once per key; prints the public key
  node packages/openbodyscore-js/build.js                     # dist/ (drops a signature that no longer matches)
  body-engine algo-sign --key secrets/algo_signing_key        # writes signature + key_id into dist/manifest.json
then commit dist/ and deploy. The app has the public keys built in (apps/ios/.../AlgoSigningKeys.swift: current and
next, the same values as packages/openbodyscore-js/signing-keys.json) and refuses an unsigned or badly signed bundle in
Release builds. The signature covers the bytes of dist/openbodyscore.js; key_id = first 16 hex digits of sha256 of the
raw 32-byte public key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import FILE, bundle, dist_dir

KEYS_FILE = Path(__file__).resolve().parents[4] / "packages" / "openbodyscore-js" / "signing-keys.json"


def raw_public(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} is not an Ed25519 private key")
    return key


def keygen(out: Path) -> dict:
    """A new private key in `out` (PEM, mode 0600; never overwrites). Returns its public key for signing-keys.json."""
    if out.exists():
        raise FileExistsError(f"{out} already exists (a signing key is never overwritten)")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    raw = raw_public(key.public_key())
    return {"key_id": key_id(raw), "public_key": base64.b64encode(raw).decode()}


def sign_dist(key_path: Path, dist: Path | None = None) -> dict:
    """Sign dist/openbodyscore.js and write signature + key_id into dist/manifest.json (kept formatted as build.js
    writes it). The manifest must already match the file (sha256, size, version). Returns the new manifest."""
    if dist is not None:
        os.environ["BODY_ALGO_JS_DIR"] = str(dist)
    d = dist_dir()
    manifest, body = bundle()   # raises Unavailable when the manifest does not describe the file
    key = load_private(key_path)
    raw = raw_public(key.public_key())
    manifest = {**manifest, "signature": base64.b64encode(key.sign(body)).decode(), "key_id": key_id(raw)}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def verify(manifest: dict, body: bytes, public_keys: list[str]) -> bool:
    """True when the manifest's signature over body verifies with one of the base64 raw public keys."""
    sig = manifest.get("signature")
    if not sig:
        return False
    for b64 in public_keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(b64)).verify(base64.b64decode(sig), body)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def known_keys() -> dict:
    """packages/openbodyscore-js/signing-keys.json: {"current": {key_id, public_key} | null, "next": ... }."""
    return json.loads(KEYS_FILE.read_text())


def check_repository() -> str | None:
    """None when the committed dist/ is unsigned or signed by a known key; else what is wrong."""
    manifest, body = bundle()
    if not manifest.get("signature"):
        return None
    keys = [k["public_key"] for k in known_keys().values() if k]
    return None if verify(manifest, body, keys) else "dist/manifest.json is signed by a key that is not in signing-keys.json"


__all__ = ["FILE", "keygen", "sign_dist", "verify", "known_keys", "check_repository"]
