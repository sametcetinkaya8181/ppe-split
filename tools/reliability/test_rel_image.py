"""FAZ 02-B hazırlık: izole yığının dashboard imajı seçimi ve compose koruması (Docker gerektirmez). Proje kökünden:
    detector\\.venv\\Scripts\\python -m pytest -q tools/reliability/test_rel_image.py"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402
import memtest  # noqa: E402
from evaluation.isolation import IsolationError, assert_env_isolated as real_assert  # noqa: E402

REAL_ENV = c.env
RC = "ppe-dashboard:0.10.7-rc.faz02a1"
FAKE_SECRET = "prod-only-value-1234567890"


@pytest.fixture
def stack(monkeypatch, tmp_path):
    """Gerçek rel.env/Docker yerine sahte yığın ortamı ve yakalanan subprocess çağrıları."""
    monkeypatch.delenv("REL_DASHBOARD_IMAGE", raising=False)
    monkeypatch.setattr(c, "env", lambda: {"POSTGRES_PASSWORD": "test-only-pw", "INGEST_TOKEN": "test-only-tok",
                                           "REL_PORT": "8100"})
    monkeypatch.setattr(c, "assert_env_isolated",
                        lambda env, context="": real_assert(env, {"prod:INGEST_TOKEN": FAKE_SECRET}, context=context))
    calls, present = [], {"value": True}

    def fake_run(cmd, **kw):
        calls.append({"cmd": list(cmd), "env": kw.get("env")})
        rc = 0 if (cmd[:3] != ["docker", "image", "inspect"] or present["value"]) else 1
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setattr(c.subprocess, "run", fake_run)
    return {"calls": calls, "present": present, "tmp": tmp_path}


def test_default_image_is_unpinned_and_keeps_old_behaviour(stack):
    assert c.dashboard_image() == ("ppe-dashboard:faz9-rel", False)
    assert c.compose_args(("up", "-d", "--build")) == ["up", "-d", "--build"]
    assert c.compose_args(("build", "dashboard")) == ["build", "dashboard"]


@pytest.mark.parametrize("ref", ["ppe-dashboard:latest", "ppe-dashboard", "  ppe-dashboard:latest  "])
def test_production_tag_is_refused(stack, monkeypatch, ref):
    monkeypatch.setenv("REL_DASHBOARD_IMAGE", ref)
    with pytest.raises(SystemExit, match="üretim etiketi"):
        c.dashboard_image()


def test_blank_value_falls_back_to_default(stack, monkeypatch):
    monkeypatch.setenv("REL_DASHBOARD_IMAGE", "   ")
    assert c.dashboard_image() == ("ppe-dashboard:faz9-rel", False)


@pytest.mark.parametrize("args", [("build", "dashboard"), ("up", "-d", "--build"), ("up", "--build", "dashboard")])
def test_pinned_image_is_never_built(stack, monkeypatch, args):
    monkeypatch.setenv("REL_DASHBOARD_IMAGE", RC)
    with pytest.raises(SystemExit, match="yeniden derlemez"):
        c.compose_args(args)


def test_pinned_up_gets_no_build_and_other_commands_untouched(stack, monkeypatch):
    monkeypatch.setenv("REL_DASHBOARD_IMAGE", RC)
    assert c.compose_args(("up", "-d", "--force-recreate", "dashboard")) == [
        "up", "--no-build", "-d", "--force-recreate", "dashboard"]
    assert c.compose_args(("up", "--no-build", "-d")) == ["up", "--no-build", "-d"]
    assert c.compose_args(("stop",)) == ["stop"]


def test_pinned_up_refused_when_image_missing_and_nothing_run(stack, monkeypatch):
    monkeypatch.setenv("REL_DASHBOARD_IMAGE", RC)
    stack["present"]["value"] = False
    with pytest.raises(SystemExit, match="yerelde yok"):
        c.compose("up", "-d")
    assert not any(call["cmd"][:2] == ["docker", "compose"] for call in stack["calls"])


def test_compose_passes_image_and_strips_stack_keys(stack, monkeypatch):
    monkeypatch.setenv("REL_DASHBOARD_IMAGE", RC)
    monkeypatch.setenv("POSTGRES_PASSWORD", "shell-value-must-not-override")
    c.compose("up", "-d", overrides=(stack["tmp"] / "override.yml",))
    call = [x for x in stack["calls"] if x["cmd"][:2] == ["docker", "compose"]][-1]
    cmd, env = call["cmd"], call["env"]
    assert cmd[cmd.index("-p") + 1] == c.PROJECT != "ppe-split"
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-f"] == [str(c.COMPOSE), str(stack["tmp"] / "override.yml")]
    assert cmd[cmd.index("up"):] == ["up", "--no-build", "-d"]
    assert env["REL_DASHBOARD_IMAGE"] == RC and "POSTGRES_PASSWORD" not in env


def test_compose_refuses_inherited_production_secret(stack, monkeypatch):
    monkeypatch.setenv("SOME_VAR", FAKE_SECRET)
    with pytest.raises(IsolationError):
        c.compose("ps")
    assert not any(call["cmd"][:2] == ["docker", "compose"] for call in stack["calls"])


def test_memtest_compose_uses_the_guarded_path(stack, monkeypatch):
    seen = {}
    monkeypatch.setattr(c, "compose", lambda *a, **kw: seen.update(args=a, kw=kw))
    ov = stack["tmp"] / "override.yml"
    memtest.compose(ov, "up", "-d", timeout=7)
    assert seen == {"args": ("up", "-d"), "kw": {"timeout": 7, "overrides": (ov,)}}


@pytest.mark.parametrize("volume_exists", [True, False])
def test_env_file_not_generated_over_existing_volume(monkeypatch, tmp_path, volume_exists):
    """rel.env yokken projenin DB volume'u varsa yeni parola üretilmez (uyuşmaz); volume yoksa üretilir."""
    envf = tmp_path / "rel.env"
    monkeypatch.setattr(c, "OUT", tmp_path)
    monkeypatch.setattr(c, "ENV_FILE", envf)
    seen = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0 if volume_exists else 1, "", "")

    monkeypatch.setattr(c.subprocess, "run", fake_run)
    if volume_exists:
        with pytest.raises(SystemExit, match="volume'u var"):
            REAL_ENV()
        assert not envf.exists()
    else:
        vals = REAL_ENV()
        assert envf.exists() and set(vals) >= {"POSTGRES_PASSWORD", "INGEST_TOKEN", "REL_PORT"}
    assert seen[0] == ["docker", "volume", "inspect", f"{c.PROJECT}_rel_pgdata"]


def test_compose_file_images_are_parameterized_and_latest_absent():
    text = c.COMPOSE.read_text(encoding="utf-8")
    images = [ln.split("image:", 1)[1].strip() for ln in text.splitlines() if ln.strip().startswith("image:")]
    dash = [i for i in images if "ppe-dashboard" in i]
    assert len(dash) == 4 and set(dash) == {"${REL_DASHBOARD_IMAGE:-ppe-dashboard:faz9-rel}"}
    assert "ppe-dashboard:latest" not in text
