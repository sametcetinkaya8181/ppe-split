"""FAZ 01: dashboard imajını kaynak commit'e izlenebilir biçimde oluşturur (OCI etiketleri + /app/build-info.json +
yerel build manifesti).

  python scripts/build_image.py                    # DEVELOPMENT imajı: ppe-dashboard-dev:<sha12>[-dirty]
  python scripts/build_image.py --release 0.10.7   # RELEASE imajı: ppe-dashboard:0.10.7 + ppe-dashboard:sha-<sha12>
  python scripts/build_image.py --dry-run [...]    # yalnız planı yazdırır, build yapmaz

Kurallar:
  - RELEASE yalnız TEMİZ çalışma ağacından (izlenen değişiklik ve izlenmeyen dosya yok), geçerli SemVer ile ve
    hedef etiketler henüz YOKKEN oluşturulur (release etiketleri değişmez; üzerine yazılmaz).
  - DEVELOPMENT imajı kirli ağaçtan da oluşturulabilir; ayrı depo adı (ppe-dashboard-dev) ve kind=development /
    tree_state=dirty etiketleri taşır, uygulama açılışta UNVERIFIED uyarısı verir.
  - `ppe-dashboard:latest` (çalışan yığının kullandığı etiket) bu araç tarafından ASLA yazılmaz/değiştirilmez.
    Yeni sürüme geçiş ayrı ve onaylı bir dağıtım adımıdır (docs/DEPLOYMENT.md "Sürüm izlenebilirliği").
  - Build sonrası etiketler `docker image inspect` ile doğrulanır; manifest build-manifests/ altına yazılır (Git dışı)."""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_REPO, DEV_REPO = "ppe-dashboard", "ppe-dashboard-dev"
PROTECTED_TAGS = {f"{RELEASE_REPO}:latest", RELEASE_REPO}
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")
SAFE = re.compile(r"^[A-Za-z0-9._/+-]{1,80}$")
MANIFEST_DIR = ROOT / "build-manifests"


class BuildRefused(RuntimeError):
    pass


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def dirty_paths(porcelain: str) -> list:
    """`git status --porcelain` çıktısı (KIRPILMAMIŞ: ilk sütun boşluk olabilir) -> yol listesi."""
    return [ln[3:] for ln in porcelain.splitlines() if len(ln) > 3]


def git_state() -> dict:
    porcelain = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, check=True,
                               capture_output=True, text=True).stdout          # strip() YOK: sütun hizası korunur
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    return {"sha": git("rev-parse", "HEAD"), "branch": branch if branch != "HEAD" else "detached",
            "dirty_paths": dirty_paths(porcelain)}


def plan(state: dict, release: str | None, existing_tags: set, now: str) -> dict:
    """Saf karar fonksiyonu (birim testli). Döner: build argümanları ve etiketler; kural ihlalinde BuildRefused."""
    sha = state["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise BuildRefused("geçersiz commit SHA")
    branch = state["branch"] if SAFE.match(state["branch"]) else "unknown"
    dirty = bool(state["dirty_paths"])
    if release is not None:
        if not SEMVER.match(release):
            raise BuildRefused(f"release sürümü SemVer olmalı (ör. 0.10.7), gelen: {release!r}")
        if dirty:
            shown = ", ".join(state["dirty_paths"][:5]) + (" …" if len(state["dirty_paths"]) > 5 else "")
            raise BuildRefused(f"RELEASE kirli çalışma ağacından oluşturulamaz ({len(state['dirty_paths'])} yol: "
                               f"{shown}). Değişiklikleri commit'leyin veya DEVELOPMENT imajı oluşturun.")
        tags = [f"{RELEASE_REPO}:{release}", f"{RELEASE_REPO}:sha-{sha[:12]}"]
        clash = [t for t in tags if t in existing_tags]
        if clash:
            raise BuildRefused(f"release etiketi zaten var (değişmez): {', '.join(clash)}")
        kind, version, tree = "release", release, "clean"
    else:
        tree = "dirty" if dirty else "clean"
        tags = [f"{DEV_REPO}:{sha[:12]}{'-dirty' if dirty else ''}"]
        kind, version = "development", f"0.0.0-dev+{sha[:12]}{'.dirty' if dirty else ''}"
    if any(t in PROTECTED_TAGS for t in tags):            # savunma: latest asla bu araçla yazılmaz
        raise BuildRefused("korunan etiket")
    return {"tags": tags, "build_args": {"PPE_GIT_SHA": sha, "PPE_GIT_BRANCH": branch, "PPE_BUILD_TIME": now,
                                         "PPE_TREE_STATE": tree, "PPE_VERSION": version, "PPE_BUILD_KIND": kind},
            "kind": kind, "dirty_paths": state["dirty_paths"]}


def existing_tags() -> set:
    out = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True,
                         text=True, check=True).stdout
    return set(out.split())


def build(p: dict) -> dict:
    cmd = ["docker", "build", str(ROOT / "dashboard")]
    for k, v in p["build_args"].items():
        cmd += ["--build-arg", f"{k}={v}"]
    for t in p["tags"]:
        cmd += ["-t", t]
    subprocess.run(cmd, check=True)
    labels = json.loads(subprocess.run(["docker", "image", "inspect", p["tags"][0], "--format",
                                        "{{json .Config.Labels}}"], capture_output=True, text=True, check=True).stdout)
    want = {"org.opencontainers.image.revision": p["build_args"]["PPE_GIT_SHA"],
            "com.ppe.build.kind": p["build_args"]["PPE_BUILD_KIND"],
            "com.ppe.build.tree-state": p["build_args"]["PPE_TREE_STATE"],
            "org.opencontainers.image.version": p["build_args"]["PPE_VERSION"]}
    bad = {k: labels.get(k) for k, v in want.items() if labels.get(k) != v}
    if bad:
        raise BuildRefused(f"imaj etiketleri beklenenle uyuşmuyor: {bad}")
    image_id = subprocess.run(["docker", "image", "inspect", p["tags"][0], "--format", "{{.Id}}"],
                              capture_output=True, text=True, check=True).stdout.strip()
    manifest = {"image_id": image_id, "tags": p["tags"], **p["build_args"], "labels_verified": True,
                "dirty_paths": p["dirty_paths"][:200]}
    MANIFEST_DIR.mkdir(exist_ok=True)
    name = p["tags"][0].replace(":", "_").replace("/", "_")
    (MANIFEST_DIR / f"{name}.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--release", metavar="SEMVER", help="release imajı (yalnız temiz ağaç)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        p = plan(git_state(), a.release, existing_tags(), now)
    except BuildRefused as exc:
        print(f"REDDEDİLDİ: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: p[k] for k in ("kind", "tags", "build_args")}, indent=1))
    if p["kind"] != "release":
        print("NOT: DEVELOPMENT/UNVERIFIED imaj — üretim veya pilot için kullanmayın.", file=sys.stderr)
    if a.dry_run:
        return 0
    try:
        m = build(p)
    except BuildRefused as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 3
    print(f"tamam: {m['image_id']} -> {', '.join(m['tags'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
