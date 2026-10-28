# PPE (KKD) İzleme: 2 parça

| Parça | Nerede çalışır | Ne yapar |
|---|---|---|
| `dashboard/` | Docker | Tarayıcı arayüzü, video akışı, uyarılar |
| `detector/` | Bilgisayarda doğrudan | YOLO + takip + KKD kararı, sonucu dashboard'a gönderir |

```
ppe-split/
├── docker-compose.yml
├── dashboard/        (app.py, Dockerfile, static/index.html)
└── detector/         (detector.py, ppe_core.py, models/best.pt, videos/video.mp4)
```

## Gereksinimler
- Docker Desktop
- Python **3.12** (3.13 ve üstü bazı paketlerde sorun çıkarabilir; 3.15'te `torch` kurulmaz)
- `detector/models/best.pt` ve `detector/videos/video.mp4` dosyaları

---

## A) İLK KURULUM (yalnızca bir kez)

### 1. Dashboard'u ve veritabanını başlat
Proje klasöründe (`docker-compose.yml`'in olduğu yer) önce `.env` oluştur; `POSTGRES_PASSWORD`, `PPE_APP_PASSWORD`, `PPE_MIGRATOR_PASSWORD` ve `INGEST_TOKEN` değerlerini üret (`python -c "import secrets; print(secrets.token_urlsafe(32))"`). `.env` Git'e eklenmez. Aynı `INGEST_TOKEN`'ı `detector/.env` içine de yaz.

    Copy-Item .env.example .env        # Mac/Linux: cp .env.example .env
    docker compose up -d --build

Bu komut PostgreSQL 17'yi (`db`) başlatır, veritabanı rollerini ayırır (`db-roles`: tablolar `ppe_migrator`'a ait, dashboard yalnız SELECT/INSERT/UPDATE yetkili `ppe_app` ile bağlanır), şemayı Alembic ile kurar (`migrate`) ve ardından dashboard'u açar. Kontrol: `docker compose ps` ve http://127.0.0.1:8000/health/ready (`"status":"ready"`).

Tarayıcıda http://localhost:8000 aç. Bu aşamada "Dedektör bağlı değil" yazması normaldir. Dashboard varsayılan olarak yalnızca bu bilgisayardan erişilir; PostgreSQL portu dışarı açılmaz.

Testler (gerçek PostgreSQL, ayrı `ppe_test` veritabanı): `docker compose --profile test run --rm tests`

**Belgeler:** [Mimari](docs/ARCHITECTURE.md) · [Veritabanı ve roller](docs/DATABASE.md) · [Güvenlik](docs/SECURITY.md) · [Yedekleme/geri yükleme](docs/BACKUP_RESTORE.md) · [Kurulum/işletim](docs/DEPLOYMENT.md) · [Test raporu](docs/TEST_REPORT.md)

**Kamera kimliği:** Her kaynağa sabit bir kimlik verin: `VIDEO_SOURCES=[giris]Giriş kapısı=rtsp://...,[kule]videos/kule.mp4`. Kimlik kaynak sırası değişse de aynı kalır. Önek yoksa eski davranış (`cam1`, `cam2`… sıraya bağlı) kullanılır ve dedektör açılışta uyarır; `CAMERA_ID_MODE=source` ile kimlik kaynaktan türetilir. Geçmiş kayıtlar run'ın kendi kaynak etiketiyle gösterilir; FAZ 5 öncesi kayıtlar "Eski kayıt: kaynak doğrulanamadı" olarak görünür.

**Yedek:** `python scripts/backup.py --quiesce` (PostgreSQL + snapshot'lar → `backups/`), doğrulama: `python scripts/restore_check.py backups\ppe-<zaman>` (izole veritabanına yükler, ana DB'ye dokunmaz).

**Kalıcı kayıt:** İhlal olayları (`violation_events`), ihlal yaşam döngüleri (`incidents`), çalışmalar (`detector_runs`) ve kişi raporları (`person_run_reports`) PostgreSQL'e yazılır; snapshot JPEG'leri `data/snapshots/alert_<event_id>.jpg` dosyasındadır (DB'de yalnızca göreli yol). Dashboard yalnızca kayıt başarıyla commit edildikten sonra `200` döner; DB erişilemezse olaylı istekler `503` alır ve dedektör aynı olay kimlikleriyle tekrar dener. Canlı görüntü DB'den bağımsızdır. Kimliği olmayan (eski sürüm dedektör) olaylar `422` ile reddedilir.

Dashboard sınırları (kök `.env` / compose ortamı): `MAX_FRAME_BYTES` (2 MiB), `MAX_SNAPSHOT_BYTES` (1 MiB), `MAX_EVENTS_PER_REQUEST` (200), `MAX_REPORT_BYTES` (1 MiB), `MAX_REPORT_ROWS` (5000), `HEARTBEAT_SEC` (30). Olay payload'ı form alanı olarak Starlette tarafından 1 MiB ile sınırlıdır.

**Geçmiş ve istatistik (PostgreSQL'den):** Dashboard'da *İhlal Geçmişi*, *İstatistikler* ve *Kamera / Run Raporları* sekmeleri. Her satır bir **ihlaldir** (incident: aynı `incident_id`'li başlangıç → sürüyor → bitiş olayları); ham olay sayısı ayrıca gösterilir, "sürüyor" tekrarları ihlal sayısını artırmaz. İz ID, run içindeki ByteTrack numarasıdır; kişi kimliği değildir. API (salt okuma, JSON):

| Uç | Açıklama |
|---|---|
| `GET /api/history/incidents` | `camera_id`, `run_id`, `from`, `to` (ISO 8601, saat dilimi zorunlu; `from` dahil, `to` hariç; ihlalin başladığı ana göre), `status` (tekrarlanabilir), `equipment` (`helmet`, `vest`, `both`, `helmet_only`, `vest_only`), `limit` (1–200), `cursor` (`next_cursor` değeri) |
| `GET /api/history/incidents/{id}` | Ayrıntı + olay zaman çizelgesi, kamera, run, snapshot, rapordaki kapanış nedeni |
| `GET /api/history/summary` | Doğrulanmış ihlal (başlangıç olayı olan), baret/yelek/ikisi, durum dağılımı, kamera dağılımı, günlük trend (`tz`, ör. `Europe/Istanbul`), ham olay sayıları |
| `GET /api/history/cameras` | Kayıtlı kameralar |
| `GET /api/history/runs`, `GET /api/history/runs/{id}` | Run geçmişi (`camera_id`, `from`, `to`, `status`, sayfalama), kişi raporu ve ihlal sayıları |

İhlal durumları: `closed` (bitiş olayı var), `active` (bitiş yok, run çalışıyor ve hem run'dan hem ihlalden son `ACTIVE_WINDOW_SEC` (varsayılan 90 sn) içinde kayıt geldi), `stale` / `run_end` (bitiş yok, run raporu bu nedenle kapattığını bildirdi), `orphan` (başlangıç olayı yok), `unknown` (kesin değil; `status_reason` nedenini verir: canlılık sinyali yok, rapor listesi taşmış, raporda yok vb.). Bitiş olayı olmayan ihlal yalnızca canlılık kanıtı varsa `active` sayılır. Geçmiş uçları da canlı uçlar gibi kimlik doğrulamasızdır; dashboard'u `127.0.0.1` dışına açmayın.

**INGEST_TOKEN:** Boşsa ingest uçları kimlik doğrulamasızdır (dashboard yalnızca `127.0.0.1`'e bağlıyken kabul edilebilir). Doluysa kök `.env` ve `detector/.env` içinde **aynı** olmalıdır; uyuşmazsa dashboard `401` döner ve dedektör o olayları **düşürür** (log'da `INGEST_TOKEN` uyarısı görünür).

### 2. Dedektörün ortamını kur
**Mac / Linux:**

    cd detector
    python3.12 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt

**Windows (PowerShell):**

    cd detector
    py -3.12 -m venv .venv
    .venv\Scripts\activate
    pip install --upgrade pip
    pip install -r requirements.txt

NVIDIA ekran kartı varsa, `pip install -r requirements.txt` öncesinde https://pytorch.org/get-started/locally/ adresindeki CUDA'lı `torch` komutunu çalıştır.

### 3. İlk çalıştırma

    python detector.py

(Cihazı elle vermek için aşağıdaki "Cihaz seçimi" bölümüne bak.)

---

## B) SONRAKİ ÇALIŞTIRMALAR (her seferinde)

Kurulum bir kez yapıldığı için sadece şunlar gerekir.

**1. Dashboard (kapalıysa aç):**

    cd ppe-split
    docker compose up -d

**2. Dedektör (ayrı terminalde):**

Mac / Linux:

    cd ppe-split/detector
    source .venv/bin/activate
    DEVICE=mps python detector.py

Windows (PowerShell):

    cd ppe-split\detector
    .venv\Scripts\activate
    $env:DEVICE="0"; python detector.py

**3. Tarayıcı:** http://localhost:8000. Durum "Canlı" olunca video akar.

**Kapatmak için:** dedektör terminalinde `Ctrl+C`, dashboard için `docker compose down`.

---

## Cihaz seçimi (konsoldan DEVICE verme)

`DEVICE` verilmezse program otomatik seçer: NVIDIA → Apple GPU (`mps`) → CPU.
Elle vermek için komutun başına yaz. Açılıştaki `Cihaz: ...` satırından kontrol et.

| Bilgisayar | Komut |
|---|---|
| Mac (Apple Silicon, GPU) | `DEVICE=mps python detector.py` |
| Mac / herhangi biri (sadece CPU) | `DEVICE=cpu python detector.py` |
| Linux + NVIDIA | `DEVICE=0 python detector.py` |
| Windows PowerShell + NVIDIA | `$env:DEVICE="0"; python detector.py` |
| Windows CMD + NVIDIA | `set DEVICE=0 && python detector.py` |

Birden fazla ayarı birlikte verebilirsin:

    DEVICE=mps IMGSZ=640 VID_STRIDE=2 python detector.py

Kamera / RTSP kullanmak için:

    DEVICE=mps VIDEO_SOURCE=0 python detector.py
    DEVICE=mps VIDEO_SOURCE="rtsp://kullanici:sifre@ip:554/stream" python detector.py

### Alternatif: `.env` dosyası
Her seferinde yazmak istemezsen `detector/.env` oluştur (örnek: `.env.example`). `detector.py` bunu otomatik okur. Terminalde verdiğin değişken `.env`'den önceliklidir.

    DEVICE=mps
    IMGSZ=640
    VID_STRIDE=2

---

## Ayarlar

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `DEVICE` | otomatik | `mps`, `0` (NVIDIA) veya `cpu` |
| `MODEL_PATH` | `models/best.pt` | Model dosyası |
| `VIDEO_SOURCE` | `videos/video.mp4` | Dosya, `0` (webcam) veya RTSP adresi |
| `CAMERA_ID_MODE` | `index` | Öneksiz kaynaklar için: `index` (cam1, cam2… sıraya bağlı) veya `source` (kaynaktan türetilir) |
| `DASHBOARD_URL` | `http://localhost:8000` | Dashboard adresi |
| `IMGSZ` | `960` | Düşürürsen hızlanır (`640`), doğruluk azalabilir |
| `VID_STRIDE` | `1` | Her N. kareyi işle (`2` ≈ 2 kat hızlı) |
| `STREAM_WIDTH` | `960` | Dashboard'a giden kare genişliği |
| `LOOP` | `1` | Video bitince başa sar |
| `INGEST_TOKEN` | boş | Doluysa dashboard (compose) ile aynı olmalı |
| `SEND_TIMEOUT` | `5` | Dashboard HTTP isteği zaman aşımı (sn) |
| `EVENT_QUEUE_MAX` | `2000` | Kamera başına bellekte bekleyebilecek en fazla olay. Dolarsa önce en eski `ongoing` atılır; atılacak `ongoing` yoksa yeni olay reddedilir (log + sayaç) |
| `DRAIN_TIMEOUT` | `10` | Ctrl+C / kapanışta bekleyen olayları göndermek için en fazla süre (sn) |

**Olay teslimi:** Her ihlal olayı `event_id`, `run_id`, `incident_id`, `camera_id` ve UTC `occurred_at` ile üretilir; dashboard'a ulaşılamazsa aynı kimliklerle tekrar denenir. Bekleyen olaylar yalnızca **bellekte** tutulur: Ctrl+C ile kapatınca en fazla `DRAIN_TIMEOUT` saniye gönderilmeye çalışılır, ama süreç zorla kapatılır veya çökerse gönderilmemiş olaylar kaybolur.

Dedektör birim testleri (GPU gerekmez): `cd detector; .venv\Scripts\python -m pip install -r requirements-dev.txt; .venv\Scripts\python -m pytest`

## Sorun giderme
- **`Cihaz: cpu` yazıyor:** `DEVICE=mps python detector.py` ile dene. Kontrol: `python -c "import torch; print(torch.backends.mps.is_available())"` → `True` olmalı.
- **`No matching distribution found for torch`:** Python sürümü çok yeni. 3.12 ile `.venv`'i yeniden oluştur (`rm -rf .venv`).
- **Dashboard'da "Dedektör bağlı değil":** `detector.py` çalışıyor mu, `docker compose ps` ile dashboard açık mı bak.
- **Kare hızı düşük:** `IMGSZ=640 VID_STRIDE=2 STREAM_WIDTH=800` dene.