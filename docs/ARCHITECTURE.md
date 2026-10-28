# Mimari

## Bileşenler

```
Kaynak (dosya / RTSP / webcam / YouTube)
   │
   ▼
detector/  (host, Python 3.12, GPU)            kamera başına 1 süreç
  YOLO (best.pt) → ByteTrack → PPEMonitor (ppe_core.py) → protocol.enrich_event → Sender (FIFO + retry)
   │  POST /ingest/frame  (multipart: JPEG kare + JSON olaylar)      X-Token: INGEST_TOKEN
   │  POST /ingest/report (JSON run raporu)
   ▼
dashboard/  (Docker, Python 3.11, FastAPI, tek uvicorn süreci)
  canlı:  RAM (CAMS, ALERTS) → /ws (1 bayt kamera idx + JPEG), /api/state, /video_feed/{cid}
  kalıcı: Pydantic doğrulama → repository (tek transaction) → PostgreSQL 17   [rol: ppe_app]
  geçmiş: db/history.py (salt okuma) → /api/history/*
  dosya:  data/snapshots/alert_<event_id>.jpg  (DB'de yalnızca göreli yol)
   │
   ▼
PostgreSQL 17 (Docker named volume ppe-split_pgdata, host'a port AÇILMAZ)
```

Canlı görüntü veritabanından bağımsızdır: DB kapalıyken kareler ve WebSocket çalışmaya devam eder; olay taşıyan
istekler ise `503` alır ve dedektör aynı olay kimlikleriyle tekrar dener.

## Kimlik modeli

| Kimlik | Üreten | Anlamı |
|---|---|---|
| `camera_id` | dedektör yapılandırması | Mantıksal kamera. `[kimlik]` önekiyle **sabit**; önek yoksa `CAMERA_ID_MODE` (aşağıda) |
| `run_id` | dedektör, her `run_once()` | Bir kaynağın bir oynatımı / bağlantı oturumu (döngü ve yeniden bağlanma = yeni run) |
| `pid` (API'de `track_id`) | ByteTrack | Yalnızca o run içindeki iz numarası. **Kişi kimliği değildir** |
| `incident_id` | dedektör `IncidentTracker` | Bir ihlal yaşam döngüsü: start → ongoing… → end |
| `event_id` | dedektör, olay doğarken | Tek olay; retry'da değişmez (idempotency, `UNIQUE`) |

### Kamera kimliği (FAZ 5)

`protocol.camera_entries()`:

1. `[giris]Giriş kapısı=rtsp://…` → kimlik `giris` (kullanıcının açık eşlemesi, önerilen).
2. Önek yoksa `CAMERA_ID_MODE=source` → kimlik kaynaktan türetilir: dosya adı (`5434223-hd_1920_1080_24fps`),
   webcam (`webcam0`), URL (`rtsp_<maskeli adresin SHA-256 özeti>`; parola özete girmez).
3. Önek yoksa ve mod `index` (varsayılan, eski davranış) → `cam1`, `cam2`… Kaynak sırası değişirse kimlik başka
   kaynağa geçer; dedektör açılışta her biri için **UYARI** basar.

Tekrarlanan veya geçersiz kimlik dedektörü başlatmaz (çıkış kodu 2).

### Run'a ait kaynak bilgisi (migration 0003)

`cameras.name` kameranın **güncel** adıdır. Geçmiş kayıtlar run'ın kendi donmuş bilgisini gösterir:
`detector_runs.source_label`, `source_kind`, `source_ref` (dosyada yalnız dosya adı, URL'de maskeli adres).
Değerler run'ın ilk kaydında yazılır ve sonra değişmez. 0003 öncesi run'larda bu alanlar `NULL`dur; arayüz
"Eski kayıt: kaynak doğrulanamadı" gösterir, güncel kamera adını geçmişe atfetmez. Geri doldurma yapılmadı.

## İhlal (incident) durumu

Olaylardan türetilir (`db/history.py`); filtre, sayfalama ve özet aynı SQL tanımını kullanır.

| Durum | Koşul |
|---|---|
| `closed` | start ve end olayı var |
| `orphan` | start olayı yok |
| `active` | end yok, run `running`, run'ın son canlılık kaydı **ve** ihlalin son olayı `ACTIVE_WINDOW_SEC` (90 sn) içinde |
| `stale` | end yok, run tamamlandı, rapor `unclosed_incidents` içinde `reason=stale` |
| `run_end` | end yok, run tamamlandı, raporda `reason=run_end` |
| `unknown` | diğer her şey; `status_reason`: `heartbeat_lost`, `no_recent_event`, `superseded`, `missing_metadata`, `metadata_overflow`, `not_reported` |

End'i olmayan ihlal canlılık kanıtı yoksa asla `active` sayılmaz. Çöken dedektörün run'ı `running` kalır
(rapor gelmez); pencere dolunca ihlalleri `unknown / heartbeat_lost` olur (gerçek GPU testinde doğrulandı).

Sayım: **doğrulanmış ihlal** = start olayı olan incident. Ham olay sayısı ayrıca raporlanır; ongoing tekrarları ihlal
sayısını artırmaz. Bir ihlal başladığı tarih aralığına sayılır. Eksik ekipman incident boyunca görülenlerin
birleşimidir (`missing_at_start` ayrıca verilir).

## Teslim garantisi ve canlılık

- Dashboard `2xx`'i yalnızca DB commit'inden sonra döner. Aynı `event_id` tekrar gelirse yeni satır ve yeni canlı
  uyarı oluşmaz; farklı içerikle gelirse `409`.
- Olaysız karelerde kamera/run canlılığı (`last_seen_at`) en fazla `HEARTBEAT_SEC`'te (30 sn) bir ve **arka planda**
  yazılır: DB yavaş/kapalıyken canlı kare isteği beklemez (kamera başına tek bekleyen yazım).
- Dedektör olayları yalnızca bellekte tutar. Ctrl+C'de kuyruk `DRAIN_TIMEOUT` (10 sn) boyunca boşaltılır ve run
  `interrupted` raporlanır (gerçek Ctrl+C testi: 2 sn'de temiz kapanış). Süreç zorla öldürülürse kuyruk kaybolur.

## Bilinen sınırlamalar

- Snapshot dosyası DB commit'inden önce yazılır; commit başarısızsa sahipsiz dosya kalabilir
  (`maintenance.storage snapshots` raporlar, silmez).
- Tek uvicorn süreci varsayımı (snapshot kilidi süreç içidir).
- `cam<n>` kimlikli eski kayıtlarda aynı kimlik farklı videolara ait olabilir; kamera bazlı dağılım kimliğe göre
  gruplandığından bu kayıtlar birlikte sayılır.
- WebSocket kareyi yalnızca değişince gönderir; karo `/api/state` ile oluşmadan gelen ilk kare atlanır (sürekli
  akışta görünmez).
