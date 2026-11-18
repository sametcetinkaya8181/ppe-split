# FAZ 7 raporu — Kalıcı outbox, çökme kurtarma ve güvenilir teslim

Tarih: 2026-11-01. Başlangıç: `58e1d7c` (FAZ 6). Plan: `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md`, FAZ 7.
Sonuç: **kritik testler PASS; FAZ 7 tamamlandı.** Hedef: outbox'a kabul edilmiş mesajlar için kontrollü yeniden
gönderim + PostgreSQL'de idempotent tekilleştirme. **"Exactly-once delivery" iddia edilmez.**

## 0. Başlangıç denetimi

HEAD `58e1d7c`; izlenmeyen tek dosya kullanıcının plan belgesi (korundu, commit'e eklenmedi). Başlangıç regresyonu:
dashboard 296/296, detector 119/119 PASS. Bulunan boşluklar: (1) bekleyen olaylar yalnız bellekte; (2) olaylar
`/ingest/frame` ile JPEG kareye bağlı — yeniden başlatmada kamera kapalıysa diskteki olaylar teslim edilemezdi.

## 1. Değişen dosyalar

Yeni: `detector/outbox.py`, `detector/detector_env.py`, `detector/tests/test_outbox.py` (34),
`dashboard/alembic/versions/0005_run_abandoned_reconcile.py`, `dashboard/maintenance/runs.py`,
`dashboard/tests/test_ingest_events.py` (9), `dashboard/tests/test_reconcile.py` (6), `docs/OUTBOX.md`,
`docs/PHASE_7_REPORT.md`.
Değişen: `detector/detector.py` (outbox'lı gönderici seçimi), `dashboard/app.py` (`POST /ingest/events`),
`db/models.py`, `db/repository.py` (abandoned geri dönüşü), `db/history.py` (`run_abandoned`), `auth/store.py`
(denetim alanları), `static/index.html` (durum etiketi), head beklentisi güncellenen 4 test dosyası, `.gitignore`
(`detector/outbox/`), `detector/.env.example`, `README.md`, `docs/ARCHITECTURE.md`, `DATABASE.md`, `DEPLOYMENT.md`.
Dokunulmayan: `best.pt`, `ppe_core.py`, YOLO/ByteTrack, WebSocket biçimi, migration 0001–0004, kullanıcı/rol sistemi,
eski `protocol.Sender` (`OUTBOX_ENABLED=0` ile hâlâ kullanılabilir, 119 testi değişmeden geçiyor).

## 2. Outbox mimarisi

Kamera başına SQLite dosyası `OUTBOX_DIR/<camera_id>.sqlite3` (varsayılan `detector/outbox`, Git dışı). Tablo
`outbox(seq, kind, event_type, msg_id UNIQUE, run_id, body, bytes, sha256, state, attempts, …)`; durumlar `pending`,
`delivered`, `dead`, `quarantined`. `DurableSender` eski Sender ile aynı üretici arayüzünü sunar; olaylar/raporlar
önce outbox'a, kareler bellekte (en yenisi kazanır). Olaylar yeni token korumalı **`POST /ingest/events`** (yalnız
JSON, sahte kare yok) ile, raporlar `/ingest/report` ile gider. Kaynak etiketi olayla dondurulur (replay eski run'a
yeni ad yazmaz — test ile bulunup düzeltilen hata). Teslim edilen gövde (snapshot dahil) hemen silinir.

## 3. SQLite WAL ve dayanıklılık

`journal_mode=WAL`, `synchronous=FULL` (commit WAL'ı fsync eder), `locking_mode=EXCLUSIVE`, `busy_timeout`, açılışta
`quick_check`, satır başına SHA-256, kapanışta `wal_checkpoint(TRUNCATE)`. Sınırlar: outbox'a yazılmadan önceki çökme,
disk/OS'nin fsync'e uymaması, donanım arızası, WAL bozulması (bozulma noktasından sonraki işlemler kurtarılamaz —
`test_corrupt_wal_does_not_crash_or_delete` ile **belgelendi**) garanti dışıdır.

## 4. Çoklu süreç güvenliği

Kamera başına ayrı süreç + ayrı dosya → dosyalar arası yazma çekişmesi yok. `locking_mode=EXCLUSIVE`: aynı kamera
için ikinci süreç `OutboxLocked` alır, o kamera başlatılmaz, diğerleri sürer. Süreç içi üretici/gönderici tek
bağlantıyı RLock ile paylaşır. Testler: 4 paralel süreç × 300 olay (karışma yok), aynı kamera ikinci süreç
(alt süreçte) kilitli, 4 thread × 100 olay eşzamanlı üretici.

## 5. Yeniden deneme ve ACK protokolü

2xx → teslim (dashboard 2xx'i yalnız PostgreSQL commit sonrası döner). Ağ/zaman aşımı/408/425/429/5xx → aynı gövde,
üstel geri çekilme + jitter, `Retry-After`. 401/403 → **duraklatma**, düşürme yok, `detector/.env` 30 sn'de bir
yeniden okunur (token düzeltilince yeniden başlatmadan devam). 404/405 (eski dashboard) → duraklatma. 409 →
`quarantined`, 400/413/422 → `dead` (batch bölünerek yalnız hatalı öğe ayrılır; sonsuz döngü yok). ACK kaybı →
birebir aynı bayt dizisi yeniden gönderilir; dashboard `event_id`/rapor tekrarını ayıklar (iki taraf da testli).

## 6. FIFO ve rapor bariyeri

Gönderim sırası `seq`; rapor, kendinden önceki tüm satırlar teslim/ölü/karantina olmadan gönderilmez. Önceki olay
ölü/karantinadaysa rapor yine gönderilir ve "veri bütünlüğü eksik" uyarısı loglanır (gövde değiştirilmez). Yeniden
başlatma sonrası sıra korunur (testli). Kameralar birbirini bloke etmez (ayrı dosya, ayrı süreç).

## 7. Çökme kurtarma senaryoları

| Senaryo | Yöntem | Sonuç |
|---|---|---|
| Python istisnası / gönderici hatası | thread ölmez, geri çekilme | birim test PASS |
| Zorla süreç öldürme | `os._exit(9)` ve `Popen.kill()` (Windows TerminateProcess) yazım döngüsünde | PASS — tüm commit'li kimlikler replay |
| Gerçek dedektör zorla öldürme | `taskkill /T /F` (GPU E2E) | PASS |
| Ctrl+C | kapanışta bekleyenler diskte kalır, sonraki başlatmada gönderilir | birim test PASS (FAZ 5 gerçek Ctrl+C yolu değişmedi) |
| Windows yeniden başlatma | **gerçek yeniden başlatma yapılmadı**; zorla öldürme + yeniden açma aynı kurtarma yolunu kullanır | SKIP (benzetim PASS) |
| Dashboard kapalı | E2E: dashboard durdurulmuşken olaylar outbox'a | PASS |
| PostgreSQL 503 | birim (503 retry) + dashboard 503 sözleşme testi; canlı "DB durdur" yalnız FAZ 5'te eski Sender ile | PASS (canlı DurableSender + DB durdurma SKIP) |
| Ağ zaman aşımı / ACK kaybı | birim + dashboard idempotency | PASS |
| SQLite kilitli | ikinci süreç `OutboxLocked`, kamera başlatılmaz | PASS |
| Disk dolu | SQLite `max_page_count` ile SQLITE_FULL → bellek yedeği + görünür hata → yer açılınca kalıcılaştırma | PASS (gerçek disk doldurma yapılmadı) |
| Bozuk dosya / WAL | silinmez/sıfırlanmaz; `OutboxCorrupt` + prosedür (OUTBOX.md) | PASS |
| Kapasite dolu | ongoing seyreltme; kritik yedek alan; sonra görünür hata | PASS |

## 8. Ölü mektup / karantina

`dead` (geçersiz) ve `quarantined` (çelişki) satırlar gövdeleriyle saklanır; `python outbox.py list-failed` (gövde
yazdırmaz), `requeue --seq N | --all` aynı kimlik/gövdeyle yeniden kuyruğa alır (testli). Sağlama toplamı uyuşmayan
satır gönderilmez, `dead` olur.

## 9. Kapasite ve metrikler

`OUTBOX_MAX_BYTES` (512 MiB), `OUTBOX_MAX_ITEMS` (100 000), `OUTBOX_HIGH_WATERMARK` (0.8),
`OUTBOX_RETRY_BACKOFF_MAX` (30 sn), `OUTBOX_DIR`, `OUTBOX_ENABLED`. Metrikler `status.json` ve `outbox.py status`:
pending_count/bytes, oldest_pending_age_sec, pending_retry_attempts, delivered_count, dead_letter_count,
quarantine_count, disk_write_errors, evicted_ongoing, dropped_ongoing_full, high_watermark, state, memory_fallback.
start/end/rapor sessizce düşürülmez; tek kayıp yolu "disk yazılamıyor + bellek yedeği dolu + süreç çökmesi" — sayılır
ve loglanır.

## 10–12. Testler

| Paket | Komut | Sonuç |
|---|---|---|
| Dashboard (gerçek PostgreSQL 17, `ppe_app_test` rolü) | `docker compose --profile test run --rm tests` | **311/311 PASS** (296 + 9 `/ingest/events` + 6 uzlaştırma; ORM↔migration drift dahil) |
| Dedektör | `cd detector; .venv\Scripts\python -m pytest` | **153/153 PASS** (119 + 34 outbox) |
| Tarayıcı (Edge + CDP) | `node dashboard/tests/ui/ui_check.mjs` | **28/28 PASS** |
| LAN TLS izole | `python deploy/lan/lan_smoke.py` | **21/21 PASS** |
| Güvenlik (FAZ 6) | dashboard paketi içinde: oturum, RBAC/kamera kapsamı, ingest token, oturum çerezi ile ingest reddi (yeni uç dahil), token'ın gövde ayrıştırmadan önce denetimi, sır maskeleme, snapshot erişimi | PASS |
| Outbox dizini ACL | `test_new_outbox_dir_acl_restricted_to_owner` (gerçek `icacls`) | PASS |

## 13–14. Gerçek GPU/video ve PostgreSQL kanıtı

RTX 3050, `[giris]` videosu, izole outbox dizini, ana PostgreSQL 17:
1. Dashboard durduruldu; dedektör START/ONGOING×2/END üretti → outbox: `pending_count 4`, 87 633 bayt (snapshot dahil).
2. END'den hemen sonra `taskkill /T /F` (rapor üretilmeden).
3. Dashboard başlatıldı; dedektör aynı outbox ile yeniden başlatıldı: "outbox kurtarma: 4 bekleyen mesaj aynı
   kimliklerle gönderilecek" → "8 olay, 1 rapor iletildi; bekleyen 0, ölü 0".
4. PostgreSQL: öldürülen run `0578da13` → 4 olay / 4 tekil `event_id` / 1 incident / 1 snapshot, etiket "Giriş
   kapısı", durum `running` (rapor yok — dürüst); ikinci run `5012bbb8` → 4 olay, `finished`, 2 kişi raporu.
   Geçmiş sorgusu iki ihlali `closed` gösterdi; snapshot dosyaları uid 10001 ile mevcut.
Ek: migration 0005 öncesi yedek + izole geri yükleme PASS.

## 15. Eski run uzlaştırması

Migration 0005: `abandoned` durumu + `reconciled_at/reason`. `maintenance.runs reconcile` dry-run ana DB'de **FAZ 5'ten
kalan 3 öksüz run'ı** (dikey/giris/kule, ~87 sa sinyalsiz) doğru listeledi. **Onay olmadığı için uygulanmadı.**
Uygulamak için: `docker compose exec dashboard python -m maintenance.runs reconcile --apply --confirm 3`
(o anda aday sayısı farklıysa komut değişiklik yapmadan durur). FAZ 7 E2E'deki öldürülen run da 60 dk sonra aday olur.

## 16. Bağımlılık güvenliği (değiştirilmedi)

`pip-audit` 2026-11-01: `starlette 0.41.3` 7 kayıt (FastAPI 0.115.5'in sabitlediği sürüm; güncel FastAPI 0.162.0 /
Starlette 1.10.0). Uygulanabilirlik:

| Kayıt | Konu | Bu projede |
|---|---|---|
| PYSEC-2026-161 | Host başlığından URL kurma | Host allowlist uygulamadan önce reddeder → düşük |
| PYSEC-2026-248 | URL kurmada yol doğrulaması | `url.path` yalnız denetim ayrıntısında, kısaltılmış → düşük |
| PYSEC-2026-249 | `request.form()` alan/parça sınırları | `/ingest/frame` multipart — yalnız geçerli token'dan sonra → kimliği doğrulanmış DoS |
| PYSEC-2026-1941 | büyük multipart dosya işleme | aynı → kimliği doğrulanmış DoS |
| PYSEC-2026-1942 | FileResponse Range | FileResponse kullanılmıyor → uygulanamaz |
| PYSEC-2026-2281 | Windows StaticFiles UNC SSRF | StaticFiles yok, Linux container → uygulanamaz |
| PYSEC-2026-2280 | HTTPEndpoint metot seçimi | HTTPEndpoint yok → uygulanamaz |

Kimliği doğrulanmamış istismar yolu bulunmadı; yine de **LAN GO öncesi** FastAPI/Starlette yükseltmesi ayrı, regresyon
testli bir çalışma olarak yapılmalı (önerilen sıra: FastAPI 0.115→güncel; 296+ test, tarayıcı ve LAN smoke ile).

## 17. Bilinen kayıp ve güvenlik riskleri

1. Outbox'a commit'ten önceki çökme, disk/OS fsync ihlali, donanım arızası, WAL bozulması garanti dışı.
2. Disk yazılamazken bellek yedeğindeki mesajlar süreç çökerse kaybolur (görünür hata + sayaç).
3. Outbox ihlal görüntülerini teslimden önce diskte tutar; **disk şifrelemesi (BitLocker) doğrulanamadı**.
4. Proje klasörü `BUILTIN\Users`'a değiştirme iznini miras bırakıyor (`.env`, `data/`, `backups/` dahil); yalnız yeni
   outbox dizini kısıtlandı. Düzeltme komutu: OUTBOX.md "Güvenlik notları" (kullanıcı kararı).
5. Kapasite dolunca `ongoing` seyreltilir (incident sayıları etkilenmez, zaman çizelgesindeki ara noktalar eksilir).
6. Starlette açıkları (bölüm 16). KVKK ve lisans NO-GO sürüyor; dashboard localhost'ta.
7. Ana DB'ye test verisi: FAZ 7 E2E'den 2 run (biri `running`). Silinmedi.

## 18. PASS / FAIL / SKIP

| Kalem | Sonuç |
|---|---|
| SQLite kalıcı transaction, kill sonrası replay, aynı event_id | PASS |
| ACK kaybında duplicate yok | PASS |
| Kilitli DB, disk dolu (SQLite benzetimi), bozuk dosya/WAL korunması | PASS |
| 503 / timeout / bağlantı kesintisi, üstel geri çekilme, Retry-After | PASS |
| 401/403 duraklatma + token düzeltme sonrası replay | PASS |
| 409/422 karantina/ölü mektup, sonsuz döngü yok | PASS |
| FIFO, rapor bariyeri, aynı pid farklı run, çoklu süreç, çoklu üretici, kapanışta kuyruk | PASS |
| Kapasite/seyreltme/görünür hata, metrikler, status dosyası, CLI (check/list/requeue/drain) | PASS |
| Güvenlik regresyonu (FAZ 6) + yeni uçta token/oturum ayrımı | PASS |
| Dashboard 311, dedektör 153, tarayıcı 28, LAN TLS 21 | PASS |
| GPU E2E: öldürme → aynı outbox ile replay → PostgreSQL'de tekil | PASS |
| Eski run uzlaştırması (araç + testler) | PASS; ana DB'de uygulama **bekliyor (onay)** |
| Gerçek Windows yeniden başlatma, gerçek disk doldurma, canlı DB-durdurma (yeni gönderici) | SKIP |
| BitLocker durumu | SKIP (yönetici yetkisi gerekir) |
| Starlette/FastAPI yükseltmesi | bilinçli olarak yapılmadı (öneri) |

## 19. Commit

Bu raporla oluşturulan commit: `git log -1` (FAZ 7). Plan belgesi commit'e eklenmedi.

## 20. FAZ 8 ön koşulları

- 3 (+1 E2E) öksüz run için uzlaştırma kararı (`--apply --confirm`).
- LAN'dan önce FastAPI/Starlette yükseltmesi (ayrı çalışma); proje klasörü ACL ve disk şifrelemesi kararı.
- FAZ 8 için izinli/etiketli saha verisi ve İSG uzmanı eşik onayı (plan belgesi FAZ 8); veri yoksa DATA_REQUIRED.
