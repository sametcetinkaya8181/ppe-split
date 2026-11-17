# FAZ 00 — Production Readiness & Technical Baseline Audit

Tarih: **2026-11-17**. Ortam: Windows 11, Git Bash/PowerShell, Docker Desktop 28.5.1, RTX 3050 Laptop (4 GB).
Kapsam: salt okunur denetim. Ana PostgreSQL, `best.pt`, çalışan konteynerler, outbox, snapshot ve kullanıcı dosyaları
**değiştirilmedi**. Ana yığın başlatılmadı ve migration çalıştırılmadı. Testler, çalışma ağacının dışa aktarılmış
kopyasında ve geçici, internetsiz bir PostgreSQL 17 konteynerinde çalıştırıldı. Gizli değer hiçbir çıktıya yazılmadı.

Kanıt seviyeleri: **DOĞRULANDI** (bu fazda koddan, Git'ten, konteynerden veya test koşusundan görüldü) ·
**ÇIKARIM** (dolaylı kanıttan yüksek güvenle) · **ÖNCEKİ RAPOR** (yalnızca eski rapor/çıktı; bu fazda yeniden koşulmadı)
· **YOK**.

> **Düzeltmeler (FAZ 01, 2026-11-17).** Aşağıdaki metin FAZ 00'daki haliyle korunmuştur; iki bulgu FAZ 01'de
> yanlış çıktı:
> 1. **"0006 sonrası yedek yok / son yedek 0005" (§1, §4.1, §6, R-09) yanlış.** `backups/ppe-20261102T030016Z`
>    0006 seviyesinde bir yedektir (quiesce edilmemiş, `db_changed_during_backup: false`); FAZ 01'de izole kümede
>    `restore_check` **PASS**. Kalan risk doğrudur ama farklıdır: bu yedek 2026-11-02 tarihlidir; dedektör
>    2026-11-09…11-12 arasında da çalıştığı için o tarihten sonraki olaylar hiçbir yedekte yoktur.
> 2. **"Browser E2E BLOCKED: Playwright kurulu değil" (§8) yanlış gerekçe.** `ui_check.mjs` Node yerleşik
>    modülleri + Edge (CDP), `ui_check_10a7.py` ise `websocket-client` + Chrome/Edge (CDP) kullanır; Playwright
>    gerekmez. FAZ 01'de ikisi de izole fixture'la koşuldu (bkz. `PHASE_01_CONSOLIDATION_REPORT.md`).
> 3. **"`ppe-team/main` ile ortak ata yok" (§13 madde 5) yanıltıcı.** Bu yalnızca yerel orphan `main` (`6c2d203`)
>    hattı için doğru. `ppe-team/main` (`858184c`), `github-main`'in ve dolayısıyla `integration/faz-01`'in atasıdır;
>    hedef repoya aktarım normal bir PR ile yapılabilir.

---

## 1. Executive Summary

- **Kod tabanı üç ayrı Git geçmişine bölünmüş durumda (P0).** Aktif branch `github-main`
  (`2cfdf06`), FAZ 10-A.4, 10-A.5A, 10-A.5B ve 10-A.6'yı **içermiyor**. Raporlanan commit'lerin (`e1f1e48`,
  `99217bf`, `b8c571d`, `4660136`, `6c2d203`) hiçbiri HEAD'in atası değil. Bunlar yalnızca `main` ve
  `birlesik/faz-10a-tam` branch'lerinde var. Hedef GitHub reposunun (`ppe-team`) `main` branch'i `858184c`'de ve
  FAZ 1 öncesinden kalma; yerel `main` ile ortak atası yok.
- **Çalışan dashboard imajı hiçbir commit'e karşılık gelmiyor (P0).** `ppe-dashboard:latest` (2026-11-09)
  içindeki `app.py` ve `index.html`, commit edilmemiş çalışma ağacıyla birebir aynı. Sürüm izlenebilirliği ve geri
  dönüş (rollback) belirsiz.
- **Ana DB migration seviyesi `0006` (ÇIKARIM, yüksek güven).** Human Review migration'ı `0007` ana DB'ye
  **uygulanmamış**. Çalışan imajda da yok.
- **Bellek düzeltmesi (FAZ 10-A.1, `check_ready()`) mevcut ve çalışan imajda var (DOĞRULANDI).** 24 saatlik soak ise
  **hiç koşulmadı**.
- **Model doğruluğu kanıtlanmadı (P0).** Eğitim doğrulamasında P 0.57, R 0.61 ve mAP50 0.60. Saha ground-truth'u yok.
  E-01…E-04 hatalarının **dördü de üretim kodunda açık**. 2026-11-12'deki 4 kameralı çalışmada track-karelerin
  **%37–54'ü** değerlendirilemedi, track'lerin **%62–74'ü** hiç değerlendirilmeden kapandı.
- **Lisans engeli (P0, ticari).** Ultralytics AGPL-3.0, `best.pt` checkpoint'inde de AGPL-3.0 yazıyor, eğitim verisi
  Ultralytics `construction-ppe` ve repoda LICENSE dosyası yok. Ticari dağıtım kararı bekliyor.
- **KVKK (P0, pilot).** Snapshot saklama süresi sınırsız, otomatik silme yok. Yedekler şifresiz. `backups/env-*`
  altında `.env` dosyalarının şifresiz kopyaları var. 0006'dan sonra alınmış yedek yok.
- **Testler sağlam.** İzole koşuda dashboard 354/354 (WIP dahil) ve 388/388 (birleşik branch) PASS. Detector
  310/310 ve 382/382 PASS. Secret audit PASS.
- **GO/NO-GO:** Production **NO-GO** · Saha pilotu **NO-GO** · Yatırımcı demosu **KOŞULLU** (kayıtlı videolarla,
  yerel kurulumda, "doğruluk kanıtlanmadı" uyarısıyla).
- **Önerilen ilk faz:** FAZ 01 — *Kod tabanı konsolidasyonu ve sürüm izlenebilirliği* (bkz. §13).

---

## 2. Repository / Git Status

| Kontrol | Sonuç | Kanıt |
|---|---|---|
| Proje kökü | `C:/Users/PC/Desktop/ppe-split-main` | `git rev-parse --show-toplevel` |
| Aktif branch / HEAD | `github-main` @ `2cfdf06` "chore: update project" (2026-11-09); upstream `benim/main` ile eşit | `git branch -vv` |
| Commit edilmemiş | 8 dosya (+233/−78): `dashboard/app.py`, `auth/{cli,routes,security,store}.py`, `static/index.html`, `tests/test_access_control.py`, `tests/test_role_visibility.py` | `git diff --stat` |
| Untracked | Yok (`.gitignore` kapsamı dışında) | `git status --untracked-files=all` |
| Remote'lar | `benim` → `MuratElberKayaa/ppe-split`; `ppe-team` → `sametcetinkaya8181/ppe-split` (hedef) | `git remote -v` |
| Hedef repo canlı durumu | `ppe-team`: `main`=`858184c` (FAZ öncesi), `sync/phase-10a6-20261102`=`6c2d203` | `git ls-remote` (salt okunur) |
| Kendi fork'unuz | `benim`: `main`=`2cfdf06`, `mac/faz-10a7-dashboard-role-ui`=`b0d2184` | `git ls-remote` |

### 2.1 Branch topolojisi (DOĞRULANDI)

```
ppe-team/main  73c8652 ─ 858184c                        (hedef repo; FAZ öncesi)
                              └─ e8c8c83 … FAZ1–9, 10-A, 10-A.2, 10-A.3, 10-A.1(c503d78) … 2cfdf06   = github-main (Mac hattı)
                                                                                └─ 452035c(10-A.1 rapor) 9cbc945(A.4)
                                                                                   603a314(A.5A) 9f03688(A.5B) 6ddabd3(A.6)
                                                                                   = birlesik/faz-10a-tam
(orphan kök 62fd1b0/14ea8c4) FAZ1–10-A … e1f1e48(A.1) 99217bf(A.4) b8c571d(A.5A) 4660136(A.5B) 6c2d203(A.6)
                                                                                   = main = ppe-team/sync/phase-10a6-20261102
```

- `main`, `github-main` ve `ppe-team/main` arasında **ortak ata yok** (`git merge-base` boş döndü). FAZ 1–10-A
  aynı içerikle iki kez yeniden yazılmış commit dizileri olarak duruyor.
- `birlesik/faz-10a-tam` (`6ddabd3`), `github-main`'in üzerine 10-A.1 raporu, A.4, A.5A, A.5B ve A.6'nın
  cherry-pick'iyle oluşturulmuş en **kapsamlı** ağaç. `benim/main`'in 5 commit önünde ve hiç push edilmemiş.
- Commit yazarları: `PC`, `D2-18`, `MuratElberKayaa`, `samet çetinkaya`. Birden fazla katkıcı var (bkz. §7 lisans).

### 2.2 Fazların çalışma ağacındaki gerçek durumu

| Faz | Raporlanan commit | Çalışma ağacında (github-main + WIP) | Not |
|---|---|---|---|
| FAZ 1–9, 10-A | — | **VAR** (Mac hattı kopyaları) | Testler PASS |
| 10-A.1 | `e1f1e48` | **VAR (kod)**: `c503d78` varyantı | `e1f1e48`'in cherry-pick'i (`452035c`) yalnızca `docs/PHASE_10A1_REPORT.md` ekliyor; kod farkı yok. Durum: **INCOMPLETE** (24 sa soak yok) |
| 10-A.2 / 10-A.3 | — | **VAR** | Ürün dokümanları ve veri altyapısı |
| 10-A.4 | `99217bf` | **YOK** | `baseline_eval.py`, `baseline/known_errors.json`, `reference_mac_20261102.json` eksik. Mac SteelBench tabanı (`922ff19`) var |
| 10-A.5A | `b8c571d` | **YOK** | `failure.py`, `failure_analysis.py`, `secret_audit.py`, `isolation.py` eksik |
| 10-A.5B | `4660136` | **YOK** | `experiments.py`, `experiments_5b.py` eksik |
| 10-A.6 | `6c2d203` | **YOK** | `0007_incident_reviews.py`, `db/reviews.py`, `test_reviews.py`, `isolated_dashboard_tests.py` eksik |
| 10-A.7 | — | **KISMEN** | Rol bazlı görünürlük var (Mac commit'leri `1bd30f4…c09184a` + commit edilmemiş WIP: `ctx.technical`, CLI `rename`/`set-role`). **Human Review UI başlanmamış** (hiçbir branch'te yok) |
| 10-A.8, 10-A.9, 10-B | — | **YOK** | Başlanmamış |

**Raporlanıp bu depoda (aktif ağaçta) bulunmayan dosyalar:** `git ls-tree main` ile `HEAD` arasındaki fark:
23 dosya. 10-A.4…10-A.6 başlıklı tüm dosyalar ve `PPE_Claude_Code_PostgreSQL_Uygulama_Plani.md`
(`2cfdf06`'da silinmiş).

**WIP ile birleşik branch'in uyumu:** commit edilmemiş yama `birlesik/faz-10a-tam` üzerine `patch --dry-run` ile
denendi. **3 hunk çakışıyor**: `dashboard/auth/routes.py` `/me` permissions bloğu, `tests/test_access_control.py:44`
ve `tests/test_role_visibility.py:79`. Diğer 5 dosya temiz uygulanıyor.

### 2.3 `.gitignore` ve dışlanan kategoriler

Dışlananlar: görseller ve video (`*.jpg|png|mp4…`), model ağırlıkları (`*.pt|onnx|engine`, `detector/models/*`),
`data/*`, `.env` (`.env.example` hariç), `backups/`, `detector/outbox/`, `detector/evaluation/{datasets,output}/`,
`tools/reliability/output/`, venv ve önbellekler. **Doğrulama:** izlenen dosyalarda `.mp4/.pt/.jpg/.png/.sqlite3/.dump`
yok. `.env` hiçbir commit'te yok. Repo dışında, diskte duranlar: `detector/videos` (59 MB, 5 video),
`detector/evaluation/datasets` (347 MB, `construction-ppe`), `data/snapshots` (385 dosya, 7.1 MB).

> Not: `.gitignore`'un ilk satırı "Videolar PUSHLANIR" diyor, ama `*.mp4` kuralı videoları dışlıyor. Yorum yanıltıcı;
> gerçek davranış: videolar Git'te **değil**.

---

## 3. Architecture Inventory

| Bileşen | Konum | Teknoloji / sürüm (DOĞRULANDI) |
|---|---|---|
| Detector (host) | `detector/detector.py` (539 sat.), `ppe_core.py` (142), `protocol.py` (503), `outbox.py` (782), `telemetry.py` (479) | Python 3.12.6 venv · torch 2.14.1+cu126 · CUDA OK · Ultralytics 8.4.183 · OpenCV 4.10.0 · lap 0.5.13 · numpy 2.5.4 |
| Model | `detector/models/best.pt` | SHA-256 `6886ad2fc301cf992152d4b047307071cac940082ab071175c0ef498fe67ba43` · 2.54 M parametre · `yolo26n.pt` tabanı · 640 px, 100 epoch · tarih 2026-10-20 |
| Dashboard (Docker) | `dashboard/app.py` (954), `auth/`, `db/`, `ingest/`, `maintenance/`, `observability.py`, `static/index.html` (1190) | İmaj Python **3.11.18** (hedef 3.12 değil) · FastAPI 0.162.0 · Starlette 1.10.0 · SQLAlchemy 2.0.55 · psycopg 3.3.6 · Alembic 1.20.1 · argon2-cffi 23.1.0 |
| Veritabanı | Docker `ppe-split-db-1`, volume `ppe-split_pgdata` | `postgres:17@sha256:b8faeff7…` (17.11) |
| Migration | `dashboard/alembic/versions/0001…0006` | Rol ayrımı: `migrate` → `ppe_migrator`, `dashboard` → `ppe_app` |
| Kalıcı outbox | `detector/outbox/<cam>.sqlite3` + `.status.json` + `.health.json` | Kamera başına SQLite, EXCLUSIVE kilit, 512 MB / 100 000 öğe sınırı |
| LAN/TLS | `deploy/lan/Caddyfile`, `docker-compose.lan.yml` | Tasarlanmış; **etkin değil** (`SECURITY_PROFILE=local`) |
| Operasyon | `scripts/{backup,restore_check,ppe_ops,venv_dedupe}.py`, `windows_acl.ps1` | — |
| Reliability araçları | `tools/reliability/*` (soak, stress, faultproxy, camsim, memtest, steelbench …) | — |

**Veri akışı:** kamera ya da video → (kamera başına ayrı `spawn` süreci) YOLO `model.track` + ByteTrack → `match_items`
(baş/gövde bölgesi) → `PPEMonitor` (kayan pencere) → `IncidentTracker` (kimlik) → `DurableSender`/outbox →
`POST /ingest/frame|events|report` (X-Token, `hmac.compare_digest`) → PostgreSQL (tek transaction, `event_id`
idempotency) + RAM (canlı) → tarayıcı: tek `/ws` WebSocket (JPEG) + REST `/api/*` (oturum çerezi + CSRF).

**Çalışma durumu (2026-11-17):** ana yığının dört konteyneri de (`ppe-split-{db,db-roles,migrate,dashboard}-1`)
**durmuş** (Exited 0). Son çalışma 2026-11-12 08:03 → 10:19 UTC arası. Makinede başka projelere ait konteynerler de
var (`career-agent-*` çalışıyor, `llminsight-*` durmuş); dokunulmadı.

---

## 4. Backend & Database State

### 4.1 Migration seviyesi

| Öğe | Değer | Kanıt seviyesi |
|---|---|---|
| Repo head (aktif ağaç) | **0006** (`0006_run_disconnected`) | DOĞRULANDI |
| Repo head (`main` / `birlesik`) | **0007** (`0007_incident_reviews`) | DOĞRULANDI |
| Çalışan imajdaki migration'lar | 0001–0006 (`docker export` listesi) | DOĞRULANDI |
| Ana DB `alembic_version` | **0006** | ÇIKARIM: imaj yalnızca 0006'yı biliyor; `migrate` 3 koşuda da çıkış kodu 0 ve "Running upgrade" satırı yok. DB 0007'de olsaydı Alembic "Can't locate revision" hatası verirdi. 10-A.6 raporu da 0006 diyor |
| Son yedekteki revision | **0005** (`backups/ppe-20261101T213616Z`, sayımlar 7/74/47/104/255) | DOĞRULANDI (manifest) |

DB doğrudan sorgulanmadı, çünkü bunun için durmuş ana veritabanının başlatılması gerekirdi. **Uyumsuzluk riski:**
`birlesik` veya `main` imajı 0006'daki DB'ye karşı başlatılırsa `/health/ready` bilinçli olarak 503 verir.
`ppe-dashboard:review-test` imajı (2026-11-02) 0007 içeriyor ve yalnızca izole testte kullanılmış.

### 4.2 Bileşen durumu

| Bileşen | Aktif ağaç | Birleşik branch | Kanıt |
|---|---|---|---|
| Authentication / session | **Var**: Argon2id, opak token (DB'de hash), idle 30 dk / mutlak 12 sa, `HttpOnly`+`SameSite=Strict` çerez (LAN'da `Secure`+`__Host-`), WS/MJPEG'de 15 sn'de bir yeniden doğrulama | Aynı | `auth/security.py`, `routes.py:70` |
| Lockout | **Var**: hesap başına 5 hata → 15 dk kilit; IP başına 20 hata | Aynı | `security.py:34-36` |
| RBAC + kamera kapsamı | **Var**: admin > supervisor(user) > viewer; `camera_scope`; snapshot'ta DB referansı + kapsam kontrolü (yoksa 404) | Aynı | `app.py:457-470` |
| Rol bazlı veri sunumu | **Var + WIP**: model skorları yalnızca admin'e; WIP ile teknik alanlar (`run_id`, `track_id`, `box`, kaynak) yalnızca admin'e, run uçları admin'e | WIP'siz | `git diff` |
| Incident yaşam döngüsü | **Var**: start/ongoing/end, `IncidentTracker`, kapanmamış incident'lar raporda | Aynı | `protocol.py` |
| Olay alımı / idempotency | **Var**: `event_id` tekil, tek transaction, 2xx yalnızca commit sonrası, çakışmada 409 | Aynı | `app.py:300-326` |
| SQLite durable outbox | **Var**: FIFO, retry/backoff, dead-letter/karantina, disk ve öğe sınırı, high-watermark | Aynı | `outbox.py`, `test_outbox*.py` |
| Snapshot | **Var**: yalnızca `start` olayında, ad deseni doğrulanıyor, symlink reddediliyor, kapsam kontrolü var | Aynı | `ingest/snapshots.py` |
| Audit log | **Var**: `ppe_app` için `UPDATE`/`DELETE` yok (yalnızca ekleme) | Aynı | `db/roles.py:99-105` |
| Human Review backend | **YOK** | **Var**: `POST /api/history/incidents/{id}/review`, sürüm (optimistic lock), not alanı | `merged app.py:911` |
| Human Review frontend | **YOK** | **YOK** (`index.html`'de "review" 0 kez geçiyor) | grep |
| Kamera / çalışma oturumu yönetimi | **Kısmen**: kameralar olaydan otomatik oluşuyor (`upsert_camera`); run durumu (`finished`/`interrupted`/`disconnected`/`error`/`abandoned`) var. **Kamera ekleme/düzenleme API'si veya UI'ı yok** (kaynaklar `detector/.env` içindeki `VIDEO_SOURCES`'ta) | Aynı | `app.py` route listesi |
| API hata yönetimi / gözlem | **Var**: `/healthz`, `/health/ready` (şema kontrolü), `/api/ops/metrics` (admin), gövde boyutu sınırları (413), sözleşme doğrulama (422) | Aynı | `app.py:663-700` |
| API performansı | ÖNCEKİ RAPOR: 4 sa soak'ta `/api/state` p95 39.9 ms, max 64 ms (4 kamera) | — | `soak4h/result.json` |

---

## 5. AI / Detector State

### 5.1 Model

| Öğe | Değer (DOĞRULANDI) |
|---|---|
| SHA-256 | `6886ad2fc301cf992152d4b047307071cac940082ab071175c0ef498fe67ba43` (10-A.5B raporundaki `6886ad2f…ba43` ile tutarlı) |
| Sınıflar | 0 helmet · 1 gloves · 2 vest · 3 boots · 4 goggles · 5 none · 6 Person · 7 no_helmet · 8 no_goggle · 9 no_gloves · 10 no_boots |
| Kullanılan eşleme | `CLASS_HELMET=0`, `CLASS_VEST=2`, `CLASS_PERSON=6`. Diğer 8 sınıf (`no_helmet` dahil) karar mantığında **kullanılmıyor** |
| Eğitim | taban `yolo26n.pt`, veri `construction-ppe.yaml` (Ultralytics), 640 px, 100 epoch, batch 16 |
| Eğitim doğrulama metrikleri (11 sınıf ortalaması) | P **0.570** · R **0.609** · mAP50 **0.605** · mAP50-95 **0.291**. Saha verisi değil, PPE sınıf bazında ayrışma yok |
| Checkpoint lisans alanı | **`AGPL-3.0`** |

### 5.2 Çıkarım ve karar hattı

- `model.track(stream=True, persist=True, tracker="bytetrack.yaml", conf=0.25, imgsz=960, vid_stride=VID_STRIDE)`.
  `IMGSZ` varsayılanı 960, model ise 640 ile eğitilmiş. **Özel ByteTrack yaml'ı yok** (Ultralytics varsayılanı).
- `detector/.env` içinde `IMGSZ`, `VID_STRIDE`, `DEVICE` ve `LOOP` tanımlı (değerler bu rapora alınmadı). Kod
  varsayılanı `VID_STRIDE=1`. Soak testleri `stride=2` ile koşulmuş.
- **PPEMonitor** (`ppe_core.py`): kişi "güvenilir" sayılırsa gözlem kuyruğuna ekleniyor. Koşullar:
  `conf ≥ 0.40`, yükseklik ≥ 60 px ve kenarlara > 6 px. Pencere 4 sn, en az 8 gözlem ve 2 sn. İhlale giriş oranı
  < %20, çıkış oranı < %40 (histerezis). Yeniden alarm 3 sn, bayatlama 10 sn. Baret ve yelek yalnızca bölge içi
  merkez eşleşmesiyle aranıyor. Ekipman sınıfına güven eşiği yok, `no_helmet` kanıtı kullanılmıyor.
- **Çoklu kamera:** kamera başına ayrı `spawn` süreci var ve her süreç kendi YOLO örneğini yüklüyor. 4 GB GPU'da 4
  kamera ~1.2 GB kullanıyor (11-12 health). **Süreç ölürse yeniden başlatılmıyor** (`main()` yalnızca "süreç sona
  erdi" yazıyor).
- **Kopma ve yeniden bağlanma:** üstel geri çekilme (2 → 60 sn, ±%20). Bekleme sırasında "KAMERA BAGLANTISI YOK"
  yazan yer tutucu kare gönderiliyor. Okuma zaman aşımı 10 sn. Run sonucu `disconnected/source_lost[_after_stall]`
  olarak sınıflandırılıyor.
- **Gözlemlenebilirlik:** kamera başına `health.json` (aşama süreleri, lag, kapsam, CPU/RSS/GPU, bayraklar), dashboard
  `/api/ops/metrics`, kapsam tablosu UI'da.

### 5.3 Gerçek kapsam ölçümü — son çalışma (2026-11-12, ~400 sn, 4 kamera; `detector/outbox/*.health.json`)

| Kamera | proc fps | Değerlendirilemeyen track-kare | Ana neden | Hiç değerlendirilmeden kapanan track | RSS | GPU kullanımı |
|---|---|---|---|---|---|---|
| cam1 | 15.8 | 3584/9673 = **%37** | low_conf 3158 | 511/697 = **%73** | 516 MB | %80 |
| cam2 | 15.0 | 1380/3396 = **%41** | edge 952 | 105/144 = **%73** | 416 MB | %82 |
| cam3 | 10.0 | 3785/8522 = **%44** | edge 3150 | 185/251 = **%74** | 820 MB | %12 |
| cam4 | 22.0 | 7792/14378 = **%54** | edge 6567 | 150/243 = **%62** | 998 MB | %76 |

Yorum: bu oranlar, sistemin görüntüdeki kişilerin önemli bir bölümü hakkında **karar vermediğini** gösteriyor. Ekranda
"ihlal yok" görünmesi "herkes uygun" anlamına gelmiyor. UI bunu "değerlendirilemiyor" olarak gösteriyor, yani durum
gizlenmiyor. Ancak İSG güvencesi açısından bu bir P0 sorunu.

### 5.4 E-01…E-04 güncel durumu

| Hata | Üretim kodunda | 10-A.5B en iyi aday | Durum |
|---|---|---|---|
| E-01 kısmen örtülen baret → yanlış "Baretsiz" | **AÇIK**: model baret kanıtı üretmiyor, `no_helmet` kullanılmıyor | H5 kanıt derecelendirme (yalnızca etiket) | NOT VALIDATED · NO-GO |
| E-02 iş makinesi → Person | **AÇIK**: kare bazlı 0.40 kapısı. Modelde makine sınıfı yok, hariç tutma bölgesi yok | P1 iz düzeyi medyan güven | Uzak çalışanların %35–54'ünü kaybetme riski · NO-GO |
| E-03 kadraj kenarı | **AÇIK**: `EDGE_MARGIN_PX=6`, tüm kenarlar. §5.3'te ana neden | A3 eşyaya özgü kenar kapısı | E-01 çözülmeden yanlış alarmı artırıyor · NO-GO |
| E-04 kimlik parçalanması | **AÇIK**: varsayılan ByteTrack, tüm sınıflar takipte | T6 yalnızca Person takibi + `match_thresh` 0.9 | Kimlik birleşmesi riski ölçülemedi · NO-GO |

Kaynak: `birlesik:docs/PHASE_10A5B_EXPERIMENT_REPORT.md` (ÖNCEKİ RAPOR) ve bu fazda `ppe_core.py` / `detector.py`
okunarak doğrulanan "üretim kodunda değişiklik yok" bilgisi. Adayların kodu aktif ağaçta **yok** (yalnızca birleşik
branch'te).

---

## 6. Reliability State

| Soru | Cevap | Kanıt / tarih |
|---|---|---|
| `check_ready()` SQLAlchemy önbellek büyümesi düzeltmesi mevcut mu? | **EVET**: sabit `text()` sorguları, `MigrationContext` kaldırılmış | `dashboard/db/health.py` (HEAD `c503d78`); `test_statement_cache.py` PASS |
| Ana dashboard bu düzeltmeyi içeren imajla mı çalışıyor? | **EVET (son çalışmada)**: imaj `6f4ac355` içindeki `health.py`, HEAD ile aynı. Şu an konteyner durmuş | `docker cp` + `diff` (2026-11-17) |
| 24 saatlik soak tamamlandı mı? | **HAYIR**: hiç koşulmadı | `PHASE_10A1_REMEDIATION_REPORT.md` §3; `output/` altında 24 sa klasörü yok |
| RSS bellek artışı ölçümü geçerli mi? | **Kısmen**: Windows'ta 90 dk taban ve düzeltme koşuları (`20261102T023118Z_mem_base90`, `…040220Z_mem_fix90`, `…041512Z_mem_fixall90`). A/B doğrulaması **macOS'ta**: anon eğim 6.1 → 2.4 MB/sa. Kalan +2.4 MB/sa'lik eğimin platoya ulaşıp ulaşmadığı kanıtlanmadı | ÖNCEKİ RAPOR + ham çıktılar mevcut |
| Çoklu kamera FPS, lag, CPU, RAM, GPU kaydı | **EVET**: 4 sa soak (2026-11-01 21:45 → 11-02 01:45 UTC, 4 kamera, stride 2). Detector RSS p50 1792 MB (ilk örnek 4315), CPU p50 %347, GPU p50 %42, GPU sıcaklığı max 74 °C, dashboard belleği 78 → 95 MB (+3.9 MB/sa, düzeltme öncesi), sistem RAM ~14.3/16 GB, başlangıçta swap 3.9 GB | `output/20261101T214427Z_soak4h/cams4_stride2/result.json` |
| RTSP kopma ve toparlanma testi | **Yalnızca simülatörle**: `relnet` TCP MPEG-TS simülatörü + faultproxy (40 dk'da bir 60 sn kesinti / 45 sn donma); 11 `disconnected` run sonrası toparlanma var. **Gerçek RTSP kamera testi YOK** | aynı `result.json`; `output/*_failures` (2026-11-01) |
| Kamera durmuşken yanlış "canlı" gösterim var mı? | **Kod düzeyinde engellenmiş**: 5 sn kare gelmezse `waiting_for_detector`, karo `opacity .35` ve "Kamera izlenemiyor" yazısı; yeniden bağlanırken etiketli yer tutucu kare. Bu fazda uçtan uca **doğrulanmadı** | `app.py:572-576`, `index.html:413-450` |
| Outbox disk sınırı ve retry doğrulandı mı? | **EVET (test + soak)**: `test_outbox*.py` PASS (bu faz). Soak sonunda bekleyen/ölü/karantina 0. Outbox dizini 1.9 → 17.6 MB (event-id saklama; eğim +1.19 MB/sa, sınır 512 MB) | bu faz + `result.json` |
| Snapshot ve olay teslim bütünlüğü | **EVET (4 sa soak)**: 4656 gönderildi = 4656 DB'de, 0 kayıp, 0 çift kayıt. 11-12 çalışmasından sonra outbox'ta bekleyen 0 | `result.json` `delivery`; `cam*.status.json` |
| Backup/restore kanıtı | **ESKİ**: FAZ 5'te `restore_check.py` iki kez PASS (0002, 0003). Son yedek 2026-11-01 (0005). **0006 sonrası yedek yok.** Ana DB'ye geri yükleme prosedürü hiç koşulmadı | `docs/BACKUP_RESTORE.md:40-44`, manifest |
| Rollback kanıtı | İmajlar mevcut: `rollback-faz9-2b6202b`, `rollback-pre-faz10a`, `faz10a-rel-base`, `faz9-rel`. 10-A.1 raporuna göre veri kaybettirmeyen geri dönüş izole ortamda kanıtlanmış. **Çalışan `latest` imajı commit'siz olduğundan "hangi sürüme dönülüyor" belirsiz** | `docker images`; ÖNCEKİ RAPOR |

---

## 7. Security / Privacy / Licensing Findings

| # | Bulgu | Önem | Kanıt | Etki | Öneri |
|---|---|---|---|---|---|
| S-01 | Secret izolasyonu | **İYİ** | Secret audit **PASS**: üretim değerleri test env dosyalarında, 10 test konteynerinde, 556 kanıt dosyasında, 179 izlenen dosyada ve 7.8 MB Git geçmişinde yok | — | Aracı (`secret_audit.py`, yalnızca birleşik branch'te) ana ağaca al |
| S-02 | `backups/env-20261028T172010Z/{root,detector}.env`: üretim sırlarının **şifresiz** kopyaları | **P0** | dosya listesi (içerik okunmadı) | Diski okuyabilen herkes DB süper kullanıcı parolasına ve ingest token'a erişir | Şifreli arşive taşı veya güvenli sil. Sonrasında rotasyonu değerlendir (bu fazda **yapılmadı**) |
| S-03 | DB yedekleri şifresiz (`db.dump` + `snapshots/` düz klasörde) | **P0 (pilot)** | `backups/ppe-*` | Kişisel görüntüler ve olay verisi açıkta | `BACKUP_RESTORE.md`'deki `age` akışını zorunlu hale getir, ACL'i uygula (`windows_acl.ps1`) |
| S-04 | Compose `tests` profili, **ana PostgreSQL kümesine süper kullanıcı** ile bağlanıyor (`TEST_DATABASE_URL=…${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/ppe_test`), `DROP DATABASE … WITH (FORCE)` ve `CREATE/DROP ROLE` çalıştırıyor | **P1** | `docker-compose.yml:102-121`, `tests/conftest.py:78-90` | `_test` son eki koruması var, ama hata veya yanlış yapılandırma üretim kümesini etkileyebilir. Testler üretim süper kullanıcı sırrını devralıyor | Varsayılanı izole geçici kümeye çevir (`isolated_dashboard_tests.py` deseni; bu fazdaki koşu bunun çalıştığını gösterdi) |
| S-05 | Detector `_load_env()` içe aktarılırken `detector/.env`'i okuyor | Düşük | `detector.py:9-23` | Testlerde `conftest.py`, `DASHBOARD_URL`/`INGEST_TOKEN` değerlerini eziyor. Diğer üretim değişkenleri (`VIDEO_SOURCES`, `MODEL_PATH`) teste sızıyor | `PPE_TESTING` bayrağıyla `.env` yüklemesini atla |
| S-06 | Nesne düzeyi yetkilendirme | **İYİ** | Snapshot: ad deseni + DB referansı + kamera kapsamı, yoksa 404. Tüm geçmiş uçlarında `camera_scope` var. WS/MJPEG her 15 sn'de yeniden doğrulanıyor | — | Review uçları birleşik branch'te; üretime alınırken matris testleri korunmalı |
| S-07 | CSRF / XSS | **İYİ / Düşük** | CSRF token + Origin kontrolü (`security.py:210`). CSP `frame-ancestors 'none'; object-src 'none'`. UI'da `textContent` ağırlıklı (30), tek `innerHTML` (`index.html:598`): metinler `esc()` ile kaçırılıyor, sayısal alanlar kaçırılmıyor (kaynak: sanitize edilmiş health) | Düşük | Sayısal alanları da `esc()`'den geçir veya DOM ile kur |
| S-08 | Audit log değişmezliği | **Orta** | `ppe_app` için UPDATE/DELETE yok | Süper kullanıcı değiştirebilir. Hash zinciri veya dış kopya yok | P2: append-only dışa aktarım / hash zinciri |
| S-09 | TLS | **P1 (LAN pilotu)** | `SECURITY_PROFILE=local`, port `127.0.0.1:8000`. Caddy LAN profili tasarlanmış ama etkin değil | LAN'a açılırsa oturum çerezleri düz metin | Pilot öncesi LAN profili + `lan_profile_check.py` |
| S-10 | DB kullanıcı yetkileri | **İYİ** | Çalışan dashboard `ppe_app`, migrate `ppe_migrator` kullanıyor. `POSTGRES_USER` varsayılan `postgres` değil. Konteyner salt okunur kök FS ile, `cap_drop ALL` ve `no-new-privileges` ile çalışıyor | — | — |
| S-11 | Saklama ve silme | **P0 (pilot)** | Snapshot'lar **süresiz** (`PRIVACY_READINESS.md:20`). `maintenance.storage retention` yalnızca rapor üretiyor | KVKK md. 4/7 ihlali riski | Saklama politikası + otomatik imha + imha kaydı |
| S-12 | Human Review notları kişisel veri içerebilir | **P1** | Serbest metin `note` (birleşik branch, `NOTE_MAX`) | Kişisel veri ve sağlık/disiplin bilgisi içerebilir | UI uyarısı, saklama süresi, KVKK aydınlatmasına ekleme |
| S-13 | Ultralytics AGPL-3.0 + `best.pt` checkpoint'inde AGPL-3.0 | **P0 (ticari)** | checkpoint `license` alanı; `docs/LICENSE_REVIEW.md` (NO-GO) | Ağ üzerinden hizmet sunulursa kaynak paylaşım yükümlülüğü doğabilir | Yönetim kararı: Enterprise lisans **veya** AGPL uyumu **veya** model/çerçeve değişimi |
| S-14 | Eğitim verisi `construction-ppe` (Ultralytics) ve demo videoları (stok görünümlü dosya adları) | **P0 (ticari) / P1 (demo)** | `train_args.data`; `detector/videos/*` | Ticari kullanım hakkı doğrulanmadı | Veri seti ve video lisanslarını belgeleyip `DATASET_CARD.md`'ye ekle |
| S-15 | Kaynak kod sahipliği | **P0 (ticari)** | Repoda LICENSE yok. 4 farklı yazar kimliği var. Hedef repo başka bir kullanıcının hesabında | Ürünleştirme ve yatırım sürecinde IP belirsizliği | Katkı/IP devri anlaşması, LICENSE dosyası, kanonik repo kararı |

---

## 8. Test Evidence Matrix

Bu fazda koşulanlar: **2026-11-17**. Dashboard testleri `ppe-dashboard:latest` imajında (Python 3.11.18) koşuldu:
kaynak dışa aktarılmış kopyadan mount edildi, internet erişimi olmayan (`--internal`) ağdaki geçici, `tmpfs`'li
`postgres:17` kümesine bağlandı, rastgele parola kullanıldı, koşu sonrasında küme silindi. Detector testleri host venv'de
(Python 3.12.6) koşuldu: `.env` dosyası olmayan kopyada, `OUTBOX_DIR` geçici dizinde.

| Kategori | Hedef | Sonuç | Ayrıntı |
|---|---|---|---|
| Detector unit/integration | aktif ağaç | **PASS** | 310 passed (64 sn) |
| Detector + evaluation (A.4/A.5A/A.5B) | `birlesik/faz-10a-tam` | **PASS** | 382 passed (70 sn) |
| Dashboard unit/integration + PostgreSQL migration + Auth/RBAC + rol görünürlüğü | aktif ağaç + WIP | **PASS** | 354 passed (84 sn) |
| Dashboard + Human Review (0007, `test_reviews.py`) | `birlesik/faz-10a-tam` | **PASS** | 388 passed (90 sn) |
| Birleşik branch + WIP | yama birleşimi | **BLOCKED** | 3 hunk çakışması (§2.2) |
| SteelBench birim testleri | aktif ağaç | **PASS** | 13 passed, 1 skipped |
| Secret audit | gerçek repo kökü (salt okunur) | **PASS** | §7 S-01 |
| Browser E2E (`tests/ui/ui_check*.{mjs,py}`) | — | **BLOCKED** | Playwright kurulu değil. Çalışan yığın ve UI parolası gerekiyor |
| Reliability (soak / failure / lifecycle) | — | **NOT_TESTED** | Bu fazda koşulmadı. Önceki geçerli kanıt: 4 sa soak 2026-11-01/02 (§6) |
| 24 saatlik soak | — | **NOT_TESTED** | Hiç koşulmadı (talimat gereği başlatılmadı) |
| Model değerlendirme (GT'ye karşı) | — | **BLOCKED** | Etiketli saha ground-truth'u yok. Altyapı testleri PASS |
| Restore / rollback | — | **NOT_TESTED** | Ana yedek ve yığın gerektiriyor. Son kanıt FAZ 5 (ÖNCEKİ RAPOR) |
| Performans benchmark (GPU) | — | **NOT_TESTED** | Son ölçümler: 11-02 `baseline_cuda`, 11-12 health (§5.3) |
| LAN/TLS profil kontrolü | — | **NOT_TESTED** | `lan_profile_check.py` çalışan yığın gerektiriyor |

Eski raporlardaki PASS sonuçları bu tabloda yalnızca "önceki kanıt" olarak anılıyor ve yeni sonuç sayılmıyor.

---

## 9. Product Feature Gap Matrix

| # | Özellik | Durum | Kanıt |
|---|---|---|---|
| 1 | Human Review UI | **EKSİK** | Hiçbir branch'in `index.html`'inde yok |
| 2 | Incident doğrulama akışı | **KISMEN** | Backend (0007, review API, sürüm) yalnızca birleşik branch'te. Ana DB'de yok, UI yok |
| 3 | Düzeltici faaliyet yönetimi | **EKSİK** | Kod yok |
| 4 | Kamera ekleme/yapılandırma UI | **EKSİK** | Kaynaklar `detector/.env` `VIDEO_SOURCES` içinde. Kamera kaydı olaydan otomatik oluşuyor |
| 5 | Kamera ve alan bazlı PPE kuralları | **EKSİK** | Global eşikler, ROI/bölge yok, yalnızca baret+yelek |
| 6 | İş makinesi–kişi risk analizi | **EKSİK** | Modelde makine sınıfı yok. Yalnızca 5B deney kodu var |
| 7 | Çoklu kamera kapasite yönetimi | **KISMEN** | Kamera başına süreç + telemetri var. Kapasite sınırı, ortak model ve süreç yeniden başlatma yok |
| 8 | Sistem arızası / kamera kopma bildirimi | **KISMEN** | UI'da sağlık bayrakları ve "izlenemiyor" yazısı var. Dış bildirim (e-posta/SMS/webhook) yok |
| 9 | İSG raporları | **KISMEN** | Geçmiş, özet ve günlük grafik var. Dışa aktarım (PDF/CSV) ve periyodik rapor yok |
| 10 | Kamera başına performans metrikleri | **MEVCUT** | `health.json`, `/api/ops/metrics`, kapsam tablosu |
| 11 | Kullanıcı ve saha yönetimi | **KISMEN** | Kullanıcı CRUD, rol ve kamera kapsamı (API + CLI) var. Saha (site) kavramı yok |
| 12 | Veri saklama ve KVKK kontrolleri | **KISMEN** | Erişim kontrolü, redaksiyon ve retention raporu var. Otomatik imha, maskeleme ve aydınlatma yok |
| 13 | Kurulum ve sürüm güncelleme | **KISMEN** | Compose, migrate servisi, rollback imajları ve `DEPLOYMENT.md` var. İmaj–commit izlenebilirliği, installer ve güncelleme kanalı yok |
| 14 | Multi-site | **EKSİK** | Şemada site/tenant yok |
| 15 | Yatırımcı demo akışı | **KISMEN** | Kayıtlı videolarla canlı panel çalışıyor. Senaryo, demo verisi ve lisanslı içerik belgelenmemiş |

---

## 10. Prioritized Risk Register

### P0 — Pilot veya güvenlik engeli

**R-01 Kod tabanı bölünmüşlüğü ve kanonik kaynak yokluğu**
- Durum: 3 ilgisiz geçmiş var. Aktif ağaçta A.4–A.6 yok. Hedef repo `main` FAZ öncesinde.
- Kanıt: §2.1, `git merge-base` boş, `git ls-remote`.
- Risk: yanlış branch'ten build, kayıp iş, çakışan düzeltmeler, denetlenemeyen sürüm.
- Bağımlılık: yok (her şeyin ön koşulu).
- Faz: **FAZ 01**.
- Kabul testi: tek entegrasyon branch'i; dashboard ≥388 + WIP testleri ve detector ≥382 testi izole koşuda PASS;
  `git merge-base` ile hedef remote ilişkisi belgelenmiş.
- Üretime etkisi: yok (yalnızca Git; push için onay gerekiyor).

**R-02 Çalışan imaj commit'siz koddan derlenmiş**
- Durum: `ppe-dashboard:latest` = commit edilmemiş WIP.
- Kanıt: §6, `docker cp` + `diff`.
- Risk: geri dönüş ve olay sonrası inceleme imkânsız.
- Bağımlılık: R-01.
- Faz: FAZ 01.
- Kabul testi: imaj etiketi = git SHA ve `org.opencontainers.image.revision` etiketi; yalnızca temiz ağaçtan build.
- Üretime etkisi: yeni imaj ile yeniden başlatma (onay gerekiyor).

**R-03 Model doğruluğu kanıtsız**
- Durum: saha GT'si yok; eğitim doğrulamasında P 0.57 / R 0.61.
- Kanıt: §5.1.
- Risk: yanlış alarm veya kaçırılan ihlal; İSG kararına dayanak olamaz.
- Bağımlılık: veri izni (10-A.3 altyapısı mevcut), R-07.
- Faz: FAZ 04.
- Kabul testi: `MODEL_EVALUATION_PROTOCOL.md`'ye göre izinli ve etiketli saha setinde sınıf bazlı P/R ve olay bazlı
  FN/FP; `ACCEPTANCE_METRICS.md` eşikleri.
- Üretime etkisi: model veya eşik değişikliği ayrı branch ve onayla.

**R-04 Değerlendirme kapsamı düşük (%37–54 değerlendirilemeyen track-kare)**
- Durum: §5.3.
- Kanıt: `cam*.health.json` (2026-11-12).
- Risk: "ihlal yok" ekranı yanlış güven veriyor.
- Bağımlılık: E-03/E-04 çözümü, kamera yerleşim rehberi.
- Faz: FAZ 04–05.
- Kabul testi: hedef kapsam oranı (ör. ≥%80 değerlendirilen track-kare) ve kamera kurulum kontrol listesi.
- Üretime etkisi: karar mantığı değişikliği → ayrı branch.

**R-05 E-01…E-04 açık**
- Durum: §5.4.
- Kanıt: 10-A.5B NO-GO; kod değişmemiş.
- Risk: örtülü barette yanlış alarm, makineye alarm, kenarda kör nokta, mükerrer incident.
- Bağımlılık: R-03 (GT).
- Faz: FAZ 04.
- Kabul testi: her aday için GT'de FN artışı yok ve hedef FP azalışı var.
- Üretime etkisi: PPEMonitor/tracker değişikliği → ayrı branch.

**R-06 Lisans ve IP**
- Durum: S-13, S-14, S-15.
- Kanıt: checkpoint `license`, `LICENSE_REVIEW.md`, LICENSE dosyası yok, 4 yazar.
- Risk: ticari dağıtımın hukuken engellenmesi; yatırım due-diligence'ında takılma.
- Bağımlılık: yönetim/hukuk kararı.
- Faz: **paralel yönetim izi** (FAZ 01 ile birlikte başlatılmalı).
- Kabul testi: yazılı lisans kararı, LICENSE dosyası, katkı/IP anlaşmaları, veri ve video lisans kaydı.
- Üretime etkisi: model veya çerçeve değişimi gerekebilir.

**R-07 KVKK: saklama, imha ve şifreleme yok**
- Durum: S-02, S-03, S-11.
- Kanıt: `PRIVACY_READINESS.md`, `backups/`.
- Risk: kişisel görüntülerin süresiz ve şifresiz saklanması.
- Bağımlılık: hukuk görüşü (saklama süresi).
- Faz: FAZ 03.
- Kabul testi: otomatik imha testi, şifreli yedek + `restore_check` PASS, `backups/env-*` temizliği, aydınlatma metni.
- Üretime etkisi: silme işi → ayrı branch, dry-run ve yedek ile.

**R-08 Uzun süreli kararlılık ve gerçek kamera kanıtı yok**
- Durum: 24 sa soak yok, gerçek RTSP yok, süreç gözetmeni yok.
- Kanıt: §6.
- Risk: sahada saatler sonra sessizce izlenmeyen kamera.
- Bağımlılık: R-01, R-02.
- Faz: FAZ 05.
- Kabul testi: `soak.py --hours 24 --memprobe` PASS ölçütleri, ≥2 gerçek RTSP kamerayla kopma/toparlanma, çöken
  kamera sürecinin otomatik yeniden başlatılması.
- Üretime etkisi: detector süreç yönetimi değişikliği.

**R-09 Güncel yedek yok (son yedek 0005, 2026-11-01)**
- Durum: §4.1, §6.
- Kanıt: manifest.
- Risk: 0007 geçişinde veya arızada veri kaybı.
- Bağımlılık: yok.
- Faz: FAZ 01 (0007'den önce zorunlu).
- Kabul testi: yeni şifreli yedek + izole `restore_check.py` PASS.
- Üretime etkisi: okuma ağırlıklı (pg_dump).

### P1 — İlk ürün için gerekli

| ID | Konu | Durum / kanıt | Bağımlılık | Faz | Kabul testi | Üretime etkisi |
|---|---|---|---|---|---|---|
| R-10 | Human Review UI + 0007'nin ana DB'ye geçişi | Backend birleşik branch'te; UI yok; DB 0006 | R-01, R-09 | FAZ 02 | 0007 yedekli geçiş, `/health/ready` revision 0007, UI E2E (Playwright) PASS | Migration → ayrı branch + onay |
| R-11 | Test izolasyonu (S-04) | Compose tests profili ana kümede | R-01 | FAZ 01 | Varsayılan test komutu ana `db`'ye bağlanamıyor | Yok |
| R-12 | TLS / LAN profili (S-09) | Etkin değil | R-01 | FAZ 03 | `lan_profile_check.py` + `lan_smoke.py` PASS | Ters vekil devreye alınır |
| R-13 | Kamera yönetimi UI/API | Yok (§9-4) | R-01 | FAZ 06 | Kamera ekle/düzenle/devre dışı bırak; detector yeniden okuması | Yeni şema → migration |
| R-14 | Dış bildirim (arıza ve ihlal) | Yok (§9-8) | R-08 | FAZ 06 | Kamera koptuğunda ≤N dk içinde bildirim | Yeni servis |
| R-15 | Düzeltici faaliyet + İSG raporu dışa aktarımı | Yok (§9-3, 9) | R-10 | FAZ 06 | Incident → faaliyet → kapanış akışı; PDF/CSV | Migration |
| R-16 | Çoklu kamera kapasitesi (4 GB GPU, kamera başına model) | §5.2, §6 | R-08 | FAZ 05 | Hedef kamera sayısında fps/lag SLO'ları | Detector mimarisi |
| R-17 | Review notu kişisel veri (S-12) | Serbest metin | R-10 | FAZ 02–03 | UI uyarısı + saklama süresi | — |
| R-18 | `detector/.env` testlere sızıyor (S-05) | `_load_env` | — | FAZ 01 | Test sırasında `.env` okunmuyor | Yok |

### P2 — Performans ve ticari farklılaşma

| ID | Konu | Faz |
|---|---|---|
| R-19 | TensorRT/FP16, ortak model sunucusu, batch çıkarım | FAZ 07 |
| R-20 | Alan/ROI kuralları, iş makinesi–kişi yakınlık riski, ek KKD sınıfları (eldiven, gözlük) | FAZ 07 |
| R-21 | Multi-site / tenant | FAZ 08 |
| R-22 | Audit log hash zinciri / dış kopya (S-08) | FAZ 07 |
| R-23 | `innerHTML` sayısal alanlarının kaçırılması (S-07), `.gitignore` yorum düzeltmesi, imaj Python 3.11 → 3.12 hizalaması | FAZ 01–02 (küçük) |

---

## 11. Phase Dependency Map

```
FAZ 01 Konsolidasyon + izlenebilirlik + güncel yedek + test izolasyonu   ← (paralel) Lisans/IP yönetim kararı
   │
   ├─► FAZ 02 0007'nin ana DB'ye geçişi + Human Review UI (10-A.7'nin tamamlanması)
   │       └─► FAZ 06 Kamera yönetimi · bildirimler · düzeltici faaliyet · İSG raporları
   │
   ├─► FAZ 03 KVKK/güvenlik operasyonu: şifreli yedek, imha, TLS/LAN, env yedeklerinin temizliği
   │
   ├─► FAZ 04 Saha GT verisi + model değerlendirmesi + E-01…E-04 doğrulaması (veri izni gerekli)
   │       └─► kapsam iyileştirmesi (karar mantığı, ayrı branch)
   │
   └─► FAZ 05 24 sa GPU soak · gerçek RTSP · süreç gözetmeni · kapasite
                 │
                 ▼
          10-A.9 GO/NO-GO kurulu  ──►  10-B shadow-mode pilot  ──►  FAZ 07–08 (P2)
```

FAZ 02, 03, 04 ve 05, FAZ 01'den sonra paralel yürütülebilir. Pilot (10-B) için 01–05'in tamamı ve lisans kararı
gerekiyor.

---

## 12. GO / NO-GO Assessment

| Karar | Sonuç | Gerekçe |
|---|---|---|
| Production (ticari dağıtım) | **NO-GO** | R-01, R-02, R-03, R-06, R-07 |
| Kontrollü saha pilotu (10-B) | **NO-GO** | R-03, R-04, R-05, R-07, R-08; Human Review UI yok |
| İç teknik demo | **GO** | Testler PASS; kayıtlı videolarla yerel yığın çalışıyor (son çalışma 11-12) |
| Yatırımcı demosu | **KOŞULLU** | Yalnızca kayıtlı ve lisansı belgelenmiş videolarla, `127.0.0.1`'de. "Doğruluk saha verisiyle kanıtlanmadı" ve "değerlendirilemeyen kişi" göstergeleri açıkça anlatılmalı. Commit'li ve etiketli bir imajdan çalıştırılmalı (R-02) |

---

## 13. Recommended Next Phase

### FAZ 01 — Kod tabanı konsolidasyonu ve sürüm izlenebilirliği

Risk sınıfı: büyük entegrasyon ve auth/RBAC içeriyor, bu yüzden **ayrı branch** gerekiyor (Git politikası madde 3).
Tüm adımlar geri alınabilir. Push, PR ve merge yalnızca onayla yapılacak.

1. **Entegrasyon branch'i:** `birlesik/faz-10a-tam`'dan (`6ddabd3`) `integration/faz-01` oluştur. Commit edilmemiş
   WIP'i (8 dosya) önce kendi branch'inde commit'le, ki kaybolmasın. Ardından entegrasyona taşı. Çakışmalar:
   - `dashboard/auth/routes.py` `/me` → `permissions` bloğu: `technical` alanı ile birleşik branch'teki alanlar birleştirilecek.
   - `dashboard/tests/test_access_control.py:44` ve `dashboard/tests/test_role_visibility.py:79`.
   - Kabul: izole koşuda dashboard testleri (388 + WIP testleri) ve detector testleri (382) PASS.
2. **Test izolasyonu (R-11, R-18):** `docker-compose.yml` `tests` servisi ana `db`'ye süper kullanıcıyla bağlanmamalı.
   Bunun yerine ayrı, geçici bir küme kullanılmalı (`tools/reliability/isolated_dashboard_tests.py` birleşik
   branch'te mevcut; bu fazda aynı yöntem doğrulandı). `detector/detector.py` `_load_env()` test bayrağıyla atlanmalı.
3. **İmaj izlenebilirliği (R-02):** `dashboard/Dockerfile`'a `LABEL org.opencontainers.image.revision`. Build betiği
   kirli ağaçta çalışmayı reddetmeli. Etiket: `ppe-dashboard:<sha>`.
4. **Güncel yedek (R-09):** 0007'den önce `scripts/backup.py` + `restore_check.py` (izole). 10-A.6 notuna göre
   `scripts/ppe_ops.py` sayım listesine `incident_reviews` tabloları eklenmeli.
5. **Kanonik repo kararı (kullanıcı kararı):** `ppe-team/main` (`858184c`) ile ortak ata yok. Seçenekler: yeni bir
   branch'e push ederek PR açmak veya `main`'i değiştirmek. Push ve force işlemleri **onaysız yapılmayacak**.
6. **Küçük temizlikler:** `.gitignore` video yorumu, `index.html:598` `esc()`, imaj Python sürümünün 3.12'ye
   hizalanması (değerlendirilmeli).

Paralel yönetim izi (kod dışı): Ultralytics lisans kararı, katkıcı IP anlaşmaları, veri seti ve video lisans kaydı,
KVKK saklama süresi için hukuk görüşü.

---

### Ek A — Bu fazda kullanılan salt okunur yöntemler

`git log/branch/ls-tree/diff/merge-base/ls-remote` · `docker ps/images/inspect/logs/export/cp` (durmuş
konteynerlerden okuma) · `sha256sum` · checkpoint metadata okuma · `secret_audit.py` (birleşik branch'ten, gerçek köke
karşı, yalnızca etiket çıktısı) · `git archive` ile scratch kopyalarında test. Geçici `ppe-audit-pg` konteyneri ve
`ppe-audit-net` ağı koşudan sonra silindi. `detector/outbox` dosyalarının zaman damgaları değişmedi (2026-11-12).
