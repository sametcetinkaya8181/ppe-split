# FAZ 9 raporu — Güvenilirlik, performans, yük testi ve operasyonel gözlem

Tarih: 2026-11-01. Başlangıç: `2385c97` (FAZ 8). Plan: `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md`, FAZ 9.
Test planı: `docs/RELIABILITY_TEST_PLAN.md`. Ölçülmüş taban çizgisi: `docs/PERFORMANCE_BASELINE.md`.

**İki ayrı statü:**

| Statü | Karar |
|---|---|
| A. Operasyonel güvenilirlik (bu donanım ve test koşullarında) | **INCOMPLETE** — test edilen tüm senaryolar PASS (kayıp 0, çift 0, kritik FAIL yok); fakat 4/24 saatlik soak, gerçek RTSP kamera ve hedef saha donanımında ölçüm yapılmadı |
| B. Gerçek şantiye hazırlığı | **NO-GO** — etiketli saha verisi yok (FAZ 8 BLOCKED), KVKK ve model lisansı NO-GO, İSG kabul eşikleri onaylanmadı, 24 saatlik soak ve gerçek RTSP kamera testi yapılmadı |

Bu fazda model eğitilmedi; `best.pt` (SHA-256 `6886ad2f…`), `ppe_core.py`, ByteTrack ayarları, üretim eşikleri,
eski migration'lar, ana PostgreSQL verisi, ana `detector/outbox` dizini ve snapshot'lar değiştirilmedi. Yıkıcı
testlerin tamamı ayrı bir compose projesinde (`ppe-rel`, ayrı DB volume'u, 127.0.0.1:8100) yapıldı.

## 0. Ön denetim

- Dal `main`, HEAD `2385c97`, çalışma ağacı temiz; izlenmeyen tek dosya kullanıcının plan belgesi (korundu,
  commit'e eklenmedi). Önceden yapılmış FAZ 9 çalışması yoktu.
- Okunanlar: plan belgesi (FAZ 9/10), `PHASE_7_REPORT.md`, `PHASE_8_REPORT.md`, `MODEL_EVALUATION_PROTOCOL.md`,
  `detector.py`, `outbox.py`, `protocol.py`, `ppe_core.py`, `app.py`, `auth/security.py`, `db/*`, compose dosyaları,
  Ultralytics 8.4.183 `LoadStreams` ve `utils/events.py`.
- Ana DB yalnız okundu: 74 run (finished 64, interrupted 6, running 4), 104 olay, 47 incident, çift `event_id` 0,
  Alembic 0005. `maintenance.runs reconcile` **dry-run**: 4 aday (FAZ 5'ten 3 + FAZ 7 E2E'den 1; ~95 sa / ~7 sa
  sinyalsiz). `--apply` kullanılmadı.

## 1. Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `detector/telemetry.py` (yeni) | Kamera başına gözlem: uptime, bağlantı durumu, işlenen/kaynak FPS, kare süresi ve aşama süreleri p50/p95/p99, lag, başlangıç süresi, son kare yaşı, CPU/RAM/tanıtıcı/thread, GPU kullanım/VRAM (NVML + torch), sinyal yok/donmuş/takılmış akış işaretleri, **analiz kapsamı**; `<kamera>.health.json` + kare meta'sında küçük özet; kare döngüsünden bağımsız sağlık yazıcısı |
| `detector/detector.py` | Telemetri çağrıları (yalnız ölçüm; karar zinciri aynı sıra ve argümanlarla), `YOLO_OFFLINE=1` varsayılanı |
| `detector/outbox.py` | `replay_success_count`, WAL/DB boyutu, disk boş alanı, kapasite oranı, uyarılar (`high_watermark`, `pending_age`, `dead_letters`, `quarantine`, `disk_write_errors`, `disk_free_low`, `memory_fallback`, `delivery_paused`, `messages_lost`) + uyarı logu, `journal_size_limit`, kare meta'sında outbox özeti; **401/403 sonrası token her yeniden denemeden önce okunur** (G1 bulgusu) |
| `dashboard/observability.py` (yeni) | Süreç içi metrik kaydı (route şablonu etiketi, kardinalite sınırlı), saf ASGI ölçüm katmanı, dedektör özetleri için beyaz liste temizleyici, PostgreSQL/snapshot deposu durumu |
| `dashboard/app.py` | `GET /api/ops/metrics` (yalnız admin), DB yazım zamanlayıcıları, olay alındı/eklendi/tekrar sayaçları, WebSocket sayaçları, `meta.health`/`meta.outbox` temizleme; **WebSocket kopmasının `receive()` ile algılanması** (K2 bulgusu) |
| `dashboard/static/index.html` | Canlı karoda "kamera izlenemiyor" (dedektör yok / sinyal yok / bağlantı koptu / donmuş / takıldı), "N kişi değerlendirilemiyor (neden)", gecikme, olay kuyruğu; durum çubuğunda "N kamera İZLENEMİYOR" |
| `dashboard/Dockerfile`, `docker-compose.yml`, `.env.example`, `detector/.env.example`, `docs/OUTBOX.md`, `.gitignore` | yeni modül kopyası, `OPS_METRICS`, yeni ortam değişkenleri, outbox metrik belgesi, `tools/reliability/output/` Git dışı |
| Testler (yeni) | `detector/tests/test_telemetry.py` (17), `detector/tests/test_outbox_monitoring.py` (6), `dashboard/tests/test_observability.py` (8), `ui_check.mjs` +1 kontrol |
| `tools/reliability/` (yeni) | izole yığın (`docker-compose.reliability.yml`), `stress.py`, `sampler.py`, `wsprobe.py`, `failures.py`, `faultproxy.py`, `eventgen.py`, `camsim.py`, `telemetry_check.py`, `report.py`, `detector_launcher.py`, `common.py` |
| `docs/PHASE_9_REPORT.md`, `docs/RELIABILITY_TEST_PLAN.md`, `docs/PERFORMANCE_BASELINE.md` (yeni) | rapor, test planı, ölçülmüş taban çizgisi |

Dokunulmayanlar: `best.pt`, `ppe_core.py`, ByteTrack, üretim eşikleri, WebSocket ikili biçimi, migration 0001–0005,
ana DB, ana outbox/snapshot dosyaları, plan belgesi (izlenmiyor, commit'e eklenmedi).

## 2. Git durumu ve commit

Başlangıç `2385c97`. FAZ 9 değişiklikleri tek commit'te (§17). FastAPI/Starlette güvenlik yükseltmesi bilinçli olarak
**ayrı** bir commit'tir (§11) — dayanıklılık değişiklikleriyle karışmasın, gerekirse tek başına geri alınabilsin.

## 3. Observability mimarisi

```
kamera süreci (her kamera ayrı)                         dashboard (FastAPI)                     operatör
 detector.py ─► telemetry.CameraTelemetry ─► <cam>.health.json (5 sn, kare döngüsünden bağımsız)
      │              └─ compact() ─► kare meta.health ──► /ingest/frame ─► sanitize_health ─► /api/state ─► canlı karo
      └─► DurableSender ─► <cam>.status.json + meta.outbox ─────────────► sanitize_outbox ──┘
                                                         OpsMiddleware + REG ─► /api/ops/metrics (admin)
```

- **Maliyet:** kare başına birkaç sayaç ve 64×36 seyrek piksel örneği (~0.05 ms); yüzdelikler 1 sn'de bir
  hesaplanır; kaynak örnekleme ve dosya yazımı 5 sn'de bir. Telemetrinin kare başı maliyeti ayrıca ölçülmedi;
  karşılaştırma için PPEMonitor p95 ≤ 0.16 ms, kare döngüsü p95 50–114 ms. Dashboard: istek başına bir sözlük
  güncellemesi.
- **Yapılandırma:** `HEALTH_EVERY`, `LAG_WARN_SEC`, `FREEZE_WARN_SEC`, `STALL_WARN_SEC`, `OUTBOX_AGE_WARN_SEC`,
  `OUTBOX_MIN_FREE_BYTES`, `OUTBOX_WAL_LIMIT_BYTES`, `OPS_METRICS`, `OPS_LATENCY_WINDOW`.
- **Gizlilik/kardinalite:** metriklerde kaynak adresi, token, görüntü, event_id/incident_id, kullanıcı/IP yok;
  route etiketi şablondur (`/api/history/incidents/{incident_id}`), eşleşmeyen yollar tek `other` etiketine düşer
  (test: kimlikler metrik çıktısında yok). Dedektörden gelen özet dashboard'da sabit anahtar/sözlük beyaz listesiyle
  temizlenir (test: `rtsp://admin:S3cr3t@…`, `<script>` ve bilinmeyen anahtarlar düşer). Mevcut log biçimi korundu.
- **Uç:** `GET /api/ops/metrics` yalnız admin (viewer/supervisor 403, oturumsuz 401); kamera başına durum, son kare
  yaşı, FPS, sağlık/kapsam, outbox; route başına istek sayısı ve gecikme p50/p95/p99; 400/401/403/404/409/413/422/
  429/500/503 sayıları; `/ingest/*` başarı/hata; olay alındı/eklendi/tekrar ayıklandı; DB yazım (olay/rapor/
  canlılık) gecikmesi ve hata sayısı; WebSocket aktif/kabul/ret/kapanış/gönderilen kare; geçmiş API gecikmesi;
  PostgreSQL hazır + havuz (size/checked_out/overflow) + kontrol süresi; snapshot deposu yazılabilirlik, disk boş
  alanı, dosya sayısı/boyutu. Sayaçlar süreç yeniden başlayınca sıfırlanır (`uptime_sec` ile okunmalı).

## 4. Kamera ve analiz kapsamı metrikleri

Güvenilirlik kapısı `ppe_core.PPEMonitor` içinde (değiştirilmedi): kişi kutusu `conf ≥ 0.40`, yükseklik ≥ 60 px ve
kadraj kenarından > 6 px. `telemetry.gate_reasons` aynı sabitlerle nedenleri (`low_conf`, `too_small`, `edge`)
üretir; 400 rastgele kutuda PPEMonitor'ün gözlem ekleme kararıyla **birebir eşdeğer** olduğu test edildi. Ek neden:
`warmup` (kapıdan geçiyor ama MIN_OBS/MIN_SPAN dolmadı). Ölçülenler: değerlendirilen/değerlendirilemeyen track-kare,
nedene göre dağılım, ANALIZ durumundaki track-kare, kapanan track'lerden hiç karar almayanlar + süre dağılımı +
baskın neden, anlık görünür/karar verilmiş/ANALIZ kişi ve **"bayat karar"** (daha önce karar almış ama şu an kapıdan
geçmeyen kişi — PPEMonitor son kararı korur; ekranda UYGUN/İHLAL görünür ama o an ölçülemiyor).

Gerçek model, stok videolar (yük testinden, ~150 sn pencere; **saha verisi değildir**):

| Video | Track-kare | Değerlendirilemeyen | Neden | ANALIZ track-kare | Hiç karar almayan kapanmış track | Süre max |
|---|---|---|---|---|---|---|
| V1 (1080p, tam boy işçiler) | 2730 | %24.2 | kenar 644, düşük güven 18 | %46 | 10 / 56 (9 warmup, 1 kenar) | 3.4 sn |
| V2 (1080p, yakın çekim) | 3152 | **%91.1** | kenar 2859, düşük güven 35 | %62 | 12 / 18 (12 kenar) | **23.9 sn (videonun tamamı)** |

FAZ 8'deki "bir kişi 286 kare ANALIZ" bulgusu sayısal olarak doğrulandı: yakın çekimde kişi kutusu kadraj kenarına
değdiği için hiç değerlendirilmiyor. Bu artık operatörden gizlenmiyor: karoda "N kişi değerlendirilemiyor (kadraj
kenarı …)" yazar; ANALIZ hiçbir yerde "Uygun" sayılmaz (durum çubuğu ayrı "Analiz ediliyor" sayacı; tarayıcı testi).
Kapı eşikleri değiştirilmedi, yeni ihlal/alarm üretilmedi, geçmiş veri yeniden sınıflandırılmadı. Track sayısı
benzersiz kişi sayısı olarak sunulmaz (çıktıda açık not).

## 5. Çoklu kamera performansı

Ayrıntılı tablolar: `PERFORMANCE_BASELINE.md` §2–4. Özet (1080p, gerçek zaman hızında yerel videolar, 120 sn):

| Ayar | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| Stride 2 — yetişiyor mu (lag p95) | ✔ (-0.08) | ✔ (-0.06) | ✔ (-0.02) | ✔ (-0.09) | ✔ (≤0.0) | ✘ (0.08–0.90) |
| Stride 2 — çıkarım p95 ms | 32 | 30–31 | 36–38 | 41–45 | 50–53 | 64–67 |
| Stride 2 — GPU % p95 / sistem CPU % p50 | 48 / 17 | 38 / 25 | 46 / 37 | 65 / 48 | 88 / 69 | 92 / 91 |
| Stride 1 — yetişiyor mu | ✔ | ✔ | ✘ (FPS 23.3–23.6, lag 1.5 sn) | – | – | – |

Ölçülmüş kapasite: stride 2'de **5 kamera** (120 sn), stride 1'de **2 kamera**. Öneri: bu dizüstüde üretim
ayarıyla ≤ 4 kamera. 3.–6. kamera aynı videoları yeniden kullanır (bağımsız içerik yükü değildir); canlı RTSP
test edilmedi. İlk matristeki (eski lag tanımı) 2–3 kameralı satırlar kullanılmadı; lag tanımı başlangıç süresini
ayıracak şekilde düzeltilip matris yeniden koşuldu.

WebSocket: oturumlu prob tüm senaryolarda kare akışını aldı. Ölçümlerde ~40 sn'de bir görülen 1011 kapanışlarının
nedeni probun ping'e pong vermemesiydi (uvicorn: 20 sn ping + 20 sn zaman aşımı; eski imajda da aynı desen). Prob
düzeltildi: 130 sn'de 0 kopma, 6 pong, en büyük kare boşluğu 0.125 sn. Tarayıcılar pong'u otomatik verir.

## 6. Uzun süreli test sonuçları

**Gerçekte koşulan:** 1 saat — 3 kamera (V1, V2, V1 tekrar), 1080p, stride 2, izole yığın, oturumlu WebSocket
probu, 15 sn örnekleme (239 örnek). Başlangıç **2026-11-01T19:42:08Z**, bitiş **2026-11-01T20:42:35Z**, ölçüm
3600.1 sn (23 sn ısınma hariç). Başlangıçta boş RAM 1.8 GB, sayfa dosyası kullanımı 3.4 GB. **4 saat ve 24 saat
koşulmadı.**

| Gösterge | 1.–10. dk (medyan) | 25.–35. dk | Son 10 dk | Yorum |
|---|---|---|---|---|
| Dedektör RSS toplam (3 süreç + ana süreç) MB | 3405 | 3267 | 2774 | Artış yok; düşüş Windows'un bellek baskısında çalışma kümesini kırpmasından (RSS = working set; özel bellek ölçülmedi) |
| Dedektör tanıtıcı (handle) | 2038 | 2038 | 2038 | Sabit |
| Dedektör thread | 89 | 86 | 86 | Sabit |
| GPU bellek (cihaz) MB | 744 | 792 | 798 | +54 MB, ikinci yarıda plato; torch rezerve (kamera 1) 170 → 178 → 180 MB |
| Dashboard container MB | 82.5 | 84.8 | 86.9 | +4.4 MB/sa; doygunluk 1 saatte kanıtlanamadı |
| PostgreSQL bağlantı | 6 | 6 | 6 | Sabit; havuz `checked_out` max 1 |
| PostgreSQL DB MB | 8.4 | 9.1 | 9.7 | Veri büyümesi (olay/run satırları; kısa videoların döngüsü saatte ~630 run üretir — canlı kamerada olmaz) |
| Outbox WAL (kamera 2) MB | 3.0 | 4.0 | 4.0 | ~4 MB'ta plato (otomatik checkpoint); kapanışta 0'a kırpıldı |
| Lag (kamera 2) sn | -0.1 | -0.1 | -0.1 | Eğim ≈ 0 (+0.003…0.017 sn/sa) |
| `/api/state` ms (prob) | 13.1 | 13.9 | 12.8 | Sabit |

| Kamera | İşlenen kare | Etkin FPS p50 (p05) | Lag p95 / max | Kare ms p95 (medyan) / p99 max | Çıkarım p95 / p99 max | Outbox max bekleyen | Kopma |
|---|---|---|---|---|---|---|---|
| relc1 (V1) | 44 184 | 12.43 (12.23) | -0.07 / -0.06 | 54.9 / 112.2 | 33.4 / 58.5 | 0 | 0 |
| relc2 (V2) | 42 533 | 12.00 (10.65) | -0.07 / 0.00 | 56.8 / 144.6 | 35.3 / 70.8 | 0 | 0 |
| relc3 (V1 tekrar) | 44 170 | 12.43 (11.90) | -0.07 / -0.05 | 57.1 / 98.5 | 34.9 / 63.8 | 0 | 0 |

Sistem: GPU % p50/p95 31/44, sıcaklık max 64 °C; sistem CPU % p50/p95 35/39; dashboard CPU % p95 39; DB olay yazımı
p95 49 ms / p99 75 ms; kare ingest p95 3.7 ms. Teslim: 597 olay (150 start, 149 end + ongoing), DB'de 597 tekil, kayıp 0,
çift 0; 632 run raporu iletildi; ölü/karantina 0; kapanış zarif (2.6 sn), bekleyen 0. Outbox dosyasıyla karşılaştırmada
DB'de "fazladan" görünen 1 olay, teslim edilmiş satırların meta kaydının 1 saat sonra silinmesinden
(`delivered_retention` 3600 sn) kaynaklanır; gönderici sayacı da 597'dir. Kapsam (1 saat): V2'de track-karelerin
%91.3'ü, V1'de %23.6'sı değerlendirilemedi (kenar); hiç karar almayan kapanmış track V1 239/1436, V2 297/447.

WebSocket probu bu soak'ta düzeltme öncesi sürümdü (soak başladıktan sonra düzeltildi) ve ping'e pong vermediği için
~40 sn'de bir 1011 ile kapatılıp yeniden bağlandı (87 kopma, 0 bağlantı hatası, 129 185 kare, en büyük boşluk 1.08 sn).
Bu sunucu kararsızlığı değildir: aynı desen FAZ 9 öncesi imajda da var; pong veren probla 130 sn'de 0 kopma.

**Sonuç:** 1 saatlik pencerede tanıtıcı, thread, bağlantı, WAL ve gecikme sabit; belirgin bellek sızıntısı
**gözlenmedi**. 1 saatlik veriden 24 saatlik sızıntı yokluğu **çıkarılamaz**; GPU belleği ve dashboard belleğindeki
küçük artışların doygunluğu 4/24 saatlik soak ile doğrulanmalı.

## 7. Arıza enjeksiyonu sonuçları

Tamamı izole `ppe-rel` yığınında. "Defter" = outbox'a commit edilmiş kimlikler; DB = PostgreSQL 17.

| Kod | Senaryo | Beklenen | Gözlenen | Toparlanma | Defter/DB, kayıp, çift | Operatöre görünen | Sonuç |
|---|---|---|---|---|---|---|---|
| A | PostgreSQL durdur 25 sn / başlat | 503, birikme, sonra teslim | olaylar outbox'ta (max 19), `backoff`; `/healthz` 200; `/health/ready` 3 sn içinde yanıt vermedi (zaman aşımı) | DB hazır olduktan 4.3 sn sonra kuyruk 0 | 38/38, 0, 0; 4 rapor | `/health/ready` başarısız; canlı görüntü sürer | PASS |
| B | Dashboard restart | bağlantı reddi → teslim | max 1 bekleyen | hazır 1.3 sn, kuyruk 0.9 sn | 33/33, 0, 0 | RAM'deki canlı uyarı listesi sıfırlanır (geçmiş DB'de) | PASS |
| K | WebSocket kopması (B sırasında) | yeniden bağlanma | prob hazır olur olmaz bağlandı, 3 sn'de 31 kare | anında | — | — | PASS |
| K2 | İstemci kare yokken kapatır | sunucu döngüsü kapanır | **FAZ 9 öncesi: 20 sn sonra bile açık** (bir önceki testten kalan ölü bağlantı da açıktı); düzeltme sonrası **0.22 sn** | — | — | `/api/ops/metrics` websocket.active doğru | PASS (düzeltildi) |
| C | Sentetik kamera 503 sırasında zorla öldürme | replay, kayıp yok | öldürmede 10 bekleyen | yeniden başlatmadan 1.0 sn sonra 10 replay | 20/20, 0, 0; öldürülen run `running` (raporsuz) | — | PASS |
| E1 | Ağ zaman aşımı (20 sn) | geri çekilme | max 12 bekleyen | 1.7 sn | 33/33, 0, 0 | — | PASS |
| E2 | ACK kaybı (sunucu işler, yanıt kaybolur) | aynı gövde tekrar, DB'de tek | 21 yanıt kaybı, sunucu 16 tekrar olayı ayıkladı | 9.3 sn | 33/33, 0, **0 çift** | — | PASS |
| F | HTTP 503 (20 sn) | geri çekilme | max 13 bekleyen | 1.3 sn | 33/33, 0, 0 | — | PASS |
| G1 | 401, gerçek yanlış token; yeniden başlatmadan düzeltme | duraklat, düşürme yok | `paused_auth`, `delivery_paused` uyarısı, max 30 bekleyen | **ilk koşu: ~48 sn (yalnız kapanışta boşaldı) → düzeltme sonrası 16.8 sn** | 27/27, 0, 0 | log + `delivery_paused` | PASS (düzeltildi) |
| G2 | 403 (15 sn) | duraklat | max 17 bekleyen | 15.3–15.9 sn (auth_retry 30 sn) | 33/33, 0, 0 | `delivery_paused` | PASS |
| H | SQLite kilitli (aynı kamera ikinci süreç) | ikinci süreç başlamaz | `OutboxLocked`, çıkış kodu 1; birinci etkilenmedi | — | 16/16, 0, 0 | log | PASS |
| I | Kapasite sınırı (MAX_ITEMS 40, 45 sn 503) | uyarı, start/end kaybı yok | `high_watermark`; max 49 bekleyen (kritik yedek alan); seyreltme/bellek yedeği tetiklenmedi (olaylar çoğunlukla start/end) | 10.0 sn | 56/56, 0, 0 | `high_watermark` | PASS (seyreltme yolu FAZ 7 birim testleriyle) |
| J | Bozuk/büyük payload | 422/413/401, DB'ye satır yok | geçersiz JSON/tip/kamera/snapshot 422 ×5, 201 olay ve 9 MiB 413 ×2, token yok 401; yalnız geçerli kontrol satırı yazıldı; `/healthz` 200 | — | — | `/api/ops/metrics` sayaçları doğru | PASS |
| D1 | Akış kopması (TCP/MPEG-TS benzetimi) | algıla, yeniden bağlan | 6.1 sn'de `no_signal` (dedektör + operatör), ~12 sn'de `camera_disconnected`, sabit 3 sn aralıklı deneme | geri gelince 9.1 sn, aynı kamera kimliği, **yeni run** | — | "Sinyal yok — izlenemiyor", sonra "dedektörden görüntü gelmiyor" | PASS (bulgu: kopan run `finished` kaydediliyor, §9) |
| D2 | Donmuş akış (soket açık, veri yok) | algıla | FAZ 9 ilk sürüm: dedektör hiçbir şey işaretlemedi (okuma bloke, sağlık dosyası güncellenmedi); düzeltme sonrası `stalled` ~6 sn; dashboard 6 sn'de "izlenemiyor" | çözülünce 3.0 sn, aynı run | — | "izlenemiyor" | PASS (düzeltildi) |
| D3 | Donmuş akış + `OPENCV_FFMPEG_CAPTURE_OPTIONS=rw_timeout;5000000` | okuma zaman aşımı → yeniden bağlanma | 11 sn'de `no_signal`, sonra yeniden bağlanma döngüsü | 3.0 sn, yeni run | — | "izlenemiyor" | PASS (yapılandırma önerisi) |
| L | Gerçek dedektör çalışırken dashboard + DB container restart | dedektör etkilenmez | dashboard hazır 2.3 sn, DB 0.3 sn; dedektör bağlı kaldı | — | outbox 9 = DB 9, 0, 0 | — | PASS |
| M | Gerçek dedektör 503 sırasında `taskkill /T /F` | replay | öldürmede 5 bekleyen; yeniden başlatmada "outbox kurtarma: 5 bekleyen" | yeniden başlatmada teslim | 10 = 10, 0, 0; öldürülen run `running` (raporsuz) | — | PASS |
| — | Gerçek disk doldurma, gerçek RTSP kamera/yanlış RTSP parolası, Windows uyku/yeniden başlatma, GPU sıfırlama/OOM, CPU'ya düşme | — | — | — | — | — | **SKIP** (güvenli yapılamaz / donanım yok) |

## 8. Outbox dayanıklılığı (FAZ 7 davranışının doğrulanması)

| Özellik | Kanıt | Sonuç |
|---|---|---|
| start/end/rapor sessizce düşmüyor | A–M defter ↔ DB; kapasite testinde de 0 | PASS |
| HTTP ACK olmadan teslim onayı yok | E2 (ACK kaybı): aynı gövde yeniden gönderildi | PASS |
| Restart sonrası aynı kimliklerle replay | C (10), M (5); `replay_success_count` | PASS |
| Tekrar `event_id` yeni satır üretmiyor | E2: 16 tekrar ayıklandı, çift 0; tüm senaryolarda `count - count(DISTINCT)` = 0 | PASS |
| Kamera kaynakları birbirinden izole | ayrı dosya/süreç; telemetri izolasyon testi; çoklu kamera yükünde kamera başına outbox | PASS |
| 401/403 mesajları koruyor | G1/G2: `paused_auth`, kayıp 0 | PASS (+ token yenileme düzeltmesi) |
| Dead-letter görünür | `dead_letters` uyarısı + `/api/ops/metrics` + karo; birim test | PASS |
| Rapor bariyeri | her senaryoda raporlar DB'de, durum uyuşmazlığı 0; FAZ 7 birim testleri geçiyor | PASS |
| WAL büyümesi kontrol altında | `journal_size_limit` (64 MiB); birim test (≈8 MB yazım sonrası sınır içinde); soak §6 | PASS |
| Disk dolunca güvenli bildirim | `disk_free_low` (< 1 GB) uyarısı, `disk_write_errors`, `memory_fallback`, `messages_lost` | PASS (gerçek disk doldurma SKIP) |
| Kapasiteye yaklaşınca alarm | `high_watermark` (I senaryosu), `pending_age` | PASS |
| Doğrulanmamış mesaj otomatik silinmez | uyarılar yalnız görünürlük; birim test (uyarı sonrası bekleyen sayısı aynı) | PASS |

## 9. Kamera yeniden bağlanma davranışı (mevcut kod korunarak analiz)

- **Algılama:** kopmada Ultralytics `LoadStreams` siyah kare üretip yeniden açmayı dener; açamazsa akış biter.
  FAZ 9 telemetrisi siyah kareyi `no_signal`, kare gelmemesini `stalled`, açılamamayı `camera_disconnected` olarak
  işaretler; "ihlal yok" ile "kamera izlenemiyor" artık birleşmez.
- **Politika:** `stream_worker` istisnada **sabit 3 sn** bekleyip sonsuza dek yeniden dener (üstel geri çekilme yok).
  Akış hatasız biterse (`LOOP=1`) hemen yeni run başlar. `LOOP=0` ile canlı kaynak koparsa kamera süreci **sona erer**.
- **Kimlik:** kamera kimliği süreç boyunca sabit; her yeniden bağlanma **yeni run** (tasarım gereği).
- **Incident:** run sonunda açık incident'lar `run_end` nedeniyle raporlanır (sahte `end` yok); aynı ihlal yeniden
  bağlanmadan sonra yeni incident olur (FAZ 8'deki duplicate bulgusuyla aynı mekanizma).
- **Outbox:** kopma olay teslimini etkilemez (kamera ve teslim bağımsız).
- **Bulgu (değiştirilmedi):** kopan canlı akışın run'ı DB'ye `finished` olarak yazılıyor (D1, D3), çünkü Ultralytics
  akışı hata fırlatmadan bitiriyor. Geçmiş ekranında normal bitiş gibi görünür. Öneri (FAZ 10 öncesi, ayrı
  değişiklik): canlı kaynakta akış sonunu `error`/`disconnected` olarak raporlamak.
- **Bulgu:** donmuş akışta OpenCV okuması ~30 sn bloke olur; dedektör artık bunu `stalled` olarak gösterir.
  Yeniden bağlanmayı hızlandırmak için `OPENCV_FFMPEG_CAPTURE_OPTIONS` ile okuma zaman aşımı önerilir (D3:
  `rw_timeout;5000000`). Gerçek RTSP'de doğru seçenek (`timeout`/`rw_timeout`) kamerayla doğrulanmalı (SKIP).
- **Eski kayıtlar:** ana DB'de 4 `running` aday (dry-run). Uygulamak kullanıcı kararıdır:
  `docker compose exec dashboard python -m maintenance.runs reconcile --apply --confirm 4`.

## 10. PostgreSQL stabilitesi

Yük altında havuz `checked_out` en fazla 1, bağlantı sayısı ≤ 6; DB olay yazımı p95 14 ms (2 kamera) – 65 ms
(6 kamera); durdur/başlat (A) ve container restart (L) sonrası `pool_pre_ping` ile otomatik toparlanma, veri kaybı
yok. Soak boyunca bağlantı sayısı ve DB boyutu: §6. Bulgu: DB yokken `/health/ready` 3 sn'den uzun sürüyor
(bağlantı zaman aşımı 3 sn + havuz denemesi); liveness (`/healthz`) etkilenmiyor. Öneri: hazır olma denetimine kısa
zaman aşımı.

## 11. Güvenlik ve regresyon

- Regresyon (FAZ 6 güvenlik testleri dashboard paketinde): oturum, RBAC/kamera kapsamı, CSRF, ingest token
  (gövde okunmadan), snapshot yetkisi, video/WebSocket yetkisi, oturum süresi — **PASS** (319/319). LAN TLS izole
  smoke **21/21 PASS** (güncel kodla). Yeni uç `/api/ops/metrics` yalnız admin (test).
- **Bağımlılıklar:** `pip-audit` (2026-11-01, canlı OSV/PyPI): yalnız `starlette 0.41.3`, FAZ 7 ile aynı 7 kayıt
  (CVE-2025-54121, CVE-2025-62727, CVE-2026-48710, CVE-2026-48817, CVE-2026-48818, CVE-2026-54282,
  CVE-2026-54283); en yüksek düzeltme 1.3.1. FastAPI 0.162.0 (`starlette>=0.46.0`, üst sınır yok) + Starlette
  1.10.0 izole test container'ında: `pip check` temiz, **319/319 PASS**. Ek geçişli bağımlılıklar: `annotated-doc`,
  `opentelemetry-api` (yalnız API; SDK/dışa aktarıcı yok → veri göndermez). Yükseltme ayrı commit'tir: FAZ 9 commit'inin ardından ayrı commit olarak uygulandı; sonuç ve geri dönüş planı: §18.
- **Ultralytics telemetrisi:** 8.4.183'te `track` modu da `on_predict_end` ile Google Analytics'e anonim olay gönderir
  (CPU/GPU adı, model dosya adı, hızlar, kare sayısı; görüntü değil). Ağ çağrıları engellenip kaydedilen alt süreç
  kanıtı (`tools/reliability/telemetry_check.py`, gerçek model + 186 kare):

  | Koşul | ONLINE | analitik etkin | Dış bağlantı denemesi |
  |---|---|---|---|
  | FAZ 9 varsayılanı (`detector.py` → `YOLO_OFFLINE=1`) | False | False | **yok** |
  | `YOLO_OFFLINE=0` | True | True | `dns:one.one.one.one`, `urlopen:www.google-analytics.com` (engellendi) |

  Kullanıcının küresel `settings.json`'u (`sync: true`) değiştirilmedi; ayar süreç düzeyindedir. Diğer dış
  bağlantı yolları (YouTube kaynakları için yt-dlp, kullanıcı RTSP adresleri) kullanıcının yapılandırmasına bağlıdır;
  hassas içerik çıkmadığı varsayımla iddia edilmez.
- LAN erişimi açılmadı; KVKK ve lisans NO-GO korunuyor.

## 12. Kaynak tüketimi

Kamera süreci başına: RSS ~1.0–1.6 GB (CUDA bağlamı dahil), VRAM ~215 MB, CPU ~1.0–1.4 çekirdek (stride 2).
Dashboard container: 72–94 MB, CPU p95 %16 (1 kamera) – %115 (6 kamera; sınır 2 CPU). PostgreSQL: ≤ 6 bağlantı.
Uzun süreli eğilimler: §6.

## 13. Bilinen darboğazlar

1. **CPU** (kare çözme, çizim + JPEG kodlama, Python döngüsü): 6 kamerada sistem CPU p50 %91.
2. **RAM:** kamera başına ayrı süreç + ayrı model/CUDA bağlamı; 3+ kamerada paylaşımlı dizüstünün belleği dolu.
3. **GPU paylaşımı:** çıkarım p50 17 ms (1 kamera) → 43 ms (6 kamera); VRAM sınır değil.
4. **Run başlangıcı:** her yeniden bağlanma/döngüde 2.8–9.1 sn ilk kare gecikmesi (kamera sayısıyla artar).
5. **Kapsam:** yakın çekim/kenar kadrajlarda kişilerin %90'a varan kısmı değerlendirilemiyor (model değil kadraj).
6. Sabit 3 sn yeniden bağlanma, üstel geri çekilme yok; donmuş akışta varsayılan 30 sn okuma blokajı.
7. `auth_retry` 30 sn: token düzeltmesi sonrası teslim en fazla ~30 sn gecikir.

## 14. Veri kaybı riskleri (kalan)

1. Outbox commit'inden önceki çökme (olay henüz diske yazılmamış) — garanti dışı (FAZ 7).
2. Disk yazılamaz + bellek yedeği dolu + süreç çökmesi → `messages_lost` sayılır ama mesaj kaybolur.
3. Kapasite dolunca `ongoing` seyreltme (incident sayısı etkilenmez).
4. Zorla öldürülen run raporsuz kalır (`running`); uzlaştırma uygulanmadıkça geçmişte "sürüyor" görünür.
5. Kopan canlı akışın run'ı `finished` görünür (veri kaybı değil, yanlış durum etiketi).
6. Dashboard RAM'indeki canlı uyarı listesi restart'ta sıfırlanır (kalıcı kayıt DB'de, kayıp yok).
7. Disk şifrelemesi (BitLocker) ve proje klasörü ACL'i hâlâ doğrulanmadı (FAZ 7).

## 15. PASS / FAIL / SKIP / BLOCKED

| Kalem | Sonuç | Kanıt |
|---|---|---|
| Dedektör regresyonu + yeni testler | **PASS 225/225** (202 + 23) | `cd detector; .venv\Scripts\python -m pytest` |
| Dashboard regresyonu + yeni testler (gerçek PostgreSQL 17) | **PASS 319/319** (311 + 8) | `docker compose --profile test run --rm tests` |
| Tarayıcı (Edge + CDP) | **PASS 29/29** (28 + 1 FAZ 9 kapsam/izlenemezlik) | `node dashboard/tests/ui/ui_check.mjs` |
| LAN TLS izole smoke | **PASS 21/21** | `python deploy/lan/lan_smoke.py` |
| Metrik doğruluğu, kapı eşdeğerliği, kamera izolasyonu, yeniden bağlanma sayaçları, frame lag, stall | PASS | `test_telemetry.py` |
| Outbox izleme, replay sayacı, WAL sınırı, uyarılar, token yenileme | PASS | `test_outbox_monitoring.py` |
| `/api/ops/metrics` RBAC, kardinalite, sayaçlar, beyaz liste, WebSocket, depolama | PASS | `test_observability.py` |
| Çoklu kamera yükü (1–6 kamera stride 2, 1–3 stride 1) | PASS (ölçüldü; kapasite 5 / 2) | `stress.py` |
| Arıza enjeksiyonu A–M, K2 | PASS (kayıp 0, çift 0) | `failures.py` |
| Gerçek PostgreSQL kesintisi / restart | PASS | A, L |
| Çökme kurtarma (gerçek dedektör) | PASS | M |
| 1 saatlik stabilite | **PASS** (3600.1 sn gerçek koşu, 19:42:08Z–20:42:35Z; sızıntı göstergesi yok) | `stress.py --matrix 3:2 --duration 3600` |
| 4 saatlik ve 24 saatlik soak | **NOT RUN** | — |
| Ultralytics çevrimdışı | PASS | `telemetry_check.py`, birim test |
| FastAPI/Starlette yükseltmesi (uygulandı, ayrı commit) | PASS — pip-audit temiz, 319/319, 29/29, 21/21 | §18 |
| Gerçek RTSP kamera, yanlış RTSP parolası, gerçek disk doldurma, uyku/yeniden başlatma, GPU OOM/sıfırlama | **SKIP** | donanım/güvenlik |
| Saha doğruluğu (etiketli veri) | **BLOCKED** (FAZ 8) | — |

Kritik FAIL yok. Bu fazda bulunup düzeltilen üç sorun: WebSocket ölü bağlantı sızıntısı (K2), donmuş akışın
dedektörde görünmemesi (D2), düzeltilmiş token'ın geç kullanılması (G1); üçü de düzeltme sonrası yeniden test edildi.

## 16. FAZ 10 ön koşulları

1. KVKK (veri sorumlusu onayı, çalışan aydınlatması) ve Ultralytics lisans kararı — NO-GO sürüyor.
2. İzinli, etiketli saha verisi ve İSG onaylı kabul eşikleri (FAZ 8 protokolü §8) — BLOCKED sürüyor.
3. Hedef saha bilgisayarında (ayrılmış donanım) aynı yük matrisi + **gerçek RTSP kameralarla** kopma/donma testi;
   `OPENCV_FFMPEG_CAPTURE_OPTIONS` zaman aşımı değerinin kamerayla doğrulanması.
4. En az 24 saatlik soak (gerçekten 24 saat koşmalı) ve uyku/yeniden başlatma testi bakım penceresinde.
5. Kopan canlı akışın run durumunun düzeltilmesi (`finished` yerine hata/kopma) — ayrı, testli değişiklik.
6. Önerilen SLO'ların (PERFORMANCE_BASELINE §7) İSG + işletme tarafından onaylanması; alarm alıcılarının
   belirlenmesi (`/api/ops/metrics` şu an yalnız admin JSON'u; dış izleme sistemi entegrasyonu yok).
7. 4 öksüz run için uzlaştırma kararı; BitLocker ve proje klasörü ACL kararı (FAZ 7'den).
8. Ana yığının yeni imajla güncellenmesi (`docker compose up -d --build dashboard`) — çalışan ana dashboard bu fazda
   bilinçli olarak yeniden başlatılmadı.

## 17. Commit

FAZ 9 commit'i: **`d3af4ca`** ("FAZ 9: Operasyonel gözlem, çoklu kamera yük/soak testi ve arıza enjeksiyonu").
Yükseltme commit'i: §18 (`git log -1`). Plan belgesi, `tools/reliability/output/`
(ham ölçümler, test yığınının üretilmiş sırları) commit'e eklenmedi.

İzole test yığını `ppe-rel` durduruldu (`docker compose -p ppe-rel -f tools/reliability/docker-compose.reliability.yml
stop`); volume'ları (`ppe-rel_rel_pgdata`, `ppe-rel_rel_data`, yalnız test verisi) **silinmedi** — temizlemek
kullanıcı kararıdır: `… down` ve gerekirse `docker volume rm ppe-rel_rel_pgdata ppe-rel_rel_data`.

## 18. FastAPI/Starlette güvenlik yükseltmesi (ayrı commit)

Değişiklik: `dashboard/requirements.txt` — `fastapi==0.115.5` → `0.162.0`, `starlette==1.10.0` açıkça sabitlendi
(FastAPI 0.162 yalnız `starlette>=0.46.0` ister; güvenlik düzeltmesinin geri kaymaması için). Yeni geçişli
bağımlılıklar: `annotated-doc`, `opentelemetry-api` (yalnız API; SDK/dışa aktarıcı yok, veri göndermez). Uygulama
kodunda değişiklik gerekmedi.

| Kontrol | Sonuç |
|---|---|
| İzole ön doğrulama (geçici container, yalnız paket değişimi) | 319/319 PASS, `pip check` temiz |
| `docker compose build dashboard` (yeni imaj: FastAPI 0.162.0, Starlette 1.10.0, Pydantic 2.14.2) | PASS |
| `pip-audit -r dashboard/requirements.txt` (2026-11-01) | **No known vulnerabilities found** (önce: starlette 7 kayıt) |
| Dashboard testleri (gerçek PostgreSQL 17) | **319/319 PASS** |
| Tarayıcı (fixture'da FastAPI 0.162.0 / Starlette 1.10.0 doğrulandı) | **29/29 PASS** |
| LAN TLS izole smoke | **21/21 PASS** |
| Yeni imaj uvicorn altında (izole `ppe-rel`): bozuk payload J, WebSocket K2, 503 F | PASS (kodlar beklendiği gibi; K2 0.22 sn; F kayıp 0 / çift 0) |

Uyarı (yalnız testler): Starlette 1.x `TestClient` için `httpx` kullanımını kullanımdan kaldırılıyor olarak
işaretliyor (`httpx2` öneriyor); test bağımlılığı ayrı bir işte güncellenmeli.

**Geri dönüş planı:** (1) `git revert <yükseltme commit'i>` (yalnız requirements.txt + bu bölüm + SECURITY.md);
(2) `docker compose build dashboard && docker compose up -d dashboard`; (3) `docker compose --profile test run --rm
tests` ile doğrula. Şema/migration değişikliği olmadığından veri geri dönüşü gerekmez. Ana dashboard container'ı bu
fazda yeniden başlatılmadı: yeni imaj (`ppe-dashboard`) derlendi ama çalışan ana container eski imajla sürüyor;
güncellemek kullanıcı kararıdır: `docker compose up -d dashboard`.
