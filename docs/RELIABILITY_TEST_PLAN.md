# Güvenilirlik, yük ve arıza testi planı (FAZ 9)

Kapsam: dedektör (YOLO26n + ByteTrack + PPEMonitor + IncidentTracker), kalıcı outbox (SQLite WAL), dashboard
(FastAPI) ve PostgreSQL 17'nin **çoklu kamera, uzun süreli çalışma ve arıza** koşullarındaki davranışı.
Model doğruluğu bu planın konusu değildir (bkz. `MODEL_EVALUATION_PROTOCOL.md`). Sonuçlar:
`PHASE_9_REPORT.md`, ölçülmüş taban çizgisi: `PERFORMANCE_BASELINE.md`.

## 1. İlkeler

1. **Yıkıcı testler yalnız izole yığında.** `tools/reliability/docker-compose.reliability.yml` ayrı bir compose
   projesidir (`ppe-rel`): ayrı PostgreSQL volume'u (`ppe-rel_rel_pgdata`), ayrı snapshot volume'u, ayrı port
   (`127.0.0.1:8100`), her kurulumda rastgele üretilen parolalar/token (`tools/reliability/output/rel.env`, Git dışı).
   Ana yığın (`ppe-split`), ana veritabanı, ana `detector/outbox` dizini ve snapshot'lar **hiçbir testte**
   durdurulmaz, değiştirilmez, silinmez. `docker compose down -v` / `prune` kullanılmaz.
2. **Gerçek kod yolu.** Yük testleri gerçek `detector.py` (gerçek model, GPU), arıza testleri ya gerçek dedektör ya da
   YOLO yerine senaryolu tespit üreten fakat geri kalan zinciri **üretim modülleriyle** çalıştıran sentetik kamera
   (`eventgen.py`: `PPEMonitor` → `enrich_event`/`IncidentTracker` → `DurableSender` → SQLite outbox → HTTP) kullanır.
3. **Kayıp ölçümü defterle.** Sentetik kamera, outbox'a COMMIT edilmiş her olay/rapor kimliğini fsync'li bir deftere
   (`ledger.jsonl`) yazar. Test sonunda defter PostgreSQL ile karşılaştırılır: eksik (kayıp), fazla, çift satır.
   Gerçek dedektör senaryolarında karşılaştırma outbox dosyasındaki `msg_id`'lerle yapılır.
4. **Uydurma yok.** Yapılmayan test SKIP/BLOCKED/NOT RUN; süre ve koşullar UTC başlangıç/bitişle yazılır; kısa testten
   bellek sızıntısı sonucu çıkarılmaz; önceden belirlenmemiş SLO "karşılandı" denmez.
5. **Gizlilik.** Ölçümlerde ve çıktılarda görüntü içeriği, kaynak adresi, token, kişi kimliği yok. Track sayısı
   benzersiz kişi sayısı olarak sunulmaz. Test videoları yereldeki stok videolardır; dış servise gönderilmez.

## 2. Gözlem (observability) bileşenleri

| Katman | Nerede | Ne |
|---|---|---|
| Dedektör | `detector/telemetry.py`, `OUTBOX_DIR/<kamera>.health.json` (HEALTH_EVERY sn), kare meta'sında `health` | çalışma süresi, kamera durumu, işlenen/kaynak FPS, kare süresi p50/p95/p99, gecikme (lag), YOLO/ByteTrack/PPEMonitor süreleri, CPU/RAM/tanıtıcı/thread, GPU kullanım/VRAM, sinyal yok/donmuş kare, **analiz kapsamı** |
| Outbox | `outbox.py` metrikleri, `<kamera>.status.json`, kare meta'sında `outbox` | pending_count/bytes, oldest_pending_age, retry, delivered, replay_success_count, dead/quarantine, disk_write_errors, WAL/dosya boyutu, disk boş alanı, uyarılar |
| Dashboard | `GET /api/ops/metrics` (yalnız admin) | route şablonu başına istek/gecikme p50/p95/p99, 401/403/409/413/422/503 sayıları, ingest başarı/hata, olay alındı/eklendi/tekrar, DB yazım gecikmesi, WebSocket bağlantıları, geçmiş API gecikmesi, PostgreSQL hazır/havuz, snapshot deposu, kamera başına canlı durum |
| Arayüz | Canlı izleme karoları | "kamera izlenemiyor" (dedektör yok / sinyal yok / bağlantı koptu / donmuş), "N kişi değerlendirilemiyor (neden)", gecikme, olay kuyruğu birikmesi |

## 3. Yük matrisi (çoklu kamera)

| Değişken | Değerler |
|---|---|
| Kamera sayısı | 1, 2, 3, 4 (her kamera ayrı süreç + ayrı model, üretim mimarisi) |
| VID_STRIDE | 2 (üretim) ve 1 (ayrı deney) |
| Kaynak | Yerel 1080p videolar (V1 25 fps, V2 23.98 fps) gerçek zaman hızında; 3. ve 4. kamera aynı videoları **yeniden kullanır** (sonuçta `reused_video` olarak işaretli) |
| Süre | Isınmadan sonra 120 sn ölçüm / senaryo (5 sn örnekleme) |
| Ölçülen | kamera başına etkin FPS, lag p50/p95/max, kare süresi p95/p99, çıkarım p50/p95/p99, ByteTrack/PPEMonitor p95, GPU kullanım/VRAM, CPU, RAM, outbox birikmesi, dashboard `/api/state` gecikmesi, DB yazım gecikmesi, WebSocket kare akışı, teslim (outbox ↔ DB) |
| Komut | `python tools/reliability/stress.py --matrix 1:2,2:2,3:2,4:2,1:1,2:1,3:1 --duration 120` |

Kapasite kararı ölçütü (öneri, SLO değil): kamera başına etkin FPS ≥ kaynak FPS / stride × 0.95 ve lag p95 < 1 sn ve
lag eğimi ≈ 0 (sürekli artmıyor). Bu koşulu sağlayan en büyük kamera sayısı "ölçülmüş kapasite" olarak raporlanır.

## 4. Uzun süreli (soak) test

| Aşama | Süre | Durum ölçütü |
|---|---|---|
| Duman | 30 sn, 1 kamera | araç zinciri, zarif kapanış, teslim |
| Stabilite | 1 saat, 3 kamera, stride 2 | bellek/tanıtıcı/VRAM/WAL/DB bağlantısı/lag eğimi, teslim, WebSocket kopması, reconnect |
| Soak | 4 saat | yalnız gerçekten koşulursa raporlanır |
| Uzun soak | 24 saat | yalnız gerçekten koşulursa raporlanır |

Komut: `python tools/reliability/stress.py --matrix 3:2 --duration 3600 --interval 15 --name soak1h`.
Zaman serisi: `samples.jsonl` + `samples.csv`. Eğimler doğrusal regresyonla (birim/saat) hesaplanır; 1 saatlik veriden
yalnız "1 saat içinde belirgin artış var/yok" sonucu çıkarılır, 24 saatlik sızıntı yokluğu iddia edilmez.

## 5. Arıza enjeksiyonu

`python tools/reliability/failures.py [--only A,B,...]`. Her senaryo: arıza öncesi akış → arıza → kaldırma →
kuyruk boşalması → doğrulama (defter ↔ DB).

| Kod | Arıza | Yöntem | Beklenen |
|---|---|---|---|
| A | PostgreSQL durdur/başlat | `docker stop/start ppe-rel-db-1` (25 sn) | `/health/ready` 503, `/healthz` 200; olaylar outbox'ta birikir (geri çekilme); DB dönünce sıra korunarak teslim; kayıp 0, çift 0 |
| B | Dashboard yeniden başlatma | `docker restart ppe-rel-dashboard-1` | bağlantı reddi → birikme → teslim; RAM'deki canlı uyarı listesi sıfırlanır (geçmiş DB'de) |
| C | Süreç zorla öldürme | sentetik kamera DB'ye ulaşamazken `taskkill /T /F`, aynı outbox ile yeniden başlatma | bekleyenler aynı kimliklerle replay; `replay_success_count` > 0; kayıp 0, çift 0 |
| D | Kamera/akış kopması | gerçek dedektör + ffmpeg MPEG-TS/TCP ağ akışı (RTSP sunucusu yok → benzetim); ffmpeg öldürülür, 25 sn sonra yeniden başlatılır | dedektör "bağlantı koptu/sinyal yok" işaretler, operatör ekranında "izlenemiyor", aynı kamera kimliğiyle yeni run, sabit 3 sn yeniden deneme |
| E1 | Ağ zaman aşımı | arıza vekili `hang` (20 sn) | istemci zaman aşımı (5 sn) → geri çekilme → teslim; kayıp 0 |
| E2 | ACK kaybı | vekil `ack_loss`: sunucu işler, yanıt kaybolur | aynı gövde yeniden gönderilir; DB'de çift satır 0 (`events_duplicate_ignored` artar) |
| F | HTTP 503 | vekil `status:503` (20 sn) | geri çekilme, sonra teslim; kayıp 0 |
| G1 | 401 (gerçek yanlış token) | yanlış token ile başla, 15 sn sonra token dosyasını düzelt (yeniden başlatma yok) | gönderim DURAKLAR, olaylar korunur; ≤ 30 sn içinde token yeniden okunur ve teslim |
| G2 | 403 | vekil `status:403` (15 sn) | duraklatma, olay düşürülmez |
| H | SQLite kilitli | aynı kamera için ikinci süreç | ikinci süreç `OutboxLocked` ile başlamaz; birincisi etkilenmez |
| I | Disk/kapasite sınırı | `OUTBOX_MAX_ITEMS=40` + 45 sn 503 | high_watermark uyarısı, yalnız `ongoing` seyreltilir, start/end kaybı 0; yedek alan dolarsa bellek yedeği + görünür hata |
| J | Bozuk/büyük payload | geçersiz JSON, yanlış tip, kötü kamera kimliği, bozuk snapshot, 201 olay, 9 MiB gövde, token yok | 422/413/401; DB'ye satır yok; servis ayakta |
| K | WebSocket kopması | B sırasında oturumlu WebSocket probu | kopma algılanır, istemci yeniden bağlanır, kare akışı sürer |
| L | Container restart (gerçek dedektör) | dedektör çalışırken dashboard, sonra DB container'ı yeniden başlatılır | dedektör etkilenmez; olaylar outbox ↔ DB eşit; çift 0 |
| M | Gerçek dedektör zorla öldürme | 503 sırasında `taskkill /T /F`, aynı outbox ile yeniden başlat | replay; öldürülen run raporsuz `running` kalır (uzlaştırma adayı); kayıp 0 |
| — | Gerçek disk doldurma, gerçek RTSP kamera, Windows uyku/yeniden başlatma, GPU sıfırlama/OOM | — | **SKIP** (ortamda güvenli yapılamaz; ayrı bakım penceresi gerekir) |

## 6. Kabul ölçütleri (test geçti/kaldı)

| Ölçüt | Eşik |
|---|---|
| Outbox'a commit edilmiş olay/rapor kaybı (her arıza) | 0 |
| DB'de aynı `event_id` ile çift satır | 0 |
| Arıza sırasında olay düşürme (start/end/rapor) | 0 (yalnız kapasite testinde `ongoing` seyreltme beklenir ve sayılır) |
| Kamera izlenemiyorken arayüz | "izlenemiyor" açıkça görünür, "ihlal yok" gibi görünmez |
| Arıza kaldırıldıktan sonra kuyruğun boşalması | ölçülür ve raporlanır (önceden SLO yok; öneri PERFORMANCE_BASELINE.md) |
| Dedektör/dashboard regresyonu | tüm testler PASS |

## 7. Çıktılar ve saklama

Tüm ham çıktılar `tools/reliability/output/<UTC>_<ad>/` (Git dışı): `result.json`, `samples.jsonl`,
`samples.csv`, `timeline.json`, `ledger.jsonl` (yalnız kimlikler), süreç logları, izole outbox dosyaları (sentetik
görüntü). İzole yığın volume'ları test sonrası **silinmez**; temizlemek kullanıcı kararıdır:
`docker compose -p ppe-rel -f tools/reliability/docker-compose.reliability.yml down` (verisi kalır) ve gerekirse
`docker volume rm ppe-rel_rel_pgdata ppe-rel_rel_data` (yalnız test verisi).
