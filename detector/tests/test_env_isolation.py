"""FAZ 01: dedektör ayar dosyası seçimi ve test ortamı izolasyonu.

Üretim davranışı (DETECTOR_ENV_FILE yok -> detector/.env, terminal değişkenleri öncelikli) korunur; testler
DETECTOR_ENV_FILE=none ile çalışır ve gerçek .env hiçbir yoldan okunmaz. Gerçek .env içeriği bu testlerde açılmaz."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import detector_env
from evaluation.isolation import IsolationError, assert_env_isolated, leaked_keys

DETECTOR_DIR = Path(__file__).resolve().parents[1]


def test_session_never_reads_real_env_file():
    assert os.environ["DETECTOR_ENV_FILE"] == "none"
    assert detector_env.env_file() is None
    assert detector_env.read_env_file() == {}
    assert detector_env.env_token() == ""                 # conftest: INGEST_TOKEN="" ; dosya okunmaz
    assert detector_env.dashboard_url() == "http://dashboard.test"


def test_production_default_is_unchanged(monkeypatch):
    monkeypatch.delenv("DETECTOR_ENV_FILE")
    assert detector_env.env_file() == detector_env.ENV_FILE == DETECTOR_DIR / ".env"
    monkeypatch.setenv("DETECTOR_ENV_FILE", "   ")       # boş değer = varsayılan (sessiz atlatma yok)
    assert detector_env.env_file() == detector_env.ENV_FILE


@pytest.mark.parametrize("value", ["none", "NONE", " None "])
def test_explicit_none_disables_file(monkeypatch, value):
    monkeypatch.setenv("DETECTOR_ENV_FILE", value)
    assert detector_env.env_file() is None and detector_env.read_env_file() == {}


def test_custom_file_is_parsed_like_before(monkeypatch, tmp_path):
    f = tmp_path / "test.env"
    f.write_text('# yorum\nINGEST_TOKEN="tok-test-123"\nCONF=\'0.3\'\nBOS=\n  VIDEO_SOURCES=a,b \n', encoding="utf-8")
    monkeypatch.setenv("DETECTOR_ENV_FILE", str(f))
    assert detector_env.read_env_file() == {"INGEST_TOKEN": "tok-test-123", "CONF": "0.3", "BOS": "",
                                            "VIDEO_SOURCES": "a,b"}
    monkeypatch.delenv("INGEST_TOKEN")
    assert detector_env.env_token() == "tok-test-123"     # token yenileme dosyadan (üretim davranışı)


def test_load_env_uses_same_selection_and_keeps_terminal_priority(monkeypatch, tmp_path):
    import detector
    f = tmp_path / "d.env"
    f.write_text("FAZ01_YENI=dosyadan\nFAZ01_VAR=dosyadan\n", encoding="utf-8")
    monkeypatch.setenv("DETECTOR_ENV_FILE", str(f))
    monkeypatch.setenv("FAZ01_VAR", "terminalden")
    monkeypatch.delenv("FAZ01_YENI", raising=False)
    detector._load_env()
    assert os.environ["FAZ01_YENI"] == "dosyadan" and os.environ["FAZ01_VAR"] == "terminalden"
    monkeypatch.delenv("FAZ01_YENI")
    monkeypatch.setenv("DETECTOR_ENV_FILE", "none")
    detector._load_env()
    assert "FAZ01_YENI" not in os.environ


def test_fresh_detector_process_with_none_loads_nothing(tmp_path):
    """Yeni süreç (spawn ile kamera süreçlerinin gördüğü gibi): DETECTOR_ENV_FILE=none -> .env'deki hiçbir anahtar yok.
    Gerçek .env'in anahtar ADLARI karşılaştırılır; değerler okunmaz/yazdırılmaz."""
    real = DETECTOR_DIR / ".env"
    names = []
    if real.exists():                                      # anahtar adları (değer değil)
        names = [ln.split("=", 1)[0].strip() for ln in real.read_text(encoding="utf-8").splitlines()
                 if "=" in ln and not ln.strip().startswith("#")]
    env = {k: v for k, v in os.environ.items() if k not in names}
    env["DETECTOR_ENV_FILE"] = "none"
    code = ("import os, json, sys; import detector; "
            "print(json.dumps(sorted(k for k in json.loads(sys.argv[1]) if k in os.environ)))")
    import json
    r = subprocess.run([sys.executable, "-c", code, json.dumps(names)], cwd=DETECTOR_DIR, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout.strip().splitlines()[-1]) == []


def test_isolation_guard_detects_inherited_secret_without_printing_it():
    secret = "uretim-gizli-0123456789"
    assert leaked_keys({"BASKA_ANAHTAR": secret, "PATH": "x"}, {"detector/.env:INGEST_TOKEN": secret}) == [
        "BASKA_ANAHTAR==detector/.env:INGEST_TOKEN"]
    with pytest.raises(IsolationError) as ei:
        assert_env_isolated({"X": secret}, {"lbl": secret}, context="test")
    assert secret not in str(ei.value)
