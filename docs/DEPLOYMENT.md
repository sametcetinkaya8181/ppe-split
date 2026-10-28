# Kurulum ve çalıştırma (Windows, yerel)

## Gereksinimler

Docker Desktop, Python 3.12 (dedektör), NVIDIA sürücüsü + CUDA'lı torch (GPU için), `detector/models/best.pt`.

## İlk kurulum

```powershell
Copy-Item .env.example .env
# .env: POSTGRES_PASSWORD, PPE_APP_PASSWORD, PPE_MIGRATOR_PASSWORD, INGEST_TOKEN değerlerini üretin:
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Değişken SIRASI önemlidir: PPE_*_PASSWORD satırları, onları kullanan DATABASE_URL satırlarından ÖNCE gelmeli.
docker compose up -d --build        # db → db-roles → migrate (0003) → dashboard
docker compose ps
curl http://127.0.0.1:8000/health/ready     # {"status":"ready",...,"revision":["0003"]}

# İlk yönetici (parola gizli sorulur; komut satırına yazılmaz). Web'den hesap oluşturulamaz.
docker compose exec dashboard python -m auth.cli create admin --role admin
# Diğer kullanıcılar (örnek): İSG sorumlusu yalnız iki kameraya
docker compose exec dashboard python -m auth.cli create isg_ali --role supervisor --cameras giris,kule
docker compose exec dashboard python -m auth.cli list

cd detector
py -3.12 -m venv .venv; .venv\Scripts\activate; pip install -r requirements.txt
Copy-Item .env.example .env         # INGEST_TOKEN = kök .env'deki değerle AYNI; kaynaklara [kimlik] verin
python detector.py
```

## Ortam değişkenleri

Kök `.env` (compose):

| Değişken | Açıklama |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | PostgreSQL ve yönetim (süper kullanıcı) hesabı |
| `PPE_APP_PASSWORD`, `PPE_MIGRATOR_PASSWORD` | rol ayrımı; boşsa `db-roles` atlanır (eski tek-rol kurulumu) |
| `DATABASE_URL` | dashboard bağlantısı (`ppe_app`) |
| `MIGRATE_DATABASE_URL` | migration bağlantısı (`ppe_migrator`); yoksa `DATABASE_URL` |
| `INGEST_TOKEN` | dedektör ile aynı |
| `DASHBOARD_BIND` | varsayılan `127.0.0.1` (değiştirmeden önce SECURITY.md) |
| `ACTIVE_WINDOW_SEC` | `active` durumu için canlılık penceresi (90) |
| `SECURITY_PROFILE` | `local` (varsayılan) veya `lan` (yalnız TLS vekil ile; LAN_DEPLOYMENT.md) |
| `SESSION_IDLE_MINUTES`, `SESSION_MAX_HOURS` | oturum boşta (30) / mutlak (12) süresi |
| `INGEST_TOKEN_NEXT` | yalnız token rotasyonu sırasında (SECURITY.md) |
| `PPE_SITE`, `LAN_BIND`, `PPE_TLS` | yalnız `docker-compose.lan.yml` ile |
| `HEARTBEAT_SEC`, `MAX_*` | dashboard sınırları (README) |

`detector/.env`: `INGEST_TOKEN`, `DASHBOARD_URL`, `DEVICE`, `IMGSZ`, `VID_STRIDE`, `LOOP`, `VIDEO_SOURCES` /
`YOUTUBE_n` / `SOURCE_n`, `CAMERA_ID_MODE` (`index` varsayılan | `source`), `SEND_TIMEOUT`, `EVENT_QUEUE_MAX`,
`DRAIN_TIMEOUT`.

## Kamera kimliği

```
VIDEO_SOURCES=[giris]Giriş kapısı=rtsp://kullanici:sifre@10.0.0.5/stream,[kule]Kule=videos/kule.mp4
python detector.py "[giris]Giriş kapısı=videos/a.mp4" "[kule]videos/b.mp4"     # komut satırı önceliklidir
```

Önek kullanılmazsa ve `CAMERA_ID_MODE=index` ise açılışta `UYARI: cam1: kimlik kaynak SIRASINDAN türetildi…`
yazılır. Var olan `cam1/cam2…` kayıtları korunur; yeni sabit kimlikler yeni kamera satırı olarak eklenir.

## Mevcut (FAZ 4) kurulumu FAZ 5'e yükseltme

Bu depoda uygulanan sıra (veri silinmez):

1. Dedektörü durdurun. `python scripts/backup.py --quiesce` ve `python scripts/restore_check.py <klasör>` → PASS.
2. Kök `.env`'ye `PPE_APP_PASSWORD`, `PPE_MIGRATOR_PASSWORD`, `MIGRATE_DATABASE_URL` ekleyin; `DATABASE_URL`'yi
   `ppe_app`'e çevirin; `INGEST_TOKEN`'ı üretip **aynı anda** `detector/.env`'ye de yazın. Eski `.env` kopyalarını
   `backups/` altında saklayın.
3. `docker compose up -d --build` → `db-roles` tablo sahipliğini `ppe_migrator`'a devreder, `migrate` 0003'ü
   uygular, dashboard `ppe_app` ile başlar. `/health/ready` ve `python -m db.roles check` ile doğrulayın.

Geri dönüş: `.env` yedeğini geri koyup `docker compose up -d` (süper kullanıcı bağlantısı sahiplikten bağımsız
çalışır). 0003 geri alınmak istenirse önce yedek, sonra `alembic -x allow_data_loss=1 downgrade 0002` (ana DB'de
FAZ 5'te çalıştırılmadı).

## FAZ 5 kurulumunu FAZ 6'ya yükseltme (oturum + rol + sertleştirme)

Bu depoda uygulanan sıra (veri silinmez):

1. `python scripts/backup.py --quiesce` ve `python scripts/restore_check.py <klasör>` → PASS.
2. `docker compose up -d --build` → `migrate` 0004'ü uygular (`app_users`, `user_sessions`, `audit_log`); dashboard
   root olmayan ve salt okunur container'la başlar.
3. `data/snapshots` önceki sürümde root tarafından oluşturulduysa bir kez (yalnız dizin sahipliği):
   `docker run --rm --user root -v C:/.../ppe-split-main/data:/data ppe-dashboard chown 10001:10001 /data/snapshots`
   Doğrulama: `/health/ready` → `{"status":"ready"}` (yazılamazsa 503).
4. İlk yöneticiyi oluşturun (yukarıdaki `auth.cli create`). **Bu adım yapılana kadar arayüze giriş yapılamaz.**

Geri dönüş: önceki commit'in imajıyla `docker compose up -d --build`; 0004 tabloları eski sürüm tarafından yok sayılır
(silmeye gerek yok).

## Bakım komutları

```powershell
python scripts/backup.py --quiesce
python scripts/restore_check.py backups\ppe-<UTC>
docker compose exec dashboard python -m maintenance.storage snapshots      # dry run
docker compose exec dashboard python -m maintenance.storage retention      # yalnız rapor
docker compose exec dashboard python -m db.roles check
```

## Sorun giderme

| Belirti | Neden / çözüm |
|---|---|
| `/health/ready` 503, `db: unreachable` | `docker compose ps db`; `.env`'de parola satırlarının sırası (compose uyarısı: `variable is not set`) |
| `schema: outdated` | `docker compose up -d` (migrate servisi) veya `docker compose logs migrate` |
| Dedektör log'unda `HTTP 401 … INGEST_TOKEN` | iki `.env`'deki token farklı; ikisini eşitleyin, dashboard'u yeniden başlatın |
| Dedektör `HATA: kaynak yapılandırması geçersiz` | tekrarlanan/geçersiz `[kimlik]` |
| Geçmişte "Eski kayıt: kaynak doğrulanamadı" | 0003 öncesi run; beklenen davranış |
| İhlal `unknown / heartbeat_lost` | dedektör rapor göndermeden kapandı (çökme/zorla kapatma) |
| `db-roles` hata | `docker compose logs db-roles` (parola yazdırılmaz) |
| Giriş ekranı: "hatalı ya da kilitli" | 5 hatalı denemeden sonra hesap 15 dk kilitlenir; yönetici `auth.cli set-password` ile sıfırlayabilir |
| Giriş: "Çok fazla başarısız deneme" (429) | IP başına sınır; 15 dk bekleyin |
| Hiç yönetici yok / parola unutuldu | `docker compose exec dashboard python -m auth.cli create …` veya `set-password …` |
| `/health/ready` 503, `storage: snapshots_not_writable` (admin) | yükseltme adımı 3 (dizin sahipliği) |
| Tarayıcıdan `http://<LAN-IP>:8000` → "gecersiz Host" | beklenen: local profil LAN'dan kullanılamaz (LAN_DEPLOYMENT.md) |
