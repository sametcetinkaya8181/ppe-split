"""PostgreSQL rol ayrımı (least privilege). Alembic migration'larından AYRI, idempotent yönetim adımı.

Roller (adlar ortamdan değiştirilebilir):
  ppe_migrator : tabloların sahibi; şemada CREATE. Yalnızca migration'lar bu rolle çalışır. SUPERUSER değil.
  ppe_app      : dashboard. CONNECT + şema USAGE + tablolarda SELECT/INSERT/UPDATE + dizilerde USAGE/SELECT.
                 DELETE, TRUNCATE, DDL, SUPERUSER, CREATEDB, CREATEROLE YOK. alembic_version'da yalnızca SELECT.

Komutlar (python -m db.roles ...):
  setup  : ADMIN_DATABASE_URL (süper kullanıcı) ile rolleri oluşturur/günceller, veritabanı ve şema yetkilerini
           ayarlar, var olan nesnelerin sahipliğini ppe_migrator'a devreder, uygulama yetkilerini verir.
           PPE_APP_PASSWORD / PPE_MIGRATOR_PASSWORD boşsa HİÇBİR şey yapmaz (eski tek-rol kurulumu çalışmaya
           devam eder) ve bunu bildirir.
  grants : DATABASE_URL (tablo sahibi = migrator) ile uygulama yetkilerini yeniden verir. Migration sonrası çalışır.
  check  : DATABASE_URL rolünün yetki özetini yazdırır (parola/adres yazdırılmaz).

Parolalar SQL'e yalnızca istemci tarafında literal olarak gömülür; hiçbir çıktıya, loga veya dosyaya yazılmaz.
Veri silinmez, tablo düşürülmez; yalnızca rol, sahiplik ve GRANT/REVOKE işlemleri yapılır."""
import os
import re
import sys

import psycopg
from psycopg import sql

ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
APP_TABLE_PRIVS = "SELECT, INSERT, UPDATE"
ROLE_ATTRS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"


class RoleError(RuntimeError):
    """Rol yapılandırması hatası (mesaj parola/adres içermez)."""


def role_names() -> tuple[str, str]:
    app, mig = os.getenv("PPE_APP_ROLE", "ppe_app"), os.getenv("PPE_MIGRATOR_ROLE", "ppe_migrator")
    for r in (app, mig):
        if not ROLE_RE.match(r):
            raise RoleError(f"geçersiz rol adı: {r!r}")
    if app == mig:
        raise RoleError("uygulama ve migration rolleri farklı olmalı")
    return app, mig


def _connect(url: str) -> psycopg.Connection:
    if not url:
        raise RoleError("veritabanı adresi tanımlı değil")
    try:
        return psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1), connect_timeout=5)
    except psycopg.Error as exc:
        raise RoleError(f"veritabanına bağlanılamadı ({type(exc).__name__})") from None


def _ensure_role(cur, name: str, password: str):
    exists = cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    cur.execute(sql.SQL(f"{verb} ROLE {{}} WITH {ROLE_ATTRS} PASSWORD {{}}").format(
        sql.Identifier(name), sql.Literal(password)))


def setup(conn: psycopg.Connection, *, app: str, migrator: str, app_password: str, migrator_password: str) -> None:
    """Süper kullanıcı bağlantısıyla, TEK transaction'da. Tekrar çalıştırılabilir."""
    cur = conn.cursor()
    if not cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
        raise RoleError("setup süper kullanıcı (POSTGRES_USER) ile çalıştırılmalı")
    db = cur.execute("SELECT current_database()").fetchone()[0]
    I = sql.Identifier
    _ensure_role(cur, migrator, migrator_password)
    _ensure_role(cur, app, app_password)
    cur.execute(sql.SQL("REVOKE CONNECT, TEMPORARY ON DATABASE {} FROM PUBLIC").format(I(db)))
    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(I(db), I(app), I(migrator)))
    cur.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO {}").format(I(db), I(migrator)))
    cur.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    cur.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(I(migrator)))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(I(app)))
    cur.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(I(app)))
    # sahiplik: tablolar (kimlik dizileri tabloyla birlikte taşınır), bağımsız diziler ve görünümler
    for kind, rel in (("TABLE", "r"), ("TABLE", "p"), ("SEQUENCE", "S"), ("VIEW", "v")):
        rows = cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = %s AND pg_get_userbyid(c.relowner) <> %s "
            "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i'))",
            (rel, migrator)).fetchall()
        for (name,) in rows:
            cur.execute(sql.SQL(f"ALTER {kind} {{}} OWNER TO {{}}").format(I("public", name), I(migrator)))
    cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT " + APP_TABLE_PRIVS
                        + " ON TABLES TO {}").format(I(migrator), I(app)))
    cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES "
                        "TO {}").format(I(migrator), I(app)))
    grants(conn, app=app)


def grants(conn: psycopg.Connection, *, app: str) -> bool:
    """Uygulama yetkilerini (yeniden) verir. Tablo sahibi veya süper kullanıcı çalıştırmalı. Rol yoksa False."""
    cur = conn.cursor()
    if not cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app,)).fetchone():
        return False
    I = sql.Identifier
    cur.execute(sql.SQL("GRANT " + APP_TABLE_PRIVS + " ON ALL TABLES IN SCHEMA public TO {}").format(I(app)))
    cur.execute(sql.SQL("REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM {}")
                .format(I(app)))
    cur.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(I(app)))
    if cur.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]:
        cur.execute(sql.SQL("REVOKE INSERT, UPDATE ON public.alembic_version FROM {}").format(I(app)))
    if cur.execute("SELECT to_regclass('public.audit_log')").fetchone()[0]:      # 0004: denetim kaydı yalnız ekleme
        cur.execute(sql.SQL("REVOKE UPDATE ON public.audit_log FROM {}").format(I(app)))
    return True


def summary(conn: psycopg.Connection) -> dict:
    """Bağlı rolün özellikleri ve tablo yetkileri (parola/adres yok)."""
    cur = conn.cursor()
    who, sup, cdb, crole = cur.execute(
        "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user").fetchone()
    privs = {}
    for (t,) in cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1").fetchall():
        privs[t] = [p for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
                    if cur.execute("SELECT has_table_privilege(current_user, %s, %s)", (f"public.{t}", p)).fetchone()[0]]
    create = cur.execute("SELECT has_schema_privilege(current_user, 'public', 'CREATE')").fetchone()[0]
    return {"role": who, "superuser": sup, "createdb": cdb, "createrole": crole, "schema_create": create,
            "tables": privs}


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        app, mig = role_names()
        if cmd == "setup":
            app_pw, mig_pw = os.getenv("PPE_APP_PASSWORD", ""), os.getenv("PPE_MIGRATOR_PASSWORD", "")
            if not app_pw or not mig_pw:
                print("db.roles: PPE_APP_PASSWORD / PPE_MIGRATOR_PASSWORD tanımlı değil; rol ayrımı ATLANDI. "
                      "Dashboard DATABASE_URL'deki rolle (eski kurulumda süper kullanıcı) çalışır.", flush=True)
                return 0
            with _connect(os.getenv("ADMIN_DATABASE_URL", "")) as conn:
                setup(conn, app=app, migrator=mig, app_password=app_pw, migrator_password=mig_pw)
            print(f"db.roles: roller hazır ({mig}: sahip/migration, {app}: uygulama).", flush=True)
        elif cmd == "grants":
            with _connect(os.getenv("DATABASE_URL", "")) as conn:
                done = grants(conn, app=app)
            print(f"db.roles: {app} yetkileri " + ("güncellendi." if done else "atlandı (rol yok)."), flush=True)
        elif cmd == "check":
            with _connect(os.getenv("DATABASE_URL", "")) as conn:
                print(summary(conn), flush=True)
        else:
            print("kullanım: python -m db.roles setup|grants|check", file=sys.stderr)
            return 2
    except (RoleError, psycopg.Error) as exc:
        msg = str(exc) if isinstance(exc, RoleError) else f"PostgreSQL hatası ({type(exc).__name__})"
        print(f"db.roles HATA: {msg}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
