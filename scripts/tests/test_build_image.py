"""FAZ 01: scripts/build_image.py karar kuralları (docker/git çağırmaz).

    python -m pytest -q scripts/tests"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_image as b  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
NOW = "2026-11-17T00:00:00Z"
CLEAN = {"sha": SHA, "branch": "integration/faz-01", "dirty_paths": []}
DIRTY = {"sha": SHA, "branch": "integration/faz-01", "dirty_paths": ["dashboard/app.py", "yeni.txt"]}


def test_release_from_clean_tree():
    p = b.plan(CLEAN, "0.10.7", set(), NOW)
    assert p["tags"] == ["ppe-dashboard:0.10.7", f"ppe-dashboard:sha-{SHA[:12]}"]
    assert p["build_args"] == {"PPE_GIT_SHA": SHA, "PPE_GIT_BRANCH": "integration/faz-01", "PPE_BUILD_TIME": NOW,
                               "PPE_TREE_STATE": "clean", "PPE_VERSION": "0.10.7", "PPE_BUILD_KIND": "release"}


def test_release_refused_on_dirty_tree_lists_paths():
    with pytest.raises(b.BuildRefused, match="kirli") as ei:
        b.plan(DIRTY, "0.10.7", set(), NOW)
    assert "dashboard/app.py" in str(ei.value)


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "01.0.0", "latest", "1.0.0 ", ""])
def test_release_requires_semver(version):
    with pytest.raises(b.BuildRefused, match="SemVer"):
        b.plan(CLEAN, version, set(), NOW)


def test_release_tags_are_immutable():
    with pytest.raises(b.BuildRefused, match="zaten var"):
        b.plan(CLEAN, "0.10.7", {"ppe-dashboard:0.10.7"}, NOW)
    with pytest.raises(b.BuildRefused, match="zaten var"):
        b.plan(CLEAN, "0.10.8", {f"ppe-dashboard:sha-{SHA[:12]}"}, NOW)


def test_development_build_allowed_on_dirty_tree_and_marked():
    p = b.plan(DIRTY, None, {"ppe-dashboard:latest"}, NOW)
    assert p["tags"] == [f"ppe-dashboard-dev:{SHA[:12]}-dirty"]
    assert p["build_args"]["PPE_BUILD_KIND"] == "development" and p["build_args"]["PPE_TREE_STATE"] == "dirty"
    assert p["build_args"]["PPE_VERSION"].endswith(".dirty")


def test_development_build_on_clean_tree():
    p = b.plan(CLEAN, None, set(), NOW)
    assert p["tags"] == [f"ppe-dashboard-dev:{SHA[:12]}"] and p["build_args"]["PPE_TREE_STATE"] == "clean"


def test_never_targets_latest():
    for state, rel in ((CLEAN, None), (DIRTY, None), (CLEAN, "1.2.3")):
        assert not any(t.endswith(":latest") or t == "ppe-dashboard" for t in b.plan(state, rel, set(), NOW)["tags"])


def test_dirty_paths_keeps_first_column_alignment():
    assert b.dirty_paths(" M .gitignore\nM  dashboard/app.py\n?? yeni.txt\n") == [".gitignore", "dashboard/app.py",
                                                                                "yeni.txt"]
    assert b.dirty_paths("") == []


def test_invalid_sha_and_unsafe_branch():
    with pytest.raises(b.BuildRefused, match="SHA"):
        b.plan({**CLEAN, "sha": "abc"}, None, set(), NOW)
    p = b.plan({**CLEAN, "branch": 'kötü"; rm -rf /'}, None, set(), NOW)
    assert p["build_args"]["PPE_GIT_BRANCH"] == "unknown"
