# Güvenlik modeli (FAZ 6)

Tehdit modeli: [THREAT_MODEL.md](THREAT_MODEL.md). LAN dağıtımı: [LAN_DEPLOYMENT.md](LAN_DEPLOYMENT.md).
Gizlilik: [PRIVACY_READINESS.md](PRIVACY_READINESS.md). Lisans: [LICENSE_REVIEW.md](LICENSE_REVIEW.md).

**Varsayılan dağıtım yalnız bu bilgisayardır** (`127.0.0.1:8000`, `SECURITY_PROFILE=local`). Tüm tarayıcı yüzeyleri
artık oturum gerektirir.

## Kimlik doğrulama ve oturum

- Yerel hesaplar (`app_users`), Argon2id özeti. Parola: 12–256 karakter, kullanıcı adını içeremez, ≥6 farklı karakter.
  Parola kurtarma/e-posta **yok**: yönetici CLI veya API ile sıfırlar.
- Sunucu tarafı oturum: çerezde 256 bit rastgele token, DB'de yalnız SHA-256 özeti. Boşta 30 dk
  (`SESSION_IDLE_MINUTES`), mutlak 12 sa (`SESSION_MAX_HOURS`). Çıkış, parola değişimi, rol/kapsam değişimi ve yönetici
  iptali oturumları geçersiz kılar (süreç içi önbellek en fazla 5 sn).
- Çerez: `HttpOnly; SameSite=Strict; Path=/`; `lan` profilinde ayrıca `Secure` ve `__Host-` önek.
- CSRF: durum değiştiren her oturumlu istek `X-CSRF-Token` (oturuma bağlı) + aynı köken ister. Giriş yalnız JSON ve
  aynı kökenden.
- Kaba kuvvet: hesap başına 5 başarısız deneme → 15 dk kilit; IP başına 15 dk'da 20 başarısız deneme → 429. Hata mesajı
  kullanıcı varlığını açığa vurmaz.
- DB kesintisi: daha önce doğrulanmış oturum `SESSION_DB_GRACE_SEC` (300) boyunca son bilinen yetkiyle kabul edilir
  (canlı izleme DB'ye bağlı kalmasın diye). Bu sürede yeni giriş yapılamaz, iptal denetlenemez.

## Roller ve uç matrisi

`viewer` < `supervisor` < `admin`. Kamera kapsamı (`camera_scope`) boşsa tüm kameralar; doluysa yalnız listedekiler.
Kapsam dışı kayıt **404** döner (varlığı açığa vurulmaz).

| Uç | Anonim | viewer | supervisor | admin | Not |
|---|---|---|---|---|---|
| `/` (uygulama kabuğu) | ✓ | ✓ | ✓ | ✓ | veri içermez; giriş ekranı |
| `/healthz` | ✓ `{"status"}` | | | | |
| `/health/ready` | ✓ `{"status"}` | | | ✓ ayrıntı | snapshot dizini yazılamazsa 503 |
| `POST /api/auth/login` | ✓ | | | | aynı köken, JSON |
| `/api/auth/me`, `logout`, `password` | ✗ 401 | ✓ | ✓ | ✓ | logout/password: CSRF |
| `/api/history/*` | ✗ 401 | ✓ kapsam | ✓ kapsam | ✓ kapsam | viewer'a görüntü bağlantısı verilmez |
| `/snapshots/{alert_<uuid>.jpg}` | ✗ 401 | ✗ 403 | ✓ kapsam | ✓ kapsam | yalnız DB'de referansı olan dosya; erişim denetlenir |
| `/api/state`, `/api/alerts`, `/api/report` | ✗ 401 | ✗ 403 | ✓ kapsam | ✓ kapsam | |
| `/ws` | ✗ | ✗ | ✓ kapsam | ✓ kapsam | aynı köken; ~15 sn'de oturum yeniden doğrulanır |
| `/video_feed/{cid}` | ✗ 401 | ✗ 403 | ✓ kapsam | ✓ kapsam | |
| `/api/admin/*` | ✗ 401 | ✗ 403 | ✗ 403 | ✓ | yazma: CSRF; son etkin yönetici korunur |
| `/ingest/frame`, `/ingest/report` | yalnız `X-Token` | | | | oturum çerezi dikkate alınmaz |
| `/docs`, `/redoc`, `/openapi.json` | 404 | | | | kapalı |

`supervisor` bugün yazma işlemi yapmaz (arayüzde inceleme/onay işlevi yok); görüntü ve canlı yayın erişimi viewer'dan
ayıran kişisel veri yetkisidir.

## Makine kimliği ve token rotasyonu

Dedektör `X-Token` ile kimlik doğrular; token middleware'de gövde okunmadan denetlenir. Kesintisiz rotasyon
(olay kaybetmeden):

1. Yeni token üretin. Kök `.env`'ye `INGEST_TOKEN_NEXT=<yeni>` ekleyin, `docker compose up -d dashboard`.
   (Dedektör eski token'la göndermeye devam eder, iki token da kabul edilir.)
2. `detector/.env`'de `INGEST_TOKEN=<yeni>`; dedektörü Ctrl+C ile durdurup başlatın (kuyruk boşaltılır).
3. Kök `.env`'de `INGEST_TOKEN=<yeni>`, `INGEST_TOKEN_NEXT=` (boş); `docker compose up -d dashboard`.

Bu fazda canlı token değiştirilmedi (FAZ 5'te üretilmiş token geçerli). İki token kabulü
`test_session_never_authorizes_ingest` ile testlidir.

## Güvenlik profilleri

| | `local` (varsayılan) | `lan` |
|---|---|---|
| Kabul edilen Host | `127.0.0.1`, `localhost`, `::1` | yalnız `ALLOWED_HOSTS` (+ makine uçları için localhost) |
| Tarayıcı uçları | HTTP | yalnız `TRUSTED_PROXIES`'ten `X-Forwarded-Proto: https` |
| Çerez | Secure değil | `__Host-` + Secure |
| HSTS | yok | `max-age=31536000` |
| Başlatma denetimi | LAN Host'u yasak | `ALLOWED_HOSTS`, `TRUSTED_PROXIES`, `INGEST_TOKEN` zorunlu; test parola profili yasak |

`local` profili LAN Host'uyla gelen isteği 400 ile reddeder; `DASHBOARD_BIND=0.0.0.0` yapılsa bile tarayıcıdan
LAN üzerinden kullanılamaz. (Host başlığı sahte gönderilebilir: asıl koruma oturumdur.)

## Başlıklar

CSP (`default-src 'none'`, `script-src` yalnız satır içi betiğin SHA-256'sı, `frame-ancestors 'none'`),
`X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, COOP/CORP same-origin, API'lerde
`Cache-Control: no-store`, `Server` başlığı yok. CORS ara katmanı yok (varsayılan kapalı).

## Denetim kaydı

`audit_log` olayları: `login` (success/failure + neden), `logout`, `access_denied`, `csrf_failure`, `ws_rejected`,
`ingest_auth`, `snapshot_access` (oturum+dosya başına saatte bir), `user_create`, `user_update`, `password_reset`,
`password_change`, `sessions_revoke`. Kayıt parola, token, çerez veya görüntü içermez; yalnız izinli alanlar
(`reason`, `path`, `role`, `target_user_id` …). Tekrarlayan red kayıtları dakikada bire indirilir. `ppe_app` rolü
kaydı güncelleyemez/silemez. Kayıt güvenlik içindir; çalışanların izlenmesi için kullanılmamalıdır
(PRIVACY_READINESS.md). Görüntüleme: `GET /api/admin/audit` (admin).

## Container ve bağımlılıklar

- Dashboard: uid 10001, salt okunur kök dosya sistemi (yazma yalnız `/data`, `/tmp`), `cap_drop: ALL`,
  `no-new-privileges`, 1 GiB / 2 CPU / 256 pid, healthcheck. Taban imaj digest ile sabit.
- PostgreSQL: digest ile sabit `postgres:17`, `no-new-privileges`, 2 GiB / 512 pid. (`cap_drop` uygulanmadı: giriş betiği
  ayrıcalık düşürmek için yetenek ister.)
- Yükseltmede: mevcut `data/snapshots` root'a aitse bir kez
  `docker run --rm --user root -v <proje>/data:/data ppe-dashboard chown 10001:10001 /data/snapshots` (yalnız dizin;
  dosyalara dokunulmaz). Yazılamazsa `/health/ready` 503 döner.
- `pip-audit` (2026-10-28): `python-multipart` 0.0.17 → **0.0.31** (7 kayıt kapandı).
- FAZ 9 (2026-11-01): `fastapi` 0.115.5 → **0.162.0**, `starlette` 0.41.3 → **1.10.0** (açıkça sabitli) — starlette'in
  7 kaydı kapandı; `pip-audit`: bilinen açık yok. Doğrulama ve geri dönüş planı: `docs/PHASE_9_REPORT.md` §18.

## Sızıntı önleme (FAZ 5'ten, regresyon testli)

RTSP kimlik bilgileri/token parametreleri maskelenir; DB hataları genel 503; beklenmeyen hata 500 gövdesinde iz yok;
snapshot base64 loglanmaz; rol betiği parola yazmaz.
