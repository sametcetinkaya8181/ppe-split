"""FAZ 01: test veritabanı hedef koruması (tests/db_guard.py) — pozitif ve negatif senaryolar.

DB gerektirmeyen denetimler sahte bağlantıyla sınanır; son test gerçek (geçici) test kümesinde pozitif yolu doğrular.
Uçtan uca negatif senaryo (işaretsiz/üretim benzeri küme -> pytest çıkış kodu 2, veri yerinde) FAZ 01 raporunda
ayrı bir koşu olarak belgelenmiştir; bir pytest oturumu kendi hedefini değiştiremez."""
import pytest

from tests.db_guard import (EPHEMERAL_MARKER, FORBIDDEN_ENV, UnsafeTestTarget, assert_safe_test_target,
                            check_environment, check_server, check_url)

GOOD = "postgresql+psycopg://ppe_ephemeral:pw-test-only@test-db:5432/ppe_test"
SECRET = "uretim-gizli-deger-123456"


# ---------------------------------------------------------------- ortam
def test_environment_clean_passes():
    check_environment({"TEST_DATABASE_URL": GOOD, "PATH": "/usr/bin"}, [SECRET])


@pytest.mark.parametrize("key", FORBIDDEN_ENV)
def test_environment_with_production_db_credentials_is_refused(key):
    with pytest.raises(UnsafeTestTarget, match=key):
        check_environment({key: "herhangi-bir-deger"}, [])


def test_environment_value_equal_to_production_secret_is_refused_without_leaking_value():
    with pytest.raises(UnsafeTestTarget) as ei:
        check_environment({"INGEST_TOKEN": SECRET}, [SECRET])
    assert "INGEST_TOKEN" in str(ei.value) and SECRET not in str(ei.value)


def test_empty_forbidden_variable_is_tolerated():
    check_environment({"DATABASE_URL": ""}, [])          # boş değer kimlik bilgisi taşımaz


# ---------------------------------------------------------------- adres
def test_url_good_passes():
    assert check_url(GOOD, [SECRET]).database == "ppe_test"


@pytest.mark.parametrize("url,why", [
    ("", "tanımlı değil"),
    ("not a url", "ayrıştırılamadı"),
    ("sqlite:///x_test", "PostgreSQL"),
    ("postgresql+psycopg://u:p@test-db:5432/ppe", "_test"),
    ("postgresql+psycopg://u:p@db:5432/ppe_test", "üretim PostgreSQL servisi"),
    ("postgresql+psycopg://u:p@DB:5432/ppe_test", "üretim PostgreSQL servisi"),
    # FAZ 02-A: host'taki/başka projelerdeki PostgreSQL servisleri — adres tarama/deneme yok
    ("postgresql+psycopg://u:p@localhost:5432/ppe_test", "izinli geçici küme"),
    ("postgresql+psycopg://u:p@127.0.0.1:5432/ppe_test", "izinli geçici küme"),
    ("postgresql+psycopg://u:p@host.docker.internal:5432/ppe_test", "izinli geçici küme"),
    ("postgresql+psycopg://u:p@career-agent-postgres:5432/ppe_test", "izinli geçici küme"),
    ("postgresql+psycopg://u:p@ppe-rel-db-1:5432/ppe_test", "izinli geçici küme"),
    ("postgresql+psycopg://u:p@/ppe_test", "izinli geçici küme"),          # unix soketi (host yok)
])
def test_url_refusals(url, why):
    with pytest.raises(UnsafeTestTarget, match=why):
        check_url(url, [])


@pytest.mark.parametrize("host", ["test-db", "ppe-review-test-db"])
def test_url_allowed_hosts(host):
    assert check_url(f"postgresql+psycopg://u:p@{host}:5432/ppe_test", []).host == host


def test_refused_host_is_never_connected(monkeypatch):
    """Reddedilen adres için motor bile oluşturulmaz (bağlantı denemesi yok)."""
    import sqlalchemy

    def boom(*a, **k):
        raise AssertionError("reddedilen hedefe bağlantı denendi")
    monkeypatch.setattr(sqlalchemy, "create_engine", boom)
    with pytest.raises(UnsafeTestTarget, match="izinli geçici küme"):
        assert_safe_test_target("postgresql+psycopg://u:p@career-agent-postgres:5432/x_test", environ={}, secrets=[])


def test_url_with_production_password_is_refused_without_leaking_value():
    with pytest.raises(UnsafeTestTarget) as ei:
        check_url(f"postgresql+psycopg://u:{SECRET}@test-db:5432/ppe_test", [SECRET])
    assert SECRET not in str(ei.value)


# ---------------------------------------------------------------- sunucu kimliği (sahte bağlantı)
class FakeConn:
    def __init__(self, cluster_name, dbs):
        self.cluster_name, self.dbs = cluster_name, dbs

    def execute(self, stmt):
        sql = str(stmt)
        if "cluster_name" in sql:
            return _Result([(self.cluster_name,)])
        if "pg_database" in sql:
            return _Result([(d,) for d in self.dbs])
        raise AssertionError(f"beklenmeyen sorgu: {sql}")     # koruma yalnız bu iki salt okunur sorguyu yapar


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalar(self):
        return self.rows[0][0]

    def __iter__(self):
        return iter(self.rows)


def test_server_marked_and_clean_passes():
    check_server(FakeConn(EPHEMERAL_MARKER, ["postgres", "ppe_test", "ppe_roles_ab12cd34_test"]))


@pytest.mark.parametrize("name", ["", "main", "ppe-ephemeral-test-x", None])
def test_server_without_marker_is_refused(name):
    with pytest.raises(UnsafeTestTarget, match="cluster_name"):
        check_server(FakeConn(name, ["postgres"]))


def test_marked_server_holding_production_database_is_refused():
    with pytest.raises(UnsafeTestTarget, match="ppe"):
        check_server(FakeConn(EPHEMERAL_MARKER, ["postgres", "ppe", "ppe_test"]))


# ---------------------------------------------------------------- gerçek geçici küme (pozitif yol)
def test_real_ephemeral_cluster_is_accepted(db_url):
    """Bu oturumun kümesi koruma koşullarını sağlar (aksi halde oturum hiç başlamazdı)."""
    assert assert_safe_test_target(db_url, environ={"TEST_DATABASE_URL": db_url}, secrets=[]).database.endswith("_test")
