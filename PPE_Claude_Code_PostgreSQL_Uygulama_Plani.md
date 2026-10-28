# PPE Split — PostgreSQL 17 Entegrasyonu / Claude Code Opus Uygulama Planı

**Kaynak:** Kullanıcının sağladığı `ppe-split-main.zip` içindeki 15 ZIP girdisi (11 dosya, 4 klasör), 28 Ekim 2026'da kod incelemesi. 3 Python dosyasının sözdizimi kontrolü başarılıdır. Sonradan sağlanan `best.pt.zip` dosyasının PyTorch checkpoint yapısı ve model meta verileri **çalıştırılmadan** kontrol edildi. Model mevcut, ancak test video/kamera verisi yok; gerçek YOLO/ByteTrack çıkarımı bu ortamda çalıştırılmadı.

## 1. Gerçek kod tabanı ve entegrasyon noktaları

- `detector/detector.py`: Python 3.12 ortamında ana makinede çalışır; `multiprocessing` ile kamera başına ayrı süreç/YOLO/ByteTrack başlatır. `run_once()` her kaynak döngüsünde `PPEMonitor()` örneği oluşturur. `Sender` adlı thread son JPEG kareyi ve bekleyen olayları `/ingest/frame` adresine multipart HTTP ile yollar; `/ingest/report` kamera raporunu gönderir.
- `detector/ppe_core.py`: `PPEMonitor.update(t, frame_wh, persons, helmet_map, vest_map)` durumları ve `start`, `ongoing`, `end` olaylarını üretir. `_rate` ve eşik hesapları ile eşleştirme algoritması **değiştirilmeyecek**. Baret sınıfı 0, yelek 2, insan 6; bunlar konfigürasyondandır.
- `dashboard/app.py`: Python 3.11, FastAPI, Docker. `CAMS`, `ALERTS`, `ALERT_SEQ` tamamen bellek içi; en fazla 300 uyarı. Snapshot `DATA_DIR/snapshots` altında dosya. `/api/state?after=`, `/api/alerts`, `/api/report`, `/ws`, `/video_feed/{cid}`, `/healthz` mevcut.
- `dashboard/static/index.html`: tek WebSocket üzerinden 1 byte kamera indeksi + JPEG alır. `/api/state` endpoint'ini 700 ms aralıklarla sorgular. İhlali ekranda gösterir; sayacı tarayıcıda `start` olayları üzerinden artırır. "Listeyi temizle" sadece tarayıcı DOM'unu temizler; kalıcı verinin silinmesi anlamına gelmemeli.
- `docker-compose.yml`: yalnızca `dashboard` servisi, `./data:/data` klasör mount'u; PostgreSQL yok. `dashboard/requirements.txt` içerisinde SQLAlchemy, psycopg veya Alembic yok.
- `detector/.env.example`: çoklu video / RTSP / YouTube kaynak örnekleri ve bazı makineye özel yollar var. Gizli erişim bilgilerini DB'ye ve loglara kopyalama.

### İncelemede belirlenen kritik mühendislik riskleri

1. Dashboard restart'ında `ALERTS` ve tüm raporlar kaybolur.
2. Sender zaman aşımı sonrası yeniden gönderir; sunucu commit'i tamamlamışken istemci yanıt alamadıysa çift kayıt oluşabilir. **Her olay için kalıcı `event_id` (UUID) + UNIQUE/PK** gerekir.
3. `pid` ByteTrack kimliğidir, kimlik doğrulama/gerçek kişi tanıma değildir. **`run_id + camera_id + pid`** bağlamı gerekir; `run_id` her video tekrarında ve yeni bağlantı/işleme oturumunda değişmelidir.
4. `t` video akışında işlenen kare ilerlemesinden türetilen saniyedir; gerçek dünya UTC zamanı değildir. İkisi ayrı alan olarak saklanmalıdır.
5. Sender farklı run'lardan bekleyen `events` listesini en son gönderilen frame payload'ıyla birleştiriyor. Yeniden bağlantıda eski olaylar yeni `run_id` ile yanlış etiketlenmemeli; run bilgisi **olay oluşturulduğu anda immutable** olmalı.
6. `post_report()` gönderici thread'i dışında aynı `requests.Session`'ı kullanıyor, ayrıca son olay gönderimi ile raporun sırası garanti değil. Kuyruk/işlem sırası ve session ownership netleştirilmeli.
7. `/ingest/frame` şu anda JSON'u doğrudan çözüp snapshot base64 verisini boyut doğrulaması olmadan diske yazıyor. Boyut sınırı, şema doğrulaması, hataya dayanıklı disk/DB işleme eklenmeli.
8. `INGEST_TOKEN` default boş; snapshot ve geçmiş endpoint'leri kontrolsüz paylaşılmamalı. Kamera meta.source alanında RTSP kullanıcı adı/parola olmamalı. PostgreSQL portunu gereksiz yere dışa açma.
9. Kamera `cam1`, `cam2` gibi sıralamaya göre oluşturuluyor. Kaynak sırası değişirse mantıksal kamera kimliği kayabilir. En azından bu sınırlamayı belgele; mümkünse ayrı stabil `camera_key`/configuration ID sun, mevcut `cam` ID'lerini bozma.
10. Her JPEG karesini veritabanına yazma. Canlı video RAM + WebSocket'te kalmalı, DB yalnızca işletim kayıtları/olay/rapor ve seyrek heartbeat tutmalı.

## 1A. Sonradan gönderilen model dosyasının doğrulanmış bilgileri

`best.pt.zip` adı biraz yanıltıcıdır: bu dosya bir dış ZIP içinde `best.pt` barındırmıyor. Kendisi doğrudan **PyTorch'un ZIP tabanlı `.pt` checkpoint dosyasıdır** (`best/data.pkl` + 712 ağırlık depolama girdisi vb.). PyTorch'un ZIP okuyucusu içeriği tanıdı; bütün ZIP girdilerinin CRC kontrolü başarılı. Bu nedenle modelin kullanılacak kopyasını `detector/models/best.pt` adıyla **baytları değiştirmeden kopyala/yeniden adlandır**; içindeki `best/` klasörünü ayrıca çıkartıp model olarak gösterme. Kaynak PyTorch checkpoint dosyalarını yalnızca güvenilir kaynaktan yükle; `YOLO()` yüklemesi pickle kaynaklı kod yürütme riski taşıyabilir.

**Checkpoint'in statik metadata bilgileri** (model yükleme/inference sonucu değildir):

- Ultralytics checkpoint sürüm bilgisi: `8.4.183`; `detector/requirements.txt` içindeki `ultralytics==8.4.183` ile eşleşiyor.
- Eğitimin başlangıç model adı: `yolo26n.pt`; görevi `detect`; checkpoint YAML'ında `end2end: true`; eğitim `imgsz: 640`.
- Sınıf sayısı: **11**.
- Sınıf sözlüğü: `0 helmet`, `1 gloves`, `2 vest`, `3 boots`, `4 goggles`, `5 none`, `6 Person`, `7 no_helmet`, `8 no_goggle`, `9 no_gloves`, `10 no_boots`.
- Mevcut `ppe_core.py` sınıf indeksleri (`CLASS_HELMET=0`, `CLASS_VEST=2`, `CLASS_PERSON=6`) bu modelle **birebir uyumlu**. Varsayılan kimlikleri değiştirme.
- `no_helmet` sınıfı modelde olmasına rağmen mevcut monitör karar algoritması bunu **doğrudan kullanmıyor**; baretin eşleşmemesini zaman penceresinde değerlendiriyor. `no_vest` adlı bir sınıf yok; yelek eksikliği pozitif `vest` eşleşmesine göre çıkarılıyor. Sırf veritabanı entegrasyonu sırasında bu semantiği değiştirme; ayrı kalite/doğrulama çalışması olarak not et.
- Bu checkpoint'in end-to-end biçiminde `model.track(..., tracker='bytetrack.yaml')` ve sonuç `boxes.id` davranışı **gerçek görüntüyle doğrulanmalı**. Statik sınıf eşleşmesinin doğru olması, takip kimliklerinin üretildiğini kanıtlamaz.
- Model metadata'sında lisans `AGPL-3.0` olarak etiketlenmiş. Dağıtım/ticari kullanım planlanıyorsa güncel lisans şartları ayrıca incelenmeli.

**Dosyayı Windows PowerShell'de konumlandırma (ZIP içeriğini çıkartmadan):**

```powershell
New-Item -ItemType Directory -Force .\detector\models | Out-Null
Copy-Item ".\best.pt.zip" ".\detector\models\best.pt"
```

Bu örnekte `best.pt.zip` dosyasının proje **kökünde** olduğu varsayılır. Yeni teslim edilen birleştirilmiş proje paketinde `detector/models/best.pt` zaten yerleştirilmiştir.

**Opus'a model smoke-test görevi:** Önce yalnızca güvenilir ve yerel `best.pt` checkpoint'iyle, kurulu Ultralytics sürümünde `YOLO('models/best.pt').names` üzerinden 11 sınıfı doğrula; ardından kullanıcıdan veya yerel repodan erişilebilen uygun inşaat videosunun kısa bir kısmında `model.track()` testi yap. `boxes.id` yoksa/boşsa nedenini raporla; test başarısızlığını sessizce "sıfır ihlal" olarak yorumlama. Test videosu yoksa gerçek çıkarım testini `SKIP` olarak belgele. PostgreSQL entegrasyonu için yeniden eğitim veya model değiştirme yapma.

## 2. Hedef sistem

```
Kamera / RTSP / video / YouTube
       |
       v
Ana bilgisayarda detector.py (YOLO + ByteTrack + PPEMonitor)
       |  multipart POST /ingest/frame  [JSON events + jpg]
       |  POST /ingest/report
       v
Docker: FastAPI dashboard (Pydantic doğrulama + DB session + repository/service)
       |\
       | \-- Canlı video WebSocket ve güncel durum -> tarayıcı
       |
       +--- SQLAlchemy 2 / psycopg 3 ---> PostgreSQL 17 (Docker named volume)
       |
       +--- `/data/snapshots/` dosyaları; DB'de göreli yol
       |
       +--- REST geçmiş API'leri -> tarih/kamera/tip filtreli tarayıcı
```

**Tercihler:** Python 3.11 dashboard, 3.12 dedektör kalır. DB sürücüsü `psycopg` v3, ORM SQLAlchemy 2.x, migration Alembic; senkron SQLAlchemy Session (mevcut `def` FastAPI handler'larıyla uyumlu), PostgreSQL 17. Üçüncü veritabanı / Redis / Celery / yeni frontend framework / login sistemi ekleme. Prod standartları güvenlikte uygulanır ama scope dışında büyük yeniden mimari yapılmaz.

## 3. Önerilen minimal veri şeması

### `cameras`
- `id` VARCHAR(40) PK — mevcut temizlenmiş kamera ID, örn. `cam1`.
- `name` VARCHAR(120) NOT NULL.
- `source_kind` VARCHAR(20) NULL — file/webcam/rtsp/youtube (gizli tam URL saklanmasın).
- `created_at`, `last_seen_at` TIMESTAMPTZ UTC.

### `detector_runs`
- `id` UUID PK — run başına bir kez yaratılır, o run'ın tüm retry'larında aynı.
- `camera_id` FK -> cameras.id, `loop_no` INTEGER, `started_at` TIMESTAMPTZ, `finished_at` TIMESTAMPTZ NULL.
- `status` VARCHAR(20): running/finished/error/interrupted (şema CHECK veya constrained type).
- `fps` NUMERIC NULL, `width`, `height` INTEGER NULL, `last_seen_at` TIMESTAMPTZ.

### `violation_events`
- `id` BIGINT IDENTITY PK — sorgulama/sıralama için **server-generated**.
- `event_id` UUID NOT NULL UNIQUE — idempotency; dedektörde tek kez üretilir.
- `run_id` UUID NOT NULL FK -> detector_runs.id.
- `camera_id` VARCHAR(40) NOT NULL FK -> cameras.id.
- `pid` INTEGER NOT NULL — yalnızca bu run içindeki ByteTrack kimliği.
- `event_type` VARCHAR(10) NOT NULL CHECK IN ('start','ongoing','end').
- `missing_helmet`, `missing_vest` BOOLEAN (özellikle start ve ongoing için); `missing_text` TEXT compat.
- `helmet_rate`, `vest_rate` SMALLINT NULL CHECK 0..100.
- `video_time_sec` NUMERIC NULL, `occurred_at` TIMESTAMPTZ NOT NULL (UTC; dedektörde olay oluşurken), `ingested_at` TIMESTAMPTZ NOT NULL (DB server default).
- `box` JSONB NULL (4 koordinat, doğrulanmış); `snapshot_path` TEXT NULL (örn. `snapshots/alert_<uuid>.jpg`).
- Index: `(camera_id, occurred_at DESC, id DESC)`, `(run_id, pid, id)`, `(event_type, occurred_at DESC)`.

**Not:** `ongoing` her 3 saniyede tekrarlanabilir. MVP'de mevcut olay sözleşmesi korunsun; veri hacmini yönetmek için sorgularda `start`/`end` ana ihlal olayları olarak ayrıştırılsın. Yüksek hacimli saha için `ongoing` kayıt politikasını/retention'ı ayrı kararla sınırlamaya hazır ol. Sistemin ham olay ve tekil ihlal sayısını aynı şey olarak sunma. İleri aşamada `violation_incidents` tablosu açılabilir ama MVP'de şart değil.

### `person_run_reports`
- `(run_id, pid)` bileşik PK. `run_id` FK -> detector_runs.id.
- `first_sec`, `last_sec` NUMERIC; `observations` INTEGER; `helmet_rate`, `vest_rate` SMALLINT; `ever_violation` BOOLEAN; `updated_at` TIMESTAMPTZ.
- Aynı rapor tekrar geldiğinde upsert et; duplicate person report oluşturma.

Bu yapı **kare kare görüntü arşivi** değildir; canlı yayın ayrı kalır.

## 4. Event sözleşmesi (geriye uyumluluk)

`/ingest/frame` halen multipart `frame` ve `payload` almalı. Payload'a:

```json
{
  "cam": "cam1",
  "name": "Giris kamerasi",
  "run_id": "8c021e2d-53ab-4f6f-9136-3bfe4b1ac087",
  "meta": {"source": "sanitized", "fps": 25.0, "width": 1280, "height": 720, "status": "running", "loop": 1},
  "summary": {"t": 12.4, "people": 2, "ok": 1, "viol": 1, "analyzing": 0},
  "people": [],
  "events": [
    {
      "event_id": "cf8f7196-7470-43ef-88cb-82be0a7f358e",
      "run_id": "8c021e2d-53ab-4f6f-9136-3bfe4b1ac087",
      "type": "start",
      "pid": 7,
      "t": 12.4,
      "occurred_at": "2026-10-28T11:29:00Z",
      "missing": "Baretsiz",
      "missing_helmet": true,
      "missing_vest": false,
      "helmet_rate": 12,
      "vest_rate": 78,
      "box": [20, 30, 200, 420]
    }
  ]
}
```

- Mevcut alanları silme; `event_id`, `run_id`, `occurred_at`, ekipman booleans alanlarını ekle.
- `run_id` ve `event_id` yeniden denemede **değişmez**.
- `end` olayında `missing` boş gösterim için korunabilir, ancak önceki ihlalle eşleştirme `run_id + pid` üzerinden yapılır; `end` ile kapanan ihlal anını işleyen servis bunun semantiğini açık belgeler.
- `detector.py` içindeki `run_once()` yeni run id'sini her çağrıda almalı/üretmeli. Yeniden bağlantı döngülerinde yeni ID yaratılmalı; sender'ın taşıdığı eski event'ler eski ID'yi muhafaza etmeli.
- `/ingest/report` alanlarına `run_id`, uygun zaman/durum metaverisi ekle. Mevcut `report` dizisi aynı kalır.
- Sunucu yalnızca DB transaction başarıyla commit edildikten sonra **başarılı ACK** vermeli. Retry edilmiş `event_id` için `ON CONFLICT DO NOTHING` ve yalnızca *yeni* event'in canlı alerta girmesi kuralı kullanılmalı.
- Snapshot JPEG dosya yolu `event_id` kullanılarak deterministik üretilebilir. Dosya ile transaction'ın atomik olmaması yüzünden temp/rename/rollback ve orphan temizliği stratejisi açıkça kod/test ile belirlenmeli.
- Eski sürüm istemci için kısa bir backward-compatibility dönemi gerektiği değerlendirilirse, eventsiz frame kabul edilmeye devam edebilir; yeni event kayıtlarında kimlik zorunlu olmalı.

## 5. Uygulama sırası — Claude Code Opus'a TEK TEK verilecek istemler

> **Çalışma yöntemi:** Her fazı ayrı Claude Code oturum/mesajında uygulat. Faz sonunda Claude çalıştırdığı komutları, test sonuçlarını, dosya farklarını ve kalan riskleri yazsın. Sonucu doğrulamadan sonraki faza geçme. Kendi bilgisayarındaki repo ZIP'den farklıysa öncelik *gerçek yerel dosyalardadır*, hiçbir şeyi körlemesine silme.

### FAZ 0 — Audit ve kararları doğrulama (kod değiştirme)

```text
Bu çalışma alanı PPE Split şantiye güvenlik ekipmanı tespit projesidir. Önce README.md, docker-compose.yml, dashboard/app.py, dashboard/static/index.html, dashboard/requirements.txt, detector/detector.py, detector/ppe_core.py, detector/requirements.txt ve .env.example dosyalarını oku. İlgili dosyaları kendin incelemeden varsayımla kod değiştirme.

HEDEF: YOLO + ByteTrack dedektörünün sonuçlarını FastAPI dashboard üzerinden PostgreSQL 17'ye kalıcı yazmak. Dedektör host üzerinde, dashboard ve DB Docker içinde kalacak. Canlı video bellekte ve WebSocket'te kalacak. SQLAlchemy 2, psycopg 3, Alembic kullanılacak. `ppe_core.py` karar eşiklerine, cihaz/GPU mantığına ve WebSocket binary frame protokolüne dokunma.

Önce yalnızca rapor oluştur: bugünkü veri akışı; kritik entegrasyon noktaları; muhtemel dosya değişiklikleri; idempotent event_id; run_id; UTC/video t farkı; transaction/ACK sırası; snapshot tutarlılığı; geçmiş sorguları; komutlar ve gerçek PostgreSQL test planı. Gereksiz refactor önerme. Model artık sağlandı: `detector/models/best.pt` dosyasını ve sınıf kimlikleri 0/2/6 eşleşmesini doğrula; video/gerçek inference testinin eksik olup olmadığını açıkça belirt. Modelin `end2end` özelliği nedeniyle ByteTrack `boxes.id` davranışının ayrıca test edileceğini plana ekle. Kod değiştirme. Faz 1'e başlamadan önce uygulanacak planı yaz.
```

**Kabul kriteri:** Repo analizi, migration ve veri sözleşmesi planı okunabilir; henüz kaynak kod değişikliği yok.

### FAZ 1 — PostgreSQL altyapısı, şema, Alembic

```text
Onaylı mimariye göre SADECE PostgreSQL alt yapısını geliştir.

1. docker-compose.yml'e PostgreSQL 17 servisi, healthcheck, named volume ve dashboard depends_on service_healthy ekle. DB portunu varsayılan olarak host'a publish etme. root .env.example örneği oluştur; şifreleri kaynak koda koyma, .env ignore kuralını koru.
2. Dashboard Python 3.11 ile uyumlu SQLAlchemy 2 + psycopg 3 + Alembic ekle; bağımlılık sürümlerini çalışır ve tekrarlanabilir biçimde sabitle. Ayrı `dashboard/db/` modülleri, base, engine/session ve ORM modelleri oluştur.
3. En az cameras, detector_runs, violation_events, person_run_reports tablolarını oluştur. event_id UUID unique, run_id FK, saatler timestamptz, indeksler ve CHECK constraint'ler olsun. Snapshot'ları BYTEA olarak kaydetme.
4. Alembic 0001 initial migration oluştur. `alembic upgrade head` ile ileri kurulum; upgrade'in tekrarlanabilirliği; migration geçmişi. `create_all()` ile üretim şeması yaratma.
5. Dashboard `/healthz` sözleşmesini bozma. DB readiness gerekiyorsa ayrı `/health/ready` endpoint'i oluştur.
6. Mevcut Dockerfile yapısı ile çalışır migration işletim yöntemini belgeleyip uygula (ayrı one-shot `docker compose run --rm dashboard alembic upgrade head` uygun olabilir). Dashboard'un DB hazır olmadan başlamasını engelle.
7. Gerçek PostgreSQL üzerinde schema constraint/migration testleri yaz ve çalıştır.

Bu fazda detektör protokolü, frontend veya PPE karar kodunu değiştirme. Faz sonunda komutlar, migration dosyaları, `docker compose ps`, test sonuçları ve riskleri bildir.
```

**Kabul kriteri:** PostgreSQL volume kalıcı, DB healthcheck yeşil, migration tekrarlanabiliyor, PK/FK/unique/index constraints test ediliyor.

### FAZ 2 — Dedektör olay kimliği ve güvenilir gönderim

```text
SADECE detector/detector.py'deki gönderici/protokol katmanını küçük ve kontrollü değişikliklerle güncelle.

Her run_once çağrısı/yeniden oynatma/yeniden bağlantı için yeni run_id UUID oluştur. Üretilen her start/ongoing/end olayına event_id UUID, run_id, UTC occurred_at, mevcut video t, missing_helmet ve missing_vest booleans ekle; eski alanları koru. `ppe_core.py`'deki eşleştirme/eşik/uygunluk algoritmasını değiştirme.

Mevcut Sender son JPEG kareyi atlayabiliyor ama events'i biriktiriyor; bu davranışı korurken sıradaki kareye eskiden kalan event'lerin YANLIŞ run_id almasını engelle. Event envelope run bilgisi, oluştuğu anda donmuş olmalı. HTTP retry event_id'yi yenilemesin. /ingest/report aynı run_id'yi içersin. Sonda gönderilen son olaylar ile raporun sıra/teslim problemini ele al; aynı requests.Session'ı eşzamanlı thread'lerde güvenli olmayan biçimde kullanma. Olay kaybı, yeniden gönderim ve kuyruk dolması davranışını açık belgele.

YOLO/GPU olmadan çalışacak Sender ve protocol unit testleri ekle: iki run'dan karışan kuyruk, timeout+retry, duplicate ID stability, report, kaynak URL gizleme. Testleri çalıştır ve sonuçlarını bildir. Mümkünse görsel tespit döngüsünü olduğu gibi bırak.
```

**Kabul kriteri:** Ayrı run'ların aynı `pid` değerleri karışmıyor; retry aynı `event_id` ile oluyor; canlı video davranışı korunuyor.

### FAZ 3 — Ingest API, DB repository, gerçek transaction

```text
FastAPI dashboard'un `/ingest/frame` ve `/ingest/report` yollarına kalıcı PostgreSQL yazımı ekle. Endpoint URL ve mevcut multipart/json formatını koru. Pydantic şemaları kullan, kontrollü limitlerle veri doğrula ve 4xx/5xx davranışını açıkla.

1. Kamera ve run için idempotent upsert, gelen event'lere event_id unique ile idempotent insert (`ON CONFLICT DO NOTHING`), person_run_reports için (run_id,pid) upsert.
2. Her JPEG karesini DB'ye yazma; DB'de olay, rapor, run, kamera ve seyrek last_seen/heartbeat güncelle.
3. DB transaction commit başarısızsa 200 dönme; 503 veya uygun 5xx ile Sender retry etsin. Başarı ACK ancak commit sonrası. Aynı event yeniden geldiğinde DB'de bir satır ve canlı feed'de bir tek uyarı olmalı.
4. Snapshot sadece start olaylarında dosyada tutulmalı, DB'de relative path olmalı. Base64 görüntü validasyonu, dosya/istek boyut limiti, güvenli adlandırma ve DB-dosya tutarlılığı sağla.
5. SQL parametrelerini güvenli bağla, secret'ları loglama. Gelen RTSP adresinin kimlik bilgilerini DB/log/API'lere sızdırma.
6. `/api/state`, `/api/alerts`, `/api/report`, `/ws` ve `/video_feed/{cid}` geriye uyumlu kalsın. Mevcut canlı akış RAM'de tutulsun, geçmiş DB'den okunabilsin.
7. Gerçek PostgreSQL integration testleri: start/end/ongoing, yeniden deneme duplicate, aynı pid başka run, 2 kamera, failed commit, snapshot, report tekrarları ve uygulama restart'ı.

Kodu sadece gerekli alanlarda değiştir. Testleri ve DB sorgusu ile kanıtları raporla.
```

**Kabul kriteri:** Retry'da tekil kayıt, API uyumluluğu, DB restart sonrası geçmişin kalması, source secret'ın sızmaması.

### FAZ 4 — Geçmiş API ve dashboard ekranı

```text
Kalıcı PostgreSQL kayıtlarından geçmiş sorgulama fonksiyonları ve mevcut statik HTML arayüzüne minimal bir "İhlal geçmişi" bölümü ekle. Frontend framework değiştirme.

- GET /api/history/events: kamera ID, başlangıç/bitiş UTC tarih aralığı, event_type, pid filtreleri; sayfalama (üst limit), kararlı desc sıralama, total ve next_cursor/pagination metadata.
- GET /api/history/summary: seçili tarihte toplam start olayları (tekil ihlal başlangıcı), end sayısı, kamera bazlı dağılım, baret/yelek eksikliği dağılımı. `ongoing` tekrarlarını yeni ihlal olarak sayma.
- GET /api/history/reports: run/camera bazlı tamamlanan person_run_reports listesi.
- Tarih aralığı + kamera filtresi, son ihlaller, snapshot ön izleme. XSS'e açık innerHTML kullanımında kullanıcı kaynaklı alanları escape et ya da DOM textContent kullan.
- Canlı kamera ızgarası ve mevcut /api/state polling 700ms bozulmasın.
- "Listeyi temizle" sadece canlı UI listesini temizlemeye devam etsin; DB kayıtlarını DELETE etmesin.
- Geçmiş sorgusunda snapshot yolu geçerli ve güvenli olsun, sırları loglama.

Backend ve frontend uygun testlerini çalıştır, screenshot/UI kontrolünü mümkünse gerçekleştir.
```

**Kabul kriteri:** Sayfa yenileme ve dashboard restart sonrası geçmiş görünür; tarih/kamera filtreleri doğru; ongoing uyarıları total'i şişirmez.

### FAZ 5 — Uçtan uca test, güvenilirlik ve dokümantasyon

```text
Şimdi tüm fazları uçtan uca test edip tamamla. Mevcut kaynakta en az bir kamera çoklu kamera kodunu bozma.

- PostgreSQL 17 container'ı ve gerçek DB ile test.
- Yapay JSON/event payload ile model/torch gerektirmeyen integration test; **model `detector/models/best.pt` mevcut**. Görüntü/video bulunursa `model.track()` ile gerçek YOLO + ByteTrack smoke/E2E testini gerçekleştir; `boxes.id`, kişi/baret/yelek sınıfları ve ihlal üretilmesini kontrol et. Video yoksa inference testini `SKIP` olarak raporla, başarılıymış gibi sunma.
- En az şu akışlar: 2 farklı kamera; aynı kamera iki run; start -> ongoing -> end; çift HTTP POST; timeout sonrası tekrar; DB unavailable ve yeniden gelmesi; dashboard restart; volume ile docker compose down/up; migration upgrade; report UPSERT; snapshot erişim/eksik dosya; pagination; sahte/bozuk base64; bozuk UUID; secret masking.
- SQL sorgularıyla row count ve event_id uniqueness doğrula. DB down sırasında teslim garantisinin sınırını raporla; process crash'e karşı kalıcı outbox/spool yoksa 'kesinlikle olay kaybı olmaz' iddiası kurma. Gerekirse bu güvenilirlik iyileştirmesini ayrı küçük PR/faz olarak öner.
- README.md'ye Windows PowerShell kurulum komutları, root .env oluşturma, Docker Compose, Alembic, gerçek test komutları, volume yedekleme/geri yükleme ve veri saklama politikasını ekle.
- En sonda `git diff --stat`, değişen dosyalar, test çıktıları, kalan riskler, config örnekleri ve rollback senaryosunu raporla.
- Geliştirme sırasında veri silen `docker compose down -v`, `DROP DATABASE`, volume temizliği, mevcut migration geçmişini silme gibi komutları onaysız çalıştırma.
```

**Kabul kriteri:** Kaynak kodu ve dokümanlar güncel, testler geçiyor, DB dosya saklama ve restart senaryoları gösterilmiş, mevcut canlı kullanım korunuyor.

## 6. Her fazın sonunda Claude'dan istenecek kısa rapor şablonu

```text
FAZ NO:
DEĞİŞEN DOSYALAR:
VERİ SÖZLEŞMESİ DEĞİŞİKLİĞİ:
ÇALIŞTIRILAN KOMUTLAR:
TEST SONUÇLARI (test adı, adet, PASS/FAIL/SKIP):
POSTGRES/DOCKER DURUMU:
MEVCUT CANLI AKIŞTA GERİLEME VAR MI?:
AÇIK RİSKLER / YAPILMAYANLAR:
SONRAKİ FAZA GİRİŞ KOŞULU:
```

## 7. Yerel kurulum için örnek sıra (kod uygulandıktan sonra)

Aşağıdaki komutlar **Claude gerekli dosyaları ekledikten sonra** kullanılmalı; şu anki ZIP hâlinde DB servisi bulunmuyor.

```powershell
# Proje kökünde
Copy-Item .env.example .env  # güçlü PostgreSQL parolasını .env içine ayarla
docker compose up -d --build
# Claude'un seçtiği migration çalıştırma konumuna göre örnek:
docker compose run --rm dashboard alembic upgrade head
docker compose ps
# detector klasöründeki venv ayrı çalışır
cd detector
.\.venv\Scripts\Activate.ps1
python detector.py
```

**Güvenlik:** `data/` snapshot'larını herkese açık internete servis etme; DB şifrelerini `.env` dosyasında ve Git dışında tut. DB portu için host'a `5432:5432` publish yalnızca gerçekten yerel DB istemcisi bağlanacaksa ve doğru erişim kontrolüyle yapılmalı.

## 8. Scope dışı, bilinçli ertelenenler

- Model yeniden eğitimi, YOLO sınıf değişikliği, `no_helmet` özel mantığının eklenmesi ve PPE eşleştirme algoritmasının yeniden yazılması (ayrı değerlendirilecek).
- Her kare / tüm video dosyalarını PostgreSQL BYTEA olarak tutma.
- Gereksiz Redis, Kafka, Celery, React veya ağır microservice yapısı.
- Gözlenen `pid`'yi bir işçinin gerçek kimliği gibi yorumlama.
- Otomatik fiziksel güvenlik müdahalesi. Model yanılabilir; sistem operasyonel destek ve insan doğrulamasına açık olmalı.
