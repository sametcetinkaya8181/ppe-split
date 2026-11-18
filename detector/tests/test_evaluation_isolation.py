"""FAZ 10-A.5B gizli bilgi izolasyonu: değerlendirme süreci üretim token'ını taşımaz, test alt süreçleri yalnız test
kimliğiyle başlar. Gerçek .env okunmaz (geçici dosyalar)."""
import pytest

from evaluation import isolation as iso

PROD = "prod-token-" + "x" * 30


def _prod_files(tmp_path):
    (tmp_path / "root").mkdir()
    (tmp_path / "detector").mkdir()
    (tmp_path / "root" / ".env").write_text(f"INGEST_TOKEN={PROD}\nPOSTGRES_PASSWORD=pg-secret-123456\nSHORT=abc\n",
                                            encoding="utf-8")
    (tmp_path / "detector" / ".env").write_text(f'INGEST_TOKEN="{PROD}"\nIMGSZ=960\n', encoding="utf-8")
    return (tmp_path / "root" / ".env", tmp_path / "detector" / ".env")


def test_production_secrets_reads_only_secret_keys(tmp_path):
    s = iso.production_secrets(_prod_files(tmp_path))
    assert sorted(s) == ["detector/.env:INGEST_TOKEN", "root/.env:INGEST_TOKEN", "root/.env:POSTGRES_PASSWORD"]
    assert set(s.values()) == {PROD, "pg-secret-123456"}


def test_scrub_removes_secret_keys_and_module_copy():
    env = {"INGEST_TOKEN": PROD, "DASHBOARD_URL": "http://prod:8000", "IMGSZ": "960", "PATH": "x"}

    class Mod:
        INGEST_TOKEN = PROD
    removed = iso.scrub_process_env(env, module=Mod)
    assert env == {"IMGSZ": "960", "PATH": "x"}
    assert Mod.INGEST_TOKEN == ""
    assert set(removed) == {"INGEST_TOKEN", "DASHBOARD_URL", "detector.INGEST_TOKEN"}
    assert PROD not in repr(removed)                       # yalnız anahtar adları


def test_guard_rejects_production_value_without_printing_it(tmp_path):
    secrets = iso.production_secrets(_prod_files(tmp_path))
    with pytest.raises(iso.IsolationError) as e:
        iso.assert_env_isolated({"INGEST_TOKEN": PROD, "OTHER": "ok"}, secrets, context="dedektör")
    assert "INGEST_TOKEN==" in str(e.value) and PROD not in str(e.value)
    with pytest.raises(iso.IsolationError):                # gömülü değer (ör. URL/argüman içinde) de yakalanır
        iso.assert_env_isolated({"X": f"Bearer {PROD}"}, secrets)


def test_guard_accepts_test_only_credentials(tmp_path):
    secrets = iso.production_secrets(_prod_files(tmp_path))
    iso.assert_env_isolated({"INGEST_TOKEN": "test-token-" + "y" * 30, "DASHBOARD_URL": "http://127.0.0.1:8200"},
                            secrets)


def test_production_settings_leaves_no_token_in_eval_process(monkeypatch):
    """production_settings `import detector` yan etkisinden sonra token süreç ortamında ve modülde kalmamalı."""
    import detector
    from evaluation import cli
    monkeypatch.setenv("INGEST_TOKEN", PROD)               # detector/.env'den yüklenmiş gibi
    monkeypatch.setattr(detector, "INGEST_TOKEN", PROD)
    s = cli.production_settings()
    import os
    assert "INGEST_TOKEN" not in os.environ and detector.INGEST_TOKEN == ""
    assert PROD not in repr(s)
