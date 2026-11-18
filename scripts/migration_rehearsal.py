"""FAZ 02-A: migration provası — bir yedeği İZOLE geçici kümede aday imajla head'e (0007) taşır ve doğrular.

  python scripts/migration_rehearsal.py <yedek-klasörü> --candidate-image ppe-dashboard:<sürüm> \
      [--baseline-image ppe-dashboard:latest] [--json rapor.json]

Ana veritabanına, ana snapshot klasörüne ve ana imaj etiketlerine DOKUNMAZ: küme scripts/ppe_ops.py
`ephemeral_cluster()` ile kurulur (internal ağ, host'a port yok, tmpfs, rastgele parolalar yalnız bellekte), aday ve
temel imajlar yalnız bu ağda tek seferlik container olarak çalışır ve sonunda silinir. İmajlar değiştirilmez.

Üretimdeki compose akışının aynısı uygulanır: pg_restore (sahiplik/yetki yok) -> `python -m db.roles setup`
(süper kullanıcı; sahiplik ppe_migrator'a) -> migrator rolüyle `alembic upgrade head && python -m db.roles grants`.

Kontroller (her biri PASS/FAIL; sonuç JSON'unda satır içeriği yoktur, yalnız sayım/özet/durum):
  1  SHA256SUMS ve geri yükleme; başlangıç revision'ı manifest ile aynı
  2  upgrade head başarılı; revision = aday imajın head'i
  3  çekirdek + isteğe bağlı tablolarda sayımlar ve satır md5 özetleri migration öncesiyle BİREBİR aynı
  4  inceleme tabloları var ve boş; uygulamanın okuma yolu (db.history.list_incidents, ppe_app rolüyle) tüm
     incident'ları UNREVIEWED döndürür
  5  ppe_app yetkileri: inceleme tablosunda SELECT/INSERT/UPDATE, geçmişte ve audit_log'da yalnız SELECT/INSERT,
     alembic_version'da yalnız SELECT, hiçbir tabloda DELETE/TRUNCATE yok, süper kullanıcı değil; sahiplik migrator
  6  aday imaj hazır (/health/ready 200, revision head)
  7  temel (eski) imaj 0007 şemasında: canlı (/healthz 200) ama HAZIR DEĞİL (schema outdated) — geri alma kısıtı
  8  inceleme kaydı varken downgrade REDDEDİLİR; revision ve kayıtlar yerinde kalır
  9  0007 durumunun pg_dump -> ayrı veritabanına geri yükleme turu: inceleme kayıtları ve özetler korunur
  10 migration başarısızlığı (çakışan nesne enjekte edilerek): işlem atomik geri alınır, revision 0006 kalır, veri
     özetleri aynı; aday imaj bu şemada hazır değil (eski imajla devam edilmeli); engel kaldırılınca tekrar başarılı
  11 inceleme verisi yokken downgrade 0006 başarılı, veri özetleri aynı; ardından yeniden upgrade başarılı"""
import argparse
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ppe_ops import (ROOT, OPTIONAL_TABLES, TABLES, OpsError, db_exec, db_facts, ephemeral_cluster,  # noqa: E402
                     fail, psql, table_digests, target_info, verify_sums, write_json)

DB, RT_DB, FAIL_DB = "ppe_rehearsal", "ppe_rehearsal_rt", "ppe_rehearsal_fail"
REVIEW_TABLES = ("incident_reviews", "incident_review_history")
APP, MIG = "ppe_app", "ppe_migrator"


class Rehearsal:
    def __init__(self, cluster: dict, candidate: str, baseline: str | None):
        self.c, self.candidate, self.baseline = cluster, candidate, baseline
        self.pw = {APP: secrets.token_urlsafe(24), MIG: secrets.token_urlsafe(24)}
        self.checks, self.ok, self.facts = [], True, {}
        self._apps = []

    # ------------------------------------------------------------ yardımcılar
    def check(self, no, name, cond, detail=""):
        self.ok &= bool(cond)
        self.checks.append({"no": no, "status": "PASS" if cond else "FAIL", "name": name, "detail": detail})
        print(f"[{'PASS' if cond else 'FAIL'}] {no:>2} {name}" + (f" — {detail}" if detail else ""), flush=True)

    def url(self, role: str, database: str) -> str:
        user, pw = (self.c["user"], self.c["password"]) if role == "admin" else (role, self.pw[role])
        return f"postgresql+psycopg://{user}:{pw}@{self.c['name']}:5432/{database}"

    def run_image(self, image: str, env: dict, *cmd, timeout=300) -> subprocess.CompletedProcess:
        """Tek seferlik container: yalnız internal ağ, salt okunur kök, yetenek yok. Değerler -e KEY ile (komut
        satırında değer yok)."""
        import os
        args = ["docker", "run", "--rm", "--network", self.c["network"], "--read-only", "--tmpfs", "/tmp",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--label", f"ppe.rehearsal={self.c['owner']}", *[x for k in env for x in ("-e", k)], image, *cmd]
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env={**os.environ, **env})

    def migrate(self, image: str, database: str, *alembic_args) -> subprocess.CompletedProcess:
        script = "alembic " + " ".join(alembic_args) + (" && python -m db.roles grants"
                                                        if alembic_args[:1] == ("upgrade",) else "")
        return self.run_image(image, {"DATABASE_URL": self.url(MIG, database)}, "sh", "-c", script)

    def roles_setup(self, database: str) -> subprocess.CompletedProcess:
        return self.run_image(self.candidate, {"ADMIN_DATABASE_URL": self.url("admin", database),
                                               "PPE_APP_PASSWORD": self.pw[APP],
                                               "PPE_MIGRATOR_PASSWORD": self.pw[MIG]},
                              "python", "-m", "db.roles", "setup")

    def restore(self, database: str, dump: Path) -> tuple[int, str]:
        psql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)', "postgres")
        psql(f'CREATE DATABASE "{database}"', "postgres")
        with open(dump, "rb") as f:
            r = db_exec("sh", "-c", f'pg_restore --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" '
                                    f'-d {database}', stdin=f, text=False, check=False)
        return r.returncode, r.stderr.decode("utf-8", "replace").strip()[-300:]

    def digests(self, database: str) -> dict:
        have = {r[0] for r in psql("SELECT table_name FROM information_schema.tables WHERE table_schema='public'",
                                   database)}
        return table_digests(database, [t for t in TABLES + OPTIONAL_TABLES if t in have])

    def start_app(self, image: str, database: str) -> str:
        import os
        env = {"DATABASE_URL": self.url(APP, database), "DATA_DIR": "/tmp/ppe-data", "SECURITY_PROFILE": "local"}
        r = subprocess.run(["docker", "run", "-d", "--network", self.c["network"], "--read-only", "--tmpfs", "/tmp",
                            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                            "--label", f"ppe.rehearsal={self.c['owner']}", *[x for k in env for x in ("-e", k)],
                            image], capture_output=True, text=True, env={**os.environ, **env})
        if r.returncode != 0:
            raise OpsError(f"uygulama container'ı başlatılamadı ({image}): {r.stderr.strip()[-200:]}")
        cid = r.stdout.strip()
        self._apps.append(cid)
        return cid

    @staticmethod
    def http(cid: str, path: str) -> tuple[int | None, dict]:
        code = ("import json,sys,urllib.request,urllib.error\n"
                "try:\n r=urllib.request.urlopen('http://127.0.0.1:8000'+sys.argv[1],timeout=5);c=r.status;b=r.read()\n"
                "except urllib.error.HTTPError as e:\n c=e.code;b=e.read()\n"
                "print(json.dumps({'code':c,'body':b.decode('utf-8','replace')[:2000]}))")
        for _ in range(60):
            r = subprocess.run(["docker", "exec", cid, "python", "-c", code, path], capture_output=True, text=True)
            if r.returncode == 0:
                out = json.loads(r.stdout)
                try:
                    return out["code"], json.loads(out["body"])
                except ValueError:
                    return out["code"], {"raw": out["body"][:200]}
            time.sleep(1)
        return None, {}

    @staticmethod
    def ready_detail(cid: str) -> dict:
        """Uygulamanın kendi hazırlık kontrolü (db.health.check_ready) container içinde: ayrıntı (revision) anonim
        HTTP yanıtında yoktur."""
        py = "import json; from db.health import check_ready; ok, d = check_ready(); print(json.dumps(d))"
        r = subprocess.run(["docker", "exec", cid, "python", "-c", py], capture_output=True, text=True)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"error": (r.stderr or r.stdout).strip()[-200:]}

    def stop_apps(self):
        for cid in self._apps:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
        self._apps.clear()

    def privileges(self, database: str) -> dict:
        out = {}
        for t in ("incident_reviews", "incident_review_history", "audit_log", "alembic_version", "incidents",
                  "violation_events"):
            out[t] = [p for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
                      if psql(f"SELECT has_table_privilege('{APP}', 'public.{t}', '{p}')", database)[0][0] == "t"]
        out["_app_superuser"] = psql(f"SELECT rolsuper FROM pg_roles WHERE rolname='{APP}'", database)[0][0] == "t"
        out["_review_owners"] = sorted({r[0] for r in psql(
            "SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename IN "
            "('incident_reviews','incident_review_history')", database)})
        return out

    # ------------------------------------------------------------ prova
    def run(self, src: Path) -> None:
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        bad = verify_sums(src)
        rc, err = self.restore(DB, src / "db.dump")
        start = db_facts(DB)
        self.check(1, "yedek bütünlüğü + geri yükleme + başlangıç revision'ı",
                   not bad and rc == 0 and start["alembic_revision"] == manifest["alembic_revision"],
                   f"bozuk {len(bad)}, pg_restore rc={rc}, revision {start['alembic_revision']}" + (f"; {err}" if rc else ""))
        if rc != 0:
            return
        r = self.roles_setup(DB)
        if r.returncode != 0:
            self.check(1, "db.roles setup", False, (r.stderr or r.stdout).strip()[-300:])
            return
        before_digest = self.digests(DB)
        self.facts["before"] = {"counts": start["counts"], "revision": start["alembic_revision"]}
        head = self.run_image(self.candidate, {}, "alembic", "heads").stdout.split()[0:1]

        r = self.migrate(self.candidate, DB, "upgrade", "head")
        after = db_facts(DB)
        self.check(2, "upgrade head", r.returncode == 0 and after["alembic_revision"] == head,
                   f"rc={r.returncode}, revision {start['alembic_revision']} -> {after['alembic_revision']} "
                   f"(aday head {head})" + ("" if r.returncode == 0 else f"; {(r.stderr or r.stdout).strip()[-300:]}"))
        if r.returncode != 0:
            return
        after_digest = self.digests(DB)
        same_counts = all(after["counts"].get(k) == v for k, v in start["counts"].items())
        same_digest = all(after_digest.get(k) == v for k, v in before_digest.items())
        self.check(3, "mevcut kayıtlar korundu (sayım + satır md5)", same_counts and same_digest,
                   f"{len(before_digest)} tablo; sayımlar {'aynı' if same_counts else 'FARKLI'}, "
                   f"özetler {'aynı' if same_digest else 'FARKLI: ' + str([k for k in before_digest if after_digest.get(k) != before_digest[k]])}")
        self.facts["after_upgrade"] = {"counts": after["counts"], "revision": after["alembic_revision"]}

        review_counts = {t: after["counts"].get(t) for t in REVIEW_TABLES}
        py = ("import collections,json\nfrom db.session import get_session\nfrom db import history\n"
              "s=get_session()\nr=history.list_incidents(s,limit=100000,active_window=90)\n"
              "print(json.dumps({'total':r['total'],'review':dict(collections.Counter(i['review_status'] for i in r['items']))}))")
        r = self.run_image(self.candidate, {"DATABASE_URL": self.url(APP, DB)}, "python", "-c", py)
        try:
            app_view = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            app_view = {"error": (r.stderr or r.stdout).strip()[-300:]}
        n_inc = after["counts"]["incidents"]
        self.check(4, "inceleme tabloları boş; uygulama tüm incident'ları UNREVIEWED görüyor",
                   review_counts == {t: 0 for t in REVIEW_TABLES} and app_view.get("total") == n_inc
                   and app_view.get("review") == ({"UNREVIEWED": n_inc} if n_inc else {}),
                   f"tablolar {review_counts}; uygulama görünümü {app_view}")

        priv = self.privileges(DB)
        want = {"incident_reviews": ["SELECT", "INSERT", "UPDATE"], "incident_review_history": ["SELECT", "INSERT"],
                "audit_log": ["SELECT", "INSERT"], "alembic_version": ["SELECT"],
                "incidents": ["SELECT", "INSERT", "UPDATE"], "violation_events": ["SELECT", "INSERT", "UPDATE"]}
        bad_priv = {t: priv.get(t) for t, w in want.items() if priv.get(t) != w}
        self.check(5, "yetki matrisi (ppe_app) ve sahiplik (ppe_migrator)",
                   not bad_priv and not priv["_app_superuser"] and priv["_review_owners"] == [MIG],
                   f"farklı: {bad_priv}; app superuser={priv['_app_superuser']}; inceleme tablosu sahibi "
                   f"{priv['_review_owners']}")
        self.facts["privileges"] = priv

        try:
            cid = self.start_app(self.candidate, DB)
            code, body = self.http(cid, "/health/ready")
            det = self.ready_detail(cid)
            self.check(6, "aday imaj hazır", code == 200 and body.get("status") == "ready" and det.get("revision") == head,
                       f"HTTP {code} {body}; check_ready {det}")
            if self.baseline:
                bid = self.start_app(self.baseline, DB)
                lc, _ = self.http(bid, "/healthz")
                rc_, rbody = self.http(bid, "/health/ready")
                bdet = self.ready_detail(bid)
                self.check(7, "temel imaj 0007 şemasında canlı ama hazır değil (geri alma kısıtı)",
                           lc == 200 and rc_ == 503 and bdet.get("schema") == "outdated",
                           f"/healthz {lc}; /health/ready {rc_} {rbody}; check_ready {bdet}")
        finally:
            self.stop_apps()

        # 8: inceleme kaydı varken downgrade reddi (kayıt süper kullanıcıyla, uygulamanın yazacağı biçimde)
        uid = psql("SELECT min(id) FROM app_users", DB)[0][0]
        inc = psql("SELECT id FROM incidents ORDER BY created_at, id LIMIT 1", DB)
        if uid and inc:
            psql(f"INSERT INTO incident_reviews (incident_id, status, version, reviewed_by, note) VALUES "
                 f"('{inc[0][0]}', 'FALSE_ALARM', 1, {uid}, 'migration provası') ;"
                 f"INSERT INTO incident_review_history (incident_id, version, status, previous_status, reviewer_id, "
                 f"note, request_id) VALUES ('{inc[0][0]}', 1, 'FALSE_ALARM', NULL, {uid}, 'migration provası', "
                 f"gen_random_uuid())", DB)
            r = self.migrate(self.candidate, DB, "downgrade", "0006")
            f8 = db_facts(DB)
            self.check(8, "inceleme kaydı varken downgrade reddedildi",
                       r.returncode != 0 and f8["alembic_revision"] == head
                       and f8["counts"].get("incident_reviews") == 1 and "Downgrade durduruldu" in (r.stderr + r.stdout),
                       f"rc={r.returncode}, revision {f8['alembic_revision']}, incident_reviews "
                       f"{f8['counts'].get('incident_reviews')}")
            # 9: 0007 yedek/geri yükleme turu (döküm container içinde akar; gerçek veri host'a yazılmaz)
            psql(f'DROP DATABASE IF EXISTS "{RT_DB}" WITH (FORCE)', "postgres")
            psql(f'CREATE DATABASE "{RT_DB}"', "postgres")
            d = db_exec("bash", "-c", f'set -o pipefail; pg_dump -Fc -U "$POSTGRES_USER" -d {DB} | '
                                    f'pg_restore --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" '
                                    f'-d {RT_DB}', check=False)
            rc9, err9 = d.returncode, (d.stderr or "").strip()[-300:]
            same9 = rc9 == 0 and self.digests(RT_DB) == self.digests(DB)
            self.check(9, "0007 dökümü -> ayrı DB: inceleme kayıtları ve tüm özetler aynı", same9,
                       f"pg_restore rc={rc9}; incident_reviews {db_facts(RT_DB)['counts'].get('incident_reviews') if rc9 == 0 else '?'}"
                       + (f"; {err9}" if rc9 else ""))
        else:
            self.check(8, "inceleme kaydı varken downgrade reddi", False, "yedekte kullanıcı veya incident yok")

        # 10: atomik başarısızlık + kurtarma
        rc, err = self.restore(FAIL_DB, src / "db.dump")
        self.roles_setup(FAIL_DB)
        base_digest = self.digests(FAIL_DB)
        psql("CREATE TABLE public.incident_reviews (enjekte_cakisma int)", FAIL_DB)
        r = self.migrate(self.candidate, FAIL_DB, "upgrade", "head")
        f10 = db_facts(FAIL_DB)
        hist_exists = psql("SELECT to_regclass('public.incident_review_history') IS NOT NULL", FAIL_DB)[0][0] == "t"
        dig10 = self.digests(FAIL_DB)
        atomic = (r.returncode != 0 and f10["alembic_revision"] == manifest["alembic_revision"] and not hist_exists
                  and all(dig10.get(k) == v for k, v in base_digest.items()))
        ready, rdet = None, {}
        try:
            cid = self.start_app(self.candidate, FAIL_DB)
            ready = self.http(cid, "/health/ready")
            rdet = self.ready_detail(cid)
        finally:
            self.stop_apps()
        psql("DROP TABLE public.incident_reviews", FAIL_DB)
        r2 = self.migrate(self.candidate, FAIL_DB, "upgrade", "head")
        self.check(10, "başarısız migration atomik geri alındı; aday imaj hazır değil; engel kalkınca başarılı",
                   atomic and ready and ready[0] == 503 and rdet.get("schema") == "outdated" and r2.returncode == 0
                   and db_facts(FAIL_DB)["alembic_revision"] == head,
                   f"ilk deneme rc={r.returncode}, revision {f10['alembic_revision']}, geçmiş tablosu "
                   f"{'VAR' if hist_exists else 'yok'}, özetler {'aynı' if atomic else 'FARKLI?'}; aday imaj "
                   f"/health/ready {ready[0] if ready else None} ({rdet.get('schema')}); ikinci deneme rc={r2.returncode}")

        # 11: inceleme verisi yokken downgrade ve yeniden upgrade
        r = self.migrate(self.candidate, FAIL_DB, "downgrade", "0006")
        f11 = db_facts(FAIL_DB)
        gone = psql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN "
                    "('incident_reviews','incident_review_history')", FAIL_DB)[0][0] == "0"
        dig11 = self.digests(FAIL_DB)
        r2 = self.migrate(self.candidate, FAIL_DB, "upgrade", "head")
        self.check(11, "veri yokken downgrade 0006 ve yeniden upgrade",
                   r.returncode == 0 and f11["alembic_revision"] == manifest["alembic_revision"] and gone
                   and all(dig11.get(k) == v for k, v in base_digest.items()) and r2.returncode == 0,
                   f"downgrade rc={r.returncode} -> {f11['alembic_revision']}, inceleme tabloları "
                   f"{'kaldırıldı' if gone else 'DURUYOR'}; yeniden upgrade rc={r2.returncode}")


def image_labels(image: str) -> dict | None:
    r = subprocess.run(["docker", "image", "inspect", image, "--format", "{{json .Config.Labels}}"],
                       capture_output=True, text=True)
    return json.loads(r.stdout or "null") if r.returncode == 0 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("backup")
    ap.add_argument("--candidate-image", required=True, help="0007 içeren aday imaj (release önerilir)")
    ap.add_argument("--baseline-image", default="ppe-dashboard:latest",
                    help="şu an üretimde çalışan imaj (yalnız geri alma uyumluluğu için çalıştırılır; değiştirilmez)")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--json", help="sonuç raporu (satır içeriği yok)")
    a = ap.parse_args(argv)
    src = Path(a.backup) if Path(a.backup).is_absolute() else ROOT / a.backup
    src = src.resolve()
    if not (src / "manifest.json").is_file():
        fail(f"manifest.json bulunamadı: {src}")
    cand = image_labels(a.candidate_image)
    if cand is None:
        fail(f"aday imaj yok: {a.candidate_image}")
    baseline = None if a.no_baseline else a.baseline_image
    if baseline and image_labels(baseline) is None:
        fail(f"temel imaj yok: {baseline}")
    kind = (cand or {}).get("com.ppe.build.kind", "unknown")
    print(f"aday imaj: {a.candidate_image} (kind={kind}, tree={(cand or {}).get('com.ppe.build.tree-state')}, "
          f"revision={(cand or {}).get('org.opencontainers.image.revision', '?')[:12]})")
    if kind != "release":
        print("UYARI: aday imaj RELEASE değil — prova geçerli, ancak üretime yalnız release imajı alınmalı.")
    try:
        with ephemeral_cluster("ppe-mig-rehearsal") as cl:
            t = target_info()
            if t["kind"] != "ephemeral":
                fail("güvenlik: hedef geçici küme değil")
            print(f"izole küme {t['container_id']} (internal ağ {cl['network']}); ana kümeye bağlanılmaz", flush=True)
            reh = Rehearsal(cl, a.candidate_image, baseline)
            try:
                reh.run(src)
            except OpsError as exc:                         # adım hatası: rapora FAIL olarak girer, temizlik sürer
                reh.check(99, "prova adımı hata verdi", False, str(exc)[-300:])
            finally:
                reh.stop_apps()
                left = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=ppe.rehearsal={cl['owner']}"],
                                      capture_output=True, text=True).stdout.split()
                if left:                                     # yalnız bu koşunun etiketli container'ları
                    subprocess.run(["docker", "rm", "-f", *left], capture_output=True)
    except OpsError as exc:
        fail(f"prova kurulamadı: {exc}")
    result = "PASS" if reh.ok and reh.checks else "FAIL"
    print("SONUÇ:", result)
    if a.json:
        write_json(Path(a.json), {"backup": src.name, "manifest_revision": json.loads(
            (src / "manifest.json").read_text(encoding="utf-8"))["alembic_revision"],
            "candidate_image": a.candidate_image, "candidate_labels": cand, "baseline_image": baseline,
            "result": result, "checks": reh.checks, "facts": reh.facts})
    return 0 if result == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
