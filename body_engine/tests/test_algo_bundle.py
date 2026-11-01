"""The JS algorithm bundle endpoints (issue #340 N1): GET /v1/algo/manifest and the download, served from
packages/openbodyscore-js/dist as it is in the repository. No database."""
import hashlib
import json
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from body_engine import algo, api
from body_engine.algo.api import router

DIST = algo.DEFAULT_DIR


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_manifest_matches_the_repository_bundle(client):
    want = json.loads((DIST / "manifest.json").read_text())
    body = (DIST / "openbodyscore.js").read_bytes()
    r = client.get("/v1/algo/manifest")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    m = r.json()
    assert m["version"] == want["version"] and m["sha256"] == hashlib.sha256(body).hexdigest() == want["sha256"]
    assert m["size"] == len(body) and m["global"] == "OpenBodyScore"
    assert m["algo_versions"]["scores"] == __import__("openbodyscore").params.ALGO_VERSION   # the server's own version
    assert m["url"] == f"/v1/algo/openbodyscore-{want['version']}.js"
    assert client.get("/v1/algo/manifest", headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_download_is_the_file_and_names_its_hash(client):
    m = client.get("/v1/algo/manifest").json()
    r = client.get(m["url"])
    assert r.status_code == 200 and r.content == (DIST / "openbodyscore.js").read_bytes()
    assert hashlib.sha256(r.content).hexdigest() == m["sha256"] == r.headers["x-algo-sha256"]
    assert r.headers["content-type"].startswith("application/javascript")
    assert "immutable" in r.headers["cache-control"]
    assert client.get("/v1/algo/openbodyscore-0.0.0-old.js").status_code == 404


def test_versions_match_the_python_algorithms():
    """The bundle's algorithm versions are the server's (algo/versions.py names the Python constant of each key): a Python
    change without a rebuilt bundle fails here. Only keys present in the bundle are compared; the 2.0.0 packages add
    theirs with `src/3N_<pkg>.js` (docs/ios-native-2.0.md section 2.2)."""
    from body_engine.algo import versions
    m = json.loads((DIST / "manifest.json").read_text())
    assert {"scores", "healthspan", "hrv", "stress", "trends"} <= set(m["algo_versions"])
    for key, version in m["algo_versions"].items():
        assert key in versions.VERSIONS, f"{key}: add it to algo/versions.py"
        assert versions.current(key) == version, f"{key}: bundle {version!r}, Python {versions.path(key)} {versions.current(key)!r}"


def test_versions_registry_resolves():
    """Shipped keys resolve to their constant; a planned key without its constant yet has no server version."""
    from openbodyscore import params
    from body_engine.algo import versions
    assert versions.current("scores") == params.ALGO_VERSION
    assert set(versions.server_versions()) >= {"scores", "healthspan", "hrv", "stress", "trends"}
    assert set(versions.VERSIONS) >= {"derive", "workouts", "stress_cards", "series", "coach", "reports", "rules", "explain"}
    assert versions.current("derive") is None or isinstance(versions.current("derive"), str)
    with pytest.raises(KeyError):
        versions.path("nope")


def test_missing_or_tampered_bundle_is_503(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BODY_ALGO_JS_DIR", str(tmp_path / "none"))
    r = client.get("/v1/algo/manifest")
    assert r.status_code == 503 and r.json()["detail"] == "算法包不可用"
    bad = tmp_path / "dist"
    shutil.copytree(DIST, bad)
    (bad / "openbodyscore.js").write_bytes((DIST / "openbodyscore.js").read_bytes() + b"\n// changed\n")
    monkeypatch.setenv("BODY_ALGO_JS_DIR", str(bad))
    assert client.get("/v1/algo/manifest").status_code == 503
    assert client.get("/v1/algo/openbodyscore-0.1.0.js").status_code == 503


def test_registered_in_the_app_without_auth():
    """The real app (no lifespan, so no database): reachable without a token; the error text has its English."""
    c = TestClient(api.app)
    m = c.get("/v1/algo/manifest")
    assert m.status_code == 200 and c.get(m.json()["url"]).status_code == 200
    assert c.get("/v1/algo/openbodyscore-0.0.0.js", headers={"X-Body-Lang": "en"}).json()["detail"] == \
        "No algorithm bundle of this version"
