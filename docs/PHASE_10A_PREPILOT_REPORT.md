# FAZ 10-A raporu — Pilot öncesi teknik hazırlık

Tarih: 2026-11-01/02 (UTC). Başlangıç: `2b6202b`. Plan: `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md` (FAZ 10'un
pilot öncesi teknik kısmı). Bu faz **saha pilotu başlatmaz**; FAZ 10-B'ye geçilmedi.

**İki ayrı karar:**

| Karar | Sonuç |
|---|---|
| Teknik pilot öncesi hazırlık | **INCOMPLETE** — bu fazın teknik işleri tamamlandı ve testleri geçti (4 saatlik soak dahil); ancak 24 saatlik soak koşulmadı, gerçek RTSP kamera testi BLOCKED, dashboard bellek eğilimi çözülmedi, dedektör ortamında açık bağımlılık güvenlik kayıtları ve geniş dosya izinleri var |
| Gerçek şantiye pilotu | **NO-GO** — etiketli saha verisi yok (FAZ 8 BLOCKED), İSG kabul eşikleri onaylanmadı, KVKK ve model lisansı onayları yok, gerçek RTSP kamera ve hedef saha bilgisayarı testi yapılmadı |

`best.pt` (SHA-256 `6886ad2f…`), `ppe_core.py`, ByteTrack eşikleri, eski migration'lar 0001–0005, ana DB kayıtları,
outbox ve snapshot dosyaları değiştirilmedi/silinmedi. LAN/internet erişimi açılmadı.

## 1. Başlangıç Git durumu

Dal `main`, HEAD `2b6202b` (FAZ 9 güvenlik yükseltmesi), öncesi `d3af4ca` (FAZ 9); ikisi de doğrulandı. Çalışma
ağacı temiz; izlenmeyen tek dosya kullanıcının plan belgesi (korundu, commit'e eklenmedi). Docker: ana yığın
`ppe-split` (dashboard + db), FAZ 9 izole yığını `ppe-rel` (durdurulmuş), başka projelerin container'ları (dokunulmadı).
Ana DB Alembic `0005`; 74 run / 104 olay / 47 incident.

## 2. Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `detector/detector.py` | Run bitiş sınıflandırması (`classify_end`: eof / user_stop / source_lost / source_lost_after_stall / exception), rapora `end_reason`; üstel yeniden bağlanma (`reconnect_delay`, 2→60 sn, ±%20); kare vermeyen kaynakta hızlı döngü önleme; canlı kaynak `LOOP`'tan bağımsız yeniden bağlanır; bağlantı yokken "KAMERA BAGLANTISI YOK" yer tutucu karesi + sağlık özeti; varsayılan ağ okuma zaman aşımı (`STREAM_READ_TIMEOUT_SEC=10` → `OPENCV_FFMPEG_CAPTURE_OPTIONS=rw_timeout`) |
| `detector/telemetry.py` | run içi en büyük kare boşluğu / tek renk kare sayısı (`run_disturbed`), `source_lost` sayacı, yeni run'da eski kare yaşının sıfırlanması; kapsam: birikimli neden dağılımı, ≥10 sn karar alamayan açık track (`open_unevaluated_long`, `unevaluated_long` işareti) |
| `dashboard/alembic/versions/0006_run_disconnected.py` (yeni) | `detector_runs.status` += `disconnected` (eklemeli; `disconnected` satır varken downgrade durur) |
| `dashboard/db/models.py`, `db/repository.py` | durum kümesi; `disconnected` terminal |
| `dashboard/ingest/schemas.py` | `run_status` += `disconnected`; `end_reason` sözlüğü ve `run_status` ile tutarlılık doğrulaması (çelişki 422) |
| `dashboard/app.py` | `end_reason` rapor metadata'sına yalnız varsa yazılır (eski raporların birebir tekrarı bozulmaz); geçmiş API `report.end_reason`, run filtresi `disconnected` |
| `dashboard/observability.py` | kapsam alanları ve `unevaluated_long` beyaz listesi |
| `dashboard/static/index.html` | Kamera bazlı **"Analiz kapsamı"** paneli; "Bağlantı koptu" durum etiketi + bitiş nedeni + filtre; "bağlantı yok" karo metni; çıkışta panel temizliği |
| Testler | yeni `detector/tests/test_lifecycle.py` (18), `test_telemetry.py` +2; yeni `dashboard/tests/test_run_lifecycle.py` (14), `test_observability.py` +1; head beklentileri 0006; `test_run_once.py` normal bitiş testi dosya kaynağına taşındı (eski hali RTSP kopmasını "finished" sayan yanlış davranışı kodluyordu); `ui_check.mjs` +1 kontrol + kapsam paneli; UI fixture'a kopan run |
| `tools/reliability/lifecycle_sim.py`, `tools/reliability/soak.py` (yeni) | yaşam döngüsü simülasyonu, 4/24 saatlik soak |
| Belgeler | bu rapor; `PERFORMANCE_BASELINE.md` (4 kamera soak), `RELIABILITY_TEST_PLAN.md` §8, `DATABASE.md`, `ARCHITECTURE.md`, `README.md`, `detector/.env.example` |

## 3. Dashboard deployment durumu

**Güncelleme öncesi (2026-11-01 ~21:00 UTC):** container `d4c45c7d…`, imaj `834babcc…` (FastAPI 0.115.5 / Starlette
0.41.3, FAZ 7 kodu); `/healthz` 200, `/health/ready` 200; Alembic 0005; admin hesabı: 1 (`admin`, etkin, tüm
kameralar); ana outbox dizini hiç oluşmamış → bekleyen 0; compose yapılandırması geçerli. Yedek
`backups/ppe-20261101T210450Z` (`--quiesce`) + izole geri yükleme doğrulaması **PASS**.

**Aşama 1 — çerçeve yükseltmesi (`2b6202b` imajı `4efb4b09`):** `docker compose up -d`; çalışan sürüm 0.162.0 / 1.10.0
doğrulandı; DB sayımları değişmedi.

> **Olay (dürüstlük notu):** Geri dönüş için eski imajı etiketleme komutu kısa kimlik biçimi nedeniyle başarısız oldu
> ve komutlar `;` ile zincirlendiği için güncelleme yine de uygulandı. Eski imaj (`834babcc`) container yeniden
> oluşturulduktan sonra Docker Desktop (containerd imaj deposu) tarafından temizlenmişti. Geri dönüş imajı, dashboard
> kodu FAZ 7–FAZ 8 arasında değişmediği ve taban imaj/paketler sabitli olduğu için Git'ten yeniden derlendi
> (`git archive 2385c97:dashboard | docker build` → `ppe-dashboard:rollback-pre-faz10a`, `835397ab…`, FastAPI 0.115.5 /
> Starlette 0.41.3 doğrulandı). Aşama 2'de etiket önce oluşturulup doğrulandı, komutlar `set -e` ile çalıştırıldı.

**Aşama 2 — FAZ 10-A kodu + migration 0006 (~21:37 UTC):** geri dönüş imajı `ppe-dashboard:rollback-faz9-2b6202b`
(`4efb4b09`) doğrulandı; yeni yedek `backups/ppe-20261101T213616Z` + izole geri yükleme **PASS**; `docker compose
build dashboard && docker compose up -d` → `migrate`: `0005 -> 0006` uygulandı; DB sayımları aynı (104 olay, 74 run,
47 incident, 1 kullanıcı); çalışan imaj FAZ 10-A kodunu içeriyor.

**Güncelleme sonrası kontroller (ana yığın, iki aşamada da):**

| Kontrol | Sonuç |
|---|---|
| `/healthz`, `/health/ready` | 200 / 200 |
| Oturumsuz `/api/history/*`, `/api/state`, `/api/ops/metrics`, `/video_feed/*`, `/snapshots/*` | 401 |
| Giriş: var olmayan kullanıcı 401, yabancı Origin 403; yabancı Host 400 | beklendiği gibi |
| `/ingest/events`: token yok 401, yanlış token 401, doğru token + geçersiz gövde 422, doğru token + boş liste 200 (yazım yok) | beklendiği gibi |
| `/ingest/report`: doğru token + çelişkili `end_reason` 422 (DB'ye satır yok) | beklendiği gibi |
| WebSocket oturumsuz el sıkışma | 403 (reddedildi) |
| PostgreSQL: revision, sayımlar | 0006, değişmedi |
| Dashboard log'unda traceback | 0 |
| **Oturumlu** RBAC/snapshot/WebSocket/canlı video/geçmiş/`/api/ops/metrics` | **ana yığında yapılmadı**: admin parolası bende yok ve ana DB'ye test kullanıcısı yazmadım. Aynı kod ve imajla izole DB'de 334 dashboard + 30 tarayıcı + 21 LAN testiyle doğrulandı. **Öneri:** bir yetkili kullanıcı bir kez giriş yapıp Canlı İzleme / Geçmiş / Run Raporları ekranlarını kontrol etsin. |
| SQLite outbox (ana) | ana `detector/outbox` dizini yok; bekleyen 0 |

**Geri dönüş prosedürü (aşama 2; veri kaybı olmadan):** (1) Uygulama yalnız: `docker compose run --rm migrate alembic
downgrade 0005` — yalnız `disconnected` run yoksa çalışır, ana DB'de downgrade **açık onay gerektirir** —, sonra
`docker tag ppe-dashboard:rollback-faz9-2b6202b ppe-dashboard:latest && docker compose up -d`. (2) Alternatif: yedek
`ppe-20261101T213616Z`'den `docs/BACKUP_RESTORE.md` prosedürü. Not: DB 0006'dayken eski imajla `docker compose up`
çalışmaz (`migrate` 0006'yı tanımaz) — önce (1)'deki downgrade.

## 4. Framework sürüm ve güvenlik kontrolü

- Çalışan ana dashboard: FastAPI **0.162.0**, Starlette **1.10.0**, uvicorn 0.32.1 (container içinden doğrulandı).
- `pip-audit -r dashboard/requirements.txt` (2026-11-01, canlı OSV/PyPI): **No known vulnerabilities found**.
- Dedektör ortamı (`pip freeze`, 48 paket; container'da `--no-deps --disable-pip`, venv değiştirilmedi): **açık
  kayıtlar var** — `pillow` 12.3.0 (çok sayıda kayıt; düzeltme 13.0.0), `filelock` 3.32.3 (Windows kilit/izin kayıtları;
  4.2.1), `setuptools` 78.1.0 (yalnız kurulum/derleme yolu; 84.1.0). `torch`/`torchvision` (+cu126) PyPI dışı → denetlenemedi.
  Uygulanabilirlik: dedektör video karelerini OpenCV ile okur; Pillow ile güvenilmeyen dosya açmaz; filelock yalnız
  yerel önbellek kilitleri. Risk düşük ama **kapatılmalı**: ayrı, testli venv güncellemesi (soak sırasında çalışan
  süreçlerin paketlerini değiştirmemek için bu fazda yapılmadı).
- Ultralytics analitiği: varsayılan kapalı (`YOLO_OFFLINE=1`, birim test geçiyor). Kullanıcının küresel
  `settings.json`'unda `sync: true` duruyor; değiştirilmedi (süreç düzeyi ayar yeterli).
- KVKK ve model lisansı: **NO-GO** korunuyor.

## 5. RTSP / canlı kaynak yaşam döngüsü düzeltmeleri

Durum makinesi (dedektör → rapor → PostgreSQL):

```
            ┌─ dosya sonu ─────────────────────────────► finished      / eof
            ├─ Ctrl+C / stop ──────────────────────────► interrupted   / user_stop
running ────┼─ canlı akış bitti (kopma, okuma z.aşımı) ─► disconnected  / source_lost | source_lost_after_stall
            ├─ istisna ────────────────────────────────► error         / exception
            └─ rapor yok + canlılık ≥60 dk yok (onaylı) ─► abandoned ──(rapor/canlılık gelirse)──► running / terminal
terminal (finished|interrupted|disconnected|error): canlılık veya gecikmiş olay durumu DEĞİŞTİRMEZ; aynı raporun
tekrarı 200 (değişiklik yok), farklı rapor 409; end_reason run_status ile çelişirse 422.
```

- `source_lost_after_stall`: kopmadan önce ≥ 5 sn kare gelmedi (`STALL_WARN_SEC`) veya ≥ 3 tek renk kare. Ultralytics
  okuma hatasında **tek** siyah kare ürettiği için tek kare sayılmaz. Simülasyonda temiz kopma bile ~6 sn kare
  boşluğu ürettiğinden çoğu gerçek kopma bu nedenle kaydedilir; ayrımın asıl amacı "kopma ≠ normal bitiş"tir.
- Sonu bilinen ağ videosu (kare sayısı > 0 ve ≥ %98'i okunmuş) `finished/eof`.
- "Kaynak yeniden bağlandı": yeni run (aynı `camera_id`, artan `loop_no`); önceki run `disconnected`.
- Incident bütünlüğü: kopmada açık incident `run_end` ile raporlanır; geçmişte `run_end` durumunda görünür (sahte
  `closed` yok; test). Eski kayıtlar tahminle değiştirilmedi (0006 yalnız izin verilen değerleri genişletir).
- Geriye uyumluluk: eski dedektör (`end_reason` yok) yeni dashboard'la çalışır; yeni dedektörün `disconnected` raporu
  **eski** dashboard'a 422 alır ve outbox'ta `dead` olur (silinmez; dashboard güncellenince
  `python outbox.py requeue <kamera> --all`). Güncelleme sırası: önce dashboard, sonra dedektör.

## 6. Reconnect / timeout sonuçları (SİMÜLASYON)

Gerçek dedektör + gerçek model + izole PostgreSQL 17 + TCP/MPEG-TS kamera benzeticisi (`lifecycle_sim.py`,
2026-11-01 21:25–21:34 UTC). **Gerçek RTSP kamera testi değildir.**

| Senaryo | Gözlenen | DB run durumu | Olay outbox → DB |
|---|---|---|---|
| R1 geçici kopma (15 sn) | dedektör ve operatör ekranı 6.1 sn'de "yeniden bağlanılıyor / sinyal yok"; geri çekilme 2.0 → 3.5 sn; kaynak dönünce **5.1 sn**'de yeni run; aynı kamera kimliği | `disconnected/source_lost_after_stall` → `interrupted/user_stop` | 3 → 3, eksik 0, çift 0 |
| R2 kalıcı kayıp (150 sn) | geri çekilme **1.6 → 4.2 → 7.5 → 16.1 → 30.7 → 62.8 sn** (sıkı döngü yok, ~60 sn tavan); operatör 6. sn'den itibaren 150 sn boyunca sürekli `reconnecting` + `camera_disconnected` gördü | `disconnected` | 1 → 1 |
| R3 donma (40 sn, varsayılan 10 sn zaman aşımı) | `stalled` 6.1 sn; run 15.1 sn'de `disconnected` olarak kapandı (önceden ~30 sn+ ve `finished`); çözülünce 2.0 sn'de toparlandı | `disconnected/source_lost_after_stall` → `interrupted` | 1 → 1 |
| R4 başlangıçta erişilemeyen uç → 70 sn sonra erişilebilir | run oluşmadı (sahte run yok); operatör karosu `reconnecting` + `camera_disconnected` + `source_unavailable` (önceden kamera ekranda **hiç görünmüyordu**); geri çekilme 2.3 → 33.5 sn; erişilebilir olduktan **41.4 sn** sonra bağlandı (bekleme penceresi) | yalnız `interrupted` (bağlandıktan sonra) | 1 → 1 |
| Yanlış RTSP kimlik bilgisi (401) | benzetimde RTSP kimlik doğrulaması yok; OpenCV açılamama yolu R4 ile aynı | — | **BLOCKED** (gerçek kamera yok) |
| RTSP `rw_timeout`/`timeout` davranışı, UDP/TCP taşıma | — | — | **BLOCKED** |

Ödünleşim: `RECONNECT_MAX_SEC=60` uzun kesintiden sonra geri gelen kamerada en fazla ~72 sn bekleme demektir (R4: 41 sn).

## 7. Analiz kapsama görünürlüğü

Canlı İzleme'de kamera bazlı **"Analiz kapsamı"** tablosu: kapıdan geçen / değerlendirilemeyen track-kare oranı ve
nedenleri (uzak/küçük, kadraj kenarı, düşük güven), şu an değerlendirilemeyen kişi, **≥10 sn'dir değerlendirilemeyen**
kişi (en uzun süre), hiç karar almadan kaybolan track / tüm track (en uzun süre). Altında açık not: değerlendirilemeyen
kişiler "Uygun" sayılmaz; sistem onlar hakkında karar vermemiştir; saha kabul eşiği **tanımlanmadı (İSG onayı
gerekir)**; track sayısı kişi sayısı değildir. Karo üstü uyarı ("N kişi değerlendirilemiyor (neden)") ve
`unevaluated_long` işareti sürüyor. Tarayıcı testi paneli doğruluyor (%9.0 / %91.0, "kadraj kenarı %90.0",
"1 kişi (en uzun 14.5 sn)", çıkışta temizlenir). PPE karar mantığı ve eşikleri değişmedi; yeni ihlal/alarm üretilmez.

Ölçülen kapsam (stok videolar; saha verisi değil): 4 saatlik soak'ta track-karelerin değerlendirilemeyen oranı V1 dosya %23.6 (66 068 / 280 480; kadraj kenarı
ağırlıklı), V2 dosya **%91.3** (301 364 / 329 964), V2 ağ benzetimi **%91.2** (288 154 / 315 959); hiç karar almadan
kaybolan track V1 957 / 5744, V2 1190 / 1788. V2'de `unevaluated_long` (≥10 sn) işareti görüldü. Bu oranlar stok
videoların kadrajına aittir; saha kamerası yerleşimiyle değişir. Kabul eşiği İSG tarafından belirlenmeli.

## 8. 4 / 24 saat soak sonuçları

**4 saat — GERÇEKTEN KOŞULDU.** Başlangıç **2026-11-01T21:45:01Z**, bitiş **2026-11-02T01:45:05Z**, ölçüm
**14 404.1 sn** (30.8 sn ısınma hariç), 479 örnek (30 sn). Ortam: izole `ppe-rel` (PostgreSQL 17 + FAZ 10-A dashboard
imajı), gerçek `detector.py` (FAZ 10-A kodu), RTX 3050 Laptop, paylaşımlı dizüstü (başlangıçta boş RAM 3.4 GB, sayfa
dosyası 4.0 GB). **4 kamera**, 1080p, stride 2: `relc1` V1 dosya, `relc2` V2 dosya, `relc3` V1 dosya (tekrar video),
`relnet` V2 üzerinden TCP/MPEG-TS **ağ kamerası benzeticisi**; `relnet`'e her 40 dk'da 60 sn kopma ve aralarda 45 sn
donma: toplam **11 arıza** (6 kopma + 5 donma). Bellek koruması tetiklenmedi; zarif kapanış 3.3 sn.

| Gösterge | ~0. sa (medyan) | 1. sa | 2. sa | 3. sa | Son 15 dk | Yorum |
|---|---|---|---|---|---|---|
| Dedektör tanıtıcı (handle) | 2556 | 2626 | 2626 | 2626.5 | 2627 | 1. saatten sonra sabit |
| Dedektör thread | 109 | 109 | 109 | 109 | 109 | Sabit |
| Dedektör RSS toplam MB | 1736 | 1826 | 1847 | 1692 | 1718 | Artış yok (Windows çalışma kümesi kırpması; özel bellek ölçülmedi) |
| torch rezerve (kamera 1) MB | 182 | 182 | 182 | 184 | 184 | ≈ sabit |
| GPU bellek (cihaz) MB | 973 | 1003 | 1019 | 1029 | 1029 | +56 MB, son saatte plato |
| **Dashboard container MB** | 80.0 | 84.2 | 88.9 | 92.0 | 94.6 | **Doğrusal artış ~3.7 MB/sa; plato yok** (bkz. risk) |
| PostgreSQL bağlantı | 5 | 5 | 5 | 5 | 5 | Sabit; havuz `checked_out` max 1 |
| PostgreSQL DB MB | 8.6 | 10.3 | 11.8 | 13.4 | 14.5 | Veri büyümesi (4656 olay + ~2500 run; kısa video döngüsü saatte ~620 run üretir) |
| Outbox WAL (kamera 2) MB | 4.0 | 4.0 | 4.0 | 4.0 | 4.0 | Plato; kapanışta 0 |
| Lag (kamera 2) sn | -0.1 | -0.1 | -0.1 | -0.1 | -0.1 | Eğim 0.000 sn/sa |
| `/api/state` ms | 19.5 | 21.0 | 20.6 | 13.8 | 12.3 | Sabit/azalan |
| DB olay yazımı p95 ms | 49.6 | 43.8 | 39.6 | 38.0 | 36.3 | Sabit/azalan |
| WebSocket aktif | 1 | 1 | 1 | 1 | 1 | Sızıntı yok |

| Kamera | İşlenen kare | FPS p50 (p05) | Lag p95 / max sn | Kare ms p95 (medyan) / p99 max | Çıkarım p50/p95 (p99 max) | ByteTrack p95 | Outbox max bekleyen | Yeniden bağlanma | Run'lar |
|---|---|---|---|---|---|---|---|---|---|
| relc1 (V1 dosya) | 177 033 | 12.42 (10.51) | -0.08 / -0.01 | 66.3 / 136.6 | 21.3/40.6 (82.4) | 2.6 | 0 | 0 | 959 `finished/eof` + 1 `interrupted` |
| relc2 (V2 dosya) | 170 585 | 12.00 (11.64) | -0.08 / -0.02 | 67.2 / 134.5 | 21.8/41.5 (87.4) | 2.7 | 0 | 0 | 596 `finished/eof` + 1 `interrupted` |
| relc3 (V1 tekrar) | 177 072 | 12.42 (10.67) | -0.08 / 0.01 | 66.8 / 148.6 | 21.5/41.4 (92.9) | 2.6 | 0 | 0 | 959 `finished/eof` + 1 `interrupted` |
| relnet (ağ benzetimi) | 162 665 | 12.00 (2.40) | -0.19 / 19.5* | 125.0** / 159.2 | 20.1/40.0 (87.7) | 3.4 | 0 | 40 deneme | **11 `disconnected/source_lost_after_stall`** + 1 `interrupted` |

\* Ağ kaynağında lag, donma/kopma süresini içerir (canlı kaynakta kaçırılan süre geri gelmez); işleme birikmesi değildir.
\*\* Canlı kaynakta kare döngüsü, kaynağın bir sonraki kareyi üretmesini beklemeyi de içerir.

Sistem: GPU % p50/p95 42/63, sıcaklık max 74 °C; sistem CPU % p50/p95 47/52; dedektör CPU p50 %347 (~3.5 çekirdek);
sistem RAM ~14.3 GB / 15.3 GB (sayfalama); dashboard CPU % p95 54. **Teslim:** dedektör sayaçlarına göre 4656 olay
gönderildi, PostgreSQL'de **4656 tekil**, kayıp **0**, çift **0**; durdurmadan sonra bekleyen 0, ölü/karantina 0;
raporlar: 2529. Dashboard izlenen hata kodları (401/403/409/413/422/429/500/503) 0. WebSocket probu (ping/pong
düzeltilmiş): 1 bağlantı, **0 kopma**, 689 448 kare, en büyük boşluk 0.69 sn.

**Sonuç (4 saat):** tanıtıcı, thread, DB bağlantısı, WAL, gecikme ve teslim stabil; 11 kaynak arızasının her biri
doğru durumla kaydedildi ve olay kaybı olmadı. **Risk:** dashboard container belleği 4 saat boyunca doğrusal arttı
(+14.6 MB); bu hızla 1 GB `mem_limit`'e ~10 günde ulaşılır (OOM → `restart: unless-stopped` → outbox replay; veri
kaybı beklenmez ama canlı ekran kesilir). Sızıntı olduğu kanıtlanmadı; 24 saatlik soak + bellek profili gerekir.

**24 saat — KOŞULMADI (INCOMPLETE).** Araç hazır: `python tools/reliability/soak.py --hours 24`. "24 saatlik
stabilite doğrulandı" iddia edilmez.

## 9. Çoklu kamera performansı

FAZ 9 taban çizgisi (120 sn pencereler): stride 2'de 5 kamera yetişti, 6'da geride kalındı; stride 1'de 2 kamera
(`PERFORMANCE_BASELINE.md` §2–4). FAZ 10-A kodu (yeniden bağlanma, kapsam sayaçları) dedektör döngüsüne yalnız
sayaç ekler; **4 kamera** 4 saatlik soak'ta yeniden ölçüldü (§8):

| Gösterge (4 kamera, stride 2, 4 sa) | Değer | FAZ 9 (4 kamera, 120 sn) |
|---|---|---|
| FPS (dosya kameraları) p50 | 12.42 / 12.00 / 12.42 (gereken 12.50 / 11.99 / 12.50) | 12.42 / 11.94 / 12.61 / 12.00 |
| Lag p95 / eğim | ≤ -0.08 sn / ≈ 0 sn/sa | ≤ -0.09 sn |
| Çıkarım p50 / p95 ms | 20–22 / 40–42 | 22–23 / 41–45 |
| GPU % p50/p95, VRAM | 42 / 63, 1029 MB | 40.5 / 65.4, 865 MB |
| Sistem CPU % p50/p95, dedektör CPU | 47 / 52, ~3.5 çekirdek | 48 / 64, ~3.6 çekirdek |
| RAM | sistem ~14.3 GB / 15.3 GB (sayfalama; paylaşımlı makine) | 15.1 GB |
| Dashboard `/api/state` p95, DB olay yazımı p95 | ~24 ms, 36–50 ms | 33 ms, 42 ms |
| Outbox birikmesi | 0 (arızalarda da 0 — ağ kopması teslimi etkilemez) | 0 |

Sonuç: bu dizüstüde **4 kamera (3 dosya + 1 ağ benzetimi) üretim ayarıyla 4 saat gerçek zamanı sürdürdü**; FAZ 10-A
değişiklikleri performansı ölçülebilir biçimde düşürmedi. **Şantiye kapasite garantisi verilmez:** gerçek RTSP
(H.264/H.265 çözme, ağ jitter'i) ve hedef saha bilgisayarı test edilmedi; 3. kamera video tekrarıdır.

## 10. Run reconciliation (dry-run)

`maintenance.runs reconcile` (dry-run, ana DB, 2026-11-01 ~21:20 UTC): **4 aday** (sabit varsayılmadı; sorgulandı).

| Run | Kamera | Durum | Son canlılık | Son olay | Olay | Sinyalsiz | Rapor | Aktif olma ihtimali |
|---|---|---|---|---|---|---|---|---|
| `0d4f5431…` | dikey | running | 2026-10-28 17:38:31Z | 17:38:31Z | 1 | 99.5 sa | yok | yok (dedektör süreci çalışmıyor) |
| `10275304…` | giris | running | 2026-10-28 17:38:28Z | 17:38:28Z | 1 | 99.5 sa | yok | yok |
| `b3269b39…` | kule | running | 2026-10-28 17:38:29Z | — | 0 | 99.5 sa | yok | yok |
| `0578da13…` | giris | running | 2026-11-01 08:42:39Z | 08:42:23Z | 4 | 12.4 sa | yok | yok (FAZ 7 zorla öldürme testi) |

`--apply` kullanılmadı, veri silinmedi, tahmine dayalı `finished` yazılmadı. Onay verilirse:
`docker compose exec dashboard python -m maintenance.runs reconcile --apply --confirm 4` (o an aday sayısı farklıysa
komut değişiklik yapmadan durur) → `abandoned` + denetim kaydı.

## 11. Güvenlik ve regresyon testleri

| Kalem | Sonuç |
|---|---|
| Dedektör (gerçek PPEMonitor/IncidentTracker/outbox; GPU yok) | **PASS 245/245** (225 + 20) |
| Dashboard (gerçek PostgreSQL 17, `ppe_app_test` rolü): auth, RBAC + kamera kapsamı, CSRF, oturum süresi, ingest token, snapshot yetkisi, WebSocket ikili biçim, geçmiş analitiği, `/api/ops/metrics`, idempotency, rapor bariyeri, migration 0001–0006 + ORM drift | **PASS 334/334** (319 + 15) |
| Tarayıcı (Edge + CDP) | **PASS 30/30** (ilk koşuda çıkış testi yeni panelin DOM'da kaldığını yakaladı → düzeltildi) |
| LAN TLS izole | **PASS 21/21** |
| Yaşam döngüsü simülasyonu R1–R4 | PASS (simülasyon) |
| SQLite çökme kurtarma, ACK kaybı, kilit, kapasite | FAZ 9 (A–M) + dedektör paketindeki outbox testleri (bu fazda yeniden koşuldu) |
| pip-audit dashboard / dedektör | temiz / **açık kayıtlar (Pillow, filelock, setuptools)** |
| Windows ACL | **Bulgu (açık):** proje klasörü, `.env`, `detector/.env`, `backups/`, `data/snapshots` `BUILTIN\Users` için kalıtsal **değiştirme** izni. Üretim outbox dizini outbox tarafından oluşturulursa sahibine kısıtlanır (FAZ 7 testi); `OUTBOX_DIR` önceden var olan bir klasörü gösterirse kısıtlanmaz (test araçlarının dizinleri bu yüzden kısıtsız). |
| Disk şifrelemesi (BitLocker) | **SKIP** — `manage-bde` yönetici yetkisi istiyor |
| Yedek şifrelemesi | **Yok** — `backups/` düz pg_dump + JPEG kopyaları içerir (kişisel veri) |
| Gizli bilgi maskeleme | testlerde PASS (kaynak adresi/token metrik, rapor, yer tutucu karede yok) |

## 12. PASS / FAIL / SKIP / BLOCKED

| Kalem | Sonuç | Kanıt |
|---|---|---|
| Ana dashboard kontrollü güncelleme (FastAPI 0.162 / Starlette 1.10 + FAZ 10-A + migration 0006) | **PASS** (oturumsuz/token/DB kontrolleri); oturumlu ekranlar ana yığında kullanıcı tarafından kontrol edilmeli | §3 |
| Yedek + izole geri yükleme (iki kez) | PASS | `restore_check.py` |
| Geri dönüş imajı | PASS (eski imaj kaybolmuştu → Git'ten yeniden derlendi; olay §3) | §3 |
| RTSP/canlı kaynak run durumu düzeltmesi + 0006 + durum makinesi | PASS | 18 + 14 test, simülasyon |
| Yeniden bağlanma geri çekilmesi, sıkı döngü önleme, operatör görünürlüğü | PASS (simülasyon) | R1–R4 |
| Gerçek RTSP kamera (kopma/donma/yanlış parola/zaman aşımı) | **BLOCKED** | kamera yok |
| Analiz kapsamı paneli | PASS | tarayıcı 30/30 |
| 4 saatlik soak (4 kamera, 11 arıza) | **PASS** (kayıp 0, çift 0; dashboard bellek artışı **risk** olarak açık) | §8 |
| 24 saatlik soak | **INCOMPLETE** (koşulmadı; araç hazır) | — |
| 4 kamera kapasitesi (bu dizüstü) | PASS (ölçüldü) | §9 |
| Run uzlaştırma dry-run | PASS (4 aday; `--apply` yok) | §10 |
| Dedektör / dashboard / tarayıcı / LAN TLS | **PASS 245/245, 334/334, 30/30, 21/21** | §11 |
| pip-audit dashboard | PASS (açık yok) | §4 |
| pip-audit dedektör venv | **FAIL (bulgu)** — Pillow, filelock, setuptools açıkları; yükseltme ayrı iş | §4 |
| Windows ACL / yedek şifreleme | **FAIL (bulgu)** — geniş izinler, şifresiz yedek | §11 |
| BitLocker | **SKIP** (yönetici yetkisi) | §11 |
| Ultralytics analitiği kapalı | PASS | birim test |
| Saha doğruluğu, KVKK, lisans, İSG eşikleri | **BLOCKED / NO-GO** | FAZ 8, plan |

Kritik test FAIL'i yok. FAIL olarak işaretlenen iki kalem kod regresyonu değil, açık güvenlik/yapılandırma
bulgularıdır (pilot öncesi kapatılmalı).

## 13. Kalan veri kaybı riskleri

1. Outbox commit'inden önceki çökme; disk/OS fsync ihlali; donanım arızası (FAZ 7, değişmedi).
2. Disk yazılamaz + bellek yedeği dolu + süreç çökmesi → `messages_lost` (sayılır, mesaj kaybolur).
3. Yeni dedektör + **eski** dashboard: `disconnected` raporu 422 → outbox `dead` (kaybolmaz; requeue gerekir).
4. Canlı akışta donma/kopma sırasında kaçırılan kareler geri gelmez (o süre için tespit yapılmaz) — operatör
   "izlenemiyor" görür; olay kaybı değil, gözlem boşluğudur.
5. Zorla öldürülen run raporsuz kalır (uzlaştırma onayı bekleyen 4 kayıt).
6. Yedekler ve outbox şifresiz; klasör izinleri geniş; BitLocker doğrulanmadı → kişisel veri sızıntısı riski (veri
   kaybı değil, gizlilik).

## 14. Gerçek saha pilotu ön koşulları (FAZ 10-B öncesi)

1. **KVKK:** veri sorumlusu onayı, çalışan aydınlatması, saklama/imha, erişim yetkileri — NO-GO.
2. **Model lisansı** (Ultralytics AGPL/Enterprise) kararı — NO-GO.
3. **Etiketli saha verisi** ve İSG imzalı kabul eşikleri (doğruluk, yanlış alarm, gecikme, **kapsam eşiği**) — BLOCKED.
4. **Gerçek RTSP kamera** testi: kopma/donma/yanlış parola, `STREAM_READ_TIMEOUT_SEC` değerinin kamerayla
   doğrulanması, UDP/TCP taşıma; hedef **saha bilgisayarında** yük matrisi.
5. **24 saatlik soak** (araç hazır: `python tools/reliability/soak.py --hours 24`); uyku/yeniden başlatma testi.
6. Dedektör venv güvenlik güncellemesi (Pillow ≥ 13.0.0, filelock ≥ 4.2.1, setuptools ≥ 84.1.0) + regresyon.
7. Klasör ACL'lerinin daraltılması, BitLocker doğrulaması, yedek şifreleme kararı.
8. Öksüz run uzlaştırma kararı; bir yetkili kullanıcının yeni dashboard'da oturumlu ekranları kontrol etmesi.
9. Operatör eğitimi: "Analiz kapsamı", "izlenemiyor", "Bağlantı koptu" göstergelerinin anlamı.

## 15. Git commit

Bu raporla oluşturulan commit: `git log` içinde "FAZ 10-A: …". Plan belgesi, `backups/`, `tools/reliability/output/`
(ham ölçümler, test yığınının üretilmiş sırları) commit'e eklenmedi. İzole test yığını `ppe-rel` durduruldu; volume'ları
silinmedi. Ana yığın FAZ 10-A imajıyla çalışıyor (Alembic 0006). Geri dönüş imajları: `ppe-dashboard:rollback-faz9-2b6202b`,
`ppe-dashboard:rollback-pre-faz10a`. FAZ 10-B'ye geçilmedi; saha pilotu başlatılmadı.
