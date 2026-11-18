"""FAZ 10-A.5A gizli bilgi denetimi: üretim INGEST_TOKEN'ının test yığınlarına / kanıt dosyalarına / Git'e sızıp
sızmadığını SALT OKUNUR denetler. Hiçbir gizli değer yazdırılmaz; çıktı yalnız eşleşme/sayım ve dosya yollarıdır.

  python tools/reliability/secret_audit.py            # insan okunur özet
  python tools/reliability/secret_audit.py --json     # makine okunur

Denetimler:
  1. Üretim değerleri: kök .env ve detector/.env INGEST_TOKEN (+ kök .env parolaları); boş/eşit mi.
  2. Test yığını env dosyaları (output/rel.env, output/eval_stack/rel.env): hiçbir üretim değerine eşit olmamalı.
  3. Test container'larının (ppe-eval, ppe-rel) çalışma ortamı (docker inspect): üretim değeri içermemeli.
  4. Kanıt klasörü tools/reliability/output/** metin dosyaları: üretim değeri geçmemeli.
  5. Git: izlenen dosyalar + commit'lenmeye aday (izlenmeyen, yok sayılmayan) dosyalar + tüm geçmiş
     (git log --all -p): üretim değeri geçmemeli.

FAZ 02-A.1:
  - `--prod-root <üretim worktree'si>`: üretim .env dosyaları bu araçla aynı worktree'de değilse (ör. araç
    integration worktree'sinden çalışırken üretim ppe-split-main'de) referans değerler oradan okunur.
  - FAIL-CLOSED: aranabilir tek bir üretim değeri bile yüklenemezse sonuç FAIL'dır (eskiden boş referans
    kümesiyle "0 eşleşme = PASS" veriyordu)."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tools" / "reliability" / "output"
PROD_FILES = {"root_env": ROOT / ".env", "detector_env": ROOT / "detector" / ".env"}
PROD_KEYS = ("INGEST_TOKEN", "INGEST_TOKEN_NEXT", "POSTGRES_PASSWORD", "PPE_APP_PASSWORD", "PPE_MIGRATOR_PASSWORD")
TEST_ENVS = {"ppe-rel": OUT / "rel.env", "ppe-eval": OUT / "eval_stack" / "rel.env"}
TEST_PROJECTS = ("ppe-eval", "ppe-rel")
TEXT_SUFFIXES = {".env", ".log", ".json", ".jsonl", ".md", ".txt", ".csv", ".yml", ".yaml"}
MIN_LEN = 12                    # kısa/boş değerler aranmaz (yanlış eşleşme)


def read_env(path: Path) -> dict:
    vals = {}
    if not path.exists():
        return vals
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _secrets(prod: dict) -> dict:
    """{etiket: değer} — yalnız aranabilir uzunluktaki üretim değerleri."""
    out = {}
    for name, vals in prod.items():
        for k in PROD_KEYS:
            v = vals.get(k, "")
            if len(v) >= MIN_LEN:
                out[f"{name}:{k}"] = v
    return out


def _hits(text: str, secrets: dict) -> list:
    return sorted(label for label, v in secrets.items() if v in text)


def audit() -> dict:
    prod = {n: read_env(p) for n, p in PROD_FILES.items()}
    secrets = _secrets(prod)
    tok_root, tok_det = prod["root_env"].get("INGEST_TOKEN", ""), prod["detector_env"].get("INGEST_TOKEN", "")
    res = {"production": {"root_env_present": PROD_FILES["root_env"].exists(),
                          "detector_env_present": PROD_FILES["detector_env"].exists(),
                          "ingest_token_root_set": bool(tok_root), "ingest_token_detector_set": bool(tok_det),
                          "ingest_token_root_equals_detector": bool(tok_root) and tok_root == tok_det,
                          "searchable_secret_labels": sorted(secrets)}}
    # 2. test env dosyaları
    tenv = {}
    for proj, p in TEST_ENVS.items():
        vals = read_env(p)
        tenv[proj] = {"present": p.exists(), "equals_production": sorted(
            f"{k}=={lbl}" for k, v in vals.items() for lbl, s in secrets.items() if v and v == s)}
    res["test_env_files"] = tenv
    # 3. container ortamları
    cont = {}
    try:
        names = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True,
                               timeout=30).stdout.split()
        for n in names:
            if not n.startswith(tuple(f"{p}-" for p in TEST_PROJECTS)):
                continue
            r = subprocess.run(["docker", "inspect", "--format", "{{json .Config.Env}}", n], capture_output=True,
                               text=True, timeout=30)
            env = json.loads(r.stdout or "[]") or []
            cont[n] = _hits("\n".join(env), secrets)
        res["test_containers"] = {"checked": len(cont), "with_production_value": {k: v for k, v in cont.items() if v}}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as e:
        res["test_containers"] = {"error": type(e).__name__}
    # 4. kanıt klasörü
    files, leaks = 0, {}
    for p in OUT.rglob("*"):
        if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES and p.stat().st_size < 50 * 2**20:
            files += 1
            h = _hits(p.read_text(encoding="utf-8", errors="replace"), secrets)
            if h:
                leaks[str(p.relative_to(ROOT)).replace("\\", "/")] = h
    res["evidence_output"] = {"text_files_scanned": files, "files_with_production_value": leaks}
    # 5. Git
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True).stdout.split("\0")
    candidates = subprocess.run(["git", "ls-files", "-z", "--others", "--exclude-standard"], cwd=ROOT,
                                capture_output=True, text=True).stdout.split("\0")
    tracked += [f for f in candidates if f]
    tleaks = {}
    for f in filter(None, tracked):
        p = ROOT / f
        if p.is_file() and p.stat().st_size < 50 * 2**20:
            h = _hits(p.read_text(encoding="utf-8", errors="replace"), secrets)
            if h:
                tleaks[f] = h
    hist = subprocess.run(["git", "log", "--all", "-p", "--no-color"], cwd=ROOT, capture_output=True,
                          encoding="utf-8", errors="replace").stdout
    res["git"] = {"tracked_files_scanned": len([f for f in tracked if f]),
                  "untracked_candidates_scanned": len([f for f in candidates if f]),
                  "tracked_with_production_value": tleaks,
                  "history_bytes_scanned": len(hist), "history_hits": _hits(hist, secrets)}
    res["production"]["reference_root"] = str(PROD_FILES["root_env"].parent)
    clean = (bool(secrets)                              # FAZ 02-A.1: referans değer yoksa denetim anlamsız -> FAIL
             and not any(v["equals_production"] for v in tenv.values())
             and not res["test_containers"].get("with_production_value") and "error" not in res["test_containers"]
             and not leaks and not tleaks and not res["git"]["history_hits"])
    res["verdict"] = "PASS" if clean else "FAIL"
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--prod-root", help="üretim .env dosyalarının bulunduğu worktree (varsayılan: bu worktree)")
    args = ap.parse_args()
    if args.prod_root:
        pr = Path(args.prod_root).resolve()
        PROD_FILES.update(root_env=pr / ".env", detector_env=pr / "detector" / ".env")
    res = audit()
    if not res["production"]["searchable_secret_labels"]:
        res["production"]["error"] = "aranabilir üretim değeri yüklenemedi (--prod-root?) — FAIL-CLOSED"
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for k, v in res.items():
            print(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    sys.exit(0 if res["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
