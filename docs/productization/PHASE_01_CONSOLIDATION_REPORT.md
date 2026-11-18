# FAZ 01 — Repository Consolidation & Safe Development Baseline

Tarih: **2026-11-17**. Ortam: Windows 11, Git Bash/PowerShell, Docker Desktop 28.5.1, PostgreSQL 17.11 (geçici
kümeler), dedektör Python 3.12.6 (venv), dashboard imajı Python 3.11.18, Node 22.11, Edge ve Chrome (headless).
Önceki rapor: [`PHASE_00_BASELINE_AUDIT.md`](PHASE_00_BASELINE_AUDIT.md) (FAZ 01'de bulunan iki hata başına not
olarak eklendi).

**Değiştirilmeyenler:** Ana PostgreSQL (`ppe-split-db-1`, volume `ppe-split_pgdata`) başlatılmadı, migrate edilmedi
ve sorgulanmadı. `ppe-dashboard:latest` ve diğer mevcut imajlar, `best.pt`, `PPEMonitor`/`IncidentTracker` karar
mantığı, `data/snapshots`, `detector/outbox` ve `backups/` değişmedi. `github-main` çalışma ağacı ve 8 WIP dosyası
olduğu gibi duruyor. Eski branch'ler ve ref'ler değişmedi. Push, PR ve merge yapılmadı.

---

## 1. Kanonik geliştirme branch'i

| Öğe | Değer |
|---|---|
| Kanonik branch | **`integration/faz-01`** (yerel; push edilmedi) |
| Taban | `birlesik/faz-10a-tam` @ `6ddabd3` (FAZ 10-A.6) |
| Worktree | `C:\Users\PC\Desktop\ppe-split-integration` (ayrı Git worktree; `github-main` ağacı değişmedi) |
| HEAD | Bu raporu içeren **tek konsolidasyon commit'i** (hash commit'ten sonra kullanıcıya bildirildi; `git log -1 integration/faz-01`) |

**Neden bu taban:** dosya, Git ve test düzeyinde doğrulandı.

- `github-main` (`2cfdf06`), `birlesik`'in **atası**; Mac hattının bütün geçmişi korunuyor.
- `birlesik`, `main`'in (`6c2d203`) üst kümesi. A.4, A.5A, A.5B ve A.6 dosyaları `main` ile byte düzeyinde aynı.
  `main`'den farklı dosyalar Mac hattındaki 10-A.3/10-A.7 ve SteelBench eklemeleri. `detector.py` farkı yalnızca
  çizim katmanında: oranlar karede yazılmıyor, karar mantığı değişmedi.
- `main`'de olup `birlesik`'te olmayan tek dosya `PPE_Claude_Code_PostgreSQL_Uygulama_Plani.md`. Kullanıcı bu
  dosyayı `2cfdf06`'da bilerek silmişti; geri getirilmedi.

**Korunan ve konsolide edilen geçmişler:**

- **Korunan:** `github-main` → `birlesik/faz-10a-tam` → `integration/faz-01` doğrusal zinciri.
- **İçerik olarak konsolide edilen:** orphan `main` geçmişi (`14ea8c4` kökü, `e1f1e48`…`6c2d203`). `birlesik`'te
  cherry-pick kopyalar olarak var (`452035c`, `9cbc945`, `603a314`, `9f03688`, `6ddabd3`).
- **Zorla birleştirilmedi:** üç bağımsız geçmiş arasında `--allow-unrelated-histories` merge'i yapılmadı.
  `main` ve `ppe-team/sync/phase-10a6-20261102` olduğu gibi duruyor.

## 2. Korunan branch'ler ve yedek

| Ref | Commit | Durum |
|---|---|---|
| `github-main` | `2cfdf06` | Değişmedi; 8 WIP dosyası commit edilmemiş haliyle yerinde |
| `birlesik/faz-10a-tam` | `6ddabd3` | Değişmedi |
| `main` | `6c2d203` | Değişmedi |
| `sync/phase-10a6-20261102` | `6c2d203` | Değişmedi |
| `benim/*`, `ppe-team/*` (remote-tracking) | — | Değişmedi |

Doğrulama: `git for-each-ref` çıktısı (yeni `integration/faz-01` hariç), yedekteki `refs.txt` ile birebir aynı.

**Git dışı yedek:** `C:\Users\PC\ppe-local-backups\faz01-20261117T184048Z\`

- `wip.patch` (`git diff --binary`) ve `wip.stat.txt`
- `files/`: 8 WIP dosyası ve untracked FAZ 00 raporu
- `all-refs.bundle`: bütün ref'ler; `git bundle verify` OK
- `refs.txt`, `README.txt` (HEAD/branch) ve `SHA256SUMS` (`sha256sum -c` OK)
- Erişim: `icacls` ile devralma kapalı, yalnızca `LAPTOP-KF99T698\PC` tam yetkili
- Gizli değer taraması: 5 üretim gizli değerinin hiçbiri yedekte yok. Bu yüzden şifreleme gerekmedi.
- Stash kullanılmadı.

## 3. WIP değişikliklerinin durumu

8 WIP dosyasının hepsi entegre edildi; hiçbir bölüm atılmadı. WIP içeriği FAZ 10-A.7 rol görünürlüğü:

- `ctx.technical`: teknik alanlar yalnızca admin'e gidiyor (iz no, video saniyesi, run kimliği, kutu, kaynak, ham
  olay sayıları).
- `/api/report` ve `/api/history/runs*` uçları admin'e daraltıldı.
- Admin olmayan rollerde canlı `meta` sadeleştirildi.
- CLI'ya `rename` ve `set-role` komutları eklendi. Son admin'in rolü düşürülemiyor ve rol değişince oturumlar iptal
  ediliyor.
- UI'da teknik alanlar gizlendi.

Uygulama `git apply --3way` ile yapıldı. 4 dosya çakışma olarak işaretlendi: FAZ 00'da `patch` ile görülen 3 hunk,
artı `app.py`.

## 4. Çözülen çakışmalar

Metin birleştirmesi değil, iki tarafın amacına göre çözüldü:

| Dosya | Ours (`birlesik`, A.6) | Theirs (WIP, A.7) | Çözüm |
|---|---|---|---|
| `dashboard/app.py` (`_incident_out` çevresi) | `_review_out` eklenmiş; incident yanıtında `review` alanı var | `_incident_out` → `_incident_full` + `technical` filtresi (`INCIDENT_TECH_KEYS`) | İkisi birleştirildi. `review` operasyonel bir karar sayıldı ve teknik filtreyle çıkarılmıyor. Viewer'da `_review_out(detail=False)` ile yalnızca durum, sürüm ve zaman gidiyor; inceleyen adı ve not supervisor+'ya |
| `dashboard/auth/routes.py` `/me` | `permissions.review` (supervisor+) | `permissions.technical` (admin) | İkisi de var: `{live, snapshots, review, admin, model_scores, technical}` |
| `tests/test_access_control.py:44` | `/reviews` ucu supervisor matrisinde | `runs`, `runs/{id}` ve `report` admin'e daraltılmış | Daraltma korundu ve `/reviews` satırı eklendi |
| `tests/test_role_visibility.py:79` | `review: True` | `technical: False` | İkisi de beklenen sözlükte |

**Güvenlik kontrolü:**

- Kamera kapsamı (`camera_scope`) ve 404 davranışı değişmedi.
- Review uçları (`POST …/review`: CSRF + supervisor; `GET …/reviews`: supervisor) yalnızca review durumu döndürüyor;
  teknik alan sızmıyor.
- Hiçbir role yeni yetki verilmedi; WIP'in daraltmaları korundu.
- Eski istemciler: admin yanıtı aynı alanları içeriyor (`OLD_INCIDENT_KEYS` testi PASS); `/me`'ye yalnızca alan eklendi.

**WIP ile eski tarayıcı testi arasındaki tutarsızlık:** `ui_check_10a7.py` (`d63e184`) WIP'ten önce yazılmıştı.
User rolünün kartta Incident/Run/iz satırlarını, video özetini ve run raporlarını göreceğini varsayıyordu; WIP bunları
admin'e daraltıyor. Test WIP semantiğine uyarlandı, hiçbir kontrol silinmedi:

- Teknik kart alanları, run raporu düğmesi, video özeti ve run geçmişi akışı **admin** oturumunda sınanıyor.
- User için yeni kontroller: bu alanlar arayüzde gizli ve API 403 döndürüyor.
- Kart ile incident eşlemesi, görünür metin yerine sayfanın `openAlerts` haritasından yapılıyor. Bu harita
  kullanıcının zaten aldığı API verisinden kuruluyor; frontend değişmedi.

`README.md`'deki rol tanımı da WIP'e göre düzeltildi: user artık run raporlarını görmüyor.

## 5. Konsolide edilen özellikler (dosya + test ile doğrulandı)

| Bileşen | Kanıt (`integration/faz-01`) | Test |
|---|---|---|
| Dashboard backend/frontend | `dashboard/app.py`, `static/index.html` | dashboard 418 PASS · UI 30/30 + 153/153 |
| Migration 0001–0007 | `dashboard/alembic/versions/0001…0007` | `test_migrations`, `test_schema` PASS · izole 0005→0007 zinciri (§8) |
| Authentication / RBAC + rol görünürlüğü | `auth/*`, `ctx.technical` | `test_access_control` 35, `test_role_visibility` 16, `test_roles` 27 PASS |
| Human Review backend (10-A.6) | `db/reviews.py`, `POST/GET …/review(s)` | `test_reviews` 32 PASS |
| Detector / YOLO / ByteTrack | `detector/detector.py` (karar mantığı değişmedi) | detector 391 PASS |
| PPEMonitor / IncidentTracker | `ppe_core.py`, `protocol.py` (değişmedi) | `test_protocol`, `test_run_once` PASS |
| Durable outbox | `detector/outbox.py` | `test_outbox*` PASS |
| Reliability araçları | `tools/reliability/*` | SteelBench 13 PASS, 1 skip |
| 10-A.4 benchmark | `evaluation/baseline.py`, `tools/reliability/baseline_eval.py`, `baseline/*.json` | `test_evaluation_baseline` PASS |
| 10-A.5A failure analysis | `evaluation/failure.py`, `tools/reliability/failure_analysis.py` | `test_evaluation_failure` PASS |
| 10-A.5B controlled experiments | `evaluation/experiments.py`, `evaluation/isolation.py`, `tools/reliability/experiments_5b.py` | `test_evaluation_experiments`, `test_evaluation_isolation` PASS |
| Güvenlik testleri / secret audit | `tools/reliability/secret_audit.py` | PASS (§9) |
| Dokümantasyon / kurulum | `docs/*`, `docker-compose*.yml`, `deploy/lan/*` | §6, §7 güncellendi |

## 6. Test izolasyonu değişiklikleri (FAZ 01-B)

**Sorun (FAZ 00 R-11/S-04):** compose `tests` profili, ana `db` kümesine süper kullanıcı ve üretim parolasıyla
bağlanıp `DROP DATABASE` / `CREATE ROLE` çalıştırıyordu.

**Çözüm (FAIL-CLOSED, atlatma değişkeni yok):**

1. `dashboard/tests/db_guard.py`, her yıkıcı işlemden **önce** dört koşulu denetliyor:
   - (a) Ortamda `DATABASE_URL`, `MIGRATE_DATABASE_URL`, `ADMIN_DATABASE_URL`, `POSTGRES_PASSWORD` veya
     `PPE_*_PASSWORD` yok; hiçbir ortam değeri üretim `.env` gizli değerine eşit değil.
   - (b) URL PostgreSQL, veritabanı adı `_test` ile bitiyor, host `db` değil, parola üretim gizli değeri değil.
   - (c) Kümenin `cluster_name` ayarı `ppe-ephemeral-test` (sunucu kimliği).
   - (d) Kümede `postgres`, şablonlar ve `*_test` dışında veritabanı yok.

   Hata mesajları yalnızca anahtar adlarını içeriyor, değer yazılmıyor.
2. Korumanın bağlandığı yerler: `conftest._test_db_name()` (oturum başı), `conftest._admin_exec()` (her süper
   kullanıcı komutu), `perf_history.py`, `lan_server.py`, `ui/serve_fixture.py` (`serve_live_fixture` dahil).
3. `docker-compose.yml`:
   - Yeni `test-db` servisi: profile `test`, tmpfs, volume yok, port yok, yalnızca internal `testnet` ağında,
     `cluster_name=ppe-ephemeral-test`, üretim dışı kimlik bilgisi.
   - `tests` servisi `test-db`'ye bağlanıyor ve ana `db`'nin `default` ağında **değil** (`testnet` + `testegress`,
     pip için). İmaj `ppe-dashboard-test:dev`; ana `ppe-dashboard` etiketi testler için yeniden oluşturulmuyor.
4. `deploy/lan/lan_smoke.py`: ana `db`'ye `pg_isready` ve `DROP DATABASE` gönderilmiyor; temizlik
   `rm -sf test-db` ile yapılıyor.
5. `tools/reliability/isolated_dashboard_tests.py` (önerilen yol):
   - Geçici küme işaretli başlatılıyor.
   - `--ui` modu artık rastgele `UI_PASSWORD` üretiyor (önceden üretmiyordu).
   - Yeni `--ui-live` modu `ui_check_10a7.py`'yi çalıştırıyor; parola koşu sonunda silinen geçici bir dosyada duruyor.
6. **Detector ortam izolasyonu (FAZ 01-B.2):**
   - `detector_env.env_file()` artık tek kaynak; `detector._load_env()` da onu kullanıyor.
   - `DETECTOR_ENV_FILE` yok veya boşsa `detector/.env` okunuyor: **üretim davranışı aynı**.
   - Yol verilirse o dosya, `none` verilirse hiçbir dosya okunuyor.
   - Test conftest'i `none` ayarlıyor ve süreç üretim gizli değerini devralmışsa oturumu başlatmıyor.
   - Reliability harness zaten `DETECTOR_ENV_FILE` veriyordu; artık `_load_env` de buna uyuyor. 10-A.5B'nin
     "kalan sınır" dediği açık böylece kapandı. Harness'in kullandığı gizli olmayan ayarlar açıkça verildiği için
     davranış değişmedi.

**Pozitif ve negatif doğrulama (gerçek koşular):**

| # | Senaryo | Sonuç |
|---|---|---|
| P1 | İşaretli geçici küme, temiz ortam (isolated runner) | 418 PASS |
| P2 | Compose `tests` profili, ayrı proje adı (`-p ppe-faz01-composetest`, sahte env) | 418 PASS; `tests` ağları `testegress` + `testnet` (`default` yok); sonrasında `down -v` |
| N1 | İşaretsiz, üretim benzeri küme (`ppe` DB + nöbetçi tablo) → tüm pytest | **Reddedildi**, rc=2; nöbetçi yerinde; DB oluşmadı |
| N2 | İşaretli ama `ppe` veritabanı içeren küme | **Reddedildi**; veri yerinde |
| N3 | Ortamda `DATABASE_URL` | **Reddedildi** |
| N4/N5 | `perf_history`, `lan_server` işaretsiz kümede | **Reddedildi** (`UnsafeTestTarget`), DB işlemi yok |
| D1 | Sahte nöbetçi `detector/.env` mevcutken detector testleri | 391 PASS; nöbetçi değerler hiçbir çıktıda yok |
| D2 | Negatif kontrol: `DETECTOR_ENV_FILE` yok → yeni süreç `.env`'i yüklüyor (üretim varsayılanı) | doğrulandı (True) |
| D3 | `DETECTOR_ENV_FILE=none` → yüklenmiyor | doğrulandı (False) |
| D4 | Kabuktan üretim değeri devralınmış (`INGEST_TOKEN`) → pytest | **Reddedildi**, hiçbir test çalışmadı (pytest rc=4: conftest yükleme aşamasında `pytest.exit`) |
| U | `test_db_guard.py` (24) ve `test_env_isolation.py` (9) birim testleri | PASS |

## 7. Docker build provenance (FAZ 01-C.1)

- **`dashboard/Dockerfile`:**
  - OCI etiketleri: `org.opencontainers.image.{revision, ref.name, created, version, title}`,
    `com.ppe.build.{kind, tree-state}`.
  - `/app/build-info.json` yazılıyor.
  - Varsayılanlar `kind=development`, `tree_state=unknown`: doğrudan `docker build` veya `compose build` imajları
    UNVERIFIED oluyor.
- **`dashboard/app.py`:**
  - `load_build_info()`; `verified` yalnızca `release` + `clean` + 40 hanelik SHA ise True.
  - Açılışta doğrulanmamış imaj için `DEVELOPMENT/UNVERIFIED` uyarısı yazılıyor.
  - `/api/ops/metrics` (yalnızca admin) yanıtında `build` alanı var.
- **`scripts/build_image.py`:**
  - DEVELOPMENT → `ppe-dashboard-dev:<sha12>[-dirty]`.
  - RELEASE → `ppe-dashboard:<semver>` + `ppe-dashboard:sha-<sha12>`. Yalnızca temiz ağaçta (izlenmeyen dosya da
    yok), geçerli SemVer ile ve etiket henüz yokken; etiketler değişmez.
  - `ppe-dashboard:latest`'e asla dokunmuyor.
  - Build sonrası etiketler `docker image inspect` ile doğrulanıyor; manifest `build-manifests/`'a yazılıyor (Git dışı).
- **Doğrulama:**
  - `scripts/tests/test_build_image.py` 14 PASS.
  - Kirli ağaçta `--release` **reddedildi** (rc=2, kirli yollar listelendi).
  - Gerçek DEVELOPMENT build: `ppe-dashboard-dev:6ddabd371c34-dirty` (`17417aee5493`). Etiketler ve `build-info.json`
    doğru; açılışta UNVERIFIED uyarısı görüldü.
  - Bulunup düzeltilen hata: porcelain çıktısının `strip()` edilmesi ilk yolu bozuyordu; test eklendi.
- **Mevcut imajlar** silinmedi ve değiştirilmedi. `ppe-dashboard:latest` (`6f4ac355`, commit'siz koddan, etiketsiz)
  olduğu gibi duruyor. Bu fazda yeni oluşan etiketler: `ppe-dashboard-dev:6ddabd371c34-dirty` ve
  `ppe-dashboard-test:dev` (compose test imajı).
- **Bilerek yapılmayan:** compose'daki üretim servislerine `${PPE_DASHBOARD_IMAGE}` eklenmedi, çünkü `build:` ile
  birlikte `docker compose build` bir release etiketinin üzerine yazabilir. Sürüm geçişi `docs/DEPLOYMENT.md`
  "Sürüm izlenebilirliği" bölümündeki onaylı `docker tag` adımıyla yapılacak.

## 8. Migration ve backup/restore doğrulaması (FAZ 01-C.2/C.3)

Hepsi işaretli, internetsiz, tmpfs'li geçici kümelerde yapıldı. Gerçek yedek verisi yalnızca bu kümelerde ve scratch
klasöründe bulundu; iş bitince silindi.

**Araç değişiklikleri (`scripts/`):**

- `ppe_ops.py`:
  - `OPTIONAL_TABLES` = `app_users`, `user_sessions`, `audit_log`, `incident_reviews`, `incident_review_history`.
    Bunlar yalnızca **varsa** sayılıyor, böylece 0006 sistemler bozulmuyor.
  - `compare_counts`: yalnızca manifestteki anahtarlar karşılaştırılıyor, ek tablolar bilgi olarak raporlanıyor.
  - `set_target`/`db_exec`: hedef işaretli geçici bir konteyner olabiliyor; işaretsiz hedef reddediliyor.
- `restore_check.py --target-container`: ana kümeye hiç bağlanmıyor; "ana DB değişmedi" kontrolü SKIP.
- `backup.py --target-container --snapshots-dir`: yalnızca doğrulama için. Varsayılan davranış aynı.
  `--out` proje dışını gösterdiğinde oluşan `ValueError` düzeltildi.

**Sonuçlar:**

| # | Kontrol | Sonuç |
|---|---|---|
| R1 | `restore_check` `ppe-20261101T213616Z` (0005, quiesced) | **PASS**: SHA256, `pg_restore`, revision, 5 çekirdek sayım, 47 snapshot referansı. Ek: users 1, sessions 3, audit 103 |
| R2 | `restore_check` `ppe-20261102T030016Z` (**0006**, quiesce yok) | **PASS**: aynı kontroller; audit 110 |
| M1 | Simüle üretim (R1 dökümü) 0005→**0006** | sayımlar 7/74/47/104/255, users 1, audit 103, snapshot ref 47 |
| M2 | 0006→**0007** | tüm sayımlar aynı; `incidents` ve `violation_events` satır md5'i 0006 ile **birebir aynı**; review tabloları boş |
| M3 | 0007→0006 rollback (veri yok) | sayımlar ve md5 aynı; review tabloları kaldırıldı |
| M4 | 0006→0007 yeniden | sayımlar ve md5 aynı |
| B1 | 0007'de test review kaydıyla `backup.py` (izole) → `restore_check` | **PASS**: `incident_reviews` 1, `incident_review_history` 1 manifestte ve geri yüklemede; not alanı korundu |
| M5 | Review verisi varken `alembic downgrade 0006` | **Reddedildi**: "Downgrade durduruldu: insan inceleme kararları var"; revision 0007, veri yerinde |
| N | İşaretsiz konteynere `restore_check` / `backup.py` | **Reddedildi**, doğrulama DB'si veya yedek klasörü oluşmadı |

**Mevcut yedeklerin durumu (salt okunur):**

- 7 DB yedeği var (0002 → 0006), hepsinde manifest ve `SHA256SUMS` mevcut.
- **Şifresiz.**
- `backups/` ACL'i devralınmış: Administrators, SYSTEM ve PC (`Users`/`Everyone` yok).
- BitLocker durumu yönetici yetkisi olmadan sorgulanamadı.

**`backups/env-20261028T172010Z` (`root.env`, `detector.env`):**

- İçerik okunmadı.
- Risk: üretim DB süper kullanıcı parolası, uygulama rol parolaları ve ingest token'ı düz metin olarak, proje
  klasöründe duruyor. Yerel yöneticiler ve bu klasörü kopyalayan her yedekleme aracı bunları okuyabilir.
- **Plan (onay gerekiyor; bu fazda uygulanmadı):**
  1. `age` kur, alıcı anahtar çiftini güvenli bir bilgisayarda üret (özel anahtar saha makinesinde **olmaz**).
  2. `tar` → `age -r <alıcı>` ile şifreli arşivi `C:\Users\PC\ppe-local-backups\` (ACL'li) altına yaz ve özetini doğrula.
  3. Deneme açması güvenli makinede yapılır.
  4. Ancak bundan sonra, kullanıcı onayıyla düz kopyalar silinir.
  5. Düz kopyaların ne kadar süre açıkta kaldığına bakarak rotasyon değerlendirilir (`docs/SECURITY.md`).

**Şifreleme:** `age` kurulu değil; `gpg` (Git Bash) var. Otomatik yedek şifrelemesi **uygulanmadı**, çünkü anahtar
yönetimi kararı gerekiyor. Tasarım `docs/BACKUP_RESTORE.md`'de.

**Yeni üretim yedeği:** **alınmadı**. Ana yığın durmuş; yedek almak `db` servisini başlatmayı gerektiriyor ve bu
açık onay ister. Gereklilik: dedektör 2026-11-09…11-12 arasında çalıştı, bu yüzden 11-02'deki 0006 yedeğinden
sonraki olaylar hiçbir yedekte yok. 0007 geçişinden **önce** zorunlu.

## 9. Test sonuçları (bu fazda koşuldu; son kod üzerinde)

| Kategori | Ortam | Sonuç |
|---|---|---|
| Dashboard testlerinin tamamı (WIP + 0007 + yeni koruma ve build testleri) | isolated runner, geçici küme | **418 passed** |
| Aynısı, compose `tests` profili | ayrı proje + `test-db` | **418 passed** |
| Human Review backend | (yukarıdakilerin içinde) `test_reviews` | 32 PASS |
| Auth/RBAC + rol görünürlüğü | `test_access_control` / `test_role_visibility` / `test_roles` | 35 / 16 / 27 PASS |
| Migration upgrade/rollback | `test_migrations` 11, `test_schema` 61 + izole gerçek veri zinciri (§8) | PASS |
| Detector testlerinin tamamı (outbox bütünlüğü dahil) | venv, sahte nöbetçi `.env` | **391 passed** |
| Build provenance kuralları | `scripts/tests` | 14 passed |
| Reliability yardımcıları | `tools/reliability` (SteelBench) | 13 passed, 1 skipped |
| Browser: `ui_check.mjs` (Edge, sentetik fixture) | `--ui` | **30/30 PASS** |
| Browser: `ui_check_10a7.py` (Chrome headless, canlı fixture, rol görünürlüğü) | `--ui-live`; `websocket-client` proje dışı scratch venv'de | **153/153 PASS** (uyarlamadan önce WIP uyumsuzluğu nedeniyle FAIL, §4) |
| Secret audit (gerçek repo kökü, 5 etiket) | salt okunur | **PASS**: test env, 10 test konteyneri, 556 kanıt dosyası, izlenen dosyalar, tüm Git geçmişi |
| Secret audit (entegrasyon worktree) | salt okunur | PASS (`.env` yok, 0 etiket) |

Bütün dashboard ve UI testleri işaretli geçici kümelerde koşuldu. Fixture'lar yalnızca `127.0.0.1`'e yayınlandı;
gerçek snapshot veya video kullanılmadı.

## 10. Eksik veya BLOCKED maddeler

| Madde | Durum | Gerekçe ve sonraki adım |
|---|---|---|
| Ana DB'nin gerçek revision'ı (`0006`) | ÇIKARIM (değişmedi) | Ana kümeye bağlanılmadı (talimat) |
| Yeni üretim yedeği (0006, 11-12 sonrası veri) | **BLOCKED (onay)** | `db` servisinin başlatılması gerekiyor |
| Yedek şifrelemesi (otomatik) | **BLOCKED (karar)** | Alıcı anahtar ve saklama kararı; `age` kurulumu |
| `backups/env-*` taşıma/şifreleme | **BLOCKED (onay)** | Plan §8'de; silme işlemi onaya bağlı |
| BitLocker / disk şifrelemesi | NOT_TESTED | Yönetici yetkisi gerekiyor |
| `deploy/lan/lan_smoke.py` (Caddy + TLS) | NOT_TESTED | Yeni `test-db` yoluna uyarlandı ama koşulmadı. Ana proje adıyla (`ppe-split`) compose çalıştırıyor; LAN fazında koşulmalı |
| `perf_history` (30 000 incident) | NOT_TESTED | Kapsam dışı; koruması doğrulandı (N4) |
| 24 sa soak, gerçek RTSP, GPU benchmark | NOT_TESTED | Talimat gereği kapsam dışı |
| Uzak depoya aktarım | Plan (§12) | Push/PR onaysız yapılmaz |

## 11. Geri dönüş yöntemi

- **Kod:** `github-main` ve diğer bütün branch'ler değişmedi; WIP orada hâlâ commit edilmemiş halde duruyor.
  Konsolidasyonu tamamen geri almak için (onayla):

  ```
  git worktree remove C:\Users\PC\Desktop\ppe-split-integration
  git branch -D integration/faz-01
  ```

  Tek commit olduğu için kısmi geri dönüş `git revert` ile de yapılabilir.
- **Yedekten:** `git clone C:\Users\PC\ppe-local-backups\faz01-20261117T184048Z\all-refs.bundle` (bütün ref'ler) ve
  `git apply wip.patch` (`2cfdf06` üzerine).
- **İmajlar:** hiçbir mevcut etiket değişmedi. Yeni etiketler gerekirse kaldırılabilir:
  `docker rmi ppe-dashboard-dev:6ddabd371c34-dirty ppe-dashboard-test:dev`.
- **Çalışan sistem:** hiçbir şey değişmedi; geri dönüş gerekmiyor.

## 12. Uzak depoya aktarım planı (ayrı görev; onay gerekli)

**Düzeltilmiş varsayım:** "`ppe-team/main` ile ortak geçmiş yok" ifadesi yalnızca yerel orphan `main` (`6c2d203`)
hattı için doğru. `ppe-team/main` (`858184c`), `integration/faz-01`'in **atasıdır**
(`git merge-base --is-ancestor` doğrulandı; `858184c` ile `6ddabd3` arasında 31 commit, üstüne konsolidasyon
commit'i).
Bu nedenle `ppe-team/main` ← `integration/faz-01` aktarımı ilişkisiz geçmiş birleştirmesi **gerektirmez**:

1. **Önerilen:** `integration/faz-01`'i hedef repoya yeni bir branch olarak push et
   (`git push ppe-team integration/faz-01`). `ppe-team/main` ← `integration/faz-01` PR'ı normal çalışır
   (fast-forward edilebilir geçmiş). PR açıklamasına bu rapor bağlanır.
2. `ppe-team/sync/phase-10a6-20261102` (orphan hat) yalnızca arşiv olarak kalır; içeriği `integration/faz-01`'de var.
3. Force push yok; `main` değiştirme kararı ekip incelemesinden sonra verilir.
4. Push öncesi: `secret_audit` tekrarı, `git log ppe-team/main..integration/faz-01 --stat` incelemesi, büyük ve ikili
   dosya taraması (`.pt`, `.mp4`, `.jpg` Git'te yok; doğrulandı).

## 13. Bu fazda yaşanan sorunlar (şeffaflık)

- Negatif hedef testinde yanlışlıkla başka bir projenin konteyneri (`career-agent-postgres`) hedef verildi. Orada
  yalnızca salt okunur `SELECT current_setting('cluster_name')` çalıştı; hiçbir şey değiştirilmedi ve araç sonraki
  adımları reddetti. Test kendi geçici konteynerimizle tekrarlandı. Aracın boş `cluster_name` durumunda düzgün mesaj
  yerine `IndexError` vermesi de düzeltildi.
- Yama betikleri Windows'ta dosyaları CRLF ile yazdı; commit'ten önce LF'ye geri çevrildi (`.gitattributes eol=lf`).

## 14. Sonraki faz için öneriler

1. **FAZ 02-A — Onaylı bakım penceresi** (ana sisteme dokunan ilk adım; ayrı onay gerekli):
   - (a) `db` başlatılır ve `backup.py --quiesce` ile yeni yedek alınır.
   - (b) `restore_check --target-container` ile izole doğrulama yapılır.
   - (c) `integration/faz-01` commit'inden `build_image.py --release 0.10.7`.
   - (d) `docker tag` ve `compose up -d migrate dashboard` ile 0007'ye geçilir.
   - (e) `/health/ready` revision 0007, `db.roles check` ve UI smoke kontrolleri.
   - Geri dönüş: önceki imaj etiketi ve yedek.
2. **FAZ 02-B — Human Review UI** (10-A.6 API sözleşmesi + 10-A.7 rol görünürlüğü; not alanı için KVKK uyarısı).
3. **Paralel (karar/onay):**
   - Yedek şifreleme anahtarı ve `backups/env-*` taşıma.
   - Ultralytics lisans kararı.
   - Uzak depoya aktarım (§12).
4. **Küçük işler:**
   - `index.html:598` sayısal alanların `esc()`'ten geçirilmesi.
   - İmaj Python 3.11 → 3.12 hizalamasının değerlendirilmesi.
   - `lan_smoke.py` koşusu (LAN fazında).
