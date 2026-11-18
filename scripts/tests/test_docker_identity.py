"""FAZ 02-A: container kimlik doğrulaması (docker ÇAĞIRMAZ; subprocess sahtelenir).

Asıl güvence: kimliği doğrulanmayan container'a hiçbir `docker exec` / `docker stop` komutu gitmez.

    python -m pytest -q scripts/tests"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import docker_identity as di  # noqa: E402
import ppe_ops  # noqa: E402

PG = di.PG_DATA


def info(*, cid="a" * 64, labels=None, mounts=(), tmpfs=None, cmd=("postgres",), ports=None, running=True):
    return {"Id": cid, "Config": {"Labels": labels or {}, "Cmd": list(cmd)},
            "Mounts": list(mounts), "HostConfig": {"Tmpfs": tmpfs or {}, "PortBindings": ports or {}},
            "State": {"Running": running}}


def vol(name, dest=PG):
    return {"Type": "volume", "Name": name, "Destination": dest}


MAIN = info(cid="b" * 64, labels={di.COMPOSE_PROJECT: "ppe-split", di.COMPOSE_SERVICE: "db"},
            mounts=[vol("ppe-split_pgdata")])
EPHEMERAL = info(cid="c" * 64, labels={di.EPHEMERAL_LABEL: "1", di.OWNER_LABEL: "x"}, tmpfs={PG: ""},
                 cmd=("postgres", "-c", f"cluster_name={di.EPHEMERAL_MARKER}", "-c", "fsync=off"))
# başka bir projenin PostgreSQL'i (FAZ 01 olayındaki gibi): kendi compose projesi ve volume'u var
FOREIGN = info(cid="d" * 64, labels={di.COMPOSE_PROJECT: "ai-career-agent", di.COMPOSE_SERVICE: "postgres"},
               mounts=[vol("ai-career-agent_pgdata")])


# ---------------------------------------------------------------- saf kurallar
def test_main_db_accepts_expected_identity():
    assert di.main_db_problems(MAIN, "ppe-split") == []


@pytest.mark.parametrize("bad, needle", [
    (FOREIGN, "compose projesi"),
    (info(labels={di.COMPOSE_PROJECT: "ppe-rel", di.COMPOSE_SERVICE: "db"}, mounts=[vol("ppe-rel_pgdata")]),
     "compose projesi"),
    (info(labels={di.COMPOSE_PROJECT: "ppe-split", di.COMPOSE_SERVICE: "dashboard"}, mounts=[vol("ppe-split_pgdata")]),
     "compose servisi"),
    # etiketler sahte/kopya ama veri volume'u başka: reddedilir
    (info(labels={di.COMPOSE_PROJECT: "ppe-split", di.COMPOSE_SERVICE: "db"}, mounts=[vol("baska_pgdata")]),
     "volume"),
    (info(labels={di.COMPOSE_PROJECT: "ppe-split", di.COMPOSE_SERVICE: "db"}, tmpfs={PG: ""}), "volume"),
    (info(labels={di.COMPOSE_PROJECT: "ppe-split", di.COMPOSE_SERVICE: "db", di.EPHEMERAL_LABEL: "1"},
          mounts=[vol("ppe-split_pgdata")]), "geçici"),
])
def test_main_db_rejects(bad, needle):
    problems = di.main_db_problems(bad, "ppe-split")
    assert problems and any(needle in p for p in problems)


def test_ephemeral_accepts_owned_cluster():
    assert di.ephemeral_problems(EPHEMERAL) == []


@pytest.mark.parametrize("bad, needle", [
    (FOREIGN, "etiketi yok"),
    (MAIN, "etiketi yok"),
    # işaret komutta var ama etiket yok (ör. başkasının elle başlattığı küme)
    (info(tmpfs={PG: ""}, cmd=("postgres", "-c", f"cluster_name={di.EPHEMERAL_MARKER}")), "etiketi yok"),
    (info(labels={di.EPHEMERAL_LABEL: "1", di.COMPOSE_PROJECT: "ppe-split"}, tmpfs={PG: ""},
          cmd=("postgres", "-c", f"cluster_name={di.EPHEMERAL_MARKER}")), "compose projesine"),
    (info(labels={di.EPHEMERAL_LABEL: "1"}, mounts=[vol("kalici")], tmpfs={PG: ""},
          cmd=("postgres", "-c", f"cluster_name={di.EPHEMERAL_MARKER}")), "kalıcı"),
    (info(labels={di.EPHEMERAL_LABEL: "1"}, mounts=[{"Type": "bind", "Source": "C:/x", "Destination": "/x"}],
          tmpfs={PG: ""}, cmd=("postgres", "-c", f"cluster_name={di.EPHEMERAL_MARKER}")), "kalıcı"),
    (info(labels={di.EPHEMERAL_LABEL: "1"}, cmd=("postgres", "-c", f"cluster_name={di.EPHEMERAL_MARKER}")), "tmpfs"),
    (info(labels={di.EPHEMERAL_LABEL: "1"}, tmpfs={PG: ""}), "işaret"),
    (info(labels={di.EPHEMERAL_LABEL: "1"}, tmpfs={PG: ""}, ports={"5432/tcp": [{"HostPort": "5432"}]},
          cmd=("postgres", "-c", f"cluster_name={di.EPHEMERAL_MARKER}")), "port"),
])
def test_ephemeral_rejects(bad, needle):
    problems = di.ephemeral_problems(bad)
    assert problems and any(needle in p for p in problems)


def test_running_required():
    assert di.running_problems(info(running=False))


# ---------------------------------------------------------------- docker sahte: komut kaydı
class FakeDocker:
    """`docker inspect`/`docker ps` meta verisini verir; diğer TÜM komutları kaydeder (exec/stop vb.)."""

    def __init__(self, containers: dict, ps_ids=None, cluster_name=di.EPHEMERAL_MARKER):
        self.containers, self.ps_ids, self.cluster_name = containers, ps_ids or [], cluster_name
        self.commands = []

    def __call__(self, cmd, **kw):
        cmd = list(cmd)
        if cmd[:2] == ["docker", "inspect"]:
            c = self.containers.get(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0 if c else 1, json.dumps([c] if c else []), "")
        if cmd[:3] == ["docker", "ps", "-a"]:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(self.ps_ids), "")
        self.commands.append(cmd)
        out = self.cluster_name + "\n" if "PPE_SQL=SELECT current_setting('cluster_name')" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def execs(self):
        return [c for c in self.commands if c[:2] == ["docker", "exec"]]


@pytest.fixture
def fake(monkeypatch):
    def install(containers, ps_ids=None, cluster_name=di.EPHEMERAL_MARKER):
        f = FakeDocker(containers, ps_ids, cluster_name)
        monkeypatch.setattr(di.subprocess, "run", f)
        monkeypatch.setattr(ppe_ops.subprocess, "run", f)
        ppe_ops._TARGET.update(container=None, kind=None)
        return f
    yield install
    ppe_ops._TARGET.update(container=None, kind=None)


@pytest.mark.parametrize("name", ["career-agent-postgres", "ppe-split-db-1", "ppe-eval-db-1"])
def test_set_target_refuses_without_any_command(fake, name):
    f = fake({"career-agent-postgres": FOREIGN, "ppe-split-db-1": MAIN,
              "ppe-eval-db-1": info(labels={di.COMPOSE_PROJECT: "ppe-eval", di.COMPOSE_SERVICE: "db"})})
    with pytest.raises(ppe_ops.OpsError):
        ppe_ops.set_target(name)
    assert f.commands == []                     # hiçbir exec/sorgu gitmedi
    assert ppe_ops._TARGET["container"] is None


def test_set_target_unknown_container(fake):
    f = fake({})
    with pytest.raises(ppe_ops.OpsError, match="bulunamadı"):
        ppe_ops.set_target("yok")
    assert f.commands == []


def test_set_target_accepts_owned_and_uses_full_id(fake):
    f = fake({"ppe-verify-1": EPHEMERAL})
    ppe_ops.set_target("ppe-verify-1")
    assert ppe_ops._TARGET == {"container": "c" * 64, "kind": "ephemeral"}
    assert f.execs() and all("c" * 64 in c for c in f.execs())    # komutlar ada değil tam ID'ye gider
    assert all("ppe-verify-1" not in c for c in f.commands)


def test_set_target_second_check_cluster_name(fake):
    f = fake({"ppe-verify-1": EPHEMERAL}, cluster_name="baska")
    with pytest.raises(ppe_ops.OpsError, match="cluster_name"):
        ppe_ops.set_target("ppe-verify-1")
    assert ppe_ops._TARGET["container"] is None
    assert len(f.execs()) == 1                  # yalnız işaret sorgusu (meta veri doğrulandıktan SONRA)


@pytest.mark.parametrize("ps_ids, containers, needle", [
    ([], {}, "0 container"),
    (["b" * 64, "e" * 64], {"b" * 64: MAIN}, "2 container"),
    # etiketleri taklit eden ama ppe-split_pgdata'yı bağlamamış container
    (["e" * 64], {"e" * 64: info(cid="e" * 64, labels={di.COMPOSE_PROJECT: "ppe-split", di.COMPOSE_SERVICE: "db"},
                                 mounts=[vol("taklit")])}, "volume"),
    (["b" * 64], {"b" * 64: {**MAIN, "State": {"Running": False}}}, "çalışmıyor"),
])
def test_main_db_resolution_refuses_without_exec(fake, ps_ids, containers, needle):
    f = fake(containers, ps_ids)
    with pytest.raises(ppe_ops.OpsError, match=needle):
        ppe_ops.db_exec("pg_isready", "-q")
    assert f.commands == []


def test_main_db_resolution_uses_verified_id(fake):
    f = fake({"b" * 64: MAIN}, ["b" * 64])
    ppe_ops.db_exec("pg_isready", "-q")
    assert f.execs() == [["docker", "exec", "b" * 64, "pg_isready", "-q"]]
    assert all("compose" not in c for c in f.commands)
    assert ppe_ops.target_info() == {"kind": "main", "container_id": "b" * 12, "project": "ppe-split",
                                     "service": "db", "volume": "ppe-split_pgdata"}


def test_snapshots_dir_from_dashboard_mount(fake):
    dash = info(cid="f" * 64, labels={di.COMPOSE_PROJECT: "ppe-split", di.COMPOSE_SERVICE: "dashboard"},
                mounts=[{"Type": "bind", "Source": "C:/prod/data", "Destination": "/data"}])
    fake({"f" * 64: dash}, ["f" * 64])
    assert ppe_ops.main_snapshots_dir() == Path("C:/prod/data") / "snapshots"


def test_snapshots_dir_refuses_without_bind(fake):
    dash = info(cid="f" * 64, labels={di.COMPOSE_PROJECT: "ppe-split", di.COMPOSE_SERVICE: "dashboard"})
    fake({"f" * 64: dash}, ["f" * 64])
    with pytest.raises(ppe_ops.OpsError, match="/data"):
        ppe_ops.main_snapshots_dir()
