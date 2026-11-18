# FAZ 10-A.1 raporu — Teknik güvenlik, bellek kararlılığı ve pilot öncesi açıkların kapatılması

Tarih: 2026-11-02 (UTC). Başlangıç: `d67854d` (FAZ 10-A). Bu faz **saha pilotu başlatmaz**; FAZ 10-B'ye geçilmedi.

**İki ayrı karar:**

| Karar | Sonuç |
|---|---|
| Technical Readiness | **INCOMPLETE** — bellek kök nedeni bulundu, düzeltildi ve aynı makinede A/B ile doğrulandı; regresyonlar PASS; veri kaybettirmeyen uygulama geri dönüşü izole ortamda kanıtlandı. Eksik: 24 saatlik GPU soak, Windows GPU eşdeğerlik (parity) kaydı, Windows ACL/BitLocker, ana ortam manuel kabul, Windows ana DB uzlaştırma dry-run |
| Real Construction Site Pilot | **NO-GO** — KVKK, model lisansı, etiketli saha verisiyle doğruluk kabulü, İSG kabul eşikleri ve gerçek RTSP kamera testi yok |

**Ortam notu (önemli):** Bu faz iki makinede yürütüldü ve sonuçlar makineye göre ayrı yazıldı:

| | Windows dizüstü (ana ortam) | macOS iMac (M1, 8 GB) |
|---|---|---|
| Rol | Ana yığın gerçek verilerle (FAZ 10-A: 104 olay, 74 run, 4 sahipsiz run); RTX 3050 (CUDA) | Projenin kopyası; **ayrı, yeni kurulmuş** ana yığın (0 run, 0 olay, 1 kullanıcı); CUDA yok |
| Bu fazda yapılan | bellek probu ve taban ölçümü (90 dk), kod düzeltmeleri, dedektör venv paket güncellemesi, GPU uçtan uca kısa koşular | düzeltmenin aynı makinede A/B doğrulaması, regresyon testleri, pip-audit, dry-run, belgeler |
| Yapılamayan | — (oturum yarıda kaldı) | GPU/CUDA, Windows ACL, BitLocker, ana veritabanı (gerçek veri) işlemleri |

Windows oturumunun kayıtları `tools/reliability/output/` altındaki ham çıktılardan okundu; kaydı olmayan iş
"yapıldı" sayılmadı.

## 1. Devralınan Git durumu

Dal `main`, HEAD `d67854d` (FAZ 10-A), commit edilmemiş FAZ 10-A.1 çalışması (Windows oturumu):
değişmiş `dashboard/db/{health,repository,session}.py`, `docs/RELIABILITY_TEST_PLAN.md`,
`tools/reliability/{common,eventgen,sampler,soak,telemetry_check}.py`; yeni `dashboard/tests/test_statement_cache.py`,
`scripts/{venv_dedupe.py,windows_acl.ps1}`, `tools/reliability/{gpu_parity,memboot,memtest}.py`; kullanıcının plan
belgesi `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md` (korundu, commit'e eklenmedi). Hiçbiri ezilmedi; üzerine
devam edildi.

macOS Docker: ana yığın `ppe-split` (dashboard + db, healthy, Alembic **0006**, FastAPI 0.162.0 / Starlette 1.10.0,
`/healthz` 200, `/health/ready` 200, log'da traceback 0); çalışan imaj çalışma ağacındaki (düzeltmeli) kodu içeriyor
(dosya özetleri karşılaştırıldı). Başka projelere ait container'lar (payment-service, otel, jaeger) vardı; dokunulmadı.
Geri dönüş imajları (`rollback-*`) yalnız Windows makinesinde.

## 2. Dashboard bellek artışı — kök neden

FAZ 10-A 4 saatlik soak: dashboard container belleği 80 → 95 MB, ~3.7 MB/sa doğrusal.

### 2.1 Yöntem

`tools/reliability/memboot.py`: uvicorn'u Dockerfile ile aynı ayarlarla başlatıp aynı süreçte örnekleyici thread
çalıştırır (yalnız izole `ppe-rel` yığını; ürün kodu değişmez). Ayrı katmanlar: cgroup (`docker stats` ile aynı
değer + **anon / file / kernel** ayrımı), süreç RSS/RssAnon, glibc `mallinfo2` (kullanımda / boş / mmap), Python
blokları, GC, tip histogramı, uygulama yapılarının boyutları, SQLAlchemy derlenmiş ifade önbelleği (ve hangi
ifadelerin girdi ürettiği). `tools/reliability/memtest.py`: GPU'suz, tekrarlanabilir yük — 4 sentetik kamera
(gerçek `PPEMonitor` → `IncidentTracker` → SQLite outbox → HTTP; gerçek video karelerinden 640 px JPEG, 12 fps,
snapshot'lı olaylar), operatör taklidi (`/ws` + 0.7 sn'de bir `/api/state` + 30 sn'de bir `/api/ops/metrics`).
Kayıp/çift defterle (`ledger`) ölçülür.

### 2.2 Bulgular (Windows taban ölçümü, düzeltme öncesi)

`mem_base90`: 2026-11-02T02:31:30Z → 04:01:30Z (5 400 sn, 160 örnek), imaj `c802d9a8…` (FAZ 10-A kodu).
Teslim: 12 590 olay → DB 12 590 tekil, **kayıp 0, çift 0**; traceback 0.

| Katman (ilk %10 → son %10 medyan) | Değer | Eğim |
|---|---|---|
| `docker stats` belleği | 83.6 → 92.7 MB | +7.8 MB/sa |
| ├ cgroup **anon** (süreç belleği) | 79.7 → 84.2 MB | +4.6 MB/sa |
| ├ cgroup **file** (sayfa önbelleği: yazılan snapshot JPEG'leri) | 1.8 → 12.0 MB | +8.4 MB/sa (geri kazanılabilir) |
| └ cgroup **kernel** (slab: dentry/inode) | 3.8 → 8.3 MB | +3.8 MB/sa (geri kazanılabilir) |
| glibc kullanımda | 21.7 → 22.4 MB | +0.6 MB/sa |
| Python blokları | 460 706 → 490 279 | +23 169 /sa |
| **SQLAlchemy derlenmiş ifade önbelleği** | **41 → 183 girdi** (max 190) | **+117 girdi/sa** |
| `Session` alt sınıfı sayısı | 2–36 arası dalgalanma | istek başına yeni `sessionmaker` |
| Thread / FD / WebSocket aktif / auth önbelleği | 11–12 / 24–25 / 1 / 1 | sabit |

Sınırlı olduğu doğrulanan yapılar: `ALERTS` (300), gecikme/DB yazım ölçüleri (`deque(maxlen)`), denetim dedupe
(10 000'de temizlenir), IP sınırlayıcı, oturum önbelleği (1), `CAMS` (kamera sayısı kadar); WebSocket bağlantı başına
yerel durum tutar ve `finally` ile kapanır (aktif = 1, kopma yok).

**Kök nedenler (üçü de düzeltildi):**

1. **Hazırlık kontrolü önbelleği büyütüyordu (ana sızıntı benzeri büyüme).** `/health/ready` ve `/api/ops/metrics`
   her çağrıda `MigrationContext.configure(conn).get_current_heads()` kullanıyordu; bu her seferinde yeni bir
   `alembic_version` `Table`/`MetaData` nesnesi kurar. SQLAlchemy önbellek anahtarı bu nesneye bağlı olduğundan her
   kontrol **yeni bir önbellek girdisi** ekliyor ve nesneleri canlı tutuyordu (LRU üst sınırı 500 girdiye kadar
   sürekli artış; docker sağlık kontrolü + operatör metrik yoklaması bunu sürekli tetikler). Düzeltme: sabit metinli
   iki sorgu (`to_regclass('alembic_version')` + `SELECT version_num`); anlam aynı (`dashboard/db/health.py`).
2. **İstek başına `sessionmaker`.** Her `get_session()` yeni bir `Session` alt sınıfı üretiyordu (çöp + sınıf
   kaydı dalgalanması). Düzeltme: fabrika motor başına bir kez (`dashboard/db/session.py`).
3. **Çok satırlı yazımlar her çağrıda yeniden derleniyordu (geçici tepe).** PostgreSQL upsert ifadeleri önbelleğe
   alınmaz; `insert(...).values(rows)` satır başına ~5 KB ifade derliyordu (5 000 kişilik raporda ~51 MB geçici tepe,
   ~6 sn). Düzeltme: executemany (`s.execute(stmt, rows)`; ~15 MB, < 1 sn). Satır sırası (kilit sırası),
   `ON CONFLICT`, `RETURNING` davranışı aynı (`dashboard/db/repository.py`).

Yeni testler (`dashboard/tests/test_statement_cache.py`, gerçek PostgreSQL 17, `ppe_app_test` rolü): 50 hazırlık
kontrolünde önbellek artışı ≤ 1; 5 000 satırlık rapor yazımında geçici bellek < 30 MB (tracemalloc); executemany
sayfa sınırını (1 000) aşan rapor + birebir tekrar 200 + farklı rapor 409; yeni/var olan karışık olaylarda yalnız
yenilerin sayılması. **4/4 PASS** (tam paket içinde, §10).

### 2.3 Düzeltmenin doğrulanması — aynı makinede A/B (macOS)

Aynı makine (macOS, Docker Desktop VM 4 GB / 8 CPU), aynı yük, iki farklı imaj, sırayla 90'ar dakika; ilk 10 dk
ısınma eğimlerden hariç. Yük: 4 sentetik kamera (gerçek outbox, 12 fps, 640 px JPEG, snapshot'lı olaylar) + operatör
taklidi (`/ws`, 0.7 sn'de `/api/state`, 30 sn'de `/api/ops/metrics`).

| | Düzeltme SONRASI (`mac_fix90`) | Düzeltme ÖNCESİ (`mac_base90`, HEAD) |
|---|---|---|
| İmaj | `a7fd21e6…` (çalışma ağacı) | `8bc40d1d…` (`git archive HEAD dashboard`) |
| Ölçüm (UTC) | 09:00:59 → 10:30:59 (5 400 sn) | 10:31:07 → 12:01:07 (5 400 sn) |
| Teslim | 12 902 → DB 12 902, kayıp 0, çift 0 | 12 908 → 12 908, kayıp 0, çift 0 |
| Operatör / WS | 7 577 `/api/state` (0 hata), 177 metrik; 1 WS, ping 269 | 7 585 (0 hata), 177; 1 WS, ping 269 |
| Traceback / kamera çıkışı | 0 / hepsi 0 (SIGINT ile zarif) | 0 / hepsi 0 |

| Katman (ilk %10 → son %10 medyan; eğim/sa) | SONRA | ÖNCE |
|---|---|---|
| **SQLAlchemy derlenmiş ifade önbelleği** | **12 → 12 (0)** | 40 → 182 (+118/sa); son ölçümde 188 girdinin **163'ü aynı `SELECT alembic_version.version_num …`** |
| `Session` alt sınıfı | **1 → 1** | 4–39 dalgalı |
| cgroup anon (süreç belleği) | 81.9 → 85.4 MB (**+2.4 MB/sa**) | 81.8 → 90.2 MB (+6.1 MB/sa) |
| RssAnon (süreç içinden) | 76.2 → 79.2 MB (+2.9 MB/sa) | 77.3 → 84.0 MB (+6.2 MB/sa) |
| glibc kullanımda | 21.4 → 21.5 MB (**+0.09 MB/sa ≈ düz**) | 21.8 → 22.6 MB (+0.59 MB/sa) |
| glibc boş (OS'e dönmemiş) / arena | +1.18 / +1.27 MB/sa | +2.62 / +3.21 MB/sa |
| Python blokları | +4 300 /sa | +23 000 /sa |
| cgroup file (sayfa önbelleği) + kernel (slab) | +8.5 + 3.8 MB/sa | +8.4 + 3.8 MB/sa (aynı) |
| `docker stats` | 81.0 → 89.6 MB (+7.8 MB/sa) | 82.1 → 93.6 MB (+10.8 MB/sa) |
| Thread / FD / WS aktif / auth önbelleği | 11 / 23 / 1 / 1 sabit | 11–12 / 23–24 / 1 / 1 |

Ham veriler (Git dışı): `tools/reliability/output/20261102T090053Z_mem_mac_fix90/`,
`…/20261102T103101Z_mem_mac_base90/` (`memprobe.jsonl`, `host.jsonl`, `result.json`). Windows taban ölçümü
(`mem_base90`, §2.2) ile önce-koşusu aynı imzayı verdi (önbellek 41 → 183, Python blokları +23 169/sa).

### 2.4 Değerlendirme

- **Kök neden kanıtlandı ve giderildi:** düzeltme öncesi önbelleğe her hazırlık kontrolünde aynı `alembic_version`
  sorgusu yeni girdi olarak ekleniyordu (163 kopya); düzeltmeden sonra önbellek 12'de sabit, `Session` sınıf
  dalgalanması yok, Python nesne artışı ~5 kat azaldı, süreç (anon) bellek eğimi 6.1 → 2.4 MB/sa.
- **`docker stats` artışının çoğu sızıntı değil:** iki koşuda da aynı olan +12.3 MB/sa, yazılan snapshot dosyalarının
  sayfa önbelleği ve dosya sistemi slab'ıdır (geri kazanılabilir kernel belleği; bellek baskısında boşaltılır).
  FAZ 10-A'daki "80 → 95 MB" bunun ve önbellek büyümesinin toplamıydı.
- **Kalan +2.4 MB/sa:** canlı veri (glibc "kullanımda") düz; artış ayırıcının boş listeleri/arena'sında (parçalanma,
  OS'e geri verilmeyen bellek). 90 dakikada platoya ulaşıp ulaşmadığı **kanıtlanamaz** → kesin karar 24 saatlik
  `soak.py --hours 24 --memprobe` ölçütüne bağlı (son 12 sa anon eğimi < 0.5 MB/sa). Gerekirse sonraki adım:
  `memtest.py --env MALLOC_ARENA_MAX=2` ile A/B (ürün koduna dokunmadan).
- Sonuç: **dashboard bellek artışı düzeltmesi PASS (90 dk A/B)**; **uzun süreli kararlılık INCOMPLETE** (24 sa yok).

## 3. 24 saatlik soak

**INCOMPLETE — koşulmadı.** Gerekçe: tanımlı 24 saatlik soak gerçek `detector.py` + gerçek model + **GPU** ile
koşar (FAZ 10-A §8 ile karşılaştırılabilir olması için); bu oturumun sürdüğü macOS makinesinde CUDA yok ve Windows
makinesine erişim yok. GPU'suz sentetik yükle 24 saat koşmak dashboard belleği için kanıt üretir ama dedektör
tanıtıcı/thread/GPU belleğini ölçmez; "24 saatlik soak" yerine geçmez.

Prosedür hazır ve yeniden başlatılabilir (`docs/RELIABILITY_TEST_PLAN.md` §9): ön koşullar (AC güç, uyku kapalı,
≥ 10 GB disk, ≥ 3 GB boş RAM, güncel izole imaj, başka GPU testi yok), komut
`python tools/reliability/soak.py --hours 24 --memprobe`, PASS ölçütleri (ölçüm ≥ 86 400 sn; bellek koruması ve
dedektör çıkışı yok; kayıp/çift/bekleyen/ölü 0; her ağ arızası doğru durumla; traceback 0; dedektör tanıtıcı/thread
1. saatten sonra sabit; **dashboard anon belleği son 12 saatte plato (eğim < 0.5 MB/sa)** ve derlenmiş ifade önbelleği
sabit), yarıda kalırsa kısmi süre "N saat, INCOMPLETE" yazılır ve baştan başlatılır (süreler birleştirilmez).

## 4. Dedektör bağımlılık güvenliği

| Paket | FAZ 10-A (rapor) | Windows `detector/.venv` (şu an, dist-info) | macOS kök `.venv` | Düzeltme sürümü |
|---|---|---|---|---|
| Pillow | 12.3.0 | **13.0.0** | 13.0.0 | 13.0.0 |
| filelock | 3.32.3 | **4.2.4** | 4.2.6 | 4.2.1 |
| setuptools | 78.1.0 | **84.2.1** | 84.2.1 | 84.1.0 |
| torch / torchvision | 2.14.1+cu126 / 0.29.1+cu126 | değişmedi | 2.15.0 / 0.29.2 (CPU) | PyPI dışı, denetlenemez |
| ultralytics / opencv-python / numpy | 8.4.183 / 4.10.0.84 / 2.5.4 | değişmedi | aynı | — |

- **pip-audit (2026-11-02 ~09:06Z, canlı OSV/PyPI):** Windows dedektör venv'inin 47 paketi (dist-info sürümleriyle;
  `+cu126` yerel sürümler hariç) **No known vulnerabilities found**; macOS venv **temiz**; `dashboard/requirements.txt`
  **temiz**.
- Kurulum bütünlüğü: `scripts/venv_dedupe.py` (kuru çalıştırma) Windows venv'inde çift `*.dist-info` bulmadı
  (yarım kalmış yükseltme izi yok).
- Uygulanabilirlik (FAZ 10-A değerlendirmesi değişmedi): dedektör kareleri OpenCV ile okur, Pillow ile güvenilmeyen
  dosya açmaz; filelock yalnız yerel önbellek kilitleri; setuptools yalnız kurulum yolu.
- **Dedektör regresyonu (macOS, güncel paket sürümleriyle, GPU yok):** 244 PASS, 1 SKIP (Windows ACL testi) — §10.
- **GPU doğrulaması:** Windows oturumunda güncellemeden sonra gerçek GPU uçtan uca kısa koşular var
  (`e2e_after_fix`: 04:09:43Z, 1 kamera 120 sn; `e2e2_after_fix`: 04:12:26Z, 2 kamera 120 sn): `best.pt` CUDA
  cihaz 0, ByteTrack kimlikleri var, 22 olay → DB 22, eksik 0, zarif kapanış. **Ancak** kayıtlarda hangi venv'in
  kullanıldığı ve `gpu_parity.py` karşılaştırma çıktısı (eski ↔ yeni venv kare kare model/ByteTrack/PPEMonitor
  eşdeğerliği) **yok**; "izole venv'de önce denendi" iddiası kayıtla doğrulanamadı.
- **Sonuç:** açık güvenlik kaydı kapandı (PASS: pip-audit). Paket yükseltmesinin **tamamlandı** sayılması için
  Windows'ta `gpu_parity.py` (eski sürümlerle izole venv ↔ güncel venv) ve tam dedektör regresyonu + kısa GPU soak
  gerekir → **INCOMPLETE (Windows'ta doğrulanacak)**. Komutlar: `docs/RELIABILITY_TEST_PLAN.md` §9.

## 5. Windows dosya izinleri ve veri güvenliği

**BLOCKED (bu makinede) — Windows gerekli; ana proje klasöründe değişiklik açık onay gerektirir.**

- Envanter bulgusu (FAZ 10-A): `C:\Users\PC\Desktop` üzerinde açık `BUILTIN\Users:(OI)(CI)(M)`; proje klasörü ve
  `.env`, `detector\.env`, `backups\`, `data\snapshots\`, `tools\reliability\output\` bunu miras alıyor.
- Hazır araç `scripts/windows_acl.ps1` (Windows oturumunda yazıldı; çalıştırıldığına dair kayıt yok), bu oturumda
  gözden geçirildi:
  - varsayılan **salt okunur envanter**; `-Backup` → `icacls /save` (proje **dışında**, `%USERPROFILE%\ppe-acl-backup`);
  - `-Apply -IConfirm DARALT` → önce yedek, sonra kökte mirası kesip yalnız geçerli kullanıcı + SYSTEM +
    Administrators `(OI)(CI)F`, alt öğelerde açık geniş girdileri kaldırır; üst klasörü (Desktop) **değiştirmez**;
  - `-Rollback -BackupFile` → mirası açar, `icacls /restore` ile birebir geri yükler.
  - Docker Desktop (kullanıcı hesabı + SYSTEM hizmeti), dedektör, yedekleme betikleri aynı kullanıcıyla çalıştığından
    erişim korunur; bind mount `./data` yazımı Docker Desktop üzerinden yapılır. Outbox kodu kendi oluşturduğu
    dizini zaten sahibine kısıtlıyor (FAZ 7).
- Önerilen sıra (Windows, kullanıcı onayıyla): `.\scripts\windows_acl.ps1` (envanter) → `-Backup` → onay →
  `-Apply -IConfirm DARALT` → doğrulama: `docker compose up -d` sonrası `/health/ready` 200, yeni snapshot yazımı,
  `python scripts/backup.py` + `restore_check.py`, dedektör outbox yazımı → sorun olursa `-Rollback`.
- **BitLocker:** BLOCKED — `manage-bde -status` yönetici PowerShell gerektirir (FAZ 10-A'da da yetki istendi);
  bu makineden doğrulanamaz.

## 6. Yedek şifreleme hazırlığı

**TASARIM hazır, uygulanmadı (karar bekliyor):** `docs/BACKUP_RESTORE.md` "Yedek şifreleme". Özet: açık anahtarlı
şifreleme (`age`, X25519) — saha bilgisayarı yalnız alıcı anahtarını bilir, yedeği çözemez; özel anahtar makinede,
Git'te, log'da, raporda tutulmaz; iki kopya (veri sorumlusu + emanet), yıllık/personel değişiminde rotasyon, eski
anahtar eski yedeklerin saklama süresi bitene kadar korunur; şifreli geri yükleme adımları ve 3 ayda bir tatbikat.
BitLocker'ın yerine geçmez. Karar gereken: araç ve lisans onayı, anahtar sahipleri, saklama süresi (KVKK).

## 7. Dashboard manuel kabul kontrol listesi

`docs/MANUAL_ACCEPTANCE_CHECKLIST.md`: ön kontrol (sürüm, revision, hazır), giriş/çıkış ve çıkış sonrası içerik
gizleme, Canlı İzleme + Analiz kapsamı, İhlal Geçmişi + snapshot, Kamera / Run Raporları + İstatistikler, kamera
bazlı yetkilendirme (geçici viewer/supervisor; devre dışı bırakma), WebSocket kopma/yeniden bağlanma, sonuç tablosu.
Yönetici parolası istenmedi, kaydedilmedi, değiştirilmedi.

**Ana ortam manuel kabul: YAPILMADI** (yöneticinin Windows ana ortamında yapması gerekir). İzole otomatik testler
(§10) bunun yerine sayılmaz.

## 8. Run reconciliation (dry-run)

- **macOS ana yığını** (2026-11-02T09:01:07Z): `python -m maintenance.runs reconcile` → `DRY RUN — eşik 60 dk;
  aday 0 run`. Bu veritabanı yeni kurulum (0 run), anlamlı değildir.
- **Windows ana veritabanı** (4 sahipsiz `running` run, FAZ 10-A §10): bu oturumdan erişilemedi → **yeniden dry-run
  yapılmadı**. Windows'ta: `docker compose exec dashboard python -m maintenance.runs reconcile` (dry-run); sayı 4
  ise ve kullanıcı onaylarsa `--apply --confirm 4` (`abandoned` + denetim kaydı; silme yok, tahmini `finished` yok).
  `--apply` çalıştırılmadı.

## 9. Süreç izolasyonu ve rollback güvenliği

### 9.1 Süreç izolasyonu

- Kod taraması: tüm sonlandırmalar aracın kendi başlattığı `Popen` PID'ine (ve ağacına) yöneliktir
  (`taskkill /PID <pid> /T /F`); ad/desen filtreli toplu sonlandırma yok. FAZ 10-A'daki olay araç kodundan değil
  elle yazılmış bir komuttan kaynaklanmış olmalı.
- Değişiklik: `tools/reliability/common.py` — POSIX desteği (yeni oturum + SIGINT ile zarif durdurma) ve POSIX'te
  süreç grubu yalnız **PID kendi grubunun lideriyse** öldürülür (başka sürece sinyal gitmez). `memtest.py` aynı
  yardımcıları kullanır.
- Kural belgelendi (`RELIABILITY_TEST_PLAN.md` §1.5): elle sonlandırmadan önce PID + başlatılma zamanı + komut
  satırı + dizin doğrulanır, yalnız o PID sonlandırılır; `taskkill /IM`, `/FI`, `Stop-Process -Name`, `pkill`
  yasak; container işlemleri yalnız `ppe-rel` / `ppe-split` proje adıyla.
- Bu oturumda: yalnız `ppe-rel` ve `ppe-split` (test profili) kullanıldı; başka projelerin container'larına,
  volume'larına, süreçlerine dokunulmadı; `prune` / `down -v` kullanılmadı.

### 9.2 Migration ve rollback

**Mevcut prosedürün etkisi (FAZ 10-A §3):** eski imaja dönüş `alembic downgrade 0005` gerektiriyordu; downgrade
`disconnected` run varken durur (veri kaybı yerine güvenli hata) ama ana DB'de şema değişikliği ve onay ister.

**İzole test (ppe-rel, 2026-11-02 12:02–12:05Z) — downgrade OLMADAN eski uygulama:** `2b6202b` (0005 kodu)
`git archive` ile `ppe-dashboard:rel-pre0006` olarak derlendi; test DB'sinde bir run `disconnected` yapıldı
(yalnız izole DB).

| Adım | Sonuç |
|---|---|
| Eski imajın `migrate` adımı (`alembic upgrade head`) | **çıkış 255**: `Can't locate revision identified by '0006'` → normal `docker compose up -d` dashboard'u başlatmaz (depends_on) |
| Eski imaj, `up -d --no-deps dashboard` (migrate atlanır) | çalıştı; `/healthz` 200; `/health/ready` **503 not_ready** (şema farkı); traceback 0 |
| Eski kodla `/api/history/runs`, `/runs/{id}`, `/incidents`, `/summary`, `/api/state` | hepsi 200; `disconnected` run'ın detayı `status: disconnected` döndü (eski ORM'de durum serbest metin) |
| Eski kodla `?status=disconnected` filtresi | 422 (eski kod değeri tanımıyor) |
| DB sayımları ve revision (önce / eski kod sonrası / güncel imaja dönüş sonrası) | **aynı**: 0006; 1 078 run, 12 908 olay, 3 228 incident, 3 234 rapor satırı |
| Güncel imaja dönüş | `/health/ready` 200; filtre 200 |

**Sonuç:** 0006 gibi **eklemeli** bir migration'da uygulama geri dönüşü şema geri alınmadan, veri kaybı olmadan
yapılabilir: `docker tag ppe-dashboard:rollback-faz9-2b6202b ppe-dashboard:latest && docker compose up -d --no-deps
dashboard`. Bilinen yan etkiler: hazırlık 503 (izleme alarmı), `disconnected` filtresi 422, yeni dedektörün
`disconnected` raporları eski dashboard'da 422 → outbox'ta `dead` (kaybolmaz; dashboard güncellenince `requeue`).
**Ana ortamda uygulanmadı** (onay gerekir; şu an ihtiyaç da yok).

**Gelecek sürümler için öneri (ileriye uyumlu, veri kaybettirmeyen):**
1. *Expand/contract:* bir sürümde yalnız eklemeli migration (yeni değer/sütun/tablo); daraltma (sütun silme, kısıt
   daraltma) en erken bir sonraki sürümde, geri dönüş penceresi kapandıktan sonra.
2. *Uyumluluk bildirimi:* her imaj "okuyabildiği en yeni şema" listesini taşısın; `migrate` DB'yi daha yeni ama
   uyumlu bir revizyonda bulursa hata yerine atlasın, hazırlık `schema: newer_compatible` desin (FAZ 10-A.6 işi).
3. Her yükseltmeden önce: yedek + `restore_check.py` + Git'ten derlenmiş ve etiketlenmiş geri dönüş imajı (FAZ 10-A
   olayı: Docker Desktop eski imajı temizlemişti).
4. Downgrade yalnız son çare ve açık onayla; ana DB'de bu fazda **downgrade, reset veya veri silme yapılmadı**.

## 10. Regresyon testleri

| Paket | Makine | Sonuç |
|---|---|---|
| Dashboard (gerçek PostgreSQL 17, `docker compose --profile test run --rm tests`; 2026-11-02T08:53–08:54Z) | macOS | **PASS 338/338** (334 + 4 yeni bellek testi) |
| Dedektör (`../.venv/bin/python -m pytest`, güncel Pillow/filelock/setuptools, GPU yok; 09:01–09:02Z) | macOS | **PASS 244/245, SKIP 1** (Windows ACL testi platform gereği) |
| LAN TLS izole smoke (`deploy/lan/lan_smoke.py`; 12:03:39–12:03:54Z; geçici `ppe_lan_test` DB ve container'lar sonunda silindi) | macOS | **PASS 21/21** |
| Tarayıcı (Edge/Chromium + Node 22 CDP) | — | **SKIP** — macOS'ta Node yok (kurulmadı); bu fazda UI (`static/index.html`) değişmedi |
| pip-audit dashboard / dedektör (Windows venv sürümleri) / macOS venv | macOS | **PASS** (açık kayıt yok) |
| Bellek A/B (§2.3) | macOS | **PASS** (önbellek sabit, anon eğimi 6.1 → 2.4 MB/sa; 0 kayıp/çift) |
| Uygulama geri dönüşü, downgrade'siz (§9.2) | macOS (izole) | **PASS** (veri değişmedi; bilinen yan etkiler belgelendi) |
| Dedektör tam paket (FAZ 10-A.3 sonrası, 12:01Z) | macOS | PASS 307/308, SKIP 1 (244 + 63 yeni veri hattı testi) |
| GPU uçtan uca (güncellenmiş venv) | Windows | kısa koşular PASS (1 ve 2 kamera, 120 sn); parity kaydı yok |

## 11. PASS / FAIL / SKIP / BLOCKED

| Kalem | Sonuç | Kanıt |
|---|---|---|
| Dashboard bellek kök nedeni + düzeltme (3 değişiklik) + 4 yeni test | **PASS** | §2, §10 |
| Bellek A/B (aynı makine, 90 + 90 dk) | **PASS** | §2.3 |
| 24 saatlik soak | **INCOMPLETE** (koşulmadı; prosedür hazır) | §3 |
| Uzun süreli bellek platosu | **INCOMPLETE** (24 sa gerekir) | §2.4 |
| Bağımlılık açıkları (Pillow/filelock/setuptools) | **PASS** (pip-audit temiz) | §4 |
| Paket yükseltmesinin GPU eşdeğerliği (`gpu_parity.py`) | **INCOMPLETE** (Windows'ta kayıt yok) | §4 |
| Dedektör regresyonu (güncel paket sürümleri, CPU) | **PASS** 244/245 + 1 SKIP | §10 |
| Dashboard regresyonu | **PASS** 338/338 | §10 |
| LAN TLS | **PASS** 21/21 | §10 |
| Tarayıcı UI | **SKIP** (Node yok; UI değişmedi) | §10 |
| Windows ACL envanteri/daraltma | **BLOCKED** (Windows + onay) | §5 |
| BitLocker | **BLOCKED** (yönetici yetkisi, Windows) | §5 |
| Yedek şifreleme | **TASARIM hazır**, uygulanmadı | §6 |
| Ana ortam manuel kabul | **YAPILMADI** (liste hazır) | §7 |
| Run uzlaştırma dry-run | macOS: PASS (0 aday); **Windows ana DB: BLOCKED** (erişim yok); `--apply` yok | §8 |
| Süreç izolasyonu | **PASS** (kod taraması + POSIX grup kontrolü + kural) | §9.1 |
| Veri kaybettirmeyen geri dönüş | **PASS** (izole kanıt) + öneri | §9.2 |
| Gerçek RTSP, saha doğruluğu, KVKK, lisans, İSG eşikleri | **BLOCKED / NO-GO** | — |

## 12. Kalan riskler

1. Dashboard süreç belleğinde kalan +2.4 MB/sa (ayırıcı parçalanması olası) — 24 sa ölçülmeden plato iddia edilmez;
   en kötü durumda 1 GB sınırına aylar mertebesinde (OOM → yeniden başlatma, outbox replay; veri kaybı beklenmez).
2. Windows dedektör venv'i güncellendi ama eşdeğerlik (model çıktısı) kaydı yok.
3. Geniş dosya izinleri ve şifresiz yedekler (kişisel veri) — onay bekliyor.
4. Windows ana DB'de 4 sahipsiz `running` run (uzlaştırma onayı bekliyor).
5. Ana ortamda oturumlu ekranlar kullanıcı tarafından kontrol edilmedi.
6. İki makine (Windows ana ortam, macOS kopya) senkron değil: bu fazın commit'leri Windows'a taşınmalı (§14).

## 13. FAZ 10-B ön koşulları

1. Windows'ta: bu commit'lerin alınması, `gpu_parity.py` (eski ↔ yeni venv) + dedektör tam regresyon + kısa GPU koşusu.
2. Windows'ta 24 saatlik soak (`soak.py --hours 24 --memprobe`) — PASS ölçütleriyle.
3. ACL envanteri → yedek → **onay** → daraltma → doğrulama; BitLocker durumu (yönetici); yedek şifreleme kararı.
4. Ana ortam manuel kabul listesinin doldurulması; uzlaştırma dry-run ve **onaylı** `--apply`.
5. FAZ 10-B için ayrıca: KVKK, lisans, İSG kabul eşikleri, etiketli saha verisiyle doğruluk (FAZ 10-A.2/10-A.3
   belgeleri), gerçek RTSP ve hedef donanım testi.

## 14. Git commit

FAZ 10-A.1 değişiklikleri, aradaki FAZ 10-A.2 (`e51be64`) ve FAZ 10-A.3 (`dcd712a`) commit'lerinden **sonra**
ayrı bir commit olarak eklendi (dosya bazlı staging; kullanıcı plan belgeleri ve prompt dosyaları hariç). Commit
kimliği oturum özetinde bildirildi. `scripts/windows_acl.ps1` ve `tools/reliability/gpu_parity.py` Windows oturumunda
yazıldı, bu oturumda gözden geçirildi ama **çalıştırılmadı** — Windows'ta doğrulanmadan kullanılmamalı.
İzole test yığını `ppe-rel` durduruldu (volume'lar silinmedi); test imajları `ppe-dashboard:faz10a-base`
(HEAD öncesi) ve `ppe-dashboard:rel-pre0006` (geri dönüş testi) yerel olarak duruyor.
