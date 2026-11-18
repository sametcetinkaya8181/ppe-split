"""FAZ 01: imaj build provenance bilgisinin okunması ve yalnız admin'e sunulması."""
import json

import app as app_module
from tests.test_access_control import client_as

SHA = "0123456789abcdef0123456789abcdef01234567"


def write(tmp_path, **kv):
    p = tmp_path / "build-info.json"
    p.write_text(json.dumps(kv), encoding="utf-8")
    return p


def test_release_clean_known_commit_is_verified(tmp_path):
    info = app_module.load_build_info(write(tmp_path, revision=SHA, branch="main", created="2026-11-17T00:00:00Z",
                                            tree_state="clean", version="0.10.7", kind="release"))
    assert info["verified"] is True and info["revision"] == SHA


def test_development_or_dirty_or_unknown_is_unverified(tmp_path):
    base = dict(revision=SHA, branch="x", created="t", tree_state="clean", version="v", kind="release")
    for over in ({"kind": "development"}, {"tree_state": "dirty"}, {"tree_state": "unknown"}, {"revision": "unknown"},
                 {"revision": SHA.upper()}):
        assert app_module.load_build_info(write(tmp_path, **{**base, **over}))["verified"] is False, over


def test_missing_or_corrupt_file_is_unverified(tmp_path):
    assert app_module.load_build_info(tmp_path / "yok.json")["kind"] == "unknown"
    bad = tmp_path / "bad.json"
    bad.write_text("{bozuk", encoding="utf-8")
    info = app_module.load_build_info(bad)
    assert info["verified"] is False and info["revision"] == "unknown"
    lst = tmp_path / "list.json"
    lst.write_text("[1, 2]", encoding="utf-8")
    assert app_module.load_build_info(lst)["verified"] is False


def test_ops_metrics_exposes_build_to_admin_only(api):
    body = api.get("/api/ops/metrics").json()
    assert set(body["build"]) == {"revision", "branch", "created", "tree_state", "version", "kind", "verified"}
    assert body["build"]["verified"] is False          # testler kaynaktan çalışır: build-info.json yok
    for role in ("viewer", "supervisor"):
        assert client_as(role).get("/api/ops/metrics").status_code == 403
