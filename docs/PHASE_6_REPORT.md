# FAZ 6 raporu — Erişim, ağ ve gizlilik sertleştirme

Tarih: 2026-10-28. Başlangıç: `0c2e9cb` (FAZ 5). Plan: `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md`, FAZ 6.
Sonuç: **teknik güvenlik kapsamı TAMAMLANDI (kritik testler PASS); KVKK ve lisans NO-GO (hukuki karar bekleniyor);
dashboard LAN'a/internete AÇILMADI.**

## 1. Başlangıç denetimi

| Kontrol | Sonuç |
|---|---|
| Git | `main`, HEAD `0c2e9cb`, yalnız kullanıcının eklediği plan belgesi izlenmiyor |
| Servisler | db healthy, dashboard çalışıyor, Alembic 0003 |
| Dashboard pytest | 260/260 PASS |
| Dedektör pytest | 119/119 PASS |
| Tarayıcı UI (23) | FAZ 5'te aynı kodda bugün PASS; FAZ 6 sonunda yeniden çalıştırıldı (aşağıda) |
| Erişim envanteri | **Hiçbir tarayıcı yüzeyinde kimlik doğrulama yoktu**: `/api/*`, `/ws`, `/video_feed`, `/snapshots` (statik mount), `/docs` anonim açıktı; yalnız `127.0.0.1` bağına güveniliyordu |

Plan belgesindeki varsayımlardan fark: "INGEST_TOKEN aktif" doğru (FAZ 5'te üretildi). Ek bulgu: Starlette/
python-multipart'ta bilinen açıklar (bkz. 8).

## 2. Değişen dosyalar

Yeni: `dashboard/auth/` (`passwords.py`, `store.py`, `security.py`, `routes.py`, `cli.py`),
`dashboard/alembic/versions/0004_auth_sessions_audit.py`, `dashboard/tests/test_access_control.py`,
`dashboard/tests/lan_profile_check.py`, `dashboard/tests/lan_server.py`, `deploy/lan/Caddyfile`,
`deploy/lan/lan_smoke.py`, `docker-compose.lan.yml`, `docs/THREAT_MODEL.md`, `docs/LAN_DEPLOYMENT.md`,
`docs/PRIVACY_READINESS.md`, `docs/LICENSE_REVIEW.md`, `docs/PHASE_6_REPORT.md`.

Değişen: `dashboard/app.py`, `db/history.py` (kamera kapsamı), `db/models.py`, `db/roles.py` (denetim kaydı yalnız
ekleme), `db/__init__.py`, `static/index.html` (giriş, rol, çıkışta veri temizleme), `Dockerfile` (uid 10001, digest),
`requirements.txt` (argon2-cffi, python-multipart 0.0.31), `docker-compose.yml` (sertleştirme), testler (oturum
fixture'ı), `.env.example`, `README.md`, `docs/SECURITY.md`, `docs/DEPLOYMENT.md`.

Dokunulmayan: `best.pt`, `ppe_core.py`, YOLO/ByteTrack, dedektör kodu, WebSocket ikili biçimi, migration 0001–0003.

## 3. Kimlik/oturum mimarisi

Sunucu tarafı oturum (PostgreSQL `user_sessions`, token yalnız SHA-256 özetiyle), Argon2id (t=3, m=64 MiB, p=4),
HttpOnly + SameSite=Strict çerez (lan: `__Host-` + Secure), CSRF token + Origin, boşta 30 dk / mutlak 12 sa, çıkış/
iptal/parola/rol değişiminde geçersiz kılma, hesap kilidi (5/15 dk) + IP sınırı (20/15 dk), genel hata mesajı,
zamanlama eşitleme. Roller: viewer < supervisor < admin; kullanıcı başına kamera kapsamı (nesne düzeyi yetki, kapsam
dışı = 404). Kullanıcı yönetimi: CLI (`python -m auth.cli`) + admin API; web arayüzünde kullanıcı yönetimi ekranı
**yok** (bilinçli: kapsamı küçük tutmak için).

Rol sadeleştirme gerekçesi: `supervisor` bugün yazma yapmaz; viewer'dan farkı kişisel veri (görüntü/canlı yayın)
erişimidir. Bu, KVKK veri en aza indirme ilkesine göre üç rolü anlamlı kılar.

Bilinçli ödünleşim: DB kesintisinde daha önce doğrulanmış oturum 300 sn son bilinen yetkiyle geçerli kalır; aksi
hâlde DB kesintisi canlı güvenlik izlemesini de keserdi (FAZ 3'ten beri canlı görüntü DB'den bağımsız).

## 4. Uç matrisi

Ayrıntılı tablo: [SECURITY.md](SECURITY.md#roller-ve-uç-matrisi). Özet: anonim yalnız `/` (veri içermeyen kabuk),
giriş, `/healthz`, `/health/ready` (yalnız durum); viewer geçmiş/istatistik (görüntüsüz); supervisor + canlı yayın,
WS, MJPEG, snapshot; admin + yönetim/denetim; `/ingest/*` yalnız makine token'ı; `/docs` kapalı.

## 5. TLS / LAN yapılandırması

`SECURITY_PROFILE=local|lan`; Caddy tabanlı `docker-compose.lan.yml` + `deploy/lan/Caddyfile` (HTTP→HTTPS, WSS,
`X-Forwarded-*` yalnız güvenilir vekilden, HSTS uygulamada). Varsayılan compose değişmedi: `127.0.0.1:8000`.
Gerçek sertifika/alan adı kurulmadı; testler Caddy'nin yerel test CA'sıyla, yalnız `127.0.0.1:8443`'te.

## 6. Güvenlik test kanıtları

| Test | Komut | Sonuç |
|---|---|---|
| Erişim kontrolü (35 test: uç matrisi, BOLA/kapsam, WS kimlik+Origin+kapsam+iptal, giriş/çerez bayrakları, kilit, IP sınırı, devre dışı hesap, CSRF ×4, boşta/mutlak süre, iptal ve rol değişimi, yetki yükseltme, parola politikası, Argon2id saklama, makine kimliği + rotasyon, başlıklar/CSP hash, CORS, Host, erken token denetimi, Range, readiness yazılabilirlik, denetim kaydı içeriği ve yalnız-ekleme, lan profili alt süreç) | `docker compose --profile test run --rm tests` | **35/35 PASS** |
| Dashboard tümü (gerçek PostgreSQL 17, uygulama `ppe_app_test` rolüyle) | aynı | **296/296 PASS** (başlangıç 260) |
| Dedektör | `cd detector; .venv\Scripts\python -m pytest` | **119/119 PASS** |
| Gerçek tarayıcı (Edge + CDP) | `tests/ui/serve_fixture.py` + `node dashboard/tests/ui/ui_check.mjs` | **28/28 PASS** (23 eski + giriş ekranı, yanlış parola, çıkış + DOM temizliği, viewer kısıtları, kamera kapsamı; 503 senaryosu giriş ekranına uyarlandı) |
| LAN TLS ters vekil (izole, gerçek TLS) | `python deploy/lan/lan_smoke.py` | **21/21 PASS**, kalıntı yok |
| Canlı sistem anonim denetimi | HTTP istekleri | `/api/*`, `/snapshots/<bilinen ad>`, `/video_feed` → 401; `/docs` 404; LAN Host 400; CSP/XFO var |
| Canlı sertleştirme | `docker inspect` | dashboard uid 10001, salt okunur kök, `cap_drop ALL`, `no-new-privileges`, limitler, healthy; db digest + `no-new-privileges` |
| Gerçek GPU E2E (sertleştirilmiş dashboard) | `python detector.py "[giris]…"` | START/ONGOING×2/END = 4 olay, 0 düşürülen, snapshot uid 10001 tarafından yazıldı, rapor kaydedildi |
| Yedek + izole geri yükleme (0004 öncesi) | `scripts/backup.py --quiesce`, `restore_check.py` | PASS |
| Bağımlılık taraması | `pip-audit -r requirements.txt` | python-multipart açıkları kapatıldı; Starlette 7 kayıt **açık** (bkz. 8) |

Testlerin bulduğu ve düzeltilen hatalar: boş denetim detayının JSON `null` yazılması (CHECK ihlali → çıkış 503);
çıkışta önceki kullanıcının verisinin DOM'da kalması (viewer, admin'in görüntü küçük resmini gördü); root olmayan
container'ın mevcut `data/snapshots`'a yazamaması (canlıda devreye almadan önce yakalandı; dizin sahipliği düzeltildi,
`/health/ready` artık bunu denetliyor).

## 7. Regresyon

Ingest/idempotency/geçmiş/rol/yedek testlerinin tamamı oturumlu fixture ile geçti; WebSocket 1 bayt idx + JPEG
(TestClient, tarayıcı ve gerçek TLS WSS ile); MJPEG; DB kesintisinde canlı akış; `INGEST_TOKEN` 401 davranışı.
Değişen beklentiler (bilinçli): `/healthz` → `{"status":"ok"}`, anonim `/health/ready` yalnız durum.

## 8. Açık riskler

1. **Starlette 0.41.3**: 7 bilinen açık (DoS, Host/URL yeniden kurma, Windows StaticFiles SSRF, HTTPEndpoint).
   Telafi: Host allowlist, Range işlenmiyor, StaticFiles/HTTPEndpoint yok, ingest token'ı gövde öncesi. Kimliği
   doğrulanmış dedektör/kullanıcı için form ayrıştırma DoS riski kalır. **FastAPI/Starlette yükseltmesi ayrı iş olarak
   önerilir.**
2. Yönetici hesabı canlı sistemde **oluşturulmadı** (parolayı kullanıcı belirlemeli): `docker compose exec dashboard
   python -m auth.cli create admin --role admin`. Oluşturulana kadar arayüze giriş yok (veri güvende).
3. MFA yok; parola kurtarma yok (yönetici sıfırlar).
4. IP hız sınırı ve oturum önbelleği süreç içi (tek uvicorn varsayımı).
5. DB kesintisinde 300 sn'lik oturum toleransı (iptal denetlenemez).
6. Yedekler ve `data/snapshots` **şifrelenmez**; host erişimi olan herkes tüm veriye erişir.
7. Denetim kaydı için saklama süresi ve kurcalamaya dayanıklı saklama yok; DB süper kullanıcısı değiştirebilir.
8. LAN dağıtımı yalnız test CA'sıyla doğrulandı; gerçek sertifika, güvenlik duvarı ve LAN istemcisi test edilmedi.
9. Ana DB'ye test verisi: FAZ 6 GPU E2E'den 1 run (`giris`), canlı anonim denetim isteklerinden `access_denied`
   denetim kayıtları. Silinmedi.

## 9. KVKK / lisans go-no-go

| Konu | Durum | Belge |
|---|---|---|
| KVKK (dayanak, aydınlatma, saklama/imha, VERBİS, ilgili kişi hakları) | **NO-GO — hukuki inceleme bekleniyor** | [PRIVACY_READINESS.md](PRIVACY_READINESS.md) |
| Ultralytics AGPL-3.0 / ağırlık lisansı | **NO-GO — ticari/kapalı kaynak kararı bekleniyor** | [LICENSE_REVIEW.md](LICENSE_REVIEW.md) |

## 10. PASS / FAIL / SKIP

| Kalem | Sonuç |
|---|---|
| Yetkisiz erişim (geçmiş, snapshot URL bilinse de, canlı video, WS, canlı API) kapalı | PASS |
| Viewer yazma/yönetim alamaz; rol yükseltme, BOLA, CSRF reddedilir | PASS |
| Admin/supervisor/viewer izinleri | PASS |
| Çerez bayrakları, oturum süresi/iptali, kaba kuvvet | PASS |
| Makine token'ı doğru/yanlış/eksik; insan oturumu ingest'e erişemez; rotasyon | PASS |
| WS kimlik doğrulama + Origin | PASS |
| HTTP (local) ve TLS-dev (lan) profilleri; varsayılan dış port açılmıyor | PASS |
| SQLi/XSS/traversal/sır maskeleme/denetim kaydı | PASS |
| Mevcut ingest, idempotency, WS ikili biçim, canlı video | PASS |
| 296 dashboard + 119 dedektör + 28 tarayıcı + 21 LAN TLS | PASS |
| Container sertleştirme (canlı) | PASS |
| Bağımlılık taraması | PARTIAL (Starlette açık) |
| Gerçek LAN istemcisi, kurum sertifikası, güvenlik duvarı | SKIP |
| Web tabanlı kullanıcı yönetimi ekranı, MFA | SKIP (kapsam dışı bırakıldı) |
| KVKK, lisans | BLOCKED (hukuki karar) |

## 11. Commit

Bu raporla birlikte oluşturulan commit: `git log -1` (FAZ 6). Kullanıcının eklediği plan belgesi
(`docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md`) commit'e dahil edilmedi.

## 12. FAZ 7 ön koşulları

- Canlı sistemde ilk yönetici hesabının oluşturulması.
- Starlette/FastAPI yükseltme kararı (FAZ 7'den önce veya ayrı değişiklik olarak).
- FAZ 5'ten açık kalan 3 `running` run (FAZ 7: yıkıcı olmayan uzlaştırma politikası; onaysız değiştirilmeyecek).
- KVKK ve lisans kararları FAZ 10 kapısına kadar izlenmeli; FAZ 7 teknik çalışması bunlardan bağımsız yürütülebilir.
