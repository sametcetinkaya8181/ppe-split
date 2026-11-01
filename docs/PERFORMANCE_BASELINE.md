# Performans ve kapasite taban çizgisi (FAZ 9)

Ölçülmüş değerler (2026-11-01) ile **önerilen** hedef eşikler ayrı tutulur. Önerilen eşikler henüz onaylanmamıştır
(İSG + işletme sorumlusu kararı gerekir); bu belgede hiçbir SLO "karşılandı" olarak işaretlenmez.
Yöntem: `docs/RELIABILITY_TEST_PLAN.md`. Ham veri: `tools/reliability/output/20261101T163836Z_stress_final/`
(yük), `…_soak1h/` (soak), `…_failures/` (arıza) — Git dışı.

## 1. Test ortamı

| Alan | Değer |
|---|---|
| Cihaz | ASUS ROG Strix G513RC (dizüstü), Windows 11 Home 10.0.26200 |
| CPU | AMD Ryzen 7 6800HS, 8 çekirdek / 16 iş parçacığı |
| RAM | 15.3 GB; **paylaşımlı geliştirici makinesi** (tarayıcı, VS Code, WSL2/Docker, başka projeler açık): senaryo başında boş RAM 4.3–6.4 GB, sayfa dosyası kullanımı 3.7–4.7 GB |
| GPU | NVIDIA GeForce RTX 3050 Laptop, 4 GB, sürücü 566.07 |
| Yazılım | Python 3.12.6, torch 2.14.1+cu126, Ultralytics 8.4.183, OpenCV 4.10.0; Docker Desktop (WSL2); dashboard container sınırı 2 CPU / 1 GB; PostgreSQL 17 (izole `ppe-rel` yığını) |
| Model / ayarlar | YOLO26n `best.pt`, IMGSZ 640, CONF 0.25, ByteTrack, STREAM_WIDTH 640, kamera başına ayrı süreç (üretim mimarisi) |
| Kaynak | Yerel 1080p videolar, gerçek zaman hızında: V1 25 fps, V2 23.98 fps. 3.–6. kamera aynı iki videoyu **tekrar kullanır** (bağımsız içerik değil). Canlı RTSP kamera **test edilmedi**. |

**Gerçek zaman ile çevrimdışı ayrımı:** buradaki değerler gerçek zaman hızında oynatılan kaynaklarla, uçtan uca
canlı akış (dedektör → outbox/HTTP → dashboard → WebSocket) çalışırken ölçüldü. FAZ 8'deki "çevrimdışı hızlı
işleme" (tek akış 37–39 FPS) bir throughput ölçüsüdür ve kapasite kararı için kullanılmaz.

Tanımlar: *Etkin FPS* = işlenen kare/sn (stride sonrası). *Gereken FPS* = kaynak FPS / stride. *Lag* = ilk
kareden bu yana geçen gerçek süre − işlenen kaynak süresi (birikme; negatif = zamanlamanın önünde, dedektör
dosyayı gerçek zamana göre bekletir). *Başlangıç* = run başlangıcından ilk kareye (kaynak açma + model hazırlığı).
*Kare ms* = kare döngüsü (çözme + YOLO + takip + karar + çizim + kuyruk). Ölçüm penceresi senaryo başına
ısınmadan sonra 120 sn, 5 sn örnekleme.

## 2. Kamera başına sonuçlar (son kod, 16:38–17:02 UTC)

| Kamera | Stride | Kamera | Kaynak FPS | Gereken | Etkin FPS p50 (p05) | Lag p50/p95/max (sn) | Kare ms p95 | Çıkarım ms p50/p95 (p99 max) | ByteTrack p95 | PPEMonitor p95 | Başlangıç max (sn) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2 | relc1 | 25.0 | 12.50 | 12.42 (12.24) | -0.10/-0.08/-0.07 | 50.1 | 17.2/32.3 (34.4) | 1.33 | 0.048 | 2.8 |
| 2 | 2 | relc1 | 25.0 | 12.50 | 12.60 (12.42) | -0.10/-0.06/0.01 | 52.3 | 16.5/29.5 (44.8) | 1.65 | 0.064 | 2.9 |
| 2 | 2 | relc2 | 24.0 | 11.99 | 12.00 (10.58) | -0.09/-0.06/-0.04 | 51.8 | 16.5/30.9 (138.5) | 1.66 | 0.042 | 3.1 |
| 3 | 2 | relc1 | 25.0 | 12.50 | 12.61 (12.42) | -0.11/-0.08/-0.07 | 59.3 | 19.7/35.5 (64.8) | 2.15 | 0.075 | 2.9 |
| 3 | 2 | relc2 | 24.0 | 11.99 | 12.00 (11.65) | -0.11/-0.09/-0.09 | 58.5 | 19.3/37.5 (61.5) | 2.15 | 0.050 | 3.2 |
| 3 | 2 | relc3 (tekrar video) | 25.0 | 12.50 | 12.60 (10.70) | -0.09/-0.02/0.01 | 59.9 | 19.2/35.6 (63.8) | 2.03 | 0.075 | 4.8 |
| 4 | 2 | relc1 | 25.0 | 12.50 | 12.42 (9.08) | -0.13/-0.10/-0.09 | 71.0 | 23.4/43.6 (66.6) | 2.87 | 0.089 | 4.1 |
| 4 | 2 | relc2 | 24.0 | 11.99 | 11.94 (11.57) | -0.13/-0.11/-0.10 | 71.6 | 21.9/42.5 (61.0) | 3.05 | 0.063 | 3.8 |
| 4 | 2 | relc3 (tekrar video) | 25.0 | 12.50 | 12.61 (12.24) | -0.13/-0.11/-0.11 | 66.2 | 22.7/41.2 (66.0) | 2.73 | 0.088 | 5.5 |
| 4 | 2 | relc4 (tekrar video) | 24.0 | 11.99 | 12.00 (11.64) | -0.12/-0.09/-0.08 | 70.1 | 22.6/44.6 (62.6) | 2.83 | 0.071 | 7.9 |
| 5 | 2 | relc1 | 25.0 | 12.50 | 12.42 (8.92) | -0.13/-0.10/0.00 | 85.4 | 31.3/52.3 (70.1) | 3.75 | 0.109 | 3.3 |
| 5 | 2 | relc2 | 24.0 | 11.99 | 12.00 (11.32) | -0.16/-0.11/-0.04 | 83.2 | 29.5/49.7 (66.6) | 3.73 | 0.080 | 3.5 |
| 5 | 2 | relc3 (tekrar video) | 25.0 | 12.50 | 12.33 (8.00) | -0.17/-0.02/0.00 | 82.1 | 29.5/50.9 (68.2) | 3.68 | 0.112 | 5.9 |
| 5 | 2 | relc4 (tekrar video) | 24.0 | 11.99 | 11.85 (11.64) | -0.15/-0.10/-0.08 | 86.5 | 31.9/53.3 (70.7) | 3.68 | 0.075 | 6.8 |
| 5 | 2 | relc5 (tekrar video) | 25.0 | 12.50 | 12.60 (8.27) | -0.16/-0.02/0.02 | 84.5 | 29.8/52.0 (69.1) | 3.79 | 0.117 | 7.5 |
| 6 | 2 | relc1 | 25.0 | 12.50 | 12.03 (8.23) | **0.11/0.90/1.02** | 113.7 | 43.1/66.7 (88.7) | 5.30 | 0.153 | 4.1 |
| 6 | 2 | relc2 | 24.0 | 11.99 | 11.64 (10.51) | -0.16/0.25/0.27 | 110.2 | 43.2/66.2 (86.6) | 6.86 | 0.114 | 3.7 |
| 6 | 2 | relc3 (tekrar video) | 25.0 | 12.50 | 12.42 (10.53) | 0.05/0.45/0.73 | 108.9 | 41.7/66.0 (103.1) | 6.25 | 0.162 | 5.1 |
| 6 | 2 | relc4 (tekrar video) | 24.0 | 11.99 | 11.82 (10.86) | -0.12/0.08/0.21 | 107.8 | 42.3/64.2 (89.4) | 6.04 | 0.108 | 8.0 |
| 6 | 2 | relc5 (tekrar video) | 25.0 | 12.50 | 12.24 (10.56) | -0.06/0.74/1.04 | 107.4 | 43.8/63.6 (84.4) | 6.00 | 0.143 | 8.5 |
| 6 | 2 | relc6 (tekrar video) | 24.0 | 11.99 | 11.63 (5.80) | -0.09/0.41/0.51 | 109.7 | 43.5/66.3 (92.0) | 6.61 | 0.102 | 9.1 |
| 1 | 1 | relc1 | 25.0 | 25.00 | 25.00 (20.88) | -0.08/-0.07/-0.05 | 42.0 | 14.8/24.9 (32.6) | 1.29 | 0.057 | 3.2 |
| 2 | 1 | relc1 | 25.0 | 25.00 | 25.00 (21.15) | -0.10/-0.07/0.00 | 54.1 | 17.8/34.9 (43.6) | 1.96 | 0.083 | 2.9 |
| 2 | 1 | relc2 | 24.0 | 23.98 | 24.00 (21.00) | -0.09/-0.04/-0.02 | 53.9 | 17.6/35.8 (45.8) | 2.02 | 0.056 | 2.9 |
| 3 | 1 | relc1 | 25.0 | 25.00 | **23.28** (20.46) | **0.55/1.46/1.52** | 62.9 | 22.1/38.6 (54.0) | 2.43 | 0.104 | 2.9 |
| 3 | 1 | relc2 | 24.0 | 23.98 | **23.31** (19.84) | **0.66/1.35/1.41** | 62.5 | 22.5/41.0 (55.1) | 2.37 | 0.069 | 3.3 |
| 3 | 1 | relc3 (tekrar video) | 25.0 | 25.00 | **23.64** (19.69) | **0.59/1.59/1.76** | 62.5 | 22.3/38.4 (57.1) | 2.44 | 0.101 | 4.5 |

Not: Etkin FPS p05'teki düşüşler (ör. 8–9) her video döngüsünde yeni run başlangıcına (kaynak açma + takip sıfırlama,
2.8–9.1 sn) denk gelen örneklerdir; lag ilk kareden ölçüldüğü için bunlar birikme sayılmaz.

## 3. Sistem kaynakları ve uçtan uca yol

| Kamera | Stride | GPU % p50/p95 | GPU bellek MB max | Sistem CPU % p50/p95 | Dedektör CPU % p50 (çok çekirdek) | Dedektör RSS MB max (toplam) | Sistem RAM MB max | `/api/state` ms p50/p95 | DB olay yazımı ms p95 | Kare ingest ms p95 | Dashboard CPU % p95 / bellek MB | PG bağlantı max | Outbox max bekleyen | Teslim eksik | Kapanış |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2 | 27.5/47.5 | 217 | 17/23 | 74 | 1962 | 12900 | 6.8/10.4 | – (olay yok) | 2.5 | 16 / 72 | 2 | 0 | 0 | zarif 1.3 sn |
| 2 | 2 | 18.0/37.8 | 436 | 25/30 | 146 | 3449 | 13780 | 6.5/10.1 | 13.9 | 3.0 | 31 / 73 | 2 | 0 | 0 / 21 | zarif 1.6 sn |
| 3 | 2 | 29.5/45.5 | 660 | 37/58 | 244 | 4918 | 15099 | 8.8/21.4 | 22.8 | 6.2 | 43 / 87 | 3 | 0 | 0 / 22 | zarif 2.7 sn |
| 4 | 2 | 40.5/65.4 | 865 | 48/64 | 363 | 4909 | 15127 | 9.3/33.3 | 41.6 | 5.5 | 39 / 85 | 4 | 0 | 0 / 48 | zarif 2.1 sn |
| 5 | 2 | 67.5/87.8 | 1099 | 69/80 | 557 | 5346 | 15365 | 23.9/50.6 | 47.2 | 13.0 | 91 / 91 | 4 | 0 | 0 / 48 | zarif 3.6 sn |
| 6 | 2 | 83.0/92.0 | 1304 | **91/95** | 812 | 6016 | 15287 | 32.7/**180.6** | 64.9 | 33.4 | 115 / 92 | 5 | 1 | 0 / 67 | zarif 3.3 sn |
| 1 | 1 | 39.0/40.0 | 219 | 21/29 | 107 | 1996 | 11727 | 9.3/17.7 | – | 2.6 | 29 / 80 | 6 | 0 | 0 | zarif 1.2 sn |
| 2 | 1 | 47.5/55.0 | 436 | 38/45 | 254 | 3437 | 13747 | 13.7/18.9 | 64.1 | 5.3 | 36 / 81 | 6 | 0 | 0 / 32 | zarif 1.4 sn |
| 3 | 1 | 63.0/71.7 | 646 | 58/63 | 445 | 4902 | 14797 | 17.6/37.5 | 63.4 | 10.6 | 69 / 94 | 6 | 0 | 0 / 30 | zarif 1.8 sn |

"Teslim eksik" = outbox'a yazılmış olay kimliklerinden PostgreSQL'de bulunmayanlar / toplam olay (gerçek modelin
stok videolarda ürettiği olaylar; çoğu FAZ 8'de yanlış alarm adayı olarak belgelenen sahneler). DB yazım ve
`/api/state` değerleri `/api/ops/metrics` kayan penceresinden (son ~2048 istek) alınmıştır. WebSocket: tüm
senaryolarda oturumlu prob kare akışını aldı (en büyük kare arası boşluk ≤ 2.0 sn); probun kendi ping/pong
eksikliğinden kaynaklanan ~40 sn'lik 1011 kopmaları sunucu hatası değildir (bkz. PHASE_9_REPORT §5).

## 4. Ölçülmüş kapasite (bu cihaz, bu koşullar)

| Ayar | Gerçek zamanı sürdüren en yüksek kamera sayısı | İlk yetişemeyen | Darboğaz göstergesi |
|---|---|---|---|
| 1080p, VID_STRIDE=2 (üretim) | **5** (120 sn; lag p95 ≤ 0, FPS = gereken) | 6 (lag p95 0.1–0.9 sn, FPS %3 altında; sistem CPU p50 %91, `/api/state` p95 181 ms) | CPU (çözme + JPEG + Python, kamera başına ~1.4 çekirdek) ve GPU paylaşımı (çıkarım p50 17 → 43 ms); RAM dolu (sayfalama) |
| 1080p, VID_STRIDE=1 | **2** | 3 (FPS 23.3–23.6 / 24–25, lag 2 dakikada ~1.5 sn'ye çıkıyor) | CPU + GPU |

VRAM sınır değildir (kamera başına ~215 MB; 6 kamerada 1.3 GB / 4 GB). RAM sınırdır: kamera süreci başına
~1.0–1.6 GB RSS; 3 kameradan itibaren sistem belleği tamamen dolu ve sayfalama var (paylaşımlı makine).
**İşletme önerisi (karar değil):** bu dizüstüde üretim ayarıyla en fazla **4 kamera** (5'te CPU p95 %80, GPU p95 %88,
pay yok). 5 kamera yalnız 120 sn'de sürdürüldü; uzun sürede doğrulanmadı. Ayrılmış bir saha bilgisayarında aynı
ölçüm tekrarlanmalı. Canlı RTSP (ağ jitter'i, H.264/H.265 kod çözme, kopmalar) farklı sonuç verebilir.

## 5. Uzun süreli çalışma (soak)

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

## 6. Arıza sonrası toparlanma (ölçülen)

| Arıza | Toparlanma (arıza kalktıktan sonra kuyruk boşalması / yeniden bağlanma) | Kayıp / çift |
|---|---|---|
| PostgreSQL durdur (25 sn) / başlat | DB hazır olduktan 4.3 sn sonra kuyruk boş (geri çekilme) | 0 / 0 |
| Dashboard restart | hazır 1.3 sn, kuyruk 0.9 sn; WebSocket hemen yeniden bağlandı | 0 / 0 |
| Sentetik kamera zorla öldürme (10 bekleyen) | yeniden başlatmadan 1.0 sn sonra 10 replay | 0 / 0 |
| Gerçek dedektör zorla öldürme (5 bekleyen) | yeniden başlatmada "outbox kurtarma: 5 bekleyen" → teslim | 0 / 0 |
| Ağ zaman aşımı (20 sn) | 1.7 sn | 0 / 0 |
| ACK kaybı (15 sn) | 9.3 sn; sunucu 16 tekrarı ayıkladı | 0 / 0 |
| HTTP 503 (20 sn) | 1.3 sn | 0 / 0 |
| Yanlış token (401), canlı düzeltme | 16.8 sn (FAZ 9 düzeltmesinden önce ~48 sn) | 0 / 0 |
| HTTP 403 (15 sn) | 15.3–15.9 sn (`auth_retry` 30 sn penceresi) | 0 / 0 |
| Kapasite (OUTBOX_MAX_ITEMS=40, 45 sn 503) | 10.0 sn; `high_watermark` uyarısı; kritik yedek alan kullanıldı | 0 / 0 |
| Akış kopması (TCP benzetimi) | algılama 6.1 sn, geri gelince 9.1 sn'de yeni run | — |
| Donmuş akış (soket açık) | `stalled` ~6 sn; dashboard "izlenemiyor" 6 sn; çözülünce 3.0 sn | — |
| Container restart (gerçek dedektör çalışırken) | dashboard hazır 2.3 sn, DB 0.3 sn | 0 / 0 |

## 7. Önerilen hedef eşikler (ONAYLANMAMIŞ — İSG + işletme kararı)

Ölçülen taban çizgisine göre başlangıç önerisi; pilot (FAZ 10) verisiyle gözden geçirilmeli.

| Gösterge | Önerilen hedef | Ölçülen (bu cihaz) | Alarm/uyarı |
|---|---|---|---|
| Kamera izlenebilirliği (vardiya başına; `waiting_for_detector`/`no_signal`/`stalled`/`camera_disconnected` dışı süre) | ≥ %99 | ölçülmedi (canlı kamera yok) | izlenemezlik 5–6 sn içinde operatöre görünür (uygulandı; `STALE_AFTER`, `STALL_WARN_SEC`) |
| Kaynak gecikmesi (lag) p95 | < 1 sn | ≤ 0 (≤ 5 kamera, stride 2) | `lagging` ≥ 2 sn (uygulandı, `LAG_WARN_SEC`) |
| Kare döngüsü p95 | < 1000 / gereken FPS (stride 2'de < 80 ms) | 50–86 ms (1–5 kamera) | — |
| Olay → PostgreSQL teslimi (DB erişilebilirken) | p95 < 5 sn | DB yazım p95 14–65 ms; arıza sonrası ≤ 17 sn | outbox `pending_age` ≥ 300 sn (uygulandı) |
| Kritik olay (start/end/rapor) kaybı | 0 | 0 (tüm arıza senaryoları) | `messages_lost`, `memory_fallback`, `dead_letters` (uygulandı) |
| Çift kayıt oranı | 0 | 0 | — |
| Outbox kapasite kullanımı | < %50 normal işletmede | %0 (normal), %100+ yedek (kapasite testi) | `high_watermark` %80 (uygulandı) |
| Disk boş alanı (outbox) | > 1 GB | ölçüldü | `disk_free_low` < 1 GB (uygulandı) |
| Toplam alarm gecikmesi (ihlal başlangıcı → operatör ekranı) | İSG belirlemeli | karar gecikmesi ~3.2 sn (FAZ 8 sentetik) + teslim | — |
