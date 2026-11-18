# Tehdit modeli (FAZ 6)

Kapsam: dashboard (FastAPI, Docker), PostgreSQL, dedektör → dashboard ingest, tarayıcı arayüzü, isteğe bağlı TLS ters
vekil. Referans: OWASP API Security Top 10 (2023).

## Varlıklar

| Varlık | Hassasiyet | Nerede |
|---|---|---|
| Canlı video kareleri | çalışan görüntüsü (kişisel veri) | dashboard RAM, `/ws`, `/video_feed` |
| İhlal snapshot'ları | kişisel veri, ihlal anı | `data/snapshots/` (yedeklerde de) |
| İhlal/olay/run kayıtları | iz numarası + konum + zaman (dolaylı kişisel veri) | PostgreSQL |
| Kullanıcı hesapları | parola özeti, rol, kamera kapsamı | `app_users` |
| Oturum token'ları | hesap ele geçirme | tarayıcı çerezi (DB'de yalnız SHA-256) |
| `INGEST_TOKEN` | sahte olay enjeksiyonu | `.env`, `detector/.env` |
| DB parolaları | tam veri erişimi | kök `.env` |
| Denetim kaydı | güvenlik izi | `audit_log` (uygulama için yalnız ekleme) |

## Aktörler ve güven sınırları

- **Anonim ağ kullanıcısı** (LAN açılırsa): yalnız `/`, `/api/auth/login`, `/healthz`, `/health/ready` görebilir.
- **Kimliği doğrulanmış kullanıcı**: rolü (viewer/supervisor/admin) ve kamera kapsamı kadar.
- **Dedektör** (makine): yalnız `/ingest/*`, `X-Token` ile. İnsan oturumu ingest'e erişemez; token tarayıcı API'lerine
  erişemez.
- **Host üzerindeki yerel kullanıcı / yönetici**: `.env`, `backups/`, Docker'a erişimi olan herkes tüm veriye erişir —
  **bu sınır uygulama tarafından korunmaz** (işletim sistemi hesapları ve disk şifrelemesi ile korunmalı).
- **TLS ters vekil** (lan profili): yalnız `TRUSTED_PROXIES` adresinden gelen `X-Forwarded-*` başlıklarına güvenilir.

## OWASP API Top 10 (2023) eşlemesi

| Risk | Önlem | Kanıt (test) | Kalan risk |
|---|---|---|---|
| API1 Nesne düzeyi yetki (BOLA) | Her geçmiş/snapshot/canlı sorguda kullanıcının kamera kapsamı SQL'e eklenir; kapsam dışı = 404 | `test_camera_scope_object_level_authorization`, UI "Kamera kapsamı" | Kapsam kamera düzeyinde; run/incident başına ayrı yetki yok |
| API2 Kimlik doğrulama | Argon2id (t=3, m=64 MiB, p=4), hesap kilidi (5 deneme/15 dk), IP başına sınır (20/15 dk), genel hata mesajı, zamanlama eşitleme, oturum sabitlemeye karşı yeni token | `test_account_lockout…`, `test_ip_rate_limit`, `test_login_cookie_flags…` | MFA yok; IP sınırı süreç içi (tek uvicorn) |
| API3 Özellik düzeyi yetki | Girdi modelleri `extra=forbid`; viewer'a görüntü bağlantısı verilmez; yönetici olmayan health ayrıntısı almaz | `test_viewer_gets_no_image_links`, `test_no_privilege_escalation` | — |
| API4 Kaynak tüketimi | Kare/olay/rapor/snapshot sınırları, ingest token'ı gövde okunmadan denetlenir, Range işlenmez, giriş hız sınırı, container bellek/CPU/pid sınırları | `test_ingest_token_checked_before_body_parsing`, `test_range_requests_not_processed` | Starlette 0.41.3'teki form ayrıştırma DoS kayıtları kimliği doğrulanmış dedektör için açık (bkz. SECURITY.md) |
| API5 Fonksiyon düzeyi yetki | Merkezi `require(rol)` bağımlılığı; uç matrisi testli | `test_endpoint_matrix_anonymous_and_roles` | — |
| API6 Hassas iş akışları | Giriş kaba kuvvet koruması; yönetici işlemleri CSRF + denetim kaydı | `test_csrf_required…` | — |
| API7 SSRF | Dashboard dış adres çağırmaz; StaticFiles kaldırıldı | kod incelemesi | Dedektör YouTube/RTSP adreslerini yalnız yerel yapılandırmadan açar |
| API8 Yanlış yapılandırma | `local`/`lan` profili (güvensiz LAN yapılandırması açılmaz), güvenlik başlıkları, CSP (betik hash'i), `/docs` kapalı, root olmayan salt okunur container | `test_lan_profile_config_validation`, `test_security_headers…`, canlı denetim | — |
| API9 Envanter | Uç matrisi (SECURITY.md), OpenAPI kapalı | `test_endpoint_matrix…` | — |
| API10 Güvenli olmayan API tüketimi | Dedektör yükü Pydantic ile katı doğrulanır, snapshot JPEG yapı denetimi | FAZ 3 testleri | — |

## Diğer tehditler

| Tehdit | Önlem | Durum |
|---|---|---|
| CSRF | SameSite=Strict çerez + `X-CSRF-Token` (oturuma bağlı) + Origin denetimi; giriş yalnız JSON | testli |
| XSS | Arayüz sunucu verisini yalnız `textContent`/`setAttribute` ile yazar; CSP `script-src` yalnız satır içi betiğin SHA-256'sı | testli (tarayıcı) |
| WebSocket ele geçirme (CSWSH) | `/ws` Origin denetimi + oturum + supervisor; ~15 sn'de bir yeniden doğrulama | testli |
| CORS | Ara katman yok = varsayılan kapalı | testli |
| Oturum çalınması | HttpOnly, Secure (lan), `__Host-` önek, boşta 30 dk / mutlak 12 sa, çıkış/iptal | testli |
| Paylaşılan bilgisayar | Çıkışta önceki kullanıcının verisi DOM'dan temizlenir | testli (tarayıcı) |
| Sahte HTTPS başlığı | `X-Forwarded-*` yalnız `TRUSTED_PROXIES`'ten | testli (gerçek TLS) |
| Token rotasyonunda olay kaybı | `INGEST_TOKEN_NEXT` ile çift token geçişi | testli |
| DB kesintisinde canlı izleme | Doğrulanmış oturum 300 sn son bilinen yetkiyle (iptal denetlenemez) | bilinçli ödünleşim |
| İç tehdit / yönetici kötüye kullanımı | Denetim kaydı yalnız ekleme (`ppe_app`), rol ayrımı | yönetici DB'ye doğrudan erişebilir |
| Yedeklerin sızması | `backups/` Git dışı | **şifrelenmez** — açık risk |
| Kişisel izleme amacıyla kullanım | Yüz tanıma/kimlik yok; iz ID run'a özel | PRIVACY_READINESS.md |
