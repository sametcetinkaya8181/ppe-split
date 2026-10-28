# Test raporu (FAZ 5, 2026-10-28)

Ortam: Windows 11, Docker Desktop (Compose v2.40), PostgreSQL 17.11, dashboard Python 3.11, dedektör Python 3.12,
NVIDIA RTX 3050 Laptop GPU, Microsoft Edge (headless) + Node 22.11.

## Komutlar

```powershell
docker compose --profile test run --rm tests                                   # dashboard (gerçek PostgreSQL)
cd detector; .venv\Scripts\python -m pytest                                     # dedektör (GPU gerekmez)
docker compose --profile test run --rm tests python -m tests.perf_history       # performans (izole DB)
# UI: izole fixture + gerçek tarayıcı
docker compose --profile test run --rm -d --name ppe-ui-fixture -p 127.0.0.1:8001:8001 -p 127.0.0.1:8002:8002 -p 127.0.0.1:8003:8003 tests sh -c "pip install -q -r requirements-dev.txt && python -m tests.ui.serve_fixture"
node dashboard/tests/ui/ui_check.mjs <ekran-görüntüsü-klasörü>
docker stop ppe-ui-fixture
python scripts/backup.py --quiesce; python scripts/restore_check.py backups\ppe-<UTC>
```

Dashboard testleri yalnız `*_test` veritabanlarını ve geçici `ppe_app_test` / `ppe_migrator_test` rollerini kullanır
(oturum sonunda silinir). Uygulama testlerde **sınırlı `ppe_app_test` rolüyle** bağlanır.

## Sonuçlar

| Paket | Sonuç |
|---|---|
| Dashboard pytest (başlangıç referansı, FAZ 4) | 215/215 PASS |
| Dashboard pytest (FAZ 5 sonu) | **260/260 PASS** |
| Dedektör pytest (başlangıç / son) | 105/105 → **119/119 PASS** |
| Tarayıcı UI (`ui_check.mjs`) | **23/23 PASS** |
| Yedek + izole geri yükleme (gerçek veri, 0002 ve 0003) | **PASS** ×2 |

Yeni dashboard testleri (45): `test_source_attribution.py` (9: run etiketi donması, aynı `cam1` iki video, eski kayıt,
RTSP sırrı, heartbeat'in canlı kareyi bekletmemesi, DB kapalıyken olay 503, 0003 downgrade/upgrade + geri doldurma
yok), `test_roles.py` (24: rol özellikleri, 10 reddedilen işlem 42501, uygulamanın gerçekten `ppe_app_test` ile
bağlanması, mevcut kurulumda sahiplik devri + migrator ile 0003 downgrade/upgrade, sıfır kurulum, idempotentlik,
parolasız CLI no-op, CLI hatasında sır yok, rol adı doğrulama, SCRAM saklama), `test_maintenance_security.py` (13:
dry-run, onay koruması, karantina/symlink, min-age, retention salt okuma, statik path traversal ×5, token yansıtmama,
genel 500, sayfada sır yok). Dedektör (14): `test_camera_ids.py`.

UI testinin bulup düzelttiği hatalar: detay penceresinde "null" metni, grafik çubuğunun hover'ı engellemesi,
ipucunun kutudan taşması, `#hash` değişiminde sekmenin değişmemesi, 390 px'te uzun değer taşması.

## Gerçek GPU / video uçtan uca

| # | Adım | Sonuç | Kanıt |
|---|---|---|---|
| 1–4 | Compose, DB healthy, Alembic 0003, `/health/ready` | PASS | `{"status":"ready","revision":["0003"]}` |
| 5–7 | GPU'da 3 kaynak, açık kimlikler `[giris] [kule] [dikey]` | PASS | `Cihaz: 0`, eski kimlik uyarısı yok |
| 8–10 | START / ONGOING / END, aynı incident | PASS | giris: 1 incident = 4 olay (start, 2 ongoing, end) → `closed` |
| 11 | End'siz ihlal | PASS | dikey: `run_end` (rapor metadata'sı) |
| 12 | Snapshot | PASS | 2 dosya, HTTP 200 `image/jpeg`, geçerli JPEG |
| 13–14 | DB'ye olay/incident, kişi raporu | PASS | 5 olay, 2 incident, 3 run raporu |
| 15–16 | History API ve dashboard sayımları | PASS | API ve UI kutucukları eşleşti (UI testi) |
| 17–18 | DB 15 sn kesinti, retry, duplicate yok | PASS | 503 → aynı istekle retry; gönderilen 5 = DB 5; `event_id` tekil; incident başına ≤1 start/end; düşürülen 0 |
| — | Ters kaynak sırası | PASS | `giris/kule/dikey` aynı dosyalara bağlı kaldı |
| — | Gerçek Ctrl+C | PASS | 2 sn'de temiz kapanış, 3 rapor, run'lar `interrupted`, çıkış 0 |
| — | Çöken dedektör | PASS | rapor yok → 90 sn sonra `unknown / heartbeat_lost`, run `no_heartbeat` |
| 19 | Dashboard restart | PASS | sayımlar aynı, RAM sıfır |
| 20 | PostgreSQL restart | PASS | sayımlar aynı, `ppe_app` yeniden bağlandı |
| 21 | Yedek / izole geri yükleme | PASS | revision, 5 tablo sayımı, 36 snapshot referansı, SHA-256 |
| 22 | Güvenlik yapılandırması | PASS | `ppe_app` süper kullanıcı değil; DELETE/TRUNCATE/DROP/CREATE 42501; token'sız/yanlış token 401; sırlar Git'te ve yedekte yok |

## SKIP / sınırlar

- Ana veritabanına gerçek geri yükleme prosedürü (BACKUP_RESTORE.md) çalıştırılmadı; yalnız izole doğrulama.
- Ana DB'de 0003 downgrade çalıştırılmadı (yalnız test veritabanlarında).
- LAN/internet erişimi ve tarayıcı kimlik doğrulaması: uygulanmadı (kapsam dışı, SECURITY.md).
- İlk Ctrl+C denemesi test düzeneği nedeniyle başarısız oldu (Git Bash zinciri "Ctrl+C'yi yok say" bayrağını
  miras bıraktı); bayrak sıfırlanınca gerçek Ctrl+C PASS. Ölen ebeveynin 3 çocuk süreci elle sonlandırıldı.

## Ana veritabanında bırakılan test verisi (temizlenmedi)

E2E çalışmaları gerçek kayıt üretti: kameralar `giris`, `kule`, `dikey`; ilk Ctrl+C denemesinin `LOOP=1` döngüsünden
14 ek run; elle sonlandırılan süreçlerden kalan **3 run `running` durumunda** (rapor gelmeyecek; `no_heartbeat`).
FAZ 5 sonu: 7 kamera, 55 run, 36 incident, 75 olay, 195 kişi raporu. İstenirse yönetim rolüyle, yedekten sonra
temizlenebilir.
