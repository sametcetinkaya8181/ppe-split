"""Kullanıcı yönetimi komut satırı (ilk yönetici dahil). Parola terminalden gizli sorulur veya --password-stdin
ile standart girdiden okunur; komut satırı argümanı olarak ALINMAZ (geçmişe/süreç listesine düşmesin).

    docker compose exec dashboard python -m auth.cli create admin --role admin
    docker compose exec -T dashboard python -m auth.cli create izleyici --role viewer --cameras giris,kule --password-stdin
    docker compose exec dashboard python -m auth.cli list
    docker compose exec dashboard python -m auth.cli set-password admin
    docker compose exec dashboard python -m auth.cli disable kullanici | enable kullanici | revoke kullanici"""
import argparse
import getpass
import sys

from sqlalchemy.exc import SQLAlchemyError

from db.session import get_session

from . import passwords, store


def _password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    p1 = getpass.getpass("Parola: ")
    if getpass.getpass("Parola (tekrar): ") != p1:
        raise SystemExit("parolalar eşleşmiyor")
    return p1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m auth.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("username")
    c.add_argument("--role", required=True, choices=store.ROLES)
    c.add_argument("--cameras", help="virgülle kamera kimlikleri (boş = tüm kameralar)")
    c.add_argument("--password-stdin", action="store_true")
    p = sub.add_parser("set-password")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true")
    sub.add_parser("list")
    for name in ("disable", "enable", "revoke"):
        sub.add_parser(name).add_argument("username")
    args = ap.parse_args(argv)
    try:
        with get_session() as s, s.begin():
            if args.cmd == "list":
                for u in store.list_users(s):
                    print(f"{u['id']:>4} {u['username']:<24} {u['role']:<10} "
                          f"{'devre dışı' if u['disabled'] else 'etkin':<10} kameralar={u['camera_scope'] or 'tümü'}")
                return 0
            if args.cmd == "create":
                cams = [x.strip() for x in args.cameras.split(",") if x.strip()] if args.cameras else None
                uid = store.create_user(s, args.username, _password(args), args.role, cams)
                store.audit(s, "user_create", "success", detail={"target_user_id": uid, "role": args.role,
                                                                 "reason": "cli"})
                print(f"kullanıcı oluşturuldu: {store.normalize_username(args.username)} ({args.role})")
                return 0
            user = store.get_user(s, username=store.normalize_username(args.username))
            if user is None:
                raise SystemExit("kullanıcı yok")
            if args.cmd == "set-password":
                store.set_password(s, user["id"], _password(args))
                store.audit(s, "password_reset", "success", detail={"target_user_id": user["id"], "reason": "cli"})
            elif args.cmd in ("disable", "enable"):
                if args.cmd == "disable" and user["role"] == "admin" and store.active_admins(s) <= 1:
                    raise SystemExit("son etkin yönetici devre dışı bırakılamaz")
                store.update_user(s, user["id"], disabled=args.cmd == "disable")
                store.revoke_user_sessions(s, user["id"], "policy_changed")
                store.audit(s, "user_update", "success", detail={"target_user_id": user["id"],
                                                                 "changes": [args.cmd], "reason": "cli"})
            else:
                n = store.revoke_user_sessions(s, user["id"], "admin_revoked")
                store.audit(s, "sessions_revoke", "success", detail={"target_user_id": user["id"], "count": n,
                                                                     "reason": "cli"})
            print("tamam")
            return 0
    except (store.StoreError, passwords.PolicyError) as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1
    except (SQLAlchemyError, RuntimeError) as exc:
        print(f"HATA: veritabanı ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
