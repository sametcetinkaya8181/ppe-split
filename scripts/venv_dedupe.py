"""Çift kurulum kaydı onarımı (FAZ 10-A.1): bir site-packages'ta aynı paketin iki `*.dist-info` dizini varsa
(yarıda kalmış yükseltme), yeni sürümün dosyalarının RECORD karmalarıyla TAM eşleştiğini doğrular; eski sürümün
dist-info dizinini ve yalnız eski sürüme ait (yeni RECORD'da olmayan) öksüz dosyaları karantina dizinine TAŞIR.
Hiçbir dosya silinmez; ağ erişimi yok; yeni sürüm dosyalarına dokunulmaz.

  python scripts/venv_dedupe.py <site-packages>                      # kuru çalıştırma (varsayılan): yalnız rapor
  python scripts/venv_dedupe.py <site-packages> --apply --quarantine <dizin>
  python scripts/venv_dedupe.py --rollback <dizin>                   # karantinadakileri yerine geri taşır

Yeni sürümün tek bir dosyası bile RECORD'la eşleşmiyorsa o paket için hiçbir şey yapılmaz (karışık kurulum)."""
import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

DIST = re.compile(r"^(?P<name>.+?)-(?P<ver>\d[^-]*)\.dist-info$")


def lp(p: Path) -> Path:
    """Windows'ta 260 karakter sınırını aşan yollar için \\\\?\\ öneki (aksi hâlde exists() yanlışlıkla False)."""
    s = str(p)
    return Path("\\\\?\\" + s) if os.name == "nt" and len(s) >= 240 and not s.startswith("\\\\?\\") else p


def vkey(v: str):
    return [(0, int(x)) if x.isdigit() else (1, x) for x in re.split(r"[.+]", v)]


def records(di: Path) -> dict:
    out = {}
    with (di / "RECORD").open(encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if row:
                out[row[0]] = row[1] if len(row) > 1 else ""
    return out


def matches(sp: Path, rec: dict) -> tuple:
    ok, bad = 0, []
    for rel, h in rec.items():
        if not h:
            continue
        f = lp((sp / rel).resolve())
        algo, _, want = h.partition("=")
        if f.is_file() and base64.urlsafe_b64encode(hashlib.new(algo, f.read_bytes()).digest()).rstrip(b"=").decode() == want:
            ok += 1
        else:
            bad.append(rel)
    return ok, bad


def plan(sp: Path) -> list:
    groups = defaultdict(list)
    for di in sp.glob("*.dist-info"):
        m = DIST.match(di.name)
        if m:
            groups[m["name"].lower().replace("_", "-")].append((m["ver"], di))
    out = []
    for name, lst in sorted(groups.items()):
        if len(lst) < 2:
            continue
        lst.sort(key=lambda x: vkey(x[0]))
        new_ver, new_di = lst[-1]
        new_rec = records(new_di)
        ok, bad = matches(sp, new_rec)
        item = {"package": name, "keep": new_ver, "new_files_ok": ok, "new_files_bad": bad[:5],
                "remove": [], "orphans": []}
        if bad:
            item["skip"] = "yeni sürüm dosyaları RECORD ile eşleşmiyor (karışık kurulum) — elle inceleyin"
        else:
            for old_ver, old_di in lst[:-1]:
                item["remove"].append(old_di.name)
                item["orphans"] += sorted(p for p in records(old_di) if p not in new_rec and ".dist-info/" not in p
                                          and lp((sp / p).resolve()).exists()
                                          and (sp / p).resolve().is_relative_to(sp.parent.parent))
        out.append(item)
    return out


def move(src: Path, dst: Path, log: list, base: Path):
    src = src.resolve()
    lp(dst.parent).mkdir(parents=True, exist_ok=True)
    shutil.move(str(lp(src)), str(lp(dst)))
    log.append({"from": str(src.relative_to(base)), "to": str(dst)})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("site_packages", nargs="?")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--quarantine")
    ap.add_argument("--rollback")
    a = ap.parse_args(argv)
    if a.rollback:
        q = Path(a.rollback)
        man = json.loads((q / "manifest.json").read_text(encoding="utf-8"))
        base = Path(man["venv"])
        for m in reversed(man["moved"]):
            dst = base / m["from"]
            lp(dst.parent).mkdir(parents=True, exist_ok=True)
            shutil.move(str(lp(Path(m["to"]))), str(lp(dst)))
        print(f"geri alındı: {len(man['moved'])} öğe -> {base}")
        return 0
    sp = Path(a.site_packages).resolve()
    items = plan(sp)
    print(json.dumps([{**i, "orphans": len(i["orphans"]), "orphan_examples":
                       [p for p in i["orphans"] if not p.endswith(".pyc")][:6]} for i in items], ensure_ascii=False, indent=1))
    if not a.apply:
        print("kuru çalıştırma: değişiklik yapılmadı (--apply --quarantine DIZIN ile uygulanır)")
        return 0
    if not a.quarantine:
        raise SystemExit("--apply için --quarantine DIZIN gerekli")
    venv = sp.parent.parent if sp.parent.name.lower() == "lib" else sp.parent
    q = Path(a.quarantine).resolve()
    q.mkdir(parents=True, exist_ok=False)
    moved = []
    for i in items:
        if i.get("skip"):
            continue
        for rel in i["orphans"]:
            move(sp / rel, q / "files" / rel, moved, venv)
        # yalnız __pycache__ kalan dizinler (ör. pkg_resources/) namespace paketi gibi içe aktarılabilir kalmasın
        dirs = sorted({(sp / rel).parent for rel in i["orphans"]}, key=lambda d: len(d.parts), reverse=True)
        for d in dirs:
            while d != sp and lp(d).is_dir() and all(e.name == "__pycache__" for e in lp(d).iterdir()):
                move(d, q / "dirs" / str(len(moved)) / d.name, moved, venv)    # benzersiz hedef (çakışma yok)
                d = d.parent
        for d in i["remove"]:
            move(sp / d, q / "dist-info" / d, moved, venv)
    (q / "manifest.json").write_text(json.dumps({"venv": str(venv), "site_packages": str(sp), "plan": items,
                                                 "moved": moved}, ensure_ascii=False, indent=1), encoding="utf-8")
    for i in items:                                   # son doğrulama: tutulan sürümler hâlâ tam
        if not i.get("skip"):
            new_di = next(sp.glob(f"*-{i['keep']}.dist-info"))
            ok, bad = matches(sp, records(new_di))
            if bad:
                print(f"UYARI: {i['package']} {i['keep']} doğrulaması başarısız: {bad[:3]}", file=sys.stderr)
                return 2
    print(f"uygulandı: {len(moved)} öğe karantinada -> {q} (geri alma: --rollback {q})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
