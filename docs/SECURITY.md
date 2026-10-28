# Güvenlik modeli

**Kapsam: tek bilgisayarda yerel kullanım.** Dashboard varsayılan olarak yalnız `127.0.0.1:8000`'e bağlanır;
PostgreSQL portu host'a açılmaz. Bu sürüm LAN veya internet erişimi için **hazır değildir** (aşağıya bakın).

## Yüzeyler

| Yüzey | Erişim | Koruma |
|---|---|---|
| `POST /ingest/frame`, `/ingest/report` | dedektör | `X-Token` = `INGEST_TOKEN` (sabit zamanlı karşılaştırma), Pydantic doğrulama, boyut sınırları |
| `/`, `/api/state`, `/api/alerts`, `/api/report`, `/api/history/*` | tarayıcı | **kimlik doğrulama yok** (yalnız localhost bağı) |
| `/ws`, `/video_feed/{cid}` | tarayıcı | **kimlik doğrulama yok** |
| `/snapshots/*` | tarayıcı | **kimlik doğrulama yok**; yalnız `data/snapshots`; path traversal testli |
| `/docs`, `/openapi.json` | tarayıcı | FastAPI varsayılanı, açık |
| PostgreSQL | compose ağı | port yayınlanmaz; uygulama `ppe_app` (DELETE/DDL yok) |

## INGEST_TOKEN

Kök `.env` ve `detector/.env` içinde **aynı** değer olmalıdır. Dashboard 401 döndürürse dedektör o olayları
düşürür (kalıcı hata, log'da `INGEST_TOKEN` uyarısı). Değiştirme yöntemi: dedektörü durdur → iki dosyayı birlikte
güncelle → `docker compose up -d` → dedektörü başlat. Token tarayıcıya hiçbir zaman gönderilmez.
FAZ 5'te bu kurulumda token üretildi ve iki dosyaya aynı anda yazıldı (değer belgelenmez); canlı sistem token'sız ve
yanlış token'lı isteği 401 ile reddediyor, dedektör doğru token'la olay gönderiyor (E2E testi).

## Veritabanı

Bkz. [DATABASE.md](DATABASE.md#roller-least-privilege). Uygulama SQL'i parametre bağlamalıdır (SQLAlchemy); dinamik
SQL yalnız `psycopg.sql.Identifier/Literal` ile (rol betiği). Parolalar `.env`'de (Git dışı); yedeklerde yok
(otomatik tarama ile doğrulandı).

## Sızıntı önleme (testlerle doğrulanır)

- RTSP kullanıcı/parola, `token`/`password` vb. sorgu parametreleri ve fragment: dedektörde ve sunucuda bağımsız
  maskelenir; DB, API, log ve kamera kimliğinde yer almaz (`source_ref` maskeli).
- DB hata mesajları HTTP'ye genel metinle döner (`503`), bağlantı adresi/parola içermez; beklenmeyen hata `500`
  gövdesi iz (traceback) içermez.
- Snapshot base64 içeriği loglanmaz ve 422 yanıtlarında geri yansıtılmaz.
- Rol betiği hata çıktısında parola/adres yazmaz.

## Girdi güvenliği (regresyon testli)

Pydantic tip/aralık doğrulama, UTC zorunluluğu, UUID biçimi, `camera_id` deseni, katı base64 + JPEG yapı kontrolü,
kare/snapshot/rapor/olay sayısı sınırları, snapshot adı yalnız `event_id`'den, geçmiş API'sinde enum/aralık/cursor
doğrulaması (422), SQL injection denemeleri (422 ve veri bütün kalır), XSS: arayüzün geçmiş kodu yalnız
`textContent`/`setAttribute` kullanır; gerçek tarayıcı testinde `<img onerror>` adlı kamera çalıştırılmadı.

## LAN / internet için gerekenler (uygulanmadı)

Dashboard'u `DASHBOARD_BIND=0.0.0.0` ile açmadan önce en az:

1. TLS sonlandıran ters vekil (ör. Caddy/nginx) ve **tüm** yollar için kimlik doğrulama: `/`, `/api/*`, `/ws`,
   `/video_feed/*`, `/snapshots/*`, `/docs`. Tarayıcı oturumu `HttpOnly; Secure; SameSite=Strict` çerez; durum
   değiştiren uç yok ama eklenirse CSRF koruması.
2. `/ingest/*` yalnız dedektör IP'sine izinli ve `INGEST_TOKEN` zorunlu (boş token'la başlatma engellenmeli).
3. Snapshot'lar kişisel veri içerir: erişim günlüğü, saklama süresi ve KVKK değerlendirmesi.
4. `/docs` ve `/openapi.json` kapatılmalı.

Bu adımlar olmadan sistem **"local deployment"** düzeyindedir.
